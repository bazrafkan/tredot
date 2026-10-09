#!/bin/bash
# A Claude Code session where "Talk to Claude" on the module arrives directly.
# Custom channels are a research preview: Claude Code asks for confirmation at every start
# ("I am using this for local development"). While this session is open the voice goes to it;
# once closed, it goes back to the always-ready agent (agent.py).
#
# Usage: talk.sh [folder]   (default: the agent's working folder, config key agent.cwd)
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/mac/.venv/bin/python"
CACHE="$("$PY" "$DIR/mac/config.py" get cache_dir)"
cd "${1:-$("$PY" "$DIR/mac/config.py" get agent.cwd)}" || exit 1
mkdir -p "$CACHE"
# the channel config is written here, with the paths of this copy of the project
"$PY" - "$PY" "$DIR/mac/mcp_server.py" > "$CACHE/channel.json" <<'PYEOF'
import json, sys
print(json.dumps({"mcpServers": {"tredot-channel": {
    "command": sys.argv[1], "args": [sys.argv[2]], "env": {"TREDOT_CHANNEL": "1"}}}}, indent=2))
PYEOF
exec claude --mcp-config "$CACHE/channel.json" \
  --dangerously-load-development-channels server:tredot-channel
