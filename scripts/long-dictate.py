#!/usr/bin/env python3
# long-dictate.py — Push-to-talk dictation daemon with Metal-accelerated transcription.
# Replaces Handy for all dictation lengths. No dropped chunks on long recordings.
#
# Usage:
#   ./scripts/dictate              # Start daemon (Right Option = push-to-talk)
#   ./scripts/dictate file.wav     # One-shot: transcribe an audio file
#
# Hotkey: Hold RIGHT OPTION to record, release to transcribe + paste.
#   Fn key uses NSEvent flagsChanged (native AppKit), other keys use pynput.
#   Change hotkey in ~/.dictation/config.json or set DICTATION_KEY env var.
#
# Requires: mlx-whisper, sounddevice, pyperclip, pynput (in .venv)
# Run via Dictation.app for proper accessibility permissions:
#   open ../Dictation.app
# Or grant accessibility to Terminal in System Settings.
#
# Gotchas:
# - First run downloads the Whisper model (~1.5GB). Subsequent runs instant.
# - Model stays loaded — no delay between recordings.
# - Metal GPU acceleration (Apple Silicon only).
# - Under 60s: single-pass (~1-2s). Over 60s: chunked progressive paste (~2s first text).
# - Audio saved to ~/Documents/Dictation/ for re-transcription if needed.
# - macOS will ask for microphone permission on first run.
# - AppKit overlay must run on the main thread. pynput listener runs in a background thread.
# - Use Quartz.CGColorCreateGenericRGB for layer colors — NSColor.CGColor() causes
#   ObjCPointerWarning in PyObjC and can silently fail.
# - Show overlay BEFORE playing sound or capturing frontmost app — eliminates
#   perceived startup lag. Sound via Popen (non-blocking).
# - Whisper hallucinates YouTube phrases ("Thanks for watching!") on trailing
#   silence. Fix: trim silence from end of audio + filter known phrases.

import sys
import os
import json
import wave
import time
import subprocess
import tempfile
import threading
import random
import numpy as np
import sounddevice as sd
import pyperclip
from pynput import keyboard

SAMPLE_RATE = 16000
CHUNK_TARGET_S = 28
CHUNK_SEARCH_S = 3
DICTATION_DIR = os.path.expanduser("~/.dictation")
CONFIG_PATH = os.path.join(DICTATION_DIR, "config.json")
WORDS_PATH = os.path.join(DICTATION_DIR, "words.txt")
LOG_PATH = os.path.join(DICTATION_DIR, "transcription-log.json")
SUBSTITUTIONS_PATH = os.path.join(DICTATION_DIR, "substitutions.json")

KEY_MAP = {
    "right_option": keyboard.Key.alt_r,
    "right_alt": keyboard.Key.alt_r,
    "right_cmd": keyboard.Key.cmd_r,
    "right_shift": keyboard.Key.shift_r,
    "left_option": keyboard.Key.alt_l,
    "left_alt": keyboard.Key.alt_l,
    "caps_lock": keyboard.Key.caps_lock,
    "f18": keyboard.Key.f18,
    "f19": keyboard.Key.f19,
    "f20": keyboard.Key.f20,
    "fn": None,
}


