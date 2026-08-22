# Handy Dictation — Free Offline Speech-to-Text Setup

A fully local, zero-cost speech-to-text setup using [Handy](https://github.com/pais/handy) with Cohere Transcribe and Gemma 4B post-processing via Ollama. Built as a free replacement for WisprFlow.

## What This Is

Configuration, scripts, and documentation for setting up high-quality offline dictation on macOS (Apple Silicon) using only free, local tools:

- **Handy** — Open-source dictation app (Tauri/Rust)
- **Cohere Transcribe** — Speech-to-text model (runs locally in Handy)
- **Ollama + Gemma 3 4B** — Local LLM for transcript cleanup (punctuation, spelling, filler word removal)

Two-hotkey workflow: `fn` for instant raw transcription, `ctrl+space` for transcription + AI cleanup (~5s).

## Quick Start

### 1. Install Handy

Download from [handy releases](https://github.com/pais/handy/releases) or build from source.

### 2. Install Ollama

```bash
brew install ollama
```

Ollama auto-starts at login via Homebrew's launchd agent. Pull the cleanup model:

```bash
ollama pull gemma3:4b
```

### 3. Apply Configuration

**Quit Handy first** (it overwrites config while running), then copy the config:

```bash
cp config/settings_store.json ~/Library/Application\ Support/com.pais.handy/settings_store.json
```

Or apply just the key settings manually in Handy's preferences.

### 4. Keep Ollama Warm (Optional)

To avoid cold-start delay (~18s) after idle periods, set keep-alive to 2 hours:

```bash
launchctl setenv OLLAMA_KEEP_ALIVE 2h
brew services restart ollama
```

## Configuration Details

### Keybindings

| Key | Action | Speed |
|-----|--------|-------|
| `fn` (hold to talk) | Raw transcription — Cohere Transcribe only | ~1-2s |
| `ctrl+space` (hold to talk) | Transcription + Gemma 4B cleanup | ~5s |
| `escape` | Cancel recording | instant |

### Transcription Model

**Cohere Transcribe** (`cohere-transcribe-03-2026-Q5_K_M.gguf`) — best accuracy among tested models. Parakeet 0.6B was second.

### Post-Processing

- **Provider:** Ollama (Custom, `http://localhost:11434/v1`)
- **Model:** `gemma3:4b` — best balance of speed and quality
- **Prompt:** Cleans spelling, capitalization, punctuation, removes filler words, preserves meaning

### Tuned Settings

| Setting | Value | Why |
|---------|-------|-----|
| `selected_language` | `en` | Saves context tokens vs `auto` detection — reduces dropped words |
| `word_correction_threshold` | `0.05` | Default `0.18` too aggressive — substitutes "started" → "StarDict". HIGHER = MORE AGGRESSIVE (not stricter). At `0.05`, only near-exact phonetic matches trigger. `0.9` is catastrophic — every word becomes a custom word |
| `extra_recording_buffer_ms` | `500` | Catches trailing words that get cut off |
| Custom words | 3 unique proper nouns | Only truly unique words (Prashil, XTEInk, CrossPoint) — technical terms handled by Gemma cleanup prompt instead |

## Scripts

### `scripts/add-custom-word.sh`

Add words to Handy's custom word list from the terminal:

```bash
./scripts/add-custom-word.sh MyWord AnotherWord
# Run with no args to list current words
./scripts/add-custom-word.sh
```

### `scripts/switch-handy-provider.sh`

Switch post-processing between Apple Intelligence and Ollama:

```bash
./scripts/switch-handy-provider.sh apple   # use Apple Intelligence
./scripts/switch-handy-provider.sh ollama  # use Ollama/Gemma
./scripts/switch-handy-provider.sh         # show current
```

### `scripts/fix-word-substitution.sh`

Fix the custom word force-substitution bug (raises threshold, trims word list):

```bash
./scripts/fix-word-substitution.sh --dry-run  # preview changes
./scripts/fix-word-substitution.sh             # apply (Handy must be quit)
```

### macOS Quick Action: "Add to Handy Words"

Select any word → right-click → Services → "Add to Handy Words". Install:

```bash
bash scripts/install-add-word-service.sh
```

## Benchmarks (Apple M2, 16GB RAM)

### Post-Processing Models Tested

| Model | Short text | Long text | Quality | Verdict |
|-------|-----------|-----------|---------|---------|
| **gemma3:4b** | ~1.3s | ~3s | Excellent — follows instructions, preserves meaning | **Winner** |
| gemma3:1b | ~1.4s | ~3s | Paraphrases too much, mangles technical terms | Not faster, worse quality |
| gemma4:12b | ~15s | ~20s | Best quality but far too slow | Unusable for dictation |
| qwen3:1.7b | N/A | N/A | Dumps chain-of-thought reasoning into output | Broken |
| Apple Intelligence | ~0.5s | ~1s | Ignores cleanup instructions, summarizes instead of cleaning, adds "Sure," | Fast but unusable |

### Key Findings

- **Apple Intelligence** runs on Neural Engine (3-4x faster) but the on-device model is too small to follow system prompts — it summarizes instead of cleaning and adds conversational prefixes
- **gemma3:1b** is not actually faster than 4B on Apple Silicon and produces worse output
- **Cohere Transcribe** significantly outperforms Whisper-based models for accuracy
- Cold start after 2h idle: ~18s (mitigated with `OLLAMA_KEEP_ALIVE=2h`)
- The cleanup prompt must use contextual vocabulary hints, not forced substitutions — otherwise "StarDict" replaces the word "start"

## Known Limitations

1. **Custom words force-substitution** (FIXED) — Handy injects custom words into Whisper's `initial_prompt` AND applies post-hoc `word_correction_threshold` substitution. Words like "StarDict" aggressively replaced "started". At `0.9`, every word becomes a custom word. **Fix applied:** `word_correction_threshold` set to `0.05` (near-exact matches only — higher values are MORE aggressive, not stricter), `custom_words` trimmed to 3 truly unique proper nouns, and technical terms moved to the Gemma cleanup prompt where they're only used when context matches. Run `scripts/fix-word-substitution.sh` to re-apply if Handy reverts the config. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for the full post-mortem.
2. **Config overwrite** — Editing `settings_store.json` while Handy is quit works, but changing ANY setting in Handy's UI causes it to save its in-memory config, reverting all JSON edits. Make all changes at once when Handy is quit.
3. **Dropped speech chunks** — Long dictation sometimes loses middle/end content. This is a VAD (Voice Activity Detection) chunking issue in the transcription engine. Mitigated by setting language to `en` (saves context tokens) and lowering `word_correction_threshold`.
4. **5s cleanup delay** — Sum of transcription (~2s) + Gemma cleanup (~3s). Use `fn` for instant raw when speed matters.
5. **Cold start** — First dictation after 2h idle takes ~18s while the model loads into memory.
6. **Custom words don't auto-learn** — Must be added manually via the script or Quick Action. No correction-based learning yet.

## File Structure

```
├── README.md                  # This file
├── TROUBLESHOOTING.md         # Detailed post-mortem on the word_correction_threshold bug
├── config/
│   └── settings_store.json    # Handy configuration (copy to ~/Library/Application Support/com.pais.handy/)
├── prompts/
│   └── cleanup-prompt.txt     # The post-processing prompt used by Gemma
└── scripts/
    ├── add-custom-word.sh     # CLI to add custom words
    ├── fix-word-substitution.sh  # Fix aggressive word substitution bug
    ├── switch-handy-provider.sh  # Switch between Apple Intelligence and Ollama
    └── install-add-word-service.sh  # Install macOS Quick Action for adding words
```

## Requirements

- macOS (Apple Silicon recommended for Ollama performance)
- [Handy](https://github.com/pais/handy) v0.9.1+
- [Ollama](https://ollama.ai) with `gemma3:4b` model (~3.3 GB)
- `jq` (for scripts): `brew install jq`
