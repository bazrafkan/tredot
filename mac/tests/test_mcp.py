"""The stdio MCP server: handshake, tools, channel. No module needed."""

import json
import os
import subprocess
import sys
import unittest

os.environ["TREDOT_LANG"] = "en"  # the English texts, whatever the Mac's language
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def session(*messages, channel=False):
    """Sends the JSON-RPC messages to the server and returns the replies that have an id."""
    env = dict(os.environ)
    env.pop("TREDOT_CHANNEL", None)
    if channel:
        env["TREDOT_CHANNEL"] = "1"
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    out = subprocess.run([sys.executable, os.path.join(HERE, "mcp_server.py")], input=stdin,
                         capture_output=True, text=True, env=env, timeout=20).stdout
    return {r["id"]: r for r in map(json.loads, out.splitlines()) if "id" in r}


def init(version="2025-06-18"):
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": version, "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}}


class McpTest(unittest.TestCase):
    def test_tools(self):
        replies = session(init(), {"jsonrpc": "2.0", "method": "notifications/initialized"},
                          {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "tredot")
        self.assertIn("tools", replies[1]["result"]["capabilities"])
        names = [t["name"] for t in replies[2]["result"]["tools"]]
        self.assertEqual(names, ["approve", "ask", "show", "listen", "photo", "search_voice_notes", "status"])

    def test_tools_accept_the_client_version(self):
        replies = session(init("2026-07-28"))
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2026-07-28")

    def test_channel_stays_on_2025_06_18(self):
        replies = session(init("2026-07-28"), channel=True)
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2025-06-18")

    def test_older_client_keeps_its_version(self):
        replies = session(init("2024-11-05"))
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2024-11-05")

    def test_channel_mode(self):
        replies = session(init(), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, channel=True)
        caps = replies[1]["result"]["capabilities"]
        self.assertEqual(caps, {"experimental": {"claude/channel": {}}})
        self.assertEqual(replies[2]["result"]["tools"], [])

    def test_ping_and_unknown_method(self):
        replies = session(init(), {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                          {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        self.assertEqual(replies[2]["result"], {})
        self.assertEqual(replies[3]["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
