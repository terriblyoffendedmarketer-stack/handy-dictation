#!/usr/bin/env python3
# settings.py — Web-based settings GUI for Dictation.app
# Usage: python3 scripts/settings.py
# Opens a local web page to configure hotkey, model, sounds, and custom vocabulary.
# Changes are saved to ~/.dictation/config.json and ~/.dictation/words.txt.
# The daemon reloads vocabulary on each transcription (no restart needed).
# Hotkey/model changes require restarting Dictation.app.
#
# Gotchas:
# - Uses stdlib only (http.server + webbrowser) — no Flask/FastAPI needed.
# - Port 9876 chosen to avoid conflicts. Falls back if busy.
# - Server shuts down after save or 10 minutes of inactivity.

import json
import os
import sys
import subprocess
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import partial

DICTATION_DIR = os.path.expanduser("~/.dictation")
CONFIG_PATH = os.path.join(DICTATION_DIR, "config.json")
WORDS_PATH = os.path.join(DICTATION_DIR, "words.txt")

HOTKEY_OPTIONS = [
    ("Right Option (⌥)", "right_option"),
    ("Right Command (⌘)", "right_cmd"),
    ("Right Shift (⇧)", "right_shift"),
    ("Left Option (⌥)", "left_option"),
    ("Caps Lock (⇪)", "caps_lock"),
    ("F18", "f18"),
    ("F19", "f19"),
    ("F20", "f20"),
]

MODEL_OPTIONS = [
    ("Whisper Medium — recommended, best punctuation", "mlx-community/whisper-medium-mlx"),
    ("Whisper Large v3 Turbo — faster, less accurate caps", "mlx-community/whisper-large-v3-turbo"),
]

SYSTEM_SOUNDS = [
    "Basso", "Blow", "Bottle", "Frog", "Funk", "Glass", "Hero",
    "Morse", "Ping", "Pop", "Purr", "Sosumi", "Submarine", "Tink",
]


