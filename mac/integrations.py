"""Connects agents to the module from the Tredot menu.

    integrations.py status           JSON: which agents are installed and which are connected
    integrations.py claude-code      the tredot plugin in Claude Code
    integrations.py claude-desktop   opens the extension (.mcpb) in Claude Desktop, which asks to confirm
    integrations.py gemini           the module's MCP server and hooks in Gemini CLI
    integrations.py refresh          at app launch: refreshes the Python symlink and the plugin

Agents start the module's scripts (hook.py, mcp_server.py) with a Python. macOS's python3 is
missing on a Mac without the developer tools, so the app's own Python is used, through a stable
symlink in Application Support (PYTHON) that the app refreshes at every launch: if the app moves,
agents don't notice. The plugin lives there too (MARKET): Claude Code wants it in a folder that
does not change.

Runs with the app's Python (Tredot.swift); from the project it is only for testing.
"""

import json
import os
import subprocess
import sys

import agent_settings
import build_extension
import build_plugin
from config import SUPPORT, tool
from i18n import t

PYTHON = os.path.join(SUPPORT, "python3")
MARKET = os.path.join(SUPPORT, "marketplace")
SCRIPTS = os.path.join(MARKET, "plugin", "scripts")
HERE = os.path.dirname(os.path.abspath(__file__))
MCPB = os.path.join(os.path.dirname(HERE), "tredot.mcpb")  # in Contents/Resources (build_app.py)

PLUGIN_ID = "tredot@tredot"
MARKETPLACES = os.path.expanduser("~/.claude/plugins/known_marketplaces.json")
INSTALLED = os.path.expanduser("~/.claude/plugins/installed_plugins.json")
DESKTOP_APPS = ["/Applications/Claude.app", os.path.expanduser("~/Applications/Claude.app")]
DESKTOP_INSTALLED = os.path.expanduser("~/Library/Application Support/Claude/extensions-installations.json")
DESKTOP_ID = "local.mcpb.tredot.tredot"


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def link():
    """PYTHON -> this app's Python. Left alone outside the app (tests from the project)."""
    if ".app/Contents/" not in sys.executable:
        return
    os.makedirs(SUPPORT, exist_ok=True)
    if os.path.islink(PYTHON) and os.readlink(PYTHON) == sys.executable:
        return
    if os.path.lexists(PYTHON):
        os.remove(PYTHON)
    os.symlink(sys.executable, PYTHON)


def market():
    """Writes the plugin into MARKET; True if its version changed."""
    before = read_json(os.path.join(MARKET, ".claude-plugin", "marketplace.json"))
    build_plugin.build(MARKET, PYTHON)
    return before.get("plugins", [{}])[0].get("version") != build_plugin.VERSION


def claude(*args):
    path = tool("claude")
    if not os.path.isabs(path):
        raise RuntimeError(t("Claude Code is not installed on this Mac"))
    return subprocess.run([path, *args], capture_output=True, text=True, timeout=120)


# ---------- status, for the menu ----------

def claude_code_state():
    present = os.path.isabs(tool("claude"))
    source = read_json(MARKETPLACES).get("tredot", {}).get("source", {})
    installed = PLUGIN_ID in read_json(INSTALLED).get("plugins", {})
    return {"present": present, "connected": installed and source.get("path") == MARKET}


def desktop_state():
    version = read_json(DESKTOP_INSTALLED).get("extensions", {}).get(DESKTOP_ID, {}).get("version")
    return {"present": any(os.path.isdir(a) for a in DESKTOP_APPS), "version": version,
            "connected": version == build_plugin.VERSION}


def gemini_state():
    settings = read_json(agent_settings.GEMINI)
    server = settings.get("mcpServers", {}).get("tredot", {})
    present = os.path.isabs(tool("gemini")) or os.path.isdir(os.path.dirname(agent_settings.GEMINI))
    hooked = any(agent_settings.ours(h) for g in settings.get("hooks", {}).get("BeforeAgent", [])
                 for h in g.get("hooks", []))  # without the hooks it needs reconnecting
    return {"present": present, "connected": server.get("command") == PYTHON and hooked}


def state():
    return {"claude_code": claude_code_state(), "claude_desktop": desktop_state(),
            "gemini": gemini_state(), "extension": os.path.exists(MCPB)}


# ---------- connecting ----------

def claude_code():
    link()
    market()
    # hooks added by agent_settings.py (from the project) would deliver every event twice
    agent_settings.write_settings(False)
    agent_settings.mcp(False)
    claude("plugin", "marketplace", "remove", "tredot")  # start over, so reconnecting is idempotent
    added = claude("plugin", "marketplace", "add", MARKET, "--scope", "user")
    if added.returncode:
        raise RuntimeError(added.stderr.strip() or added.stdout.strip())
    done = claude("plugin", "install", PLUGIN_ID, "--scope", "user")
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or done.stdout.strip())
    return t("Tredot is in Claude Code. New sessions get it; restart the open ones.")


def claude_desktop():
    link()
    if not desktop_state()["present"]:
        raise RuntimeError(t("Claude Desktop is not installed on this Mac"))
    if not os.path.exists(MCPB):
        raise RuntimeError(t("the extension is missing from the app"))
    subprocess.run(["open", "-a", "Claude", MCPB], check=True)
    return t("Confirm with “Install” in Claude Desktop. New chats then have Tredot.")


def gemini():
    link()
    market()
    if not gemini_state()["present"]:
        raise RuntimeError(t("Gemini CLI is not installed on this Mac"))
    agent_settings.gemini(True, {"command": PYTHON, "args": [os.path.join(SCRIPTS, "mcp_server.py")],
                          "timeout": 300000},  # approve waits up to 2 minutes for the person
                   hook=f'"{PYTHON}" "{os.path.join(SCRIPTS, "hook.py")}"')
    return t("Tredot is in Gemini CLI, with its state on the screen. New sessions get it.")


def refresh():
    """At app launch: the Python symlink, Gemini's hooks if they went missing, and the plugin if
    it changed."""
    link()
    server = read_json(agent_settings.GEMINI).get("mcpServers", {}).get("tredot", {})
    if server.get("command") == PYTHON and not gemini_state()["connected"]:
        gemini()
    if claude_code_state()["connected"] and market():
        claude("plugin", "marketplace", "update", "tredot")
        claude("plugin", "update", PLUGIN_ID)
    return "ok"


ACTIONS = {"claude-code": claude_code, "claude-desktop": claude_desktop, "gemini": gemini,
           "refresh": refresh}


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "status"
    if what == "status":
        print(json.dumps(state()))
        return
    try:
        print(json.dumps({"ok": ACTIONS[what]()}, ensure_ascii=False))
    except Exception as e:  # the menu shows the reason
        print(json.dumps({"error": str(e)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
