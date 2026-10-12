"""The Tredot module as an MCP server (stdio), in two modes.

Tools (default): approve / ask / show / listen / photo / status, for any MCP agent (Claude Code,
Claude Desktop, Gemini CLI, Cursor...).

Channel (TREDOT_CHANNEL=1): no tools; pushes the sentences spoken with "Talk to Claude" into the
session as <channel source="tredot-channel"> events. Claude answers with the show tool of the
regular server. Channels are a research preview: a custom channel only loads in terminal `claude`
with --dangerously-load-development-channels (see talk.sh).

Standard library only (module.py too): it starts with macOS's python3 from a plugin or an
extension, with nothing to install. Hand-written JSON-RPC, no SDK: Claude Code does not register
as a channel a server that negotiates a revision newer than 2025-06-18.
"""

import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hook  # noqa: E402
import module  # noqa: E402
from i18n import t  # noqa: E402

CHANNEL_PROTOCOL = "2025-06-18"  # required for channels
# For clients that send no revision. Otherwise the client's revision is accepted: we only use
# initialize, tools/list, tools/call and ping, which are the same in every revision.
LATEST = "2025-06-18"
CHANNEL = bool(os.environ.get("TREDOT_CHANNEL"))

TOOLS = [
    {"name": "approve",
     "description": "Ask the user to approve an action on their desk module (a small round touch "
                    "screen next to them) BEFORE you do it. Use it for anything consequential or "
                    "hard to undo: sending messages or emails, deleting or overwriting data, "
                    "spending money, deploying, pushing, running destructive commands. The user "
                    "reads 'action' and 'details' on the screen and taps Approve or Deny. Returns "
                    "'approved', 'denied' or 'no answer' (after 2 minutes). Only go ahead if the "
                    "result is 'approved'; treat 'no answer' as denied.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "action": {"type": "string",
                                    "description": "What you want to do, in a few words (max ~40 "
                                                   "characters), e.g. 'Send email to Anna'."},
                         "details": {"type": "string",
                                     "description": "The specifics the user needs to decide: "
                                                    "recipient, command, amount, files (keep it "
                                                    "short: ~80 characters fit on the screen)."}},
                     "required": ["action"]}},
    {"name": "ask",
     "description": "Ask the user a quick question on their desk module with 2-4 short options "
                    "(max ~18 characters each) to tap. Returns the chosen option.",
     "inputSchema": {"type": "object",
                     "properties": {"question": {"type": "string"},
                                    "options": {"type": "array", "items": {"type": "string"},
                                                "minItems": 2, "maxItems": 4}},
                     "required": ["question", "options"]}},
    {"name": "show",
     "description": "Show a message on the user's desk module: a small round screen (~20 "
                    "characters per line, 7 lines per page; a tap turns the page). Plain short "
                    "text, no tables or code.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "listen",
     "description": "Show a question on the user's desk module and record the spoken answer "
                    "(ends when the user taps the screen). Returns the transcription.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}},
                     "required": ["question"]}},
    {"name": "photo",
     "description": "Turn on the desk module's camera: the user frames the shot and taps to take "
                    "it. Returns the photo. 'instruction' appears on the viewfinder (a few words, "
                    "e.g. 'Show me the plant').",
     "inputSchema": {"type": "object", "properties": {"instruction": {"type": "string"}},
                     "required": ["instruction"]}},
    {"name": "search_voice_notes",
     "description": "Search the user's own Tredot voice notes. ALWAYS use this when the user asks what "
                    "they said, noted, recorded or decided about something, in any language "
                    "(\"what did I say about the garden?\", \"cosa ho detto sul giardino?\"): the answer "
                    "is in their notes, not in your memory. Searches the user's voice notes through their knowledge graph (the notes and the "
                    "people, projects, places and topics they mention). Without 'about': an overview "
                    "(most mentioned things, latest notes). With 'about' (a name, a project, a word): "
                    "the notes about it, with their summaries and file paths, and what is related. "
                    "Use it when the user asks what they said or noted about something.",
     "inputSchema": {"type": "object", "properties": {"about": {"type": "string"}}}},
    {"name": "status",
     "description": "The real status of the desk module, read from the module itself: firmware "
                    "and protocol version, how it is connected (Wi-Fi, USB cable, Bluetooth) and "
                    "whether it is encrypted, Wi-Fi network, camera. Use it when asked about the "
                    "module instead of guessing.",
     "inputSchema": {"type": "object", "properties": {}}},
]

