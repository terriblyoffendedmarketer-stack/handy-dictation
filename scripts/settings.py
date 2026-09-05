#!/usr/bin/env python3
# settings.py — Web-based settings GUI for Dictation.app
# Usage: python3 scripts/settings.py
# Opens a local web page to configure hotkey, model, sounds, and custom vocabulary.
# Changes are saved to ~/.dictation/config.json and ~/.dictation/words.txt.
#
# Gotchas:
# - Uses stdlib only (http.server + webbrowser) — no Flask/FastAPI needed.
# - Port 9876 chosen to avoid conflicts. Falls back if busy.
# - The /status endpoint is polled every 2s to keep daemon status live.
# - Daemon toggle (start/stop) works via pgrep + open/pkill.
# - Auto-start toggle uses osascript Login Items (not launchd).

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
    ("Fn (Globe)", "fn"),
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
    ("Whisper Medium — best punctuation", "mlx-community/whisper-medium-mlx"),
    ("Whisper Large v3 Turbo — faster", "mlx-community/whisper-large-v3-turbo"),
    ("Whisper Large v3 — most accurate", "mlx-community/whisper-large-v3-mlx"),
]

SYSTEM_SOUNDS = [
    "Basso", "Blow", "Bottle", "Frog", "Funk", "Glass", "Hero",
    "Morse", "Ping", "Pop", "Purr", "Sosumi", "Submarine", "Tink",
]


