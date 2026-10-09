"""The Claude Code plugin (../plugin) matches mac/: rebuild it with `python build_plugin.py`."""

import filecmp
import json
import os
import shlex
import sys
import tempfile
import unittest
import zipfile

os.environ["TREDOT_LANG"] = "en"  # the English texts, whatever the Mac's language
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_extension  # noqa: E402
import build_plugin  # noqa: E402
from agent_settings import HOOKS  # noqa: E402


class PluginTest(unittest.TestCase):
    def test_scripts_are_copies_of_mac(self):
        for name in build_plugin.SCRIPTS:
            self.assertTrue(filecmp.cmp(os.path.join(build_plugin.HERE, name),
                                        os.path.join(build_plugin.PLUGIN, "scripts", name), shallow=False),
                            f"plugin/scripts/{name} is stale: run python build_plugin.py")

    def test_hooks_match_agent_settings(self):
        with open(os.path.join(build_plugin.PLUGIN, "hooks", "hooks.json")) as f:
            self.assertEqual(json.load(f), build_plugin.hooks())
        self.assertEqual(set(build_plugin.hooks()["hooks"]), set(HOOKS))

    def test_marketplace_points_to_plugin(self):
        with open(os.path.join(build_plugin.ROOT, ".claude-plugin", "marketplace.json")) as f:
            market = json.load(f)
        self.assertEqual(market["plugins"][0]["source"], "./plugin")
        with open(os.path.join(build_plugin.PLUGIN, ".claude-plugin", "plugin.json")) as f:
            self.assertEqual(json.load(f)["name"], market["plugins"][0]["name"])

    def test_app_copy_uses_app_python(self):
        # the copy the app writes (integrations.py): its own Python, despite the space in Application Support
        python = "/Users/x/Library/Application Support/Tredot/python3"
        with tempfile.TemporaryDirectory() as root:
            built = build_plugin.build(root, python)
            with open(os.path.join(built, "hooks", "hooks.json")) as f:
                command = json.load(f)["hooks"]["Stop"][0]["hooks"][0]["command"]
            self.assertEqual(shlex.split(command)[0], python)
            with open(os.path.join(built, ".mcp.json")) as f:
                self.assertEqual(json.load(f)["mcpServers"]["tredot"]["command"], python)

    def test_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(build_extension.build(os.path.join(tmp, "x.mcpb"))) as z:
                manifest = json.loads(z.read("manifest.json"))
                names = set(z.namelist())
        self.assertEqual(manifest["version"], build_plugin.VERSION)
        self.assertTrue(manifest["server"]["mcp_config"]["command"].startswith("${HOME}/Library/"))
        self.assertEqual(names, {"manifest.json"} | {"server/" + n for n in build_plugin.SCRIPTS})


if __name__ == "__main__":
    unittest.main()