INSTRUCTIONS_TOOLS = (
    "These tools act on Tredot, the user's desk module: a small round touch screen with a "
    "microphone and a camera, next to the keyboard. Use approve before consequential actions (the user confirms "
    "with a tap, away from the computer); ask for quick choices, listen for a spoken answer, "
    "photo to see something, show for a short notice. Keep texts short: the screen is small. "
    "Separately, search_voice_notes searches the user's own Tredot voice notes (their knowledge graph): use it "
    "whenever they ask what they said, noted or recorded about something (a person, a project, "
    "a topic), before answering from memory."
)
INSTRUCTIONS_CHANNEL = (
    "The user talks to you from Tredot, their desk module: each sentence arrives as "
    "<channel source=\"tredot-channel\">transcription</channel> (Whisper, may contain errors: fix "
    "them where the meaning is clear). Do what is asked, then ALWAYS answer with the show tool of "
    "the tredot server, briefly and in the user's language: the screen is small. Use ask for "
    "choices, photo to see something, approve before consequential actions."
)

_out = threading.Lock()


def send(obj):
    with _out:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def text(t):
    return [{"type": "text", "text": t}]


def ensure_bridge():
    """Starts the bridge if needed (hook.bridge()); the probe connection is closed right away."""
    s = hook.bridge()
    if s:
        s.close()


def run_tool(name, args):
    if name == "approve":
        result = module.approve(args["action"], args.get("details", ""), sid="mcp")
        return text(result or "no answer")
    if name == "ask":
        choice = module.ask(args["question"], args["options"], sid="mcp")
        return text(f"The user chose: {choice}" if choice
                    else "No answer: the user did not choose within 2 minutes.")
    if name == "show":
        module.show(args["text"])
        return text("shown")
    if name == "listen":
        said = module.listen(args["question"])
        return text(f"The user said: {said}" if said
                    else "No answer: the user did not speak, or the module was busy.")
    if name == "photo":
        shot = module.photo(args["instruction"])
        if not shot:
            return text("No photo: the user did not take it, or the module was busy.")
        path, data = shot
        return text(f"Photo saved to {path}") + [
            {"type": "image", "data": data, "mimeType": "image/jpeg"}]
    if name == "search_voice_notes":
        import knowledge
        return text(knowledge.lookup(args.get("about", "")))
    if name == "status":
        return text(module.describe())
    raise ValueError(f"unknown tool: {name}")


def call_tool(req_id, name, args):
    try:
        ensure_bridge()
        send({"jsonrpc": "2.0", "id": req_id,
              "result": {"content": run_tool(name, args or {}), "isError": False}})
    except Exception as e:  # an unplugged module must not break the session
        send({"jsonrpc": "2.0", "id": req_id,
              "result": {"content": text(f"The module is not responding: {e}"), "isError": True}})


def push_voice():
    """Channel: every sentence spoken to the module becomes an event in the session."""
    while True:
        try:
            ensure_bridge()
            for event in module.subscribe():
                said = event.get("text")  # transcribed by the bridge
                if not said:
                    module.show(t("I didn't hear anything. Try again closer."))
                    continue
                send({"jsonrpc": "2.0", "method": "notifications/claude/channel",
                      "params": {"content": said, "meta": {"via": "voice"}}})
        except Exception as e:
            print(f"tredot-channel: {e!r}", file=sys.stderr, flush=True)
        time.sleep(3)  # bridge restarted or module unplugged: retry


def handle(msg):
    method, req_id = msg.get("method"), msg.get("id")
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion", LATEST)
        caps = {"experimental": {"claude/channel": {}}} if CHANNEL else {"tools": {}}
        send({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": min(asked, CHANNEL_PROTOCOL) if CHANNEL else asked,
            "capabilities": caps,
            "serverInfo": {"name": "tredot-channel" if CHANNEL else "tredot", "version": "1.0.0"},
            "instructions": INSTRUCTIONS_CHANNEL if CHANNEL else INSTRUCTIONS_TOOLS,
        }})
    elif method == "notifications/initialized":
        if CHANNEL:
            threading.Thread(target=push_voice, daemon=True).start()
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": [] if CHANNEL else TOOLS}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        # ask/listen/photo wait for the person: run in a thread so ping and the rest still answer
        threading.Thread(target=call_tool, args=(req_id, params.get("name"), params.get("arguments")),
                         daemon=True).start()
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": req_id, "result": {}})
    elif req_id is not None:
        send({"jsonrpc": "2.0", "id": req_id,
              "error": {"code": -32601, "message": f"method not supported: {method}"}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception as e:
            print(f"tredot: {e!r}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
