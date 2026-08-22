#!/bin/bash
# add-custom-word.sh — Add a word to Handy's custom_words list
# Usage: add-custom-word.sh <word> [word2] [word3] ...
# Requires: jq (brew install jq), Handy must NOT be running
#
# Gotchas:
# - Handy overwrites settings_store.json while running. Quit it first.
# - Words are case-sensitive in the list but Handy may match case-insensitively.

CONFIG="$HOME/Library/Application Support/com.pais.handy/settings_store.json"

if [ $# -eq 0 ]; then
    echo "Usage: add-custom-word.sh <word> [word2] [word3] ..."
    echo ""
    echo "Current custom words:"
    jq -r '.settings.custom_words[]' "$CONFIG" 2>/dev/null | sort
    exit 0
fi

if ! command -v jq &>/dev/null; then
    echo "Error: jq is required. Install with: brew install jq"
    exit 1
fi

if pgrep -x Handy &>/dev/null; then
    echo "Error: Handy is running. Quit it first (it overwrites config while running)."
    exit 1
fi

if [ ! -f "$CONFIG" ]; then
    echo "Error: Handy config not found at $CONFIG"
    exit 1
fi

ADDED=0
SKIPPED=0

for word in "$@"; do
    EXISTS=$(jq -r --arg w "$word" '.settings.custom_words | map(select(. == $w)) | length' "$CONFIG")
    if [ "$EXISTS" -gt 0 ]; then
        echo "  skip: \"$word\" (already exists)"
        SKIPPED=$((SKIPPED + 1))
    else
        jq --arg w "$word" '.settings.custom_words += [$w]' "$CONFIG" > "${CONFIG}.tmp" && mv "${CONFIG}.tmp" "$CONFIG"
        echo "  added: \"$word\""
        ADDED=$((ADDED + 1))
    fi
done

echo ""
echo "Done: $ADDED added, $SKIPPED skipped. Total: $(jq '.settings.custom_words | length' "$CONFIG") custom words."
