"""The module's agent: Claude (Agent SDK) talking with you through the module.

The bridge starts it the first time you tap "Talk to Claude" and passes it every recorded
sentence, already transcribed. The agent hands it to Claude and shows the answer on the screen,
in pages. Claude also gets the module's tools (mcp_server.TOOLS):

  show     writes on the screen           photo    asks you to frame a shot and looks at it
  ask      options to tap                 listen   asks you a question and records the answer
  approve  confirm an action with a tap   status   the module's real status

Reading (files in the working folder, web, calendar, email, Drive) is free; everything else goes
through the permission hook, so you confirm it on the module. One conversation per day: the log
is in the notes folder, agent-YYYY-MM-DD.md.

Model, working folder and personal instructions come from the configuration (config.py, "agent").
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server, tool  # noqa: E402
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock  # noqa: E402

import mcp_server  # noqa: E402
import module  # noqa: E402
from config import CACHE, CONFIG, NOTES, tool as program  # noqa: E402
from i18n import t  # noqa: E402
from notes import invented  # noqa: E402

AGENT = CONFIG["agent"]

SYSTEM = """You talk with me through a small desk module with a 240-pixel round screen,
a microphone, a camera and touch. I speak to you (you get Whisper's transcription, which may
contain errors: fix them where the meaning is clear). Answer in the language I speak.

Your final answer appears on the screen, about 20 characters per line and 7 lines per page:
be brief (usually 1-3 sentences), no tables or code blocks. If a long answer is needed, put it
in a Markdown file in {notes} and tell me its name.

The module's tools:
- approve: before creating, changing, sending or deleting something: I approve it with a tap.
- ask: when I have to pick among a few alternatives, give me 2-4 short options to tap.
- listen: when you need an open answer from me.
- photo: when you need to see something; tell me in a few words what to frame.
- show: for a notice while you work on something long.
- status: when I ask about the module (firmware, connection).

You have web search (weather, news, times, prices: search instead of saying you don't know; for
data that changes, like the weather, open a reliable source's page with WebFetch instead of
trusting the snippets in the results) and, if connected, Google Calendar, Gmail and Google Drive.
You can read and search on your own; anything that creates, changes, sends or deletes something
I confirm on the module, and you say so first.
"""


def system_prompt():
    text = SYSTEM.format(notes=NOTES)
    return text + ("\n" + AGENT["prompt"].strip() + "\n" if AGENT["prompt"].strip() else "")


GOOGLE_READ = [
    "mcp__claude_ai_Google_Calendar__" + t
    for t in ("list_calendars", "list_events", "get_event", "search_events", "suggest_time")
] + [
    "mcp__claude_ai_Gmail__" + t
    for t in ("search_threads", "get_thread", "get_message", "list_labels", "list_drafts", "get_draft")
] + [
    "mcp__claude_ai_Google_Drive__" + t
    for t in ("search_files", "read_file_content", "get_file_metadata", "list_recent_files")
]


# ---------- module tools ----------

def text_result(text):
    return {"content": [{"type": "text", "text": text}]}


# The MCP server's tools (mcp_server.TOOLS: names, descriptions, execution) inside the agent.
# Except photo: here the image must be in the Claude message format.

def module_tool(spec):
    @tool(spec["name"], spec["description"], spec["inputSchema"])
    async def run(args):
        if spec["name"] == "photo":
            shot = await asyncio.to_thread(module.photo, args["instruction"])
            if not shot:
                return text_result("No photo: the user did not take it, or the module was busy.")
            path, data = shot
            return {"content": [
                {"type": "text", "text": f"Photo saved to {path}"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            ]}
        return {"content": await asyncio.to_thread(mcp_server.run_tool, spec["name"], args)}
    return run


MODULE = create_sdk_mcp_server(name="tredot", version="1.0.0",
                               tools=[module_tool(spec) for spec in mcp_server.TOOLS])


def options():
    return ClaudeAgentOptions(
        model=AGENT["model"],  # Sonnet: faster and cheaper than Opus for a voice conversation
        effort="medium",
        system_prompt={"type": "preset", "preset": "claude_code", "append": system_prompt()},
        mcp_servers={"tredot": MODULE},
        allowed_tools=[f"mcp__tredot__{spec['name']}" for spec in mcp_server.TOOLS] + [
                       "Read", "Grep", "Glob",
                       # reading the web (weather, news, times) changes nothing: no confirmation
                       "WebSearch", "WebFetch"] + GOOGLE_READ,
        cwd=AGENT["cwd"],
        add_dirs=[NOTES] + AGENT["add_dirs"],
        # the settings' hooks stay active: the state on the module (as "agent") and the
        # permissions to confirm with a tap
        env={"TREDOT_AGENT": "1"},
        # the installed Claude Code: the app drops claude-agent-sdk's bundled copy (build_app.py)
        cli_path=claude if os.path.isabs(claude := program("claude")) else None,
    )


# ---------- conversation ----------

def log_turn(who, text):
    os.makedirs(NOTES, exist_ok=True)
    path = os.path.join(NOTES, f"agent-{date.today():%Y-%m-%d}.md")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            heading = t("# Conversation with the agent, {date}", date=f"{date.today():%Y-%m-%d}")
            f.write(heading + "\n\n")
        f.write(f"**{who}** ({datetime.now():%H:%M}): {text}\n\n")


async def answer(client):
    final = ""
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            texts = [b.text for b in message.content if isinstance(b, TextBlock) and b.text.strip()]
            if texts:
                final = "\n".join(texts)
        elif isinstance(message, ResultMessage):
            if message.is_error:
                final = final or t("Something went wrong: {errors}",
                                   errors="; ".join(message.errors or []))
            elif message.result:
                final = message.result
            logging.info("turn: %s, $%.4f", message.subtype, message.total_cost_usd or 0)
    return final.strip() or t("(no answer)")


async def events():
    """The events the bridge pushes to the agent, on a connection that stays open."""
    reader, writer = await asyncio.open_unix_connection(module.SOCKET, limit=1 << 20)
    writer.write(b'{"op": "subscribe"}\n')
    await writer.drain()
    while line := await reader.readline():
        yield json.loads(line)


async def main():
    os.makedirs(AGENT["cwd"], exist_ok=True)
    logging.basicConfig(filename=os.path.join(CACHE, "agent.log"),
                        level=logging.INFO, format="%(asctime)s %(message)s")
    client, day = None, None
    try:
        async for event in events():
            if event.get("event") != "talk":
                continue
            text = event.get("text")  # transcribed by the bridge
            if not text or invented(text):
                await asyncio.to_thread(module.show, t("I didn't hear anything. Try again closer."))
                continue
            if day != date.today():  # one conversation per day
                if client:
                    await client.disconnect()
                client, day = ClaudeSDKClient(options()), date.today()
                await client.connect()
            logging.info("you: %s", text)
            log_turn(t("You"), text)
            await client.query(f"[{datetime.now():%A %d/%m/%Y %H:%M}] {text}")
            reply = await answer(client)
            log_turn("Claude", reply)
            await asyncio.to_thread(module.show, reply)
    finally:
        if client:
            await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logging.exception("agent stopped")
        try:
            module.show(t("The agent stopped on an error: see agent.log."), t("Error"))
        except OSError:
            pass
        time.sleep(1)