def load_config():
    cfg = {
        "hotkey": "fn",
        "model": "mlx-community/whisper-medium-mlx",
        "language": "en",
        "sound_start": "/System/Library/Sounds/Tink.aiff",
        "sound_stop": "/System/Library/Sounds/Pop.aiff",
        "llm_cleanup": False,
        "llm_model": "gemma3:4b",
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def load_custom_words():
    """Load custom vocabulary from words.txt as bare word list for initial_prompt.
    Sentence-style prompts cause whisper to hallucinate prompt text when audio
    doesn't match the prompt topic. Bare word lists bias spelling without
    poisoning the decoder's language model."""
    if not os.path.exists(WORDS_PATH):
        return ""
    words = []
    with open(WORDS_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    if not words:
        return ""
    return "Vocabulary: " + ", ".join(words) + "."


def log_transcription(wav_filename, text, duration):
    log = []
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH) as f:
                log = json.load(f)
        except Exception:
            log = []
    log.append({
        "file": wav_filename,
        "text": text,
        "duration": round(duration, 1),
        "model": MODEL_REPO,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    log = log[-500:]
    try:
        with open(LOG_PATH, "w") as f:
            json.dump(log, f, indent=2)
    except Exception:
        pass


def load_substitutions():
    if os.path.exists(SUBSTITUTIONS_PATH):
        try:
            with open(SUBSTITUTIONS_PATH) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def apply_substitutions(text, word_probs=None):
    """Apply learned substitutions, gated by whisper's per-word confidence.
    word_probs: dict mapping lowercase word -> probability (0.0-1.0).
    Only substitutes when whisper was uncertain (prob < 0.7) or no probs available."""
    import re
    subs = load_substitutions()
    if not subs:
        return text
    CONFIDENCE_THRESHOLD = 0.7
    for orig, replacement in subs.items():
        if word_probs and orig.lower() in word_probs:
            if word_probs[orig.lower()] >= CONFIDENCE_THRESHOLD:
                continue
        pattern = re.compile(r'\b' + re.escape(orig) + r'\b', re.IGNORECASE)
        text = pattern.sub(replacement, text)
    return text


def extract_word_probs(result):
    """Extract per-word confidence scores from whisper result with word_timestamps."""
    probs = {}
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            word = w.get("word", "").strip().strip(".,!?;:\"'()[]{}").lower()
            if word:
                probs[word] = w.get("probability", 1.0)
    return probs


CONFIG = load_config()
MODEL_REPO = os.environ.get("WHISPER_MODEL", CONFIG["model"])
HOTKEY_NAME = os.environ.get("DICTATION_KEY", CONFIG["hotkey"])
HOTKEY = KEY_MAP.get(HOTKEY_NAME, keyboard.Key.alt_r)
INITIAL_PROMPT = load_custom_words()

recording = False
frames = []
audio_stream = None
record_start = None
target_app = None
audio_level = 0.0
wav_dir = os.path.join(DICTATION_DIR, "recordings")
overlay = None


# ── Overlay ──────────────────────────────────────────────────────────────────

class Overlay:
    """Minimal floating status pill at bottom-center of screen."""

    BAR_COLOR = (0.95, 0.3, 0.25, 1.0)
    BLUE = (0.3, 0.6, 1.0, 1.0)
    GREEN = (0.3, 0.8, 0.45, 1.0)
    BG = (0.08, 0.08, 0.08, 0.55)
    NUM_BARS = 4
    BAR_W = 3
    BAR_GAP = 3
    MAX_BAR_H = 14
    MIN_BAR_H = 3

    def __init__(self):
        import AppKit, Quartz

        self._AK = AppKit
        self._Q = Quartz
        self._timer = None
        self._start_time = 0

        screen = AppKit.NSScreen.mainScreen().frame()
        pw, ph = 56, 24
        self._pw, self._ph = pw, ph
        x = screen.origin.x + (screen.size.width - pw) / 2
        y = screen.origin.y + 50

        self._panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, y), (pw, ph)),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self._panel.setLevel_(AppKit.NSStatusWindowLevel)
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self._panel.setIgnoresMouseEvents_(True)
        self._panel.setHasShadow_(True)
        self._panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )

        content = AppKit.NSView.alloc().initWithFrame_(((0, 0), (pw, ph)))

        self._bg = AppKit.NSView.alloc().initWithFrame_(((0, 0), (pw, ph)))
        self._bg.setWantsLayer_(True)
        self._bg.layer().setCornerRadius_(ph / 2)
        self._bg.layer().setMasksToBounds_(True)
        self._bg.layer().setBackgroundColor_(self._cg(*self.BG))
        content.addSubview_(self._bg)

        bars_total = self.NUM_BARS * self.BAR_W + (self.NUM_BARS - 1) * self.BAR_GAP
        start_x = (pw - bars_total) / 2
        self._bars = []
        for i in range(self.NUM_BARS):
            bx = start_x + i * (self.BAR_W + self.BAR_GAP)
            bar = AppKit.NSView.alloc().initWithFrame_(
                ((bx, (ph - self.MIN_BAR_H) / 2), (self.BAR_W, self.MIN_BAR_H))
            )
            bar.setWantsLayer_(True)
            bar.layer().setCornerRadius_(1.5)
            bar.layer().setBackgroundColor_(self._cg(*self.BAR_COLOR))
            bar.setHidden_(True)
            content.addSubview_(bar)
            self._bars.append(bar)

        self._label = AppKit.NSTextField.labelWithString_("")
        self._label.setFrame_(((0, 2), (pw, ph - 4)))
        self._label.setTextColor_(AppKit.NSColor.whiteColor())
        self._label.setFont_(AppKit.NSFont.monospacedSystemFontOfSize_weight_(11, AppKit.NSFontWeightMedium))
        self._label.setAlignment_(AppKit.NSTextAlignmentCenter)
        self._label.setBackgroundColor_(AppKit.NSColor.clearColor())
        self._label.setBezeled_(False)
        self._label.setEditable_(False)
        self._label.setSelectable_(False)
        self._label.setHidden_(True)
        content.addSubview_(self._label)

        self._panel.setContentView_(content)

    def _cg(self, r, g, b, a):
        return self._Q.CGColorCreateGenericRGB(r, g, b, a)

    def _on_main(self, fn):
        self._AK.CFRunLoopPerformBlock(
            self._AK.CFRunLoopGetMain(), self._AK.kCFRunLoopCommonModes, fn
        )
        self._AK.CFRunLoopWakeUp(self._AK.CFRunLoopGetMain())

    def _resize_pill(self, w):
        pw, ph = w, self._ph
        screen = self._AK.NSScreen.mainScreen().frame()
        x = screen.origin.x + (screen.size.width - pw) / 2
        y = screen.origin.y + 50
        self._panel.setFrame_display_(((x, y), (pw, ph)), True)
        self._panel.contentView().setFrame_(((0, 0), (pw, ph)))
        self._bg.setFrame_(((0, 0), (pw, ph)))

    def show_recording(self):
        def _do():
            self._resize_pill(56)
            self._label.setHidden_(True)
            bars_total = self.NUM_BARS * self.BAR_W + (self.NUM_BARS - 1) * self.BAR_GAP
            start_x = (56 - bars_total) / 2
            for i, bar in enumerate(self._bars):
                bx = start_x + i * (self.BAR_W + self.BAR_GAP)
                bar.setFrame_(((bx, (self._ph - self.MIN_BAR_H) / 2), (self.BAR_W, self.MIN_BAR_H)))
                bar.layer().setBackgroundColor_(self._cg(*self.BAR_COLOR))
                bar.setHidden_(False)
            self._panel.orderFront_(None)
            self._start_time = time.time()
            self._stop_timer()
            self._tick_bars()
        self._on_main(_do)

    def _tick_bars(self):
        if not recording:
            return
        level = min(1.0, audio_level * 25)
        for bar in self._bars:
            bl = level * (0.3 + 0.7 * random.random())
            h = max(self.MIN_BAR_H, int(bl * self.MAX_BAR_H))
            bx = bar.frame().origin.x
            bar.setFrame_(((bx, (self._ph - h) / 2), (self.BAR_W, h)))
        self._timer = threading.Timer(0.08, lambda: self._on_main(self._tick_bars))
        self._timer.daemon = True
        self._timer.start()

    def show_transcribing(self, chunk_i=0, chunk_n=1):
        def _do():
            self._stop_timer()
            for bar in self._bars:
                bar.setHidden_(True)
            self._resize_pill(48)
            self._label.setFrame_(((0, 2), (48, self._ph - 4)))
            self._label.setTextColor_(
                self._AK.NSColor.colorWithRed_green_blue_alpha_(*self.BLUE)
            )
            if chunk_n > 1:
                self._label.setStringValue_(f"{chunk_i}/{chunk_n}")
            else:
                self._label.setStringValue_("...")
            self._label.setHidden_(False)
            self._panel.orderFront_(None)
        self._on_main(_do)

    def show_done(self):
        def _do():
            self._stop_timer()
            for bar in self._bars:
                bar.setHidden_(True)
            self._resize_pill(36)
            self._label.setFrame_(((0, 2), (36, self._ph - 4)))
            self._label.setTextColor_(
                self._AK.NSColor.colorWithRed_green_blue_alpha_(*self.GREEN)
            )
            self._label.setStringValue_("✓")
            self._label.setHidden_(False)
            self._panel.orderFront_(None)
            threading.Timer(0.8, lambda: self._on_main(self._hide)).start()
        self._on_main(_do)

    def _hide(self):
        self._panel.orderOut_(None)

    def hide(self):
        self._on_main(self._hide)

    def _stop_timer(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None


# ── Audio ────────────────────────────────────────────────────────────────────

def audio_callback(indata, frame_count, time_info, status):
    global audio_level
    if recording:
        frames.append(indata.copy())
        audio_level = float(np.sqrt(np.mean(indata ** 2)))


def start_recording():
    global recording, frames, audio_stream, record_start, target_app, audio_level
    frames = []
    audio_level = 0.0
    recording = True
    record_start = time.time()

    if overlay:
        overlay.show_recording()

    target_app = get_frontmost_app()

    audio_stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=audio_callback,
        blocksize=1024,
    )
    audio_stream.start()
    subprocess.Popen(
        ["afplay", CONFIG["sound_start"]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("  [REC]", end="", flush=True)


def stop_recording():
    global recording, audio_stream, audio_level
    recording = False
    audio_level = 0.0
    if audio_stream:
        audio_stream.stop()
        audio_stream.close()
        audio_stream = None

    subprocess.Popen(
        ["afplay", CONFIG["sound_stop"]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    if not frames:
        print(" (empty)")
        if overlay:
            overlay.hide()
        return None

    audio = np.concatenate(frames, axis=0).flatten()
    duration = len(audio) / SAMPLE_RATE
    print(f" {duration:.1f}s", flush=True)
    return audio


def split_at_silence(audio):
    chunk_target = CHUNK_TARGET_S * SAMPLE_RATE
    search_window = CHUNK_SEARCH_S * SAMPLE_RATE

    if len(audio) <= chunk_target + search_window:
        return [audio]

    chunks = []
    pos = 0
    while pos < len(audio):
        end = min(pos + chunk_target, len(audio))
        if end < len(audio) - SAMPLE_RATE:
            ss = max(pos + chunk_target - search_window, pos)
            se = min(pos + chunk_target + search_window, len(audio))
            w = audio[ss:se]
            fs = SAMPLE_RATE // 10
            energies = [
                np.sqrt(np.mean(w[i : i + fs] ** 2))
                for i in range(0, len(w) - fs, fs)
            ]
            if energies:
                end = ss + np.argmin(energies) * fs
        chunks.append(audio[pos:end])
        pos = end

    return chunks


def trim_trailing_silence(audio, threshold=0.008, buffer_s=0.3):
    """Remove trailing silence to prevent whisper hallucination."""
    frame_size = SAMPLE_RATE // 10
    last_speech = 0
    for i in range(0, len(audio) - frame_size, frame_size):
        if np.sqrt(np.mean(audio[i : i + frame_size] ** 2)) > threshold:
            last_speech = i + frame_size
    end = min(len(audio), last_speech + int(buffer_s * SAMPLE_RATE))
    if len(audio) - end > SAMPLE_RATE:
        return audio[:end]
    return audio


HALLUCINATION_TAILS = [
    "thanks for watching",
    "thank you for watching",
    "thank you.",
    "thank you!",
    "please subscribe",
    "like and subscribe",
    "see you next time",
    "see you in the next video",
    "don't forget to subscribe",
    "subscribe to the channel",
]

HALLUCINATION_PATTERNS = [
    "please provide",
    "i need the original",
    "i need the text",
    "here's the corrected",
    "let me know if",
    "feel free to",
    "i'll deliver the",
    "once you paste",
    "is there anything else",
]


def strip_hallucinations(text):
    stripped = text.rstrip(" !.,")
    lower = stripped.lower()
    for phrase in HALLUCINATION_TAILS:
        if lower.endswith(phrase):
            text = stripped[: -len(phrase)].rstrip(" .,!?")
            break
    # Detect AI-assistant-style hallucinations (whole segments of invented text)
    for pattern in HALLUCINATION_PATTERNS:
        if pattern in text.lower():
            print(f"  [!] Hallucination detected: '{pattern}' — dropping segment", flush=True)
            return ""
    return text.strip()


def llm_cleanup(text, word_probs=None):
    config = load_config()
    if not config.get("llm_cleanup") or not text:
        return text
    try:
        import urllib.request
        model = config.get("llm_model", "gemma3:4b")
        uncertain = ""
        if word_probs:
            low_conf = [w for w, p in word_probs.items() if p < 0.5]
            if low_conf:
                uncertain = (
                    " The transcriber was uncertain about these words "
                    "(they may be wrong): " + ", ".join(low_conf) + "."
                )
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": (
                    "Fix transcription errors in the following dictated text. "
                    "The speaker may mumble or use informal grammar — preserve "
                    "their actual words, don't rewrite to sound more polished. "
                    "Only fix clear mishearings, spelling, and capitalization." +
                    uncertain +
                    " Output ONLY the corrected text, nothing else."
                )},
                {"role": "user", "content": text},
            ],
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            cleaned = result["choices"][0]["message"]["content"].strip()
            if cleaned and len(cleaned) > len(text) * 0.3:
                return cleaned
    except Exception as e:
        print(f"  (LLM cleanup failed: {e})", flush=True)
    return text


def warmup_llm():
    """Ping Ollama to pre-load the LLM so first cleanup isn't slow."""
    try:
        import urllib.request
        config = load_config()
        if not config.get("llm_cleanup"):
            return
        model = config.get("llm_model", "gemma3:4b")
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=30)
        print(" LLM ready.", end="", flush=True)
    except Exception:
        pass