def load_config():
    cfg = {
        "hotkey": "fn",
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


def is_daemon_running():
    result = subprocess.run(["pgrep", "-f", "long-dictate.py"], capture_output=True)
    return result.returncode == 0


def is_autostart_enabled():
    result = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to get the name of every login item'],
        capture_output=True, text=True
    )
    return "Dictation" in result.stdout


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
    llm_cleanup = config.get("llm_cleanup", False)
    llm_model = config.get("llm_model", "gemma3:4b")
    daemon_running = is_daemon_running()
    autostart_on = is_autostart_enabled()

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
.container {{ width: 100%; max-width: 560px; }}
h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 8px; color: #fff; }}
.subtitle {{ color: #888; font-size: 14px; margin-bottom: 24px; }}
.section {{
    background: #16213e;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 16px;
    border: 1px solid #1a1a3e;
}}
.section-title {{
    font-size: 13px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.5px; color: #6c7a96; margin-bottom: 16px;
}}
.field {{ margin-bottom: 16px; }}
.field:last-child {{ margin-bottom: 0; }}
label {{
    display: block; font-size: 14px; font-weight: 500;
    margin-bottom: 6px; color: #c0c8d8;
}}
select, textarea {{
    width: 100%; padding: 10px 12px; border: 1px solid #2a3a5c;
    border-radius: 8px; background: #0f1729; color: #e0e0e0;
    font-size: 14px; font-family: inherit; outline: none;
    transition: border-color 0.2s;
}}
select:focus, textarea:focus {{ border-color: #4a6fa5; }}
textarea {{
    font-family: "Menlo", "SF Mono", monospace;
    font-size: 13px; height: 200px; resize: vertical; line-height: 1.5;
}}
.hint {{ font-size: 12px; color: #5a6a8a; margin-top: 6px; }}
.sound-row {{ display: flex; align-items: center; gap: 8px; }}
.sound-row select {{ flex: 1; }}
.play-btn {{
    padding: 8px 14px; border: 1px solid #2a3a5c; border-radius: 8px;
    background: #0f1729; color: #e0e0e0; cursor: pointer;
    font-size: 16px; transition: background 0.2s;
}}
.play-btn:hover {{ background: #1a2a4c; }}

/* Daemon status section */
.status-bar {{
    display: flex; align-items: center; justify-content: space-between;
    background: #16213e; border-radius: 12px; padding: 16px 24px;
    margin-bottom: 16px; border: 1px solid #1a1a3e;
}}
.status-left {{ display: flex; align-items: center; gap: 12px; }}
.status-dot {{
    width: 10px; height: 10px; border-radius: 50%;
    background: #555; transition: background 0.3s;
}}
.status-dot.running {{ background: #4ade80; box-shadow: 0 0 8px #4ade8066; }}
.status-dot.stopped {{ background: #f87171; }}
.status-label {{ font-size: 14px; font-weight: 500; }}
.toggle-btn {{
    padding: 8px 20px; border: none; border-radius: 8px;
    font-size: 13px; font-weight: 500; cursor: pointer;
    transition: all 0.2s;
}}
.toggle-btn.start {{ background: #22c55e; color: #fff; }}
.toggle-btn.start:hover {{ background: #16a34a; }}
.toggle-btn.stop {{ background: #ef4444; color: #fff; }}
.toggle-btn.stop:hover {{ background: #dc2626; }}
.toggle-btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}

/* Toggle switch */
.toggle-row {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 4px 0;
}}
.toggle-row label {{ margin-bottom: 0; }}
.switch {{
    position: relative; width: 44px; height: 24px;
    background: #2a3a5c; border-radius: 12px; cursor: pointer;
    transition: background 0.3s;
}}
.switch.on {{ background: #22c55e; }}
.switch-knob {{
    position: absolute; top: 2px; left: 2px;
    width: 20px; height: 20px; border-radius: 50%;
    background: #fff; transition: left 0.2s;
}}
.switch.on .switch-knob {{ left: 22px; }}

/* Buttons */
.btn-row {{ display: flex; gap: 8px; margin-top: 24px; }}
.btn {{
    padding: 10px 20px; border: none; border-radius: 8px;
    font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.2s;
}}
.btn-primary {{ background: #4a6fa5; color: #fff; flex: 1; }}
.btn-primary:hover {{ background: #5a7fb5; }}
.btn-secondary {{ background: #2a3a5c; color: #c0c8d8; }}
.btn-secondary:hover {{ background: #3a4a6c; }}

/* Toast */
.toast {{
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    padding: 12px 24px; border-radius: 8px; font-size: 14px;
    opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100;
}}
.toast.show {{ opacity: 1; }}
.toast.success {{ background: #1a3a2a; color: #80c8a0; border: 1px solid #2a5a3a; }}
.toast.error {{ background: #3a1a1a; color: #e0a0a0; border: 1px solid #5a2a2a; }}
</style>
</head>
<body>
<div class="container">
    <h1>Dictation</h1>
    <p class="subtitle">Push-to-talk speech-to-text</p>

    <!-- Daemon status -->
    <div class="status-bar">
        <div class="status-left">
            <div id="statusDot" class="status-dot {'running' if daemon_running else 'stopped'}"></div>
            <span id="statusLabel" class="status-label">{'Listening' if daemon_running else 'Stopped'}</span>
        </div>
        <button id="toggleBtn" class="toggle-btn {'stop' if daemon_running else 'start'}"
                onclick="toggleDaemon()">{'Stop' if daemon_running else 'Start'}</button>
    </div>

    <form id="settingsForm">
    <!-- Hotkey -->
    <div class="section">
        <div class="section-title">Hotkey</div>
        <div class="field">
            <label>Push-to-talk key</label>
            <select name="hotkey">{hotkey_options}</select>
            <p class="hint">Hold to record, release to transcribe and paste.</p>
        </div>
    </div>

    <!-- Model -->
    <div class="section">
        <div class="section-title">Model</div>
        <div class="field">
            <label>Whisper model</label>
            <select name="model">{model_options}</select>
            <p class="hint">Large v3 is most accurate but uses ~3GB more RAM and is slower. Medium is the best balance.</p>
        </div>
    </div>

    <!-- Sounds -->
    <div class="section">
        <div class="section-title">Sounds</div>
        <div class="field">
            <label>Start recording</label>
            <div class="sound-row">
                <select name="sound_start">{sound_start_opts}</select>
                <button type="button" class="play-btn" onclick="playSound('sound_start')">&#9654;</button>
            </div>
        </div>
        <div class="field">
            <label>Stop recording</label>
            <div class="sound-row">
                <select name="sound_stop">{sound_stop_opts}</select>
                <button type="button" class="play-btn" onclick="playSound('sound_stop')">&#9654;</button>
            </div>
        </div>
    </div>

    <!-- Vocabulary -->
    <div class="section">
        <div class="section-title">Custom Vocabulary</div>
        <div class="field">
            <label>Words &amp; phrases (one per line)</label>
            <textarea name="words">{words_escaped}</textarea>
            <p class="hint">Helps recognize proper nouns and technical terms. Changes apply on next recording.</p>
        </div>
    </div>

    <!-- LLM Cleanup -->
    <div class="section">
        <div class="section-title">AI Cleanup (Ollama)</div>
        <div class="toggle-row">
            <label>Clean up transcription with LLM</label>
            <div id="llmSwitch" class="switch {'on' if llm_cleanup else ''}" onclick="toggleLlm()">
                <div class="switch-knob"></div>
            </div>
        </div>
        <p class="hint" style="margin-top:8px">When enabled, each transcription is cleaned by a local LLM (fixes punctuation, spelling, filler words). Adds ~1-3s. Requires Ollama running with {llm_model}.</p>
    </div>

    <!-- Startup -->
    <div class="section">
        <div class="section-title">Startup</div>
        <div class="toggle-row">
            <label>Start at login</label>
            <div id="autostartSwitch" class="switch {'on' if autostart_on else ''}" onclick="toggleAutostart()">
                <div class="switch-knob"></div>
            </div>
        </div>
    </div>

    <div class="btn-row">
        <button type="submit" class="btn btn-primary">Save Settings</button>
    </div>
    </form>
</div>

<div id="toast" class="toast"></div>

<script>
function showToast(msg, type) {{
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast ' + type + ' show';
    setTimeout(() => t.className = 'toast', 3000);
}}

function playSound(field) {{
    const sel = document.querySelector('select[name="' + field + '"]');
    fetch('/play?sound=' + encodeURIComponent(sel.value));
}}

function toggleDaemon() {{
    const btn = document.getElementById('toggleBtn');
    btn.disabled = true;
    btn.textContent = '...';
    const running = btn.classList.contains('stop');
    fetch(running ? '/stop' : '/start')
        .then(r => r.json())
        .then(() => {{ setTimeout(updateStatus, 1500); }});
}}

function toggleLlm() {{
    const sw = document.getElementById('llmSwitch');
    const enabling = !sw.classList.contains('on');
    fetch('/toggle_llm?enable=' + (enabling ? '1' : '0'))
        .then(r => r.json())
        .then(data => {{
            if (data.ok) {{
                sw.classList.toggle('on');
                showToast(enabling ? 'LLM cleanup enabled' : 'LLM cleanup disabled', 'success');
            }}
        }});
}}

function toggleAutostart() {{
    const sw = document.getElementById('autostartSwitch');
    const enabling = !sw.classList.contains('on');
    fetch('/autostart?enable=' + (enabling ? '1' : '0'))
        .then(r => r.json())
        .then(data => {{
            if (data.ok) {{
                sw.classList.toggle('on');
                showToast(enabling ? 'Auto-start enabled' : 'Auto-start disabled', 'success');
            }}
        }});
}}

function updateStatus() {{
    fetch('/status').then(r => r.json()).then(data => {{
        const dot = document.getElementById('statusDot');
        const label = document.getElementById('statusLabel');
        const btn = document.getElementById('toggleBtn');
        dot.className = 'status-dot ' + (data.running ? 'running' : 'stopped');
        label.textContent = data.running ? 'Listening' : 'Stopped';
        btn.className = 'toggle-btn ' + (data.running ? 'stop' : 'start');
        btn.textContent = data.running ? 'Stop' : 'Start';
        btn.disabled = false;
    }});
}}

setInterval(updateStatus, 3000);

document.getElementById('settingsForm').addEventListener('submit', function(e) {{
    e.preventDefault();
    const formData = new FormData(this);
    fetch('/save', {{
        method: 'POST',
        body: new URLSearchParams(formData),
    }}).then(r => r.json()).then(data => {{
        if (data.ok) {{
            showToast('Settings saved. Restart daemon for hotkey/model changes.', 'success');
        }} else {{
            showToast('Error: ' + data.error, 'error');
        }}
    }});
}});
</script>
</body>
</html>"""


class SettingsHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/status":
            running = is_daemon_running()
            self._json({"running": running})
            return

        if parsed.path == "/play":
            params = urllib.parse.parse_qs(parsed.query)
            sound = params.get("sound", ["Tink"])[0]
            path = f"/System/Library/Sounds/{sound}.aiff"
            if os.path.exists(path):
                subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._json({"ok": True})
            return

        if parsed.path == "/start":
            if not is_daemon_running():
                subprocess.Popen(["open", "/Applications/Dictation.app"])
            self._json({"ok": True})
            return

        if parsed.path == "/stop":
            subprocess.run(["pkill", "-f", "long-dictate.py"], capture_output=True)
            self._json({"ok": True})
            return

        if parsed.path == "/toggle_llm":
            params = urllib.parse.parse_qs(parsed.query)
            enable = params.get("enable", ["0"])[0] == "1"
            cfg = load_config()
            cfg["llm_cleanup"] = enable
            save_config(cfg)
            self._json({"ok": True})
            return

        if parsed.path == "/autostart":
            params = urllib.parse.parse_qs(parsed.query)
            enable = params.get("enable", ["0"])[0] == "1"
            if enable:
                subprocess.run([
                    "osascript", "-e",
                    'tell application "System Events" to make login item at end '
                    'with properties {path:"/Applications/Dictation.app", hidden:true}'
                ], capture_output=True)
            else:
                subprocess.run([
                    "osascript", "-e",
                    'tell application "System Events" to delete login item "Dictation"'
                ], capture_output=True)
            self._json({"ok": True})
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
            config["hotkey"] = params.get("hotkey", ["fn"])[0]
            config["model"] = params.get("model", [config["model"]])[0]

            start = params.get("sound_start", ["Tink"])[0]
            stop = params.get("sound_stop", ["Pop"])[0]
            config["sound_start"] = f"/System/Library/Sounds/{start}.aiff"
            config["sound_stop"] = f"/System/Library/Sounds/{stop}.aiff"

            save_config(config)

            words = params.get("words", [""])[0]
            save_words(words)

            self._json({"ok": True})
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, status=500)

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def main():
    port = 9876
    for p in (port, 9877, 9878):
        try:
            server = HTTPServer(("127.0.0.1", p), SettingsHandler)
            port = p
            break
        except OSError:
            continue
    else:
        print("Could not bind to any port")
        sys.exit(1)

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
