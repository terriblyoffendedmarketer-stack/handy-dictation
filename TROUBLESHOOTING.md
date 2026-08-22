# Troubleshooting: Custom Word Substitution Bug

A detailed post-mortem on the `word_correction_threshold` bug in Handy — how custom words can catastrophically break transcription, the incorrect assumptions made during debugging, and the actual fix.

## The Problem

After adding custom words to Handy (Prashil, XTEInk, CrossPoint, StarDict, Wiktionary, Gemma, etc.) and adjusting `word_correction_threshold`, transcription output became completely unusable. Every spoken word was replaced with one of the custom words:

**Input (spoken):** "Testing the tool now, seeing if it's working better."
**Output:** "XTEInk CrossPoint Prashil CrossPoint CrossPoint XTEInk."

This happened with:
- Both raw transcription (`fn` key) and post-processed transcription
- Multiple transcription models (Cohere Transcribe AND Parakeet)
- Any speech input whatsoever

## Root Cause

**`word_correction_threshold` controls how LOOSELY words are matched to custom words. HIGHER values = MORE AGGRESSIVE matching, not stricter.**

| Threshold | Behavior |
|-----------|----------|
| `0.0` | Disabled — no word correction at all |
| `0.05` | Very conservative — only near-exact phonetic matches |
| `0.18` | Default — moderate matching, can cause false positives with common-sounding custom words |
| `0.5` | Aggressive — many words get substituted |
| `0.9` | **Catastrophic** — virtually every word matches a custom word |

The bug was introduced when `word_correction_threshold` was changed from `0.18` (default) to `0.9`, under the incorrect assumption that higher values meant "stricter matching" (i.e., requiring a closer match). The actual behavior is the opposite: higher values cast a wider net, matching more loosely.

At `0.9`, the phonetic distance tolerance is so high that common English words like "testing", "the", "now", "seeing" all fall within matching range of at least one custom word — so every word gets replaced.

## How Handy's Word Correction Works

Handy applies custom words in **two separate layers**:

1. **Whisper `initial_prompt` injection**: Custom words are injected into the speech-to-text model's initial prompt, biasing the decoder toward producing those words. This is a soft bias — it makes the model more likely to output custom words but doesn't force it.

2. **Post-hoc `word_correction_threshold` substitution**: After transcription, Handy compares every transcribed word against the custom word list using phonetic/edit distance. If any word falls within the threshold, it gets replaced. This is the aggressive layer — at high thresholds, it overrides the model's actual output.

The combination means custom words get applied twice: once during transcription (soft bias) and once after (hard replacement). The post-hoc layer is what causes the catastrophic behavior at high thresholds.

## Incorrect Assumptions During Debugging

### Assumption 1: "Higher threshold = stricter matching"
**Wrong.** The parameter name `word_correction_threshold` suggests a quality gate — you might think "I want a high bar for corrections, so set threshold high." But it's actually a distance tolerance: higher = more distance allowed = looser matching.

### Assumption 2: "The problem is the custom words themselves"
**Partially wrong.** Having 13 custom words (including common-sounding ones like "Handy", "Claude", "Gemma") at the default threshold (0.18) did cause some false positives ("started" → "StarDict"). But the catastrophic behavior was entirely caused by the threshold, not the word list. Even with just 3 unique proper nouns (Prashil, XTEInk, CrossPoint), threshold 0.9 replaces everything.

### Assumption 3: "Emptying custom_words and setting threshold to 0.0 will fix it"
**Correct fix, but couldn't be applied.** This was identified as the right approach, but Handy has a config overwrite behavior: when the app is running, it holds settings in memory. If you edit `settings_store.json` on disk while Handy is running, Handy will overwrite your changes the next time it saves (which happens whenever any UI setting changes, or on restart). The fix was being applied to the JSON file, but Handy was overwriting it with its in-memory state.

### Assumption 4: "Switching transcription models will fix it"
**Wrong.** Testing with Parakeet instead of Cohere Transcribe produced the same garbage output. This proved the bug was in Handy's word correction layer (which runs after transcription), not in the speech-to-text model itself. The model's raw output was correct — it was being mangled by post-hoc word substitution.

