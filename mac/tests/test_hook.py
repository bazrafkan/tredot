"""The hook's pure functions: line wrapping, tool summaries, permission options."""

import os
import sys
import unittest

os.environ["TREDOT_LANG"] = "en"  # the English texts, whatever the Mac's language
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hook import from_gemini, options_for, plain, summary, when_short, wrap  # noqa: E402


class WrapTest(unittest.TestCase):
    def test_short_text_one_line(self):
        self.assertEqual(wrap("git status"), ["git status"])

    def test_wraps_on_spaces(self):
        lines = wrap("a sentence that is long enough to wrap", 22, 9)
        self.assertTrue(all(len(line) <= 22 for line in lines))
        self.assertEqual(" ".join(lines), "a sentence that is long enough to wrap")

    def test_long_word_is_cut(self):
        path = "/Users/someone/Developer/project/folder/file.py"
        lines = wrap(path, 22, 9)
        self.assertTrue(all(len(line) <= 22 for line in lines))
        self.assertEqual("".join(lines), path)

    def test_wide_characters_count_double(self):
        self.assertTrue(all(len(line) <= 11 for line in wrap("汉字" * 10, 22, 9)))

    def test_max_lines(self):
        self.assertEqual(len(wrap("a " * 100, 22, 4)), 4)


class SummaryTest(unittest.TestCase):
    def test_bash_uses_description(self):
        self.assertEqual(summary("Bash", {"command": "git push", "description": "Publish"}),
                         ("Bash", "Publish"))

    def test_file_path_is_shortened(self):
        self.assertEqual(summary("Edit", {"file_path": "/a/b/c/d.py"}), ("Edit", "c/d.py"))

    def test_mcp_calendar(self):
        name, detail = summary("mcp__claude_ai_Google_Calendar__create_event",
                               {"summary": "Train", "start": {"dateTime": "2026-10-08T18:00:00+02:00"}})
        self.assertEqual((name, detail), ("Create event", "Train · 08/10 18:00"))

    def test_plugin_tool_shows_its_text(self):
        self.assertEqual(summary("mcp__plugin_tredot_tredot__approve", {"action": "Send email"}),
                         ("Approval", "Send email"))
        self.assertEqual(summary("mcp__plugin_tredot_tredot__status", {}), ("Status", "tredot"))

    def test_when_short_leaves_unknown_alone(self):
        self.assertEqual(when_short("tomorrow"), "tomorrow")


class OptionsTest(unittest.TestCase):
    def test_always_yes_no_and_claude(self):
        labels = [c[0] for c in options_for({})]
        self.assertEqual(labels, ["Yes", "No", "In Claude"])

    def test_one_rule_suggestion(self):
        event = {"permission_suggestions": [
            {"type": "addRules", "behavior": "allow", "rules": [{"toolName": "Bash", "ruleContent": "git push:*"}]},
            {"type": "setMode", "mode": "acceptEdits"}]}
        choices = options_for(event)
        self.assertEqual([c[0] for c in choices], ["Yes", "Yes, always: git push", "No", "In Claude"])
        self.assertEqual(choices[1][1]["updatedPermissions"], [event["permission_suggestions"][0]])

    def test_no_stops_claude(self):
        no = next(c for c in options_for({}) if c[0] == "No")
        self.assertEqual(no[1]["behavior"], "deny")
        self.assertFalse(no[2])

    def test_in_claude_has_no_decision(self):
        self.assertIsNone(options_for({})[-1][1])


class PlainTest(unittest.TestCase):
    def test_strips_markdown(self):
        self.assertEqual(plain("**Done**: see [the file](a.md)\n```\ncode\n```"),
                         "Done: see the file")


class GeminiTest(unittest.TestCase):
    """Gemini CLI events become Claude Code events: the same state on the module."""

    def test_events_are_translated(self):
        self.assertEqual(from_gemini({"hook_event_name": "BeforeAgent", "prompt": "hello"})["hook_event_name"],
                         "UserPromptSubmit")
        done = from_gemini({"hook_event_name": "AfterAgent", "prompt_response": "All **done**",
                            "transcript_path": "/tmp/x.json"})
        self.assertEqual((done["hook_event_name"], done["last_assistant_message"], done["transcript_path"]),
                         ("Stop", "All **done**", None))

    def test_mcp_tool_names(self):
        tool = from_gemini({"hook_event_name": "BeforeTool", "tool_name": "mcp_tredot_approve",
                            "tool_input": {"action": "Send email"}})
        self.assertEqual(tool["tool_name"], "mcp__tredot__approve")
        self.assertEqual(summary(tool["tool_name"], tool["tool_input"]), ("Approval", "Send email"))
        shell = from_gemini({"hook_event_name": "BeforeTool", "tool_name": "run_shell_command",
                             "tool_input": {"command": "npm test"}})
        self.assertEqual(summary(shell["tool_name"], shell["tool_input"]), ("Shell", "npm test"))

    def test_notifications(self):
        self.assertEqual(from_gemini({"hook_event_name": "Notification", "notification_type": "ToolPermission"})
                         ["hook_event_name"], "Notification")
        self.assertEqual(from_gemini({"hook_event_name": "Notification", "notification_type": "Other"}), {})
        # Claude Code's own (without notification_type) is left alone
        self.assertIsNone(from_gemini({"hook_event_name": "Notification", "message": "x"}))
        self.assertIsNone(from_gemini({"hook_event_name": "PreToolUse", "tool_name": "Bash"}))


if __name__ == "__main__":
    unittest.main()