def load_config():
    cfg = {
        "hotkey": "right_option",
        "model": "mlx-community/whisper-medium-mlx",
        "language": "en",
        "sound_start": "/System/Library/Sounds/Tink.aiff",
        "sound_stop": "/System/Library/Sounds/Pop.aiff",
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    os.makedirs(DICTATION_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)


def load_words():
    if not os.path.exists(WORDS_PATH):
        return ""
    with open(WORDS_PATH) as f:
        return f.read()


def save_words(text):
    os.makedirs(DICTATION_DIR, exist_ok=True)
    with open(WORDS_PATH, "w") as f:
        f.write(text)


def sound_name(path):
    return os.path.basename(path).replace(".aiff", "")


def build_html(config, words):
    hotkey_options = ""
    for label, value in HOTKEY_OPTIONS:
        sel = " selected" if value == config["hotkey"] else ""
        hotkey_options += f'<option value="{value}"{sel}>{label}</option>\n'

    model_options = ""
    for label, value in MODEL_OPTIONS:
        sel = " selected" if value == config["model"] else ""
        model_options += f'<option value="{value}"{sel}>{label}</option>\n'

    start_sound = sound_name(config["sound_start"])
    stop_sound = sound_name(config["sound_stop"])

    sound_start_opts = ""
    sound_stop_opts = ""
    for s in SYSTEM_SOUNDS:
        sel_start = " selected" if s == start_sound else ""
        sel_stop = " selected" if s == stop_sound else ""
        sound_start_opts += f'<option value="{s}"{sel_start}>{s}</option>\n'
        sound_stop_opts += f'<option value="{s}"{sel_stop}>{s}</option>\n'

    words_escaped = words.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Dictation Settings</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro", system-ui, sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    min-height: 100vh;
    display: flex;
    justify-content: center;
    padding: 40px 20px;
}}
.container {{
    width: 100%;
    max-width: 560px;
}}
h1 {{
    font-size: 24px;
    font-weight: 600;
    margin-bottom: 8px;
    color: #fff;
}}
.subtitle {{
    color: #888;
    font-size: 14px;
    margin-bottom: 32px;
}}
.section {{
    background: #16213e;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 16px;
    border: 1px solid #1a1a3e;
}}
.section-title {{
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #6c7a96;
    margin-bottom: 16px;
}}
.field {{
    margin-bottom: 16px;
}}
.field:last-child {{ margin-bottom: 0; }}
label {{
    display: block;
    font-size: 14px;
    font-weight: 500;
    margin-bottom: 6px;
    color: #c0c8d8;
}}
select, textarea {{
    width: 100%;
    padding: 10px 12px;
    border: 1px solid #2a3a5c;
    border-radius: 8px;
    background: #0f1729;
    color: #e0e0e0;
    font-size: 14px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.2s;
}}
select:focus, textarea:focus {{
    border-color: #4a6fa5;
}}
textarea {{
    font-family: "Menlo", "SF Mono", monospace;
    font-size: 13px;
    height: 240px;
    resize: vertical;
    line-height: 1.5;
}}
.hint {{
    font-size: 12px;
    color: #5a6a8a;
    margin-top: 6px;
}}
.sound-row {{
    display: flex;
    align-items: center;
    gap: 8px;
}}
.sound-row select {{ flex: 1; }}
.play-btn {{
    padding: 8px 14px;
    border: 1px solid #2a3a5c;
    border-radius: 8px;
    background: #0f1729;
    color: #e0e0e0;
    cursor: pointer;
    font-size: 16px;
    transition: background 0.2s;
}}
.play-btn:hover {{ background: #1a2a4c; }}
.btn-row {{
    display: flex;
    gap: 8px;
    margin-top: 24px;
}}
.btn {{
    padding: 10px 20px;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
}}
.btn-primary {{
    background: #4a6fa5;
    color: #fff;
    flex: 1;
}}
.btn-primary:hover {{ background: #5a7fb5; }}
.btn-secondary {{
    background: #2a3a5c;
    color: #c0c8d8;
}}
.btn-secondary:hover {{ background: #3a4a6c; }}
.btn-danger {{
    background: #5c2a2a;
    color: #e0a0a0;
}}
.btn-danger:hover {{ background: #6c3a3a; }}
.status {{
    text-align: center;
    padding: 12px;
    border-radius: 8px;
    margin-top: 16px;
    display: none;
    font-size: 14px;
}}
.status.success {{ display: block; background: #1a3a2a; color: #80c8a0; }}
.status.error {{ display: block; background: #3a1a1a; color: #e0a0a0; }}
</style>
</head>
<body>
<div class="container">
    <h1>Dictation Settings</h1>
    <p class="subtitle">Configure your push-to-talk dictation tool</p>

    <form id="settingsForm" method="POST" action="/save">
    <div class="section">
        <div class="section-title">Hotkey</div>
        <div class="field">
            <label>Push-to-talk key</label>
            <select name="hotkey">{hotkey_options}</select>
            <p class="hint">Hold to record, release to transcribe. Fn key cannot be captured (macOS limitation).</p>
        </div>
    </div>

    <div class="section">
        <div class="section-title">Model</div>
        <div class="field">
            <label>Whisper model</label>
            <select name="model">{model_options}</select>
            <p class="hint">Medium has better punctuation. Turbo is ~10% faster on long recordings.</p>
        </div>
    </div>

    <div class="section">
        <div class="section-title">Sounds</div>
        <div class="field">
            <label>Start recording</label>
            <div class="sound-row">
                <select name="sound_start">{sound_start_opts}</select>
                <button type="button" class="play-btn" onclick="playSound('sound_start')">▶</button>
            </div>
        </div>
        <div class="field">
            <label>Stop recording</label>
            <div class="sound-row">
                <select name="sound_stop">{sound_stop_opts}</select>
                <button type="button" class="play-btn" onclick="playSound('sound_stop')">▶</button>
            </div>
        </div>
    </div>

    <div class="section">
        <div class="section-title">Custom Vocabulary</div>
        <div class="field">
            <label>Words &amp; phrases (one per line)</label>
            <textarea name="words">{words_escaped}</textarea>
            <p class="hint">Helps Whisper recognize proper nouns, abbreviations, and technical terms. Lines starting with # are comments. Changes take effect on the next recording — no restart needed.</p>
        </div>
    </div>

    <div class="btn-row">
        <button type="submit" class="btn btn-primary">Save Settings</button>
        <button type="button" class="btn btn-secondary" onclick="restartDaemon()">Restart Daemon</button>
    </div>
    </form>
    <div id="status" class="status"></div>
</div>

<script>
function playSound(field) {{
    const sel = document.querySelector(`select[name="${{field}}"]`);
    fetch('/play?sound=' + encodeURIComponent(sel.value));
}}

function restartDaemon() {{
    fetch('/restart').then(r => r.json()).then(data => {{
        const el = document.getElementById('status');
        el.className = 'status success';
        el.textContent = 'Daemon restarted';
        setTimeout(() => el.style.display = 'none', 3000);
    }});
}}

document.getElementById('settingsForm').addEventListener('submit', function(e) {{
    e.preventDefault();
    const formData = new FormData(this);
    fetch('/save', {{
        method: 'POST',
        body: new URLSearchParams(formData),
    }}).then(r => r.json()).then(data => {{
        const el = document.getElementById('status');
        if (data.ok) {{
            el.className = 'status success';
            el.textContent = 'Settings saved! Vocabulary changes take effect immediately. Restart for hotkey/model changes.';
        }} else {{
            el.className = 'status error';
            el.textContent = 'Error saving: ' + data.error;
        }}
        setTimeout(() => el.style.display = 'none', 5000);
    }});
}});
</script>
</body>
</html>"""


class SettingsHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, shutdown_flag=None, **kwargs):
        self.shutdown_flag = shutdown_flag
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/play":
            params = urllib.parse.parse_qs(parsed.query)
            sound = params.get("sound", ["Tink"])[0]
            path = f"/System/Library/Sounds/{sound}.aiff"
            if os.path.exists(path):
                subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if parsed.path == "/restart":
            subprocess.run(["pkill", "-f", "long-dictate.py"], capture_output=True)
            subprocess.Popen(["open", "/Applications/Dictation.app"])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        config = load_config()
        words = load_words()
        html = build_html(config, words)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_POST(self):
        if self.path != "/save":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        params = urllib.parse.parse_qs(body)

        try:
            config = load_config()
            config["hotkey"] = params.get("hotkey", ["right_option"])[0]
            config["model"] = params.get("model", [config["model"]])[0]

            start = params.get("sound_start", ["Tink"])[0]
            stop = params.get("sound_stop", ["Pop"])[0]
            config["sound_start"] = f"/System/Library/Sounds/{start}.aiff"
            config["sound_stop"] = f"/System/Library/Sounds/{stop}.aiff"

            save_config(config)

            words = params.get("words", [""])[0]
            save_words(words)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())


def main():
    port = 9876
    handler = partial(SettingsHandler, shutdown_flag=None)
    try:
        server = HTTPServer(("127.0.0.1", port), handler)
    except OSError:
        port = 9877
        server = HTTPServer(("127.0.0.1", port), handler)

    server.timeout = 600

    url = f"http://localhost:{port}"
    print(f"Dictation Settings: {url}")
    webbrowser.open(url)
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
