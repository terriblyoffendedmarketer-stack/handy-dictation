#!/bin/bash
# install-add-word-service.sh — Creates a macOS Quick Action (right-click menu)
# to add selected text as a Handy custom word
# Usage: bash install-add-word-service.sh
#
# After install: select a word anywhere → right-click → Services → "Add to Handy Words"
# The word gets added to Handy's custom_words in settings_store.json.
# Shows a notification confirming the addition.
#
# Gotchas:
# - Handy must NOT be running when you use this (it overwrites config on quit).
#   The service warns you if Handy is running.
# - macOS may take a moment to register the new service. Log out/in if it doesn't appear.

SERVICE_NAME="Add to Handy Words"
SERVICE_DIR="$HOME/Library/Services"
SERVICE_PATH="$SERVICE_DIR/${SERVICE_NAME}.workflow"

mkdir -p "$SERVICE_DIR"

# Remove old version if exists
rm -rf "$SERVICE_PATH"

# Create workflow bundle structure
mkdir -p "$SERVICE_PATH/Contents"

# Info.plist — defines the Quick Action
cat > "$SERVICE_PATH/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>NSServices</key>
	<array>
		<dict>
			<key>NSMenuItem</key>
			<dict>
				<key>default</key>
				<string>Add to Handy Words</string>
			</dict>
			<key>NSMessage</key>
			<string>runWorkflowAsService</string>
			<key>NSSendTypes</key>
			<array>
				<string>NSStringPboardType</string>
			</array>
		</dict>
	</array>
</dict>
</plist>
PLIST

# document.wflow — the actual workflow (runs a shell script)
cat > "$SERVICE_PATH/Contents/document.wflow" << 'WFLOW'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>AMApplicationBuild</key>
	<string>523</string>
	<key>AMApplicationVersion</key>
	<string>2.10</string>
	<key>AMDocumentVersion</key>
	<string>2</string>
	<key>actions</key>
	<array>
		<dict>
			<key>action</key>
			<dict>
				<key>AMAccepts</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Optional</key>
					<false/>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.string</string>
					</array>
				</dict>
				<key>AMActionVersion</key>
				<string>1.0.2</string>
				<key>AMApplication</key>
				<array>
					<string>Automator</string>
				</array>
				<key>AMLargeIconName</key>
				<string>RunShellScript</string>
				<key>AMParameterProperties</key>
				<dict>
					<key>COMMAND_STRING</key>
					<dict/>
					<key>CheckedForUserDefaultShell</key>
					<dict/>
					<key>inputMethod</key>
					<dict/>
					<key>shell</key>
					<dict/>
					<key>source</key>
					<dict/>
				</dict>
				<key>AMProvides</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.string</string>
					</array>
				</dict>
				<key>ActionBundlePath</key>
				<string>/System/Library/Automator/Run Shell Script.action</string>
				<key>ActionName</key>
				<string>Run Shell Script</string>
				<key>ActionParameters</key>
				<dict>
					<key>COMMAND_STRING</key>
					<string>#!/bin/bash
CONFIG="$HOME/Library/Application Support/com.pais.handy/settings_store.json"
WORD=$(echo "$1" | xargs)

if [ -z "$WORD" ]; then
    osascript -e 'display notification "No word selected" with title "Handy Words"'
    exit 0
fi

if pgrep -x Handy &amp;&gt;/dev/null; then
    osascript -e 'display notification "Quit Handy first — it overwrites config while running" with title "Handy Words" subtitle "⚠️ Cannot add word"'
    exit 1
fi

EXISTS=$(/usr/bin/jq -r --arg w "$WORD" '.settings.custom_words | map(select(. == $w)) | length' "$CONFIG")
if [ "$EXISTS" -gt 0 ]; then
    osascript -e "display notification \"\\\"$WORD\\\" is already in custom words\" with title \"Handy Words\""
    exit 0
fi

/usr/bin/jq --arg w "$WORD" '.settings.custom_words += [$w]' "$CONFIG" &gt; "${CONFIG}.tmp" &amp;&amp; mv "${CONFIG}.tmp" "$CONFIG"
COUNT=$(/usr/bin/jq '.settings.custom_words | length' "$CONFIG")
osascript -e "display notification \"Added \\\"$WORD\\\" ($COUNT words total)\" with title \"Handy Words\" subtitle \"✓ Word added\""</string>
					<key>CheckedForUserDefaultShell</key>
					<true/>
					<key>inputMethod</key>
					<integer>0</integer>
					<key>shell</key>
					<string>/bin/bash</string>
					<key>source</key>
					<string></string>
				</dict>
				<key>BundleIdentifier</key>
				<string>com.apple.RunShellScript</string>
				<key>CFBundleVersion</key>
				<string>1.0.2</string>
				<key>CanShowSelectedItemsWhenRun</key>
				<false/>
				<key>CanShowWhenRun</key>
				<true/>
				<key>Category</key>
				<array>
					<string>AMCategoryUtilities</string>
				</array>
				<key>Class Name</key>
				<string>RunShellScriptAction</string>
				<key>InputUUID</key>
				<string>A1B2C3D4-E5F6-7890-ABCD-EF1234567890</string>
				<key>Keywords</key>
				<array>
					<string>Shell</string>
					<string>Script</string>
					<string>Command</string>
					<string>Run</string>
				</array>
				<key>OutputUUID</key>
				<string>B2C3D4E5-F6A1-8901-BCDE-F12345678901</string>
				<key>UUID</key>
				<string>C3D4E5F6-A1B2-9012-CDEF-123456789012</string>
				<key>UnlocalizedApplications</key>
				<array>
					<string>Automator</string>
				</array>
			</dict>
		</dict>
	</array>
	<key>connectors</key>
	<dict/>
	<key>workflowMetaData</key>
	<dict>
		<key>workflowTypeIdentifier</key>
		<string>com.apple.Automator.servicesMenu</string>
	</dict>
</dict>
</plist>
WFLOW

echo "Installed Quick Action: $SERVICE_PATH"
echo ""
echo "Usage: Select a word anywhere → right-click → Services → 'Add to Handy Words'"
echo ""
echo "Note: macOS may need a moment to register it. If it doesn't appear,"
echo "try logging out and back in, or run:"
echo "  /System/Library/CoreServices/pbs -update"
