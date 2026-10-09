"""Claude Code (and Gemini CLI) hook: forwards events to the bridge and, for permissions, waits
for a tap on the module.

Registered by the plugin (build_plugin.py) or agent_settings.py for UserPromptSubmit, PreToolUse,
PostToolUse, Stop, StopFailure, Notification (permission_prompt), PermissionRequest and
MessageDisplay. Reads the event from stdin.

Golden rule: if anything goes wrong (bridge down, module unplugged, error) it exits 0 without
printing anything, and the agent behaves as if the hook did not exist.
"""

import json
import re
import os
import socket
import subprocess
import sys
import time
import unicodedata

from config import APP, CACHE, CONFIG, SOCKET
from i18n import t

HERE = os.path.dirname(os.path.abspath(__file__))
# seconds on the module, then the request goes back to Claude
ASK_TIMEOUT = int(os.environ.get("TREDOT_ASK_TIMEOUT", CONFIG["ask_timeout"]))


def connect():
    s = socket.socket(socket.AF_UNIX)
    s.connect(SOCKET)
    return s


def bridge():
    try:
        return connect()
    except OSError:
        pass
    os.makedirs(os.path.dirname(SOCKET), exist_ok=True)
    if os.path.isdir(APP):
        # inside its app the bridge can use Bluetooth; -g: without bringing it to the front
        subprocess.Popen(["open", "-g", "-a", APP], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:  # app not built yet: the bridge still runs, but only over the USB cable
        subprocess.Popen(
            [sys.executable, os.path.join(HERE, "bridge.py")],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    for _ in range(30):
        time.sleep(0.1)
        try:
            s = connect()
            time.sleep(0.3)  # give the bridge time to open the serial port
            return s
        except OSError:
            continue
    return None


def send(msg, wait):
    s = bridge()
    if s is None:
        return {}
    s.settimeout(wait)
    with s:
        s.sendall((json.dumps(msg) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b"{}")


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def wrap(text, cols=22, max_lines=4):
    """Lines for the round screen: break on spaces, or mid-word when a word is too long."""
    out, line = [], ""
    for word in " ".join(str(text or "").split()).split(" "):
        while width(word) > cols:  # long paths and URLs
            room = cols - width(line) - (1 if line else 0)
            if room < 4:
                out.append(line)
                line, room = "", cols
            cut = 0  # how many characters fit in `room` columns (CJK characters take 2)
            while cut < len(word) and width(word[:cut + 1]) <= room:
                cut += 1
            out.append((line + " " + word[:cut]).strip())
            line, word = "", word[cut:]
        if width(line) + 1 + width(word) <= cols:
            line = (line + " " + word).strip()
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    return out[:max_lines]


def options_for(event):
    """The choices of Claude's permission dialog, shortened: (label, decision, resumes)."""
    allow = {"behavior": "allow"}
    choices = [(t("Yes"), allow, True)]
    for sug in event.get("permission_suggestions") or []:
        kind = sug.get("type")
        if kind == "addRules" and sug.get("behavior") == "allow" and sug.get("rules"):
            rule = sug["rules"][0]
            what = (rule.get("ruleContent") or rule.get("toolName") or "").rstrip("*: ")
            label = t("Yes, always: {what}", what=what) if what else t("Yes, don't ask again")
        elif kind == "setMode" and sug.get("mode") == "acceptEdits":
            label = t("Yes, all edits")
        elif kind == "addDirectories":
            label = t("Yes, add folder")
        else:
            continue
        choices.append((label, {"behavior": "allow", "updatedPermissions": [sug]}, True))
        break  # the screen has room for one rule only
    choices.append((t("No"), {"behavior": "deny", "interrupt": True,
                           "message": "The user denied this on the Tredot module."}, False))
    choices.append((t("In Claude"), None, False))
    return choices


def plain(markdown):
    """Claude's Markdown reduced to screen text, without formatting symbols."""
    text = re.sub(r"```.*?```", " ", markdown, flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [text](link) -> text
    text = re.sub(r"[*_`#>|]+", "", text)
    return " ".join(text.split())


NAMES = {name: t(label) for name, label in {
    "approve": "Approval", "ask": "Question", "show": "Show", "listen": "Listen",
    "photo": "Photo", "status": "Status",  # the module's own tools (mcp_server.py)
    "create_event": "Create event", "update_event": "Edit event",
    "delete_event": "Delete event", "respond_to_event": "Reply to event",
    "send_message": "Send email", "create_draft": "Draft email",
    # Gemini CLI tools
    "run_shell_command": "Shell", "write_file": "Write file", "replace": "Edit file",
    "read_file": "Read file", "read_many_files": "Read files", "glob": "Find files",
    "search_file_content": "Search text", "list_directory": "Folder",
    "web_fetch": "Web", "google_web_search": "Web search", "save_memory": "Memory",
}.items()}

# Gemini CLI events (geminicli.com/docs/hooks) mapped to Claude Code's, for the same state on the
# module. Gemini asks for permissions in its own terminal: the module shows "Reply in Gemini".
GEMINI = {"BeforeAgent": "UserPromptSubmit", "BeforeTool": "PreToolUse", "AfterTool": "PostToolUse",
          "AfterAgent": "Stop", "Notification": "Notification"}


def from_gemini(event):
    """A Gemini CLI event rewritten as a Claude Code event, or None if it is not from Gemini."""
    name = event.get("hook_event_name", "")
    if name not in GEMINI or (name == "Notification" and "notification_type" not in event):
        return None  # Claude Code has Notification too: Gemini's carries notification_type
    if name == "Notification" and event["notification_type"] != "ToolPermission":
        return {}  # other Gemini notices: nothing to show
    out = dict(event, hook_event_name=GEMINI[name], transcript_path=None)  # its transcript has another format
    if name == "AfterAgent":
        out["last_assistant_message"] = event.get("prompt_response", "")
    tool = event.get("tool_name", "")
    if tool.startswith("mcp_") and not tool.startswith("mcp__"):  # mcp_<server>_<tool>
        server, _, name_ = tool[4:].partition("_")
        out["tool_name"] = f"mcp__{server}__{name_}"
    return out


def when_short(stamp):
    """2026-10-08T18:30:00+02:00 -> 08/10 18:30; anything else is returned unchanged."""
    m = re.match(r"\d{4}-(\d{2})-(\d{2})(?:T(\d{2}:\d{2}))?", str(stamp))
    if not m:
        return str(stamp)
    return f"{m[2]}/{m[1]}" + (f" {m[3]}" if m[3] else "")


def summary(tool, inp):
    """Title and detail readable on a 240-pixel screen."""
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        name = NAMES.get(parts[-1], parts[-1])
        what = next((inp[k] for k in ("summary", "title", "subject", "query", "url", "action",
                                      "question", "instruction", "text")
                     if isinstance(inp.get(k), str) and inp[k]), "")
        start = inp.get("start") or inp.get("startTime") or ""
        if isinstance(start, dict):
            start = start.get("dateTime") or start.get("date") or ""
        when = when_short(start)
        server = parts[1].replace("claude_ai_", "") if len(parts) > 2 else ""
        if server.startswith("plugin_"):  # mcp__plugin_<plugin>_<server>__tool: the server is enough
            server = server.rsplit("_", 1)[-1]
        server = server.replace("_", " ")
        return name, " · ".join(x for x in (what, when) if x) or server
    # for Bash the description is what Claude shows in its dialog; the command is the fallback
    for key in ("description", "command", "file_path", "notebook_path", "url", "query", "pattern",
                "prompt", "path"):
        value = inp.get(key)
        if isinstance(value, str) and value:
            if key in ("file_path", "notebook_path", "path"):
                value = "/".join(value.rstrip("/").split("/")[-2:])
            return NAMES.get(tool, tool), value
    first = next((v for v in inp.values() if isinstance(v, str)), "")
    return NAMES.get(tool, tool), first


def main():
    event = json.load(sys.stdin)
    gemini = from_gemini(event)
    if gemini == {}:
        return
    event = gemini or event
    name = event.get("hook_event_name", "")
    if os.environ.get("TREDOT_SKIP") and name != "PermissionRequest":
        return  # voice-note `claude -p` runs: no state on the module, but permissions yes
    if os.environ.get("TREDOT_DEBUG") or os.path.exists(os.path.join(CACHE, "debug")):
        with open(os.path.join(CACHE, "events.log"), "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {event.get('session_id', '')[:8]} {name} {event.get('tool_name', '')}\n")
    session = os.path.basename(event.get("cwd", "").rstrip("/"))
    sid = event.get("session_id", "")
    transcript = event.get("transcript_path")  # the bridge reads interruptions (Esc) from it
    if os.environ.get("TREDOT_AGENT"):
        session = t("agent")
        if name == "MessageDisplay":
            return  # agent.py shows the agent's whole answer, in pages
        if name == "Stop":
            # no text (the answer arrives in pages), but the bridge must know the turn ended,
            # otherwise the module stays on "Thinking…" after the answer
            send({"op": "state", "sid": sid, "session": session, "state": "done",
                  "transcript": transcript}, wait=2)
            return
    tool = event.get("tool_name", "")
    inp = event.get("tool_input") or {}

    if name == "MessageDisplay":
        # Claude waits for this hook before showing the lines: deliver and print nothing, so the
        # text in Claude stays as it is
        send({"op": "text", "sid": sid, "message": event.get("message_id", ""),
              "delta": event.get("delta", ""), "session": session}, wait=1)
        return

    if name == "PermissionRequest":
        title, detail = summary(tool, inp)
        choices = options_for(event)
        reply = send({"op": "ask", "sid": sid, "title": title, "lines": wrap(detail), "session": session,
                      "options": [c[0] for c in choices], "resumes": [c[2] for c in choices],
                      "timeout": ASK_TIMEOUT}, wait=ASK_TIMEOUT + 10)
        answer = reply.get("answer")
        decision = choices[answer][1] if isinstance(answer, int) else None
        if decision is None:
            return  # "In Claude", timeout or module unplugged: Claude's own dialog decides
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                                 "decision": decision}}))
        return

    if name == "UserPromptSubmit":
        state = {"state": "think"}
    elif name == "PostToolUse":
        state = {"state": "think"}
    elif name == "PreToolUse":
        title, detail = summary(tool, inp)
        state = {"state": "tool", "title": title, "lines": wrap(detail, 22, 3)}
    elif name == "Stop":
        state = {"state": "done", "lines": wrap(plain(event.get("last_assistant_message", "")), 22, 3)}
    elif name == "Notification":
        # for Gemini the bridge adds the lines: the tool announced just before
        state = {"state": "wait", "title": t("Reply in Gemini") if gemini else ""}
    elif name == "StopFailure":  # the turn ended on an API error: no Stop follows
        state = {"state": "error", "title": t("Claude error"),
                 "lines": wrap(event.get("error", "").replace("_", " "), 22, 3)}
    else:
        return
    send({"op": "state", "sid": sid, "session": session, "transcript": transcript, **state},
         wait=2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never block Claude because of the module
    sys.exit(0)
