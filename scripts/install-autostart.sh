#!/bin/bash
# install-autostart.sh — Add Dictation to macOS Login Items (auto-start at login)
# Usage: bash scripts/install-autostart.sh        (install)
#        bash scripts/install-autostart.sh remove  (uninstall)

APP_PATH="/Applications/Dictation.app"

if [ ! -d "$APP_PATH" ]; then
    echo "Error: Dictation.app not found in /Applications/"
    echo "Run: bash scripts/install-app.sh first"
    exit 1
fi

if [ "$1" = "remove" ]; then
    osascript -e "tell application \"System Events\" to delete login item \"Dictation\"" 2>/dev/null
    echo "Removed Dictation from Login Items"
    exit 0
fi

osascript -e "tell application \"System Events\" to delete login item \"Dictation\"" 2>/dev/null
osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP_PATH\", hidden:true}"

echo "Dictation added to Login Items — starts automatically at login"
echo "To remove: bash scripts/install-autostart.sh remove"
