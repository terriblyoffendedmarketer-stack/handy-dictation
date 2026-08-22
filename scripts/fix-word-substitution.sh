#!/bin/bash
# fix-word-substitution.sh — Fix Handy's aggressive custom word substitution
# Usage: fix-word-substitution.sh [--dry-run]
# Requires: jq (brew install jq), Handy must NOT be running
#
# Gotchas:
# - Handy injects custom_words into Whisper's initial_prompt, biasing the decoder.
#   Common-sounding words (Handy, Claude, Gemma) cause false substitutions.
# - word_correction_threshold controls matching looseness: HIGHER = MORE AGGRESSIVE.
#   Default 0.18 already too aggressive. 0.9 = catastrophic (every word becomes custom word).
#   Safe value: 0.05 (only near-exact phonetic matches trigger substitution).
# - selected_language "auto" wastes context tokens on language detection.
#   Setting to "en" frees those tokens for better transcription accuracy.

CONFIG="$HOME/Library/Application Support/com.pais.handy/settings_store.json"

if ! command -v jq &>/dev/null; then
    echo "Error: jq required. Install: brew install jq"
    exit 1
fi

if pgrep -x Handy &>/dev/null; then
    echo "Error: Quit Handy first (it overwrites config while running)."
    echo "  Run: osascript -e 'tell application \"Handy\" to quit'"
    exit 1
fi

if [ ! -f "$CONFIG" ]; then
    echo "Error: Handy config not found at $CONFIG"
    exit 1
fi

DRY_RUN=false
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
    echo "=== DRY RUN — no changes will be written ==="
    echo ""
fi

echo "Current state:"
echo "  word_correction_threshold: $(jq '.settings.word_correction_threshold' "$CONFIG")"
echo "  selected_language: $(jq -r '.settings.selected_language' "$CONFIG")"
echo "  custom_words ($(jq '.settings.custom_words | length' "$CONFIG")):"
jq -r '.settings.custom_words[]' "$CONFIG" | sed 's/^/    /'
echo ""

# Only keep words the transcription model can't produce on its own.
# Common-sounding words (Handy, Claude, Gemma) are handled by the Gemma cleanup prompt.
KEEP_WORDS='["Prashil", "XTEInk", "CrossPoint"]'

echo "Planned changes:"
echo "  word_correction_threshold: → 0.05 (near-exact matches only)"
echo "  selected_language: auto → en (saves context tokens)"
echo "  custom_words: trim to only unique proper nouns:"
echo "$KEEP_WORDS" | jq -r '.[]' | sed 's/^/    /'
echo ""
echo "  Removed words (now handled by cleanup prompt only):"
jq -r --argjson keep "$KEEP_WORDS" '.settings.custom_words | map(select(. as $w | $keep | index($w) | not))[]' "$CONFIG" | sed 's/^/    /'
echo ""

if $DRY_RUN; then
    echo "Dry run complete. Remove --dry-run to apply."
    exit 0
fi

cp "$CONFIG" "${CONFIG}.bak"
echo "Backup saved to settings_store.json.bak"

jq --argjson keep "$KEEP_WORDS" '
  .settings.word_correction_threshold = 0.05 |
  .settings.selected_language = "en" |
  .settings.custom_words = $keep
' "$CONFIG" > "${CONFIG}.tmp" && mv "${CONFIG}.tmp" "$CONFIG"

echo ""
echo "Applied. Restart Handy to pick up changes."
echo "  Run: open -a Handy"
