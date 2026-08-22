#!/bin/bash
# switch-handy-provider.sh — Switch Handy's post-processing between Ollama and Apple Intelligence
# Usage: switch-handy-provider.sh [apple|ollama]
# Requires: jq, Handy must NOT be running

CONFIG="$HOME/Library/Application Support/com.pais.handy/settings_store.json"

if ! command -v jq &>/dev/null; then
    echo "Error: jq required. Install: brew install jq"
    exit 1
fi

if pgrep -x Handy &>/dev/null; then
    echo "Error: Quit Handy first."
    exit 1
fi

CURRENT=$(jq -r '.settings.post_process_provider_id' "$CONFIG")

case "${1:-}" in
    apple)
        jq '.settings.post_process_provider_id = "apple_intelligence"' "$CONFIG" > "${CONFIG}.tmp" && mv "${CONFIG}.tmp" "$CONFIG"
        echo "Switched to Apple Intelligence (was: $CURRENT)"
        ;;
    ollama)
        jq '.settings.post_process_provider_id = "custom"' "$CONFIG" > "${CONFIG}.tmp" && mv "${CONFIG}.tmp" "$CONFIG"
        echo "Switched to Ollama/gemma3:4b (was: $CURRENT)"
        ;;
    "")
        echo "Current provider: $CURRENT"
        echo "Usage: switch-handy-provider.sh [apple|ollama]"
        ;;
    *)
        echo "Unknown provider: $1. Use 'apple' or 'ollama'."
        exit 1
        ;;
esac
