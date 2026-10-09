"""Builds the Claude Desktop extension: a .mcpb, opened with a double click.

It holds the module's MCP server (approve, ask, show, listen, photo, status) with the same scripts
as the Claude Code plugin (build_plugin.SCRIPTS, standard library only). It runs with Tredot's
Python, through the stable symlink in Application Support (integrations.py), which exists even on
a Mac without the developer tools. build_app.py builds it into the app (the menu's Claude Desktop
item opens it).

    python build_extension.py      ../build/tredot.mcpb, to try it by hand

A .mcpb is a zip with manifest.json at the top: built here, without npx (@anthropic-ai/mcpb).
"""

import json
import os
import sys
import zipfile

from mcp_server import TOOLS
from build_plugin import HERE, MANIFEST, ROOT, SCRIPTS, VERSION

OUTPUT = os.path.join(ROOT, "build", "tredot.mcpb")
# Claude Desktop expands ${HOME}: the same symlink as integrations.PYTHON
PYTHON = "${HOME}/Library/Application Support/Tredot/python3"

EXTENSION = {
    "manifest_version": "0.3",
    "name": "tredot",
    "display_name": "Tredot",
    "version": VERSION,
    "description": "Approve Claude's actions with a tap on the Tredot desk module, answer by voice, "
                   "show it photos. Needs the Tredot app on this Mac.",
    "long_description": MANIFEST["description"],
    "author": MANIFEST["author"],
    "server": {
        "type": "python",
        "entry_point": "server/mcp_server.py",
        "mcp_config": {"command": PYTHON, "args": ["${__dirname}/server/mcp_server.py"]},
    },
    "tools": [{"name": t["name"], "description": t["description"]} for t in TOOLS],
    "compatibility": {"platforms": ["darwin"]},
}


def build(output=OUTPUT):
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(EXTENSION, indent=2, ensure_ascii=False) + "\n")
        for name in SCRIPTS:
            z.write(os.path.join(HERE, name), "server/" + name)
    return output


if __name__ == "__main__":
    print(f"  {build(*sys.argv[1:])}")
