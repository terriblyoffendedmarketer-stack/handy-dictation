# Dictation — Free Offline Push-to-Talk for macOS

A fully local, zero-cost speech-to-text tool for macOS (Apple Silicon). Hold a key, speak, release — your words are transcribed and pasted into the focused app. No cloud, no subscription, no API keys.

Built with [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) (Metal GPU acceleration) for fast, accurate transcription entirely on-device.

## Features

- **Push-to-talk** — Hold Fn (or any configurable key) to record, release to transcribe + paste
- **Fast** — Short recordings transcribe in ~1-2s, long recordings use progressive paste (first text in ~2s)
- **Accurate** — Matches or exceeds Cohere Transcribe; never drops content on long recordings
- **Custom vocabulary** — Teach it proper nouns, abbreviations, and technical terms
- **Minimal overlay** — Tiny transparent pill shows recording/transcribing/done status
- **Anti-hallucination** — Filters out Whisper's "Thanks for watching!" artifacts
- **Settings UI** — Web-based config for hotkey, model, sounds, and vocabulary
- **Auto-start** — Optional login item so it's always ready

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/terriblyoffendedmarketer-stack/handy-dictation.git
cd handy-dictation
python3 -m venv .venv
source .venv/bin/activate
pip install mlx-whisper sounddevice pyperclip pynput numpy pyobjc-framework-Cocoa pyobjc-framework-Quartz
```

### 2. Install the app

```bash
bash scripts/install-app.sh
```

This copies the runtime to `~/.dictation/` and installs `Dictation.app` to `/Applications/`.

### 3. Grant permissions

Open **System Settings > Privacy & Security**:
- **Accessibility** — Enable `Dictation.app`
- **Microphone** — Allow when prompted on first recording

### 4. Launch

Double-click **Dictation.app** in `/Applications/`, or use Spotlight. First launch downloads the Whisper model (~1.5GB).

### 5. Use it

Hold **Fn** to record, release to transcribe and paste. That's it.

## Configuration

Double-click **Dictation.app** while the daemon is running to open the settings page, or run:

```bash
python3 ~/.dictation/settings.py
```

### Hotkey options

| Key | Config value |
|-----|-------------|
| **Fn (Globe)** | `fn` (default) |
| Right Option (⌥) | `right_option` |
| Right Command (⌘) | `right_cmd` |
| Caps Lock (⇪) | `caps_lock` |
| F18–F20 | `f18`, `f19`, `f20` |

If using Fn, set **System Settings > Keyboard > "Press Globe key to"** to **"Do Nothing"** so macOS doesn't intercept it.

### Custom vocabulary

Edit `~/.dictation/words.txt` — one word per line. Helps Whisper recognize proper nouns and technical terms (e.g., `EPUB`, `KOReader`, `Calibre`). Changes take effect on the next recording, no restart needed.

### Config file

`~/.dictation/config.json` — hotkey, model, language, start/stop sounds. Edit directly or use the settings UI.

## Auto-start at login

```bash
bash scripts/install-autostart.sh          # enable
bash scripts/install-autostart.sh remove   # disable
```

## How it works

- **Model**: `whisper-medium` via mlx-whisper (Metal GPU, Apple Silicon)
- **Short recordings** (<28s): single-pass transcription
- **Long recordings** (28s+): split at silence boundaries (~25s chunks), each chunk transcribes and pastes progressively
- **Overlay**: AppKit NSPanel — animated bars during recording, fraction progress during transcription, checkmark when done
- **Fn key**: Captured via CGEventTap (Quartz) since pynput can't see modifier-only keys
- **Anti-hallucination**: Trailing silence trimming + known phrase filter + `condition_on_previous_text=False`
- Audio saved to `~/.dictation/recordings/` for re-transcription if needed

## File structure

```
├── Dictation.app/              # macOS app bundle (LSUIElement — no dock icon)
├── scripts/
│   ├── long-dictate.py         # Main daemon: push-to-talk, transcribe, overlay, paste
│   ├── settings.py             # Web-based settings UI
│   ├── dictate                 # Shell launcher (activates venv, runs daemon)
│   ├── install-app.sh          # Install to /Applications + ~/.dictation/
│   └── install-autostart.sh    # Add/remove Login Items auto-start
├── config/
│   └── settings_store.json     # Legacy Handy config (not used by this tool)
└── scripts/                    # Legacy Handy scripts (add-custom-word, fix-word-substitution, etc.)
```

## Uninstall

```bash
bash scripts/install-app.sh remove
```

## Requirements

- macOS on Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- ~3GB disk for the Whisper model (downloaded on first run)
- ~300MB RAM while running

## Accuracy

Tested against Handy's Cohere Transcribe on the same recordings:
- **46.9s**: virtually identical output
- **54.8s**: mlx-whisper more accurate on ambiguous phrases
- **75.2s**: Handy dropped the final third; this tool captured everything

## Background

This started as a configuration repo for [Handy](https://github.com/pais/handy) (a Tauri/Rust dictation app). Handy works well for short recordings but drops content on long ones due to decoder token truncation. The `long-dictate.py` daemon was built as a replacement — it now handles all dictation with better accuracy and no content loss. The legacy Handy config and scripts remain in the repo for reference.
