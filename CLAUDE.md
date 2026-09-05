# Handy Dictation — Push-to-Talk Dictation Daemon

## Status

**Phase: Daily driver, accuracy optimization in progress.**
Daemon replaces Handy — Fn key push-to-talk, mlx-whisper medium on Metal GPU, silence-aware chunking, progressive paste, minimal floating overlay, anti-hallucination filtering, web-based settings UI.

**Current:** All core features complete. Benchmark run across 6 models (37 recordings). whisper-medium is the production model — best balance of accuracy (18.1% WER) and no hallucination. CGEventTap auto-re-enable fix for daemon reliability.

**Next:** Voice-level self-learning (speaker-adapted ASR). See Roadmap Phase 3.

## Critical Learnings (DO NOT re-test — these are settled)

- **whisper-medium is the best model for this user's voice.** Benchmark WER (37 recordings): medium 18.1%, distil-large-v3 16.9%, turbo 22.1%, qwen3-asr-0.6b 28.5%. BUT distil-large-v3 hallucinates entire AI-assistant-style paragraphs (generated fake text like "Please provide me with the dictated text..."). Medium does not.
- **whisper-large-v3-turbo is WORSE than medium.** Tested twice. Poor punctuation, wrong words ("then" vs "than"). Do not suggest turbo.
- **distil-whisper-large-v3 hallucinates badly.** Despite winning on WER (16.9%), it generates paragraphs of text never spoken — AI-assistant-style responses. Reverted to medium.
- **qwen3-asr-0.6b performs poorly on casual speech** (28.5% WER) despite great LibriSpeech benchmarks. Don't recommend for this use case.
- **qwen3-asr-1.7b won't fit on disk** (~4.2GB needed, only ~3GB free).
- **Moonshine Base produces garbage** on casual speech (100%+ WER on longer clips). Skip it.
- **Parakeet TDT 0.6B not supported by mlx-audio** (no model_type mapping). Skip it.
- **beam_size is NOT implemented in mlx-whisper.** Only greedy decoder works. best_of also not implemented (silently ignored). Do not add these params.
- **CGEventTap gets silently disabled by macOS** when the process is slow. Must re-enable periodically (5s timer).
- **NSEvent monitor objects get garbage collected** by Python — causes Fn detection to silently stop. CGEventTap approach is required.
- **ffmpeg not on PATH when launched from .app.** Must either set PATH in launcher or pass numpy arrays directly.
- **`source activate` breaks in .app context** when venv path has spaces. Call venv python directly instead.
- **LLM cleanup via Ollama adds 8-15s latency.** gemma3:4b times out at 8s on cold start. Currently disabled. Needs model warmup and longer timeout (15s) if re-enabled.
- **General WER benchmarks don't reflect real-world accuracy** for this user. Trust actual test results over published numbers.

## Roadmap

### Phase 1: Core Daemon [DONE]
- [x] Push-to-talk daemon with Fn hotkey (CGEventTap)
- [x] mlx-whisper Metal GPU transcription (whisper-medium)
- [x] Silence-aware audio chunking (25s target, ±3s search window)
- [x] Progressive paste into focused app
- [x] Sound feedback (Tink/Pop, non-blocking)
- [x] Audio saved to ~/.dictation/recordings/
- [x] Dictation.app wrapper (LSUIElement, no dock icon)
- [x] Floating overlay (animated bars, chunk progress, checkmark)
- [x] Anti-hallucination (silence trim, phrase filter, AI-response detection)
- [x] Auto-start at login (Login Items via osascript)

### Phase 2: Configuration & Accuracy [DONE]
- [x] Custom vocabulary (words.txt → whisper initial_prompt)
- [x] Configurable hotkey, model, sounds (config.json)
- [x] Web-based settings UI with daemon start/stop/status
- [x] Double-click app opens settings in browser
- [x] LLM cleanup toggle (Ollama post-processing, currently disabled)
- [x] CGEventTap auto-re-enable timer (5s)
- [x] Recording watchdog (force-stop after 5min)
- [x] Auto-restart daemon on model/hotkey change from settings
- [x] Benchmark script (37 recordings, 6 models tested)

### Phase 3: Voice-Level Self-Learning [PLANNED]
Goal: Personalized ASR — the tool learns how the user pronounces specific words.
- [ ] Correction collection: detect when user corrects transcribed text, save audio-correction pairs
- [ ] Audio fingerprint database: map audio segments to correct transcriptions
- [ ] Fine-tune whisper-medium on user's audio-correction pairs
- [ ] Evaluate if fine-tuned model improves proper noun recognition (Seedhe Maut, etc.)
- [ ] Integration: load fine-tuned model weights at daemon startup

Approach notes:
- Custom words/initial_prompt has limited effectiveness for unusual nouns
- LLM post-processing can't fix it — transcript is too far from actual speech for LLM to infer
- Need audio-level solution: either fine-tuning or audio similarity matching
- User's 37+ recordings in ~/.dictation/recordings/ are a starting corpus
- Ground truth for all recordings saved in ~/.dictation/ground-truth.json

## File Map

- `Dictation.app/` — macOS .app bundle. Double-click opens settings, starts daemon if needed.
- `scripts/long-dictate.py` — Main daemon. Push-to-talk, mlx-whisper, chunking, overlay, hallucination filter, CGEventTap.
- `scripts/settings.py` — Web-based settings GUI (hotkey, model, sounds, vocabulary, LLM toggle, daemon control).
- `scripts/benchmark.py` — Accuracy benchmark. Ground truth via large-v3, compares models by WER.
- `scripts/dictate` — Shell launcher for terminal use.
- `scripts/install-app.sh` — Install runtime to ~/.dictation/ and app to /Applications/.
- `scripts/install-autostart.sh` — Add/remove auto-start at login.
- `.venv/` — Python venv: mlx-whisper, sounddevice, pyperclip, pynput, numpy, mlx-qwen3-asr, mlx-audio.

## Runtime Files (~/.dictation/)

- `long-dictate.py` — Copy of daemon script
- `settings.py` — Copy of settings script
- `config.json` — Runtime config (hotkey, model, sounds, llm_cleanup)
- `words.txt` — Custom vocabulary
- `.venv/` — Python virtual environment
- `recordings/` — Saved audio (37+ WAV files)
- `ground-truth.json` — Large-v3 transcriptions for all recordings
- `benchmark-results.json` — WER results per model

## Setup & Run

```bash
cd "/Users/apple/Documents/Claude Code/handy-dictation"
python3 -m venv .venv && source .venv/bin/activate
pip install mlx-whisper sounddevice pyperclip pynput numpy
bash scripts/install-app.sh
open /Applications/Dictation.app
# Grant Accessibility in System Settings > Privacy & Security > Accessibility
```

## Architecture

- AppKit NSRunLoop on main thread (drives overlay panel)
- Quartz CGEventTap for Fn key detection (with auto-re-enable timer)
- Audio callback in sounddevice thread
- Transcription in daemon thread (one per recording)
- Overlay updates via CFRunLoopPerformBlock to main thread
- Settings: stdlib http.server on port 9876, browser opened by launcher script