def save_wav(audio, path):
    audio_int16 = (audio * 32767).astype(np.int16)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_int16.tobytes())


def get_frontmost_app():
    result = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to get bundle identifier of first process whose frontmost is true',
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def paste_text(text, target_app=None):
    pyperclip.copy(text)
    time.sleep(0.05)
    if target_app:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application id "{target_app}" to activate',
            ],
            capture_output=True,
        )
        time.sleep(0.1)
    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ],
        capture_output=True,
    )


def transcribe_and_paste(audio, app_id=None):
    import mlx_whisper

    global INITIAL_PROMPT
    INITIAL_PROMPT = load_custom_words()

    os.makedirs(wav_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    wav_path = os.path.join(wav_dir, f"dictation-{timestamp}.wav")
    save_wav(audio, wav_path)

    audio = trim_trailing_silence(audio)
    chunks = split_at_silence(audio)
    n = len(chunks)

    if overlay:
        overlay.show_transcribing(1, n)

    transcribe_opts = dict(
        path_or_hf_repo=MODEL_REPO, language="en",
        condition_on_previous_text=False,
        hallucination_silence_threshold=2.0,
        temperature=0.0,
        compression_ratio_threshold=2.4,
        word_timestamps=True,
    )
    if INITIAL_PROMPT:
        transcribe_opts["initial_prompt"] = INITIAL_PROMPT

    final_text = ""

    if n == 1:
        t0 = time.time()
        result = mlx_whisper.transcribe(audio, **transcribe_opts)
        elapsed = time.time() - t0
        text = strip_hallucinations(result["text"].strip())
        word_probs = extract_word_probs(result)
        text = apply_substitutions(text, word_probs)
        text = llm_cleanup(text, word_probs)
        final_text = text

        if text:
            paste_text(text, target_app=app_id)
            print(f"  -> {elapsed:.1f}s | {text[:120]}{'...' if len(text)>120 else ''}")
        else:
            print("  (no speech)")
    else:
        print(f"  {n} chunks |", end="", flush=True)
        all_text = []
        prev_text = ""
        for i, chunk in enumerate(chunks):
            if overlay:
                overlay.show_transcribing(i + 1, n)
            chunk_opts = dict(transcribe_opts)
            if prev_text:
                prompt_parts = []
                if INITIAL_PROMPT:
                    prompt_parts.append(INITIAL_PROMPT)
                prompt_parts.append(prev_text[-200:])
                chunk_opts["initial_prompt"] = " ".join(prompt_parts)
            result = mlx_whisper.transcribe(chunk, **chunk_opts)
            text = strip_hallucinations(result["text"].strip())
            word_probs = extract_word_probs(result)
            text = apply_substitutions(text, word_probs)
            text = llm_cleanup(text, word_probs)

            if text:
                all_text.append(text)
                prev_text = text
                prefix = " " if i > 0 else ""
                paste_text(prefix + text, target_app=app_id)
                print(f" [{i+1}]", end="", flush=True)

        final_text = " ".join(all_text)
        print(f" | {len(final_text)} chars")

    log_transcription(os.path.basename(wav_path), final_text,
                      len(audio) / SAMPLE_RATE)

    if overlay:
        overlay.show_done()


def cleanup_old_recordings(max_age_days=14):
    """Delete recordings older than max_age_days, preserving any with corrections."""
    wav_dir = os.path.join(DICTATION_DIR, "recordings")
    if not os.path.isdir(wav_dir):
        return
    corrections = set()
    if os.path.exists(os.path.join(DICTATION_DIR, "corrections.json")):
        try:
            with open(os.path.join(DICTATION_DIR, "corrections.json")) as f:
                corrections = set(json.load(f).keys())
        except Exception:
            pass
    gt = set()
    gt_path = os.path.join(DICTATION_DIR, "ground-truth.json")
    if os.path.exists(gt_path):
        try:
            with open(gt_path) as f:
                gt = set(json.load(f).keys())
        except Exception:
            pass
    preserve = corrections | gt
    cutoff = time.time() - max_age_days * 86400
    deleted = 0
    for fname in os.listdir(wav_dir):
        if not fname.endswith(".wav"):
            continue
        if fname in preserve:
            continue
        fpath = os.path.join(wav_dir, fname)
        if os.path.getmtime(fpath) < cutoff:
            os.remove(fpath)
            deleted += 1
    if deleted:
        print(f"  Cleaned up {deleted} recordings older than {max_age_days} days")


def load_model():
    import mlx_whisper

    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    mlx_whisper.transcribe(silence, path_or_hf_repo=MODEL_REPO, language="en")


def transcribe_file(filepath):
    if not os.path.exists(filepath):
        print(f"  File not found: {filepath}")
        sys.exit(1)

    print("  Loading model...")
    load_model()

    with wave.open(filepath) as w:
        audio = (
            np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(
                np.float32
            )
            / 32768.0
        )

    print(f"  Transcribing: {filepath} ({len(audio)/SAMPLE_RATE:.1f}s)")
    transcribe_and_paste(audio)


def run_daemon():
    global overlay

    print(f"\n  Dictation Daemon")
    print(f"  Hotkey: {HOTKEY_NAME} (hold to talk, release to transcribe)")
    print(f"  Model: {MODEL_REPO}")
    print("  Loading model...", end="", flush=True)

    cleanup_old_recordings(max_age_days=14)

    t0 = time.time()
    load_model()
    print(f" ready ({time.time()-t0:.1f}s)")

    threading.Thread(target=warmup_llm, daemon=True).start()

    import AppKit

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    overlay = Overlay()

    print(f"\n  Listening. Hold {HOTKEY_NAME} and speak. Ctrl+C to quit.\n")

    is_pressed = False

    def handle_press():
        nonlocal is_pressed
        if not is_pressed:
            is_pressed = True
            start_recording()

    def handle_release():
        nonlocal is_pressed
        if is_pressed:
            is_pressed = False
            app_id = target_app
            audio = stop_recording()
            if audio is not None and len(audio) > SAMPLE_RATE * 0.3:
                threading.Thread(
                    target=transcribe_and_paste, args=(audio, app_id), daemon=True
                ).start()

    # Watchdog: if recording runs >5min, force release (stuck state protection)
    MAX_RECORDING_S = 300

    def check_stuck_recording():
        if recording and record_start and (time.time() - record_start > MAX_RECORDING_S):
            print("  [!] Recording exceeded 5min — force stopping", flush=True)
            handle_release()

    recording_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        30.0, True, lambda t: check_stuck_recording()
    )

    if HOTKEY_NAME == "fn":
        import Quartz
        FN_FLAG = 0x800000
        fn_was_down = [False]

        def cg_event_callback(proxy, event_type, event, refcon):
            flags = Quartz.CGEventGetFlags(event)
            fn_down = bool(flags & FN_FLAG)
            if fn_down and not fn_was_down[0]:
                fn_was_down[0] = True
                handle_press()
            elif not fn_down and fn_was_down[0]:
                fn_was_down[0] = False
                handle_release()
            return event

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            cg_event_callback,
            None,
        )
        if tap:
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            Quartz.CFRunLoopAddSource(
                Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
            )
            Quartz.CGEventTapEnable(tap, True)
            print("  Fn key: CGEventTap active", flush=True)

            # macOS disables event taps when the process is slow to respond.
            # Re-enable periodically so Fn detection doesn't silently die.
            def reenable_tap():
                if not Quartz.CGEventTapIsEnabled(tap):
                    print("  [!] CGEventTap was disabled — re-enabling", flush=True)
                    Quartz.CGEventTapEnable(tap, True)

            tap_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                5.0, True, lambda t: reenable_tap()
            )
        else:
            print("  ERROR: CGEventTap failed — check Accessibility permission", flush=True)
    else:
        def on_press(key):
            if key == HOTKEY:
                handle_press()

        def on_release(key):
            if key == HOTKEY:
                handle_release()

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()

    try:
        AppKit.NSRunLoop.currentRunLoop().run()
    except KeyboardInterrupt:
        print("\n  Stopped.")


def main():
    if len(sys.argv) > 1:
        transcribe_file(sys.argv[1])
    else:
        run_daemon()


if __name__ == "__main__":
    main()