### Assumption 5: "Clearing transcription history will fix it"
**Wrong.** Deleting all entries from `history.db` had no effect. The word correction is driven by the live config (custom_words + threshold), not by historical transcription data.

### Assumption 6: "Deleting settings_store.json.bak will fix it"
**Wrong.** The `.bak` file is just a backup and isn't read by Handy during normal operation.

## The Actual Fix

1. **Quit Handy completely** — critical, because Handy overwrites `settings_store.json` from its in-memory state
2. **Rename the broken config** to `settings_store.json.broken` (preserving it for reference)
3. **Write a clean config** with:
   - `word_correction_threshold: 0.05` (very conservative — only near-exact phonetic matches)
   - `custom_words: ["Prashil", "XTEInk", "CrossPoint"]` (only truly unique proper nouns that don't sound like any common English word)
   - Technical term hints (StarDict, Wiktionary, etc.) moved to the Gemma cleanup prompt where they're applied contextually, not as forced substitutions
4. **Restart Handy** — it loads the new config from disk

### Why 0.05 and not 0.0?

At `0.0`, word correction is completely disabled — custom words have no effect at all (they still get injected into Whisper's initial_prompt as a soft decoder bias, but the post-hoc correction is off). At `0.05`, you get the benefit of correcting near-exact matches (e.g., if the model outputs "prasheel" it gets corrected to "Prashil") without false positives on unrelated words.

### Why trim the word list?

The original list of 13 custom words included common-sounding words:
- "Handy" — sounds like "handy" (a real English word)
- "Claude" — sounds like "clawed", "cloud"
- "Gemma" — sounds like "gem", "gemma"
- "StarDict" — sounds like "started", "star dict"
- "Cohere" — sounds like "cohere" (a real English word)

Even at conservative thresholds, these create false positives because they're phonetically close to everyday words. The fix was to:
- Keep only 3 words that are truly unique (Prashil, XTEInk, CrossPoint)
- Move all technical terms to the post-processing cleanup prompt, where Gemma applies them only when the surrounding context clearly matches

## Config Overwrite Gotcha

**Handy overwrites `settings_store.json` from its in-memory state.** This is the #1 gotcha when configuring Handy:

- Editing the JSON while Handy is running does nothing — Handy will save its in-memory config on top of your changes
- You MUST quit Handy before editing the config file
- Even quitting and restarting isn't enough if Handy saves on quit — kill the process if needed (`pkill -f Handy.app`)
- After editing, start Handy and verify in the logs (`~/Library/Logs/com.pais.handy/handy.log`) that your values were loaded

Handy does have Tauri IPC commands that can change settings while the app is running (`change_word_correction_threshold_setting`, `update_custom_words`, etc.), but these require sending commands through Tauri's IPC mechanism, not through the JSON file.

## Recovery Script

If you hit this bug, run the fix script:

```bash
# Preview what will change
./scripts/fix-word-substitution.sh --dry-run

# Apply the fix (Handy must be quit)
pkill -f Handy.app
./scripts/fix-word-substitution.sh
open -a Handy
```

## Key Takeaways

1. **`word_correction_threshold` is inversely named** — higher values are more aggressive, not stricter. Treat it like a "fuzziness dial": 0 = exact only, 1 = match anything.
2. **Custom words affect two layers** — decoder bias (soft) and post-hoc replacement (hard). The post-hoc layer is the dangerous one.
3. **Only add words the model literally cannot produce** — proper nouns with unusual spelling (Prashil, XTEInk). Never add common-sounding words as custom words.
4. **Use the LLM cleanup prompt for technical vocabulary** — Gemma/Ollama post-processing can apply domain terms contextually without the false-positive risk of phonetic matching.
5. **Always quit Handy before editing its config** — the app overwrites the JSON file from memory.
6. **Test with raw transcription first** — if raw (`fn` key) produces garbage, the problem is in word correction, not in the model or post-processing.
7. **Check the logs** — `~/Library/Logs/com.pais.handy/handy.log` shows exactly what settings were loaded and what the raw transcription result was after word correction.
