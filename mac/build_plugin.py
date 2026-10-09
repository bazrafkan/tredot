"""Builds the Claude Code plugin (../plugin) and the marketplace that holds it (../.claude-plugin).

The plugin brings into Claude Code the hooks (state and permissions on the module) and the MCP
server (approve, ask, show, listen, photo, status). Its scripts are copies of those in mac/, the
single source: edit them here, then run `python build_plugin.py` (tests/test_plugin.py checks
they match). They use the standard library only; the bridge stays in the Tredot app.

Two copies, same content, different Python:
  - the project's (../plugin, `python build_plugin.py`): macOS's python3, for installing from
    the repository;
  - the one the app writes to Application Support (integrations.py, "Connect to Claude Code"):
    the app's Python, which exists even on a Mac without the developer tools.
"""

import json
import os
import shutil

from agent_settings import HOOKS

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugin")
# what they need, nothing more
SCRIPTS = ["hook.py", "mcp_server.py", "module.py", "config.py", "i18n.py"]
VERSION = "1.1.0"

MANIFEST = {
    "name": "tredot",
    "version": VERSION,
    "description": "Tredot for Claude Code: see what Claude is doing on a small round "
                   "screen, approve its actions with a tap, answer by voice, show it photos.",
    "author": {"name": "tredot"},
}

MARKETPLACE = {
    "name": "tredot",
    "owner": {"name": "tredot"},
    "description": "Tredot: approve your agent's actions with a tap.",
    "plugins": [{"name": "tredot", "source": "./plugin", "version": VERSION,
                 "description": MANIFEST["description"]}],
}


def hooks(python="python3"):
    """The same hooks as agent_settings.py (events, matchers, timeouts), from the plugin."""
    command = f'{quote(python)} "${{CLAUDE_PLUGIN_ROOT}}/scripts/hook.py"'
    out = {}
    for event, (matcher, timeout) in HOOKS.items():
        group = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        if matcher:
            group = {"matcher": matcher, **group}
        out[event] = [group]
    return {"hooks": out}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def quote(path):
    """For the hook shell: quotes only when needed (Application Support has a space)."""
    return f'"{path}"' if " " in path else path


def build(root=ROOT, python="python3"):
    """The marketplace in root: root/.claude-plugin/marketplace.json and the plugin in root/plugin."""
    plugin = os.path.join(root, "plugin")
    if os.path.exists(plugin):
        shutil.rmtree(plugin)
    os.makedirs(os.path.join(plugin, "scripts"))
    for name in SCRIPTS:
        shutil.copy2(os.path.join(HERE, name), os.path.join(plugin, "scripts", name))
    write_json(os.path.join(plugin, ".claude-plugin", "plugin.json"), MANIFEST)
    write_json(os.path.join(plugin, "hooks", "hooks.json"), hooks(python))
    write_json(os.path.join(plugin, ".mcp.json"), {"mcpServers": {"tredot": {
        "command": python, "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/mcp_server.py"]}}})
    write_json(os.path.join(root, ".claude-plugin", "marketplace.json"), MARKETPLACE)
    return plugin


if __name__ == "__main__":
    print(f"  {build()}")
