#!/bin/bash
# install-app.sh — Install Dictation.app to /Applications and set up runtime files
# Usage: bash scripts/install-app.sh         (install/update)
#        bash scripts/install-app.sh remove  (uninstall)
#
# Copies runtime files to ~/.dictation/ (not protected by macOS TCC)
# and installs the .app to /Applications/

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$HOME/.dictation"
APP_SRC="$PROJECT_DIR/Dictation.app"
APP_DST="/Applications/Dictation.app"

if [ "$1" = "remove" ]; then
    osascript -e "tell application \"System Events\" to delete login item \"Dictation\"" 2>/dev/null
    osascript -e 'tell application "Dictation" to quit' 2>/dev/null
    rm -rf "$APP_DST" "$RUNTIME_DIR"
    echo "Dictation uninstalled"
    exit 0
fi

echo "Installing Dictation..."

# Kill existing if running
pkill -f "long-dictate.py" 2>/dev/null

# Copy runtime files
mkdir -p "$RUNTIME_DIR"
cp "$PROJECT_DIR/scripts/long-dictate.py" "$RUNTIME_DIR/"
cp "$PROJECT_DIR/scripts/settings.py" "$RUNTIME_DIR/"
if [ ! -d "$RUNTIME_DIR/.venv" ]; then
    echo "  Copying venv (first install, ~1.4GB)..."
    cp -R "$PROJECT_DIR/.venv" "$RUNTIME_DIR/.venv"
else
    echo "  Venv exists, skipping"
fi

# Install app
cp -R "$APP_SRC" "$APP_DST"
# Fix run script to use runtime dir
cat > "$APP_DST/Contents/MacOS/run" << 'RUNEOF'
#!/bin/bash
DICTATION_DIR="$HOME/.dictation"

# If daemon is already running, open settings instead
if pgrep -f "long-dictate.py" > /dev/null 2>&1; then
    source "$DICTATION_DIR/.venv/bin/activate"
    exec python3 "$DICTATION_DIR/settings.py" "$@"
fi

# Otherwise start the daemon
exec > /tmp/dictation.log 2>&1
source "$DICTATION_DIR/.venv/bin/activate"
exec python3 "$DICTATION_DIR/long-dictate.py" "$@"
RUNEOF
chmod +x "$APP_DST/Contents/MacOS/run"

# Clear quarantine
xattr -cr "$APP_DST"

# Update recording path in runtime copy
sed -i '' 's|~/Documents/Dictation|~/.dictation/recordings|' "$RUNTIME_DIR/long-dictate.py"

# Create default config/words if not present
if [ ! -f "$RUNTIME_DIR/config.json" ]; then
    cat > "$RUNTIME_DIR/config.json" << 'CFGEOF'
{
    "hotkey": "right_option",
    "model": "mlx-community/whisper-medium-mlx",
    "language": "en",
    "sound_start": "/System/Library/Sounds/Tink.aiff",
    "sound_stop": "/System/Library/Sounds/Pop.aiff"
}
CFGEOF
    echo "  Created default config.json"
fi

if [ ! -f "$RUNTIME_DIR/words.txt" ]; then
    cat > "$RUNTIME_DIR/words.txt" << 'WORDSEOF'
# Custom vocabulary — one word or phrase per line
# These help Whisper recognize proper nouns, abbreviations, and technical terms.
# Edit via: python3 ~/.dictation/settings.py
WORDSEOF
    echo "  Created default words.txt"
fi

echo "  Installed to /Applications/Dictation.app"
echo ""
echo "Next steps:"
echo "  1. Open Dictation.app (double-click in /Applications or Spotlight)"
echo "  2. Grant Accessibility permission when prompted:"
echo "     System Settings > Privacy & Security > Accessibility > enable Dictation"
echo "  3. (Optional) Auto-start at login:"
echo "     bash scripts/install-autostart.sh"
echo ""
echo "Hold Right Option to record, release to transcribe + paste."
