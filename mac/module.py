"""What the module can do for an agent, through the bridge; used by mcp_server.py and agent.py.

Standard library only: the bridge transcribes speech and shrinks photos, so this file (and the
MCP server) runs with macOS's python3, from a plugin or an extension.

Every function blocks (it waits for the person at the module): from async code call them with
asyncio.to_thread.
"""

import json
import socket

from config import SOCKET
from hook import wrap
from i18n import t

LINKS = {"wifi": "Wi-Fi", "usb": "USB cable", "ble": "Bluetooth"}


def call(msg, wait=10):
    """One request to the bridge and its reply."""
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(wait)
        s.connect(SOCKET)
        s.sendall((json.dumps(msg) + "\n").encode())
        return json.loads(s.makefile().readline() or "{}")


def show(text, title="Claude"):
    """Text on the screen, in pages: a tap turns the page."""
    call({"op": "show", "title": title, "text": text})


def ask(question, options, sid="agent", session=None):
    """2-4 options to tap; returns the chosen one, or None after 2 minutes."""
    options = [str(o) for o in options][:4]
    reply = call({"op": "ask", "sid": sid, "title": t("Question"),
                  "session": session or t("agent"), "lines": wrap(question, 22, 3), "options": options,
                  "resumes": [True] * len(options), "timeout": 120}, 130)
    answer = reply.get("answer")
    return options[answer] if isinstance(answer, int) else None


def approve(action, details="", sid="agent", session=None):
    """Approval of an action before doing it: "approved", "denied" or None (no answer).

    On screen: the action as title, the details below, "Approve" / "Deny" (two taps).
    """
    reply = call({"op": "ask", "sid": sid, "title": str(action)[:40],
                  "session": session or t("agent"), "lines": wrap(details or action, 22, 4),
                  "options": [t("Approve"), t("Deny")],
                  "resumes": [True, False], "timeout": 120}, 130)
    answer = reply.get("answer")
    return None if answer is None else ("approved" if answer == 0 else "denied")


def listen(question):
    """Asks the question and records the spoken answer; returns the transcription or None."""
    return call({"op": "listen", "prompt": question}, 200).get("text")


def photo(instruction):
    """Viewfinder with the instruction, the user takes the shot: (path, 1280 px JPEG in base64) or None."""
    reply = call({"op": "photo", "prompt": instruction}, 200)
    return (reply["path"], reply["image"]) if reply.get("path") and reply.get("image") else None


def status():
    """The module's status as the bridge knows it (from the module's H greeting and I lines)."""
    return call({"op": "status"})


def describe(state=None):
    """The status in one sentence, for an agent: versions, link, network. Real data, from the module."""
    state = state or status()
    module = state.get("module") or {}
    bridge = f"bridge on the Mac {state.get('bridge', '?')} (protocol {state.get('protocol', '?')})"
    if not state.get("connected"):
        seen = f", last firmware seen {module['firmware']}" if module.get("firmware") else ""
        return f"The module is not connected right now{seen}. {bridge.capitalize()}."
    parts = [f"Module {module.get('id', '?')}: firmware {module.get('firmware', '?')} "
             f"(protocol {module.get('protocol', '?')})",
             f"connected via {LINKS.get(state.get('link'), state.get('link'))}, "
             f"{'encrypted' if state.get('secure') else 'NOT encrypted (not paired)'}"]
    if module.get("wifi"):
        parts.append(f"Wi-Fi: {module['wifi']}")
    if module.get("cam"):
        parts.append(f"camera {module['cam']}")
    parts.append(f"microphone {module.get('mic', '?')}")
    parts.append(bridge)
    return " · ".join(parts) + "."


def subscribe():
    """The sentences spoken with "Talk to Claude", one at a time, while the bridge runs:
    {"wav": path, "text": transcription or None}.

    The bridge sends them to the latest subscriber; when it leaves, they go back to the previous one.
    """
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(SOCKET)
        s.sendall(b'{"op": "subscribe"}\n')
        for line in s.makefile():
            event = json.loads(line)
            if event.get("event") == "talk":
                yield event
