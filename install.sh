#!/bin/bash
# Installs the Mac side of the module: Python environment, tools, configuration, the app (with
# Whisper inside; the app downloads the model itself) and the Claude Code link (plugin: hooks + MCP
# server). Safe to run again: it only redoes what is missing. To remove everything: ./install.sh remove
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/mac/.venv/bin/python"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

if [ "${1:-}" = "remove" ]; then
  [ -x "$PY" ] && "$PY" "$DIR/mac/agent_settings.py" remove
  claude plugin uninstall tredot@tredot >/dev/null 2>&1 || true
  claude plugin marketplace remove tredot >/dev/null 2>&1 || true
  pkill -f "Tredot.app" || true
  pkill -f "$DIR/mac/bridge.py" || true
  APP_PATH="$("$PY" -c "import sys; sys.path.insert(0, '$DIR/mac'); import config; print(config.APP)")"
  rm -rf "$APP_PATH" "$HOME/Library/LaunchAgents/ai.tredot.mac.plist"
  echo "Done. Configuration (~/.config/tredot), notes and media are left: delete them by hand if you want."
  exit 0
fi

echo "1. Tools"
command -v brew >/dev/null || { echo "  Homebrew is required: https://brew.sh" >&2; exit 1; }
# cmake: to build Whisper the first time, inside the app (mac/build_app.py)
for cmd in uv cmake; do
  command -v "$cmd" >/dev/null || { echo "  installing $cmd"; brew install "$cmd"; }
done
command -v claude >/dev/null || {
  echo "  Claude Code is missing: https://docs.claude.com/claude-code (then run this again)" >&2; exit 1; }
command -v swiftc >/dev/null || {
  echo "  the Xcode command line tools are missing (to build the app): xcode-select --install, then run this again" >&2
  exit 1; }
echo "  uv, cmake, swiftc and claude are installed"

echo "2. Python environment"
# uv creates mac/.venv with the versions in mac/uv.lock (and downloads Python if needed)
uv sync --quiet --project "$DIR/mac"
echo "  $("$PY" --version) in mac/.venv"

echo "3. Configuration"
CONFIG="${TREDOT_CONFIG:-$HOME/.config/tredot/config.json}"
if [ ! -f "$CONFIG" ]; then
  mkdir -p "$(dirname "$CONFIG")"
  echo '{}' > "$CONFIG"
  echo "  created $CONFIG (defaults; full example in config.example.json)"
else
  echo "  $CONFIG already exists"
fi
mkdir -p "$("$PY" "$DIR/mac/config.py" get notes_dir)" "$("$PY" "$DIR/mac/config.py" get media_dir)"

echo "4. Tredot, the menu-bar app (keeps the bridge running)"
echo "  the first time it also builds Whisper (a few minutes); the app downloads the model (574 MB)"
"$PY" "$DIR/mac/build_app.py"
APP_PATH="$("$PY" -c "import sys; sys.path.insert(0, '$DIR/mac'); import config; print(config.APP)")"
open -g -a "$APP_PATH"

echo "5. Agents"
# same as the app's menu items (mac/integrations.py), with the app's Python
APP="$APP_PATH/Contents/Resources"
integration() { "$APP/python/bin/python3" "$APP/bridge/integrations.py" "$1"; }
integration claude-code | grep -q '"ok"' && echo "  Claude Code: tredot plugin" \
  || echo "  Claude Code: not connected (try again from the app's menu)"
integration gemini | grep -q '"ok"' && echo "  Gemini CLI: module added"
echo "  Claude Desktop: from the app's menu, \"Install in Claude Desktop…\""

echo
echo "Done. The three Tredot dots are in the menu bar, top right."
echo "Connect the module with the USB cable and choose \"Pair This Mac…\" from the menu (tap \"Yes\""
echo "twice on the module), then \"Connect to Wi-Fi…\". From a terminal: mac/pair.py and mac/wifi.py."
echo "Claude Code sessions that are already open do not see the module; new sessions do."
