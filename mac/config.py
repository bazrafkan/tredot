"""Configuration in one place, read by every process on the Mac side.

The file is ~/.config/tredot/config.json (or the one named by TREDOT_CONFIG). A missing file or a
missing key falls back to the defaults below. Full example: config.example.json.

From a terminal (talk.sh uses it):  python config.py get agent.cwd
"""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILE = os.path.expanduser(os.environ.get("TREDOT_CONFIG", "~/.config/tredot/config.json"))


def _app():
    """Where Tredot.app is: the one containing this code (the bridge runs inside the app, see
    build_app.py), otherwise in /Applications (from the .dmg) or ~/Applications (from build_app.py)."""
    if ".app/Contents/" in HERE:
        return HERE.split(".app/Contents/")[0] + ".app"
    for folder in ("/Applications", "~/Applications"):
        path = os.path.expanduser(os.path.join(folder, "Tredot.app"))
        if os.path.isdir(path):
            return path
    return os.path.expanduser("~/Applications/Tredot.app")


# the bridge lives in its own app (build_app.py): only then does macOS grant it Bluetooth
APP = _app()
# app data that is not part of the bundle: the Whisper model, downloaded by the app
SUPPORT = os.path.expanduser("~/Library/Application Support/Tredot")

# where to look for programs: first inside the app (whisper-cli, tredot-speech, tredot-video), then
# where installers put them on macOS (hooks start with a reduced PATH)
SEARCH = [os.path.join(APP, "Contents", "MacOS"), "/opt/homebrew/bin", "/usr/local/bin",
          os.path.expanduser("~/.local/bin")]

# The Whisper model: large-v3-turbo quantized (574 MB, MIT license). The app downloads it on first
# launch (Tredot.swift) and checks it against the SHA-256 Hugging Face publishes for the file.
MODEL_NAME = "ggml-large-v3-turbo-q5_0.bin"
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/" + MODEL_NAME
MODEL_SHA256 = "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"
MODEL_SIZE = 574041195

DEFAULTS = {
    "notes_dir": "~/Documents/Tredot/notes",
    "media_dir": "~/Documents/Tredot/media",
    "cache_dir": "~/.cache/tredot",  # socket, logs
    "whisper_model": os.path.join(SUPPORT, MODEL_NAME),
    "language": "auto",  # spoken language for transcription; auto: the same as ui_language
    "ui_language": "auto",  # texts on the screen and in notes: en, it; auto: the Mac's language
    # names Whisper gets wrong on its own: it reads them as context before transcribing
    "vocabulary": "Claude, Claude Code, Tredot.",
    "ask_timeout": 60,  # seconds on the module, then the request goes back to Claude
    "link": "auto",  # how to reach the module: auto (Wi-Fi, cable, Bluetooth), usb, wifi, ble
    "tools": {"whisper-cli": None, "claude": None},  # None: search for them
    "agent": {
        "model": "claude-sonnet-5-5",
        "cwd": None,  # None: the notes folder
        "add_dirs": [],
        "prompt": "",  # appended to the agent's instructions: who you are, your projects
    },
}


def _merge(base, extra):
    out = dict(base)
    for key, value in (extra or {}).items():
        out[key] = _merge(base[key], value) if isinstance(base.get(key), dict) else value
    return out


def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            user = json.load(f)
    except FileNotFoundError:
        user = {}
    cfg = _merge(DEFAULTS, user)
    # TREDOT_CACHE_DIR: lets tests run an isolated bridge in a temporary folder
    if os.environ.get("TREDOT_CACHE_DIR"):
        cfg["cache_dir"] = os.environ["TREDOT_CACHE_DIR"]
    for key in ("notes_dir", "media_dir", "cache_dir", "whisper_model"):
        cfg[key] = os.path.expanduser(cfg[key])
    agent = cfg["agent"]
    agent["cwd"] = os.path.expanduser(agent["cwd"] or cfg["notes_dir"])
    agent["add_dirs"] = [os.path.expanduser(d) for d in agent["add_dirs"]]
    return cfg


CONFIG = load()
CACHE = CONFIG["cache_dir"]
SOCKET = os.path.join(CACHE, "bridge.sock")
NOTES = CONFIG["notes_dir"]
MEDIA = CONFIG["media_dir"]


def tool(name):
    """Path of a program (whisper-cli, claude, ...): from the configuration, or searched for,
    inside the app first."""
    found = CONFIG["tools"].get(name) or shutil.which(
        name, path=os.pathsep.join(SEARCH + [os.environ.get("PATH", "")]))
    return os.path.expanduser(found) if found else name


def get(dotted):
    value = CONFIG
    for part in dotted.split("."):
        value = value[part]
    return value


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "get":
        value = get(sys.argv[2])
        print(json.dumps(value) if isinstance(value, (dict, list)) else value)
    else:
        print(json.dumps(CONFIG, indent=2, ensure_ascii=False))
