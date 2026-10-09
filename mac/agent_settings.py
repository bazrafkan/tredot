"""Connects (or disconnects) the module to Claude Code from the project, without the plugin:
hooks in ~/.claude/settings.json and the MCP server.

    python agent_settings.py          connect: adds the hooks and registers the "tredot" MCP server
    python agent_settings.py remove   removes both
    python agent_settings.py gemini   adds the MCP server to Gemini CLI

Safe to run again and again: it first removes the module's entries it finds (also those of a copy
of the project in another folder), then adds this copy's. Other entries in settings.json are left
alone, and a backup is saved to settings.json.bak before writing.
"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.expanduser("~/.claude/settings.json")
PYTHON = os.path.join(HERE, ".venv", "bin", "python")
COMMAND = f'"{PYTHON}" "{os.path.join(HERE, "hook.py")}"'

# event -> (matcher, seconds): permissions wait for the person, the rest must be instant
HOOKS = {
    "UserPromptSubmit": (None, 5),
    "PreToolUse": ("*", 5),
    "PostToolUse": ("*", 5),
    "Stop": (None, 5),
    "StopFailure": (None, 5),
    "Notification": ("permission_prompt", 5),
    "PermissionRequest": ("*", 90),
    "MessageDisplay": (None, 3),
}


def ours(hook):
    """A hook entry of the module, from this copy of the project or another one."""
    command = hook.get("command", "")
    return hook.get("type") == "command" and "hook.py" in command and (
        "tredot" in command.lower() or HERE in command)


def without_ours(hooks):
    out = {}
    for event, groups in hooks.items():
        kept = []
        for group in groups:
            rest = [h for h in group.get("hooks", []) if not ours(h)]
            if rest:
                kept.append(dict(group, hooks=rest))
        if kept:
            out[event] = kept
    return out


def write_settings(add):
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
        shutil.copy(SETTINGS, SETTINGS + ".bak")
    except FileNotFoundError:
        settings = {}
    hooks = without_ours(settings.get("hooks", {}))
    if add:
        for event, (matcher, timeout) in HOOKS.items():
            group = {"hooks": [{"type": "command", "command": COMMAND, "timeout": timeout}]}
            if matcher:
                group = {"matcher": matcher, **group}
            hooks.setdefault(event, []).append(group)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")


def mcp(add):
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(claude):
        print("  claude not found: register the MCP server by hand (see README)")
        return False
    subprocess.run([claude, "mcp", "remove", "--scope", "user", "tredot"], capture_output=True)
    if add:
        subprocess.run([claude, "mcp", "add", "--scope", "user", "tredot", "--",
                        PYTHON, os.path.join(HERE, "mcp_server.py")], check=True,
                       stdout=subprocess.DEVNULL)
    return True


GEMINI = os.path.expanduser("~/.gemini/settings.json")
# Gemini CLI uses the same scripts as the Claude Code plugin (standard library only, python3)
GEMINI_SERVER = {"command": "python3",
                 "args": [os.path.join(os.path.dirname(HERE), "plugin", "scripts", "mcp_server.py")],
                 "timeout": 300000}  # approve waits up to 2 minutes for the person


# Gemini CLI hooks (hook.py maps them to Claude Code's): event -> seconds
GEMINI_HOOKS = {"BeforeAgent": 5, "BeforeTool": 5, "AfterTool": 5, "AfterAgent": 5, "Notification": 5}


def gemini(add, server=None, hook=None):
    """The module in Gemini CLI (if installed): the MCP server and the hooks for the on-screen state.
    server, hook: the app's (integrations.py); hook is the command that runs hook.py."""
    if not shutil.which("gemini") and not os.path.isdir(os.path.dirname(GEMINI)):
        return False
    try:
        with open(GEMINI, encoding="utf-8") as f:
            settings = json.load(f)
        shutil.copy(GEMINI, GEMINI + ".bak")
    except FileNotFoundError:
        settings = {}
    servers = settings.setdefault("mcpServers", {})
    servers.pop("tredot", None)
    if add:
        servers["tredot"] = server or GEMINI_SERVER
    if not servers:
        settings.pop("mcpServers")
    hooks = without_ours(settings.get("hooks", {}))
    if add and hook:
        for event, seconds in GEMINI_HOOKS.items():
            hooks.setdefault(event, []).append(
                {"hooks": [{"type": "command", "name": "tredot", "command": hook, "timeout": seconds * 1000}]})
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    os.makedirs(os.path.dirname(GEMINI), exist_ok=True)
    with open(GEMINI, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return True


def main():
    command = sys.argv[1:]
    if command == ["gemini"]:
        print("  Gemini CLI: Tredot added" if gemini(True) else "  Gemini CLI is not installed")
        return
    if command not in ([], ["remove"]):
        sys.exit(__doc__)
    add = not command
    write_settings(add)
    print(("  hooks added to " if add else "  hooks removed from ") + SETTINGS)
    if mcp(add):
        print("  MCP server \"tredot\" " + ("registered" if add else "removed"))


if __name__ == "__main__":
    main()
