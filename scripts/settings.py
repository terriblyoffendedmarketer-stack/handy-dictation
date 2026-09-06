#!/usr/bin/env python3
# settings.py — Web-based settings GUI for Dictation.app
# Usage: python3 scripts/settings.py
# Opens a local web page to configure hotkey, model, sounds, custom vocabulary,
# and review transcription history with correction capability for self-learning.
# Changes are saved to ~/.dictation/config.json and ~/.dictation/words.txt.
#
# Gotchas:
# - Uses stdlib only (http.server + webbrowser) — no Flask/FastAPI needed.
# - Port 9876 chosen to avoid conflicts. Falls back if busy.
# - The /status endpoint is polled every 2s to keep daemon status live.
# - Daemon toggle (start/stop) works via pgrep + open/pkill.
# - Auto-start toggle uses osascript Login Items (not launchd).
# - Corrections saved to corrections.json, substitutions mined via word-level diffs.
# - Corrected proper nouns auto-added to words.txt for whisper's initial_prompt.

import json
import os
import sys
import subprocess
import webbrowser
import urllib.parse
import difflib
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

DICTATION_DIR = os.path.expanduser("~/.dictation")
CONFIG_PATH = os.path.join(DICTATION_DIR, "config.json")
WORDS_PATH = os.path.join(DICTATION_DIR, "words.txt")
LOG_PATH = os.path.join(DICTATION_DIR, "transcription-log.json")
CORRECTIONS_PATH = os.path.join(DICTATION_DIR, "corrections.json")
SUBSTITUTIONS_PATH = os.path.join(DICTATION_DIR, "substitutions.json")
RECORDINGS_DIR = os.path.join(DICTATION_DIR, "recordings")

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
    ("Distil Whisper Large v3 — best accuracy (recommended)", "mlx-community/distil-whisper-large-v3"),
    ("Whisper Medium — good accuracy, small model", "mlx-community/whisper-medium-mlx"),
    ("Whisper Large v3 — slowest, most RAM", "mlx-community/whisper-large-v3-mlx"),
]

SYSTEM_SOUNDS = [
    "Basso", "Blow", "Bottle", "Frog", "Funk", "Glass", "Hero",
    "Morse", "Ping", "Pop", "Purr", "Sosumi", "Submarine", "Tink",
]


