# Handy Dictation — Push-to-Talk Dictation Daemon

## Status

**Phase: Self-learning infrastructure complete, collecting corrections.**
Daemon replaces Handy — Fn key push-to-talk, mlx-whisper medium on Metal GPU, silence-aware chunking, progressive paste, minimal floating overlay, anti-hallucination filtering, web-based settings UI, transcription history with correction-based self-learning.

**Current:** Phase 3a complete. Daemon logs every transcription. History tab in settings UI lets user play audio, correct transcriptions, and save corrections. Word-level diffs mine auto-substitution rules (applied post-transcription). Corrected proper nouns auto-added to whisper initial_prompt vocabulary.

**Next:** Use the tool daily and correct errors to build substitution corpus. Phase 3b: fine-tune whisper-medium once enough correction pairs accumulated (50+).

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
- **Backgrounded processes die when .app exits.** macOS sends SIGHUP to the .app's process group. Use `nohup` + `disown` in run script, `start_new_session=True` in subprocess.Popen from settings.py.
- **LLM cleanup via Ollama adds 8-15s latency.** gemma3:4b times out at 8s on cold start. Currently disabled. Needs model warmup and longer timeout (15s) if re-enabled.
- **General WER benchmarks don't reflect real-world accuracy** for this user. Trust actual test results over published numbers.
- **Post-transcription substitution works well for consistent misrecognitions.** Word-level diffs from user corrections reliably capture proper noun errors. Combined with auto-vocabulary update (feeding corrected words back to initial_prompt), this gives a two-layer fix: whisper prompt biasing + post-processing substitution.
- **Punctuation must be stripped before word-level diffing.** Without stripping, "tool." vs "EPUB." produces substitutions with embedded periods that break regex matching.

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

### Phase 3a: Self-Learning Infrastructure [DONE]
- [x] Transcription logging: daemon saves every transcription to transcription-log.json
- [x] History tab in settings UI: browse transcriptions, play audio, edit text
- [x] Correction storage: save corrected transcriptions to corrections.json
- [x] Auto-substitution: mine word-level diffs from corrections → substitutions.json
- [x] Post-transcription substitution: daemon applies learned substitutions after whisper
- [x] Auto-vocabulary: corrected proper nouns added to words.txt → whisper initial_prompt
- [x] Punctuation-aware diffing: strips punctuation before comparing words

### Phase 3b: Fine-Tuning [PLANNED]
Goal: Fine-tune whisper-medium on user's audio-correction pairs for proper nouns.
- [ ] Accumulate 50+ correction pairs through daily use
- [ ] Fine-tune whisper-medium on user's audio-correction pairs via mlx
- [ ] Evaluate if fine-tuned model improves proper noun recognition
- [ ] Integration: load fine-tuned model weights at daemon startup

Approach notes:
- Phase 3a substitutions give immediate improvement without fine-tuning
- Fine-tuning needs sufficient data (50+ diverse correction pairs)
- User's 89+ recordings in ~/.dictation/recordings/ are a starting corpus
- Ground truth for 38 recordings saved in ~/.dictation/ground-truth.json

## File Map

- `Dictation.app/` — macOS .app bundle. Double-click opens settings, starts daemon if needed.
- `scripts/long-dictate.py` — Main daemon. Push-to-talk, mlx-whisper, chunking, overlay, hallucination filter, CGEventTap.
- `scripts/settings.py` — Web-based settings GUI (hotkey, model, sounds, vocabulary, LLM toggle, daemon control, transcription history with correction UI).
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
- `recordings/` — Saved audio (89+ WAV files)
- `transcription-log.json` — Log of all daemon transcriptions (file, text, duration, model, timestamp)
- `corrections.json` — User-corrected transcriptions (keyed by WAV filename)
- `substitutions.json` — Auto-mined word substitution rules from corrections
- `ground-truth.json` — Large-v3 transcriptions for 38 recordings
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
