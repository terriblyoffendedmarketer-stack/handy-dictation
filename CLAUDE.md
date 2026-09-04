# Handy Dictation — Push-to-Talk Dictation Daemon

## Status

**Phase: Working tool, ready for daily use.**
Dictation daemon replaces Handy — Right Option push-to-talk, mlx-whisper medium on Metal GPU, silence-aware chunking, progressive paste, minimal floating overlay, anti-hallucination filtering.

**Current:** All core features complete. Auto-start at login ready to install. Accuracy matches/exceeds Cohere Transcribe. Hallucination fix (trailing silence trim + phrase filter + condition_on_previous_text=False) deployed.

**Next:** Monitor accuracy in daily use. Consider post-processing via Ollama for grammar cleanup if needed.

## Roadmap

- [x] Push-to-talk daemon with Right Option hotkey
- [x] mlx-whisper Metal GPU transcription (whisper-medium)
- [x] Silence-aware audio chunking (25s target, ±3s search window)
- [x] Progressive paste into focused app
- [x] Sound feedback (Tink on start, Pop on stop — non-blocking)
- [x] Audio saved to ~/Documents/Dictation/
- [x] Dictation.app wrapper (LSUIElement, no dock icon)
- [x] Floating overlay — animated bars during recording, chunk progress, checkmark done
- [x] Accuracy comparison vs Cohere Transcribe
- [x] Anti-hallucination: trailing silence trim + phrase filter + condition_on_previous_text=False
- [x] Auto-start at login (launchd agent)

## File Map

- `Dictation.app/` — macOS .app bundle. Double-click to start. LSUIElement (no dock icon). Needs Accessibility permission.
- `scripts/long-dictate.py` — Main daemon. Push-to-talk, mlx-whisper, chunking, overlay, anti-hallucination, progressive paste.
- `scripts/dictate` — Shell launcher that activates .venv and runs long-dictate.py.
- `scripts/install-autostart.sh` — Add/remove auto-start at login via launchd.
- `com.local.dictation.plist` — launchd agent config for auto-start.
- `.venv/` — Python venv: mlx-whisper, sounddevice, pyperclip, pynput, numpy.
- `config/settings_store.json` — Handy app config (legacy).
- `prompts/cleanup-prompt.txt` — Gemma post-processing prompt (legacy, for Handy's ctrl+space mode).
- `scripts/add-custom-word.sh` — Add words to Handy's custom word list.
- `scripts/fix-word-substitution.sh` — Fix Handy's word_correction_threshold bug.
- `scripts/switch-handy-provider.sh` — Switch Handy between Apple Intelligence and Ollama.
- `scripts/install-add-word-service.sh` — Install macOS Quick Action for adding words.

## Setup & Run

```bash
# First time
cd "/Users/apple/Documents/Claude Code/handy-dictation"
python3 -m venv .venv
source .venv/bin/activate
pip install mlx-whisper sounddevice pyperclip pynput numpy

# Run
open Dictation.app          # preferred — gets proper accessibility permission
# or: ./scripts/dictate     # if Terminal has accessibility permission

# Auto-start at login
bash scripts/install-autostart.sh          # install
bash scripts/install-autostart.sh remove   # uninstall

# First run downloads whisper-medium model (~1.5GB)
```

Grant Accessibility to Dictation.app (or Terminal) in System Settings > Privacy & Security > Accessibility.

## Architecture

- AppKit NSRunLoop on main thread (drives overlay panel)
- pynput keyboard listener in background thread
- Audio callback in sounddevice thread (also feeds audio_level to overlay bars)
- Transcription in daemon thread (one per recording)
- Overlay updates via CFRunLoopPerformBlock to main thread
- Quartz CGColorCreateGenericRGB for layer colors (avoids PyObjC pointer warnings)
- Anti-hallucination: trim_trailing_silence() before chunking, strip_hallucinations() after transcription, condition_on_previous_text=False