def load_config():
    cfg = {
        "hotkey": "fn",
        "model": "mlx-community/distil-whisper-large-v3",
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


# ── Transcription log & corrections ─────────────────────────────────────────

def load_transcription_log():
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def load_corrections():
    if os.path.exists(CORRECTIONS_PATH):
        try:
            with open(CORRECTIONS_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_corrections(corrections):
    os.makedirs(DICTATION_DIR, exist_ok=True)
    with open(CORRECTIONS_PATH, "w") as f:
        json.dump(corrections, f, indent=2)


def load_substitutions():
    if os.path.exists(SUBSTITUTIONS_PATH):
        try:
            with open(SUBSTITUTIONS_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _strip_punct(word):
    return word.strip(".,!?;:\"'()[]{}—-")


def build_substitutions_from_corrections(corrections):
    """Mine word-level substitution rules from user corrections."""
    subs = {}
    for entry in corrections.values():
        orig = entry.get("original", "")
        corr = entry.get("corrected", "")
        if not orig or not corr or orig == corr:
            continue
        orig_words = [_strip_punct(w) for w in orig.split() if _strip_punct(w)]
        corr_words = [_strip_punct(w) for w in corr.split() if _strip_punct(w)]
        sm = difflib.SequenceMatcher(None, orig_words, corr_words)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "replace":
                orig_phrase = " ".join(orig_words[i1:i2])
                corr_phrase = " ".join(corr_words[j1:j2])
                if orig_phrase != corr_phrase:
                    subs[orig_phrase.lower()] = corr_phrase
    return subs


def save_substitutions(subs):
    os.makedirs(DICTATION_DIR, exist_ok=True)
    with open(SUBSTITUTIONS_PATH, "w") as f:
        json.dump(subs, f, indent=2)


COMMON_WORDS = {
    "i", "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
    "to", "for", "of", "in", "on", "at", "by", "it", "do", "so", "if",
    "my", "we", "he", "she", "they", "this", "that", "not", "with", "from",
    "be", "have", "has", "had", "will", "would", "can", "could", "also",
    "just", "then", "than", "very", "well", "now", "here", "there", "when",
    "what", "how", "all", "some", "any", "no", "yes", "ok", "okay",
}


def auto_update_vocabulary(subs):
    """Add corrected proper nouns to words.txt for whisper's initial_prompt."""
    words_text = load_words()
    existing = set(
        line.strip().lower() for line in words_text.split("\n")
        if line.strip() and not line.strip().startswith("#")
    )
    new_vocab = []
    for orig_key, replacement in subs.items():
        words = replacement.split()
        if not words:
            continue
        orig_words_lower = set(orig_key.lower().split())
        changed_words = [w for w in words if w.lower() not in orig_words_lower]
        has_proper = any(
            w[0].isupper() and w.lower() not in COMMON_WORDS
            for w in changed_words if w
        )
        if has_proper and replacement.lower() not in existing:
            new_vocab.append(replacement)
            existing.add(replacement.lower())
    if new_vocab:
        lines = words_text.rstrip("\n")
        for word in new_vocab:
            lines += f"\n{word}"
        save_words(lines + "\n")
    return new_vocab


# ── Static JS/CSS for history tab (regular strings, no f-string escaping) ───

EXTRA_CSS = """
/* Tab bar */
.tab-bar { display: flex; gap: 4px; margin-bottom: 16px; }
.tab {
    padding: 8px 20px; border: 1px solid #2a3a5c; border-radius: 8px;
    background: transparent; color: #6c7a96; font-size: 14px; font-weight: 500;
    cursor: pointer; transition: all 0.2s;
}
.tab.active { background: #16213e; color: #e0e0e0; border-color: #4a6fa5; }
.tab:hover:not(.active) { border-color: #3a4a6c; color: #c0c8d8; }

/* History entries */
.history-entry {
    background: #16213e; border-radius: 12px; padding: 16px 20px;
    margin-bottom: 12px; border: 1px solid #1a1a3e;
}
.entry-header {
    display: flex; align-items: center; gap: 12px; margin-bottom: 10px;
    font-size: 13px;
}
.entry-time { color: #6c7a96; }
.entry-duration { color: #5a6a8a; }
.entry-badge {
    font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 500;
}
.entry-badge.corrected { background: #1a3a2a; color: #80c8a0; }
.history-entry audio { width: 100%; height: 32px; margin-bottom: 8px; }
.entry-text {
    width: 100%; padding: 10px 12px; border: 1px solid #2a3a5c;
    border-radius: 8px; background: #0f1729; color: #e0e0e0;
    font-size: 14px; font-family: inherit; outline: none;
    resize: vertical; min-height: 60px; line-height: 1.5;
    transition: border-color 0.2s;
}
.entry-text:focus { border-color: #4a6fa5; }
.entry-text.modified { border-color: #f59e0b; }
.entry-actions { display: flex; gap: 8px; margin-top: 8px; }
.save-correction-btn {
    padding: 6px 16px; border: none; border-radius: 6px;
    background: #f59e0b; color: #000; font-size: 13px;
    font-weight: 500; cursor: pointer; transition: background 0.2s;
}
.save-correction-btn:hover { background: #fbbf24; }
.sub-rule {
    padding: 6px 0; font-size: 14px; border-bottom: 1px solid #1a1a3e;
}
.sub-rule:last-child { border-bottom: none; }
.sub-from { color: #f87171; text-decoration: line-through; }
.sub-to { color: #4ade80; }
.sub-arrow { color: #5a6a8a; margin: 0 8px; }
.empty-state { text-align: center; padding: 40px 20px; color: #5a6a8a; }
"""

HISTORY_JS = """
function switchTab(tab) {
    var tabs = document.querySelectorAll('.tab');
    for (var i = 0; i < tabs.length; i++) tabs[i].classList.remove('active');
    document.querySelector('.tab[data-tab="' + tab + '"]').classList.add('active');
    document.getElementById('settingsTab').style.display = tab === 'settings' ? '' : 'none';
    document.getElementById('historyTab').style.display = tab === 'history' ? '' : 'none';
    if (tab === 'history') loadHistory();
}

function loadHistory() {
    document.getElementById('historyList').innerHTML = '<p class="empty-state">Loading...</p>';
    fetch('/history').then(function(r) { return r.json(); }).then(renderHistory);
}

function esc(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function escAttr(s) {
    return esc(s).replace(/"/g, '&quot;');
}

function renderHistory(data) {
    var subsDiv = document.getElementById('subsSection');
    var subs = data.substitutions || {};
    var subKeys = Object.keys(subs);
    var sh = '<div class="section"><div class="section-title">Learned Substitutions (' + subKeys.length + ')</div>';
    if (subKeys.length > 0) {
        for (var i = 0; i < subKeys.length; i++) {
            var k = subKeys[i];
            sh += '<div class="sub-rule"><span class="sub-from">' + esc(k) +
                  '</span><span class="sub-arrow">\\u2192</span><span class="sub-to">' +
                  esc(subs[k]) + '</span></div>';
        }
    } else {
        sh += '<p class="hint">No substitutions learned yet. Correct transcriptions below to teach the tool.</p>';
    }
    sh += '</div>';
    subsDiv.innerHTML = sh;

    var listDiv = document.getElementById('historyList');
    var entries = data.entries || [];
    if (entries.length === 0) {
        listDiv.innerHTML = '<p class="empty-state">No transcriptions yet. Start dictating to build history.</p>';
        return;
    }
    var html = '';
    for (var i = 0; i < entries.length; i++) {
        var e = entries[i];
        var hasCorrected = e.corrected != null;
        var displayText = hasCorrected ? e.corrected : e.text;
        var ts = new Date(e.timestamp);
        var timeStr = ts.toLocaleDateString('en-US', {month:'short', day:'numeric'}) + ', ' +
                     ts.toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit'});
        html += '<div class="history-entry" data-file="' + escAttr(e.file) + '">';
        html += '<div class="entry-header">';
        html += '<span class="entry-time">' + timeStr + '</span>';
        html += '<span class="entry-duration">' + e.duration + 's</span>';
        if (hasCorrected) html += '<span class="entry-badge corrected">corrected</span>';
        html += '</div>';
        html += '<audio controls preload="none" src="/audio/' + encodeURIComponent(e.file) + '"></audio>';
        html += '<textarea class="entry-text" data-original="' + escAttr(e.text) +
                '" oninput="onTextEdit(this)">' + esc(displayText) + '</textarea>';
        html += '<div class="entry-actions">';
        html += '<button class="save-correction-btn" onclick="saveCorrection(this)" style="display:none">Save Correction</button>';
        html += '</div></div>';
    }
    listDiv.innerHTML = html;
}

function onTextEdit(textarea) {
    var original = textarea.getAttribute('data-original');
    var modified = textarea.value !== original;
    textarea.classList.toggle('modified', modified);
    var btn = textarea.parentElement.querySelector('.save-correction-btn');
    if (btn) btn.style.display = modified ? '' : 'none';
}

function saveCorrection(btn) {
    var entry = btn.closest('.history-entry');
    var file = entry.getAttribute('data-file');
    var textarea = entry.querySelector('.entry-text');
    var original = textarea.getAttribute('data-original');
    var corrected = textarea.value;
    btn.disabled = true;
    btn.textContent = 'Saving...';
    fetch('/correct', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: 'file=' + encodeURIComponent(file) +
              '&original=' + encodeURIComponent(original) +
              '&corrected=' + encodeURIComponent(corrected)
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (data.ok) {
            var msg = 'Correction saved';
            if (data.new_vocab && data.new_vocab.length > 0) {
                msg += ' \\u2014 added to vocabulary: ' + data.new_vocab.join(', ');
            }
            showToast(msg, 'success');
            textarea.classList.remove('modified');
            btn.style.display = 'none';
            var header = entry.querySelector('.entry-header');
            if (!header.querySelector('.corrected')) {
                var badge = document.createElement('span');
                badge.className = 'entry-badge corrected';
                badge.textContent = 'corrected';
                header.appendChild(badge);
            }
        } else {
            showToast('Error: ' + (data.error || 'unknown'), 'error');
        }
        btn.disabled = false;
        btn.textContent = 'Save Correction';
    });
}
"""


# ── HTML builder ─────────────────────────────────────────────────────────────

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

{EXTRA_CSS}
</style>
</head>
<body>
<div class="container">
    <h1>Dictation</h1>
    <p class="subtitle">Push-to-talk speech-to-text</p>

    <!-- Tab bar -->
    <div class="tab-bar">
        <button type="button" class="tab active" data-tab="settings" onclick="switchTab('settings')">Settings</button>
        <button type="button" class="tab" data-tab="history" onclick="switchTab('history')">History</button>
    </div>

    <!-- Daemon status -->
    <div class="status-bar">
        <div class="status-left">
            <div id="statusDot" class="status-dot {'running' if daemon_running else 'stopped'}"></div>
            <span id="statusLabel" class="status-label">{'Listening' if daemon_running else 'Stopped'}</span>
        </div>
        <button id="toggleBtn" class="toggle-btn {'stop' if daemon_running else 'start'}"
                onclick="toggleDaemon()">{'Stop' if daemon_running else 'Start'}</button>
    </div>

    <!-- Settings Tab -->
    <div id="settingsTab">
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
            <p class="hint">Distil Large v3 is the most accurate on your audio (16.9% WER). Medium is a close second (18.1% WER).</p>
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

    <!-- History Tab -->
    <div id="historyTab" style="display:none">
        <div id="subsSection"></div>
        <div id="historyList">
            <p class="empty-state">Loading...</p>
        </div>
    </div>
</div>

<div id="toast" class="toast"></div>

<script>
{HISTORY_JS}

function showToast(msg, type) {{
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast ' + type + ' show';
    setTimeout(function() {{ t.className = 'toast'; }}, 3000);
}}

function playSound(field) {{
    var sel = document.querySelector('select[name="' + field + '"]');
    fetch('/play?sound=' + encodeURIComponent(sel.value));
}}

function toggleDaemon() {{
    var btn = document.getElementById('toggleBtn');
    btn.disabled = true;
    btn.textContent = '...';
    var running = btn.classList.contains('stop');
    fetch(running ? '/stop' : '/start')
        .then(function(r) {{ return r.json(); }})
        .then(function() {{ setTimeout(updateStatus, 1500); }});
}}

function toggleLlm() {{
    var sw = document.getElementById('llmSwitch');
    var enabling = !sw.classList.contains('on');
    fetch('/toggle_llm?enable=' + (enabling ? '1' : '0'))
        .then(function(r) {{ return r.json(); }})
        .then(function(data) {{
            if (data.ok) {{
                sw.classList.toggle('on');
                showToast(enabling ? 'LLM cleanup enabled' : 'LLM cleanup disabled', 'success');
            }}
        }});
}}

function toggleAutostart() {{
    var sw = document.getElementById('autostartSwitch');
    var enabling = !sw.classList.contains('on');
    fetch('/autostart?enable=' + (enabling ? '1' : '0'))
        .then(function(r) {{ return r.json(); }})
        .then(function(data) {{
            if (data.ok) {{
                sw.classList.toggle('on');
                showToast(enabling ? 'Auto-start enabled' : 'Auto-start disabled', 'success');
            }}
        }});
}}

function updateStatus() {{
    fetch('/status').then(function(r) {{ return r.json(); }}).then(function(data) {{
        var dot = document.getElementById('statusDot');
        var label = document.getElementById('statusLabel');
        var btn = document.getElementById('toggleBtn');
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
    var formData = new FormData(this);
    fetch('/save', {{
        method: 'POST',
        body: new URLSearchParams(formData),
    }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
        if (data.ok) {{
            var msg = data.restarted ? 'Settings saved. Daemon restarting with new config...' : 'Settings saved.';
            showToast(msg, 'success');
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
                venv_python = os.path.join(DICTATION_DIR, ".venv", "bin", "python3")
                daemon_script = os.path.join(DICTATION_DIR, "long-dictate.py")
                subprocess.Popen(
                    [venv_python, daemon_script],
                    stdout=open("/tmp/dictation.log", "w"),
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
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

        if parsed.path == "/history":
            log = load_transcription_log()
            corrections = load_corrections()
            subs = load_substitutions()
            entries = []
            for entry in reversed(log):
                filename = entry.get("file", "")
                corr = corrections.get(filename)
                entries.append({
                    "file": filename,
                    "text": entry.get("text", ""),
                    "duration": entry.get("duration", 0),
                    "timestamp": entry.get("timestamp", ""),
                    "corrected": corr["corrected"] if corr else None,
                })
            self._json({"entries": entries[:50], "substitutions": subs})
            return

        if parsed.path.startswith("/audio/"):
            filename = urllib.parse.unquote(parsed.path[7:])
            if "/" in filename or "\\" in filename or ".." in filename:
                self.send_response(403)
                self.end_headers()
                return
            filepath = os.path.join(RECORDINGS_DIR, filename)
            if not os.path.exists(filepath):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(os.path.getsize(filepath)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(filepath, "rb") as f:
                self.wfile.write(f.read())
            return

        config = load_config()
        words = load_words()
        html = build_html(config, words)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/correct":
            params = urllib.parse.parse_qs(body)
            filename = params.get("file", [""])[0]
            original = params.get("original", [""])[0]
            corrected = params.get("corrected", [""])[0]

            if not filename or not corrected:
                self._json({"ok": False, "error": "missing fields"}, status=400)
                return

            corrections = load_corrections()
            corrections[filename] = {
                "original": original,
                "corrected": corrected,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            save_corrections(corrections)

            subs = build_substitutions_from_corrections(corrections)
            save_substitutions(subs)

            new_vocab = auto_update_vocabulary(subs)

            self._json({"ok": True, "substitutions": subs, "new_vocab": new_vocab})
            return

        if parsed.path == "/save":
            params = urllib.parse.parse_qs(body)
            try:
                old_config = load_config()
                config = dict(old_config)
                config["hotkey"] = params.get("hotkey", ["fn"])[0]
                config["model"] = params.get("model", [config["model"]])[0]

                start = params.get("sound_start", ["Tink"])[0]
                stop = params.get("sound_stop", ["Pop"])[0]
                config["sound_start"] = f"/System/Library/Sounds/{start}.aiff"
                config["sound_stop"] = f"/System/Library/Sounds/{stop}.aiff"

                save_config(config)

                words = params.get("words", [""])[0]
                save_words(words)

                needs_restart = (
                    config["hotkey"] != old_config.get("hotkey")
                    or config["model"] != old_config.get("model")
                )
                restarted = False
                if needs_restart and is_daemon_running():
                    subprocess.run(["pkill", "-f", "long-dictate.py"], capture_output=True)
                    time.sleep(1)
                    venv_python = os.path.join(DICTATION_DIR, ".venv", "bin", "python3")
                    daemon_script = os.path.join(DICTATION_DIR, "long-dictate.py")
                    subprocess.Popen(
                        [venv_python, daemon_script],
                        stdout=open("/tmp/dictation.log", "w"),
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    restarted = True

                self._json({"ok": True, "restarted": restarted})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=500)
            return

        self.send_response(404)
        self.end_headers()

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

    url = f"http://localhost:{port}"
    print(f"Dictation Settings: {url}")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
