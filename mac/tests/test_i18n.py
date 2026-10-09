"""The UI language (i18n.py): which one is chosen, translations, fallback to English."""

import json
import os
import string
import subprocess
import sys
import tempfile
import unittest

os.environ["TREDOT_LANG"] = "en"  # the English texts, whatever the Mac's language
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import i18n  # noqa: E402
import notes  # noqa: E402


def run(code, **env):
    """code run by a fresh Python in mac/, with env added (None removes a variable): its stdout."""
    full = dict(os.environ)
    for key, value in env.items():
        if value is None:
            full.pop(key, None)
        else:
            full[key] = value
    return subprocess.run([sys.executable, "-c", code], cwd=HERE, env=full, capture_output=True,
                          text=True, check=True, timeout=20).stdout.strip()


class TranslateTest(unittest.TestCase):
    def test_english_is_the_key(self):
        self.assertEqual(i18n.LANG, "en")
        self.assertEqual(i18n.t("Reply in Gemini"), "Reply in Gemini")

    def test_italian(self):
        self.assertEqual(i18n.translate("Reply in Gemini", "it"), "Rispondi in Gemini")
        self.assertEqual(i18n.translate("Yes", "it"), "Sì")

    def test_placeholders(self):
        self.assertEqual(i18n.t("Yes, always: {what}", what="git push"), "Yes, always: git push")
        self.assertEqual(i18n.translate("Yes, always: {what}", "it", what="git push"),
                         "Sì, sempre: git push")
        self.assertEqual(i18n.translate("{time} of audio", "it", time="1:05"), "1:05 di audio")

    def test_unknown_text_stays_english(self):
        self.assertEqual(i18n.translate("Not in any table", "it"), "Not in any table")

    def test_unknown_language_is_english(self):
        self.assertEqual(i18n.translate("Reply in Gemini", "fr"), "Reply in Gemini")

    def test_every_translation_has_the_same_placeholders(self):
        for lang, table in i18n.TABLES.items():
            for english, text in table.items():
                with self.subTest(lang=lang, text=english):
                    self.assertEqual(sorted(placeholders(english)), sorted(placeholders(text)))

    def test_variants(self):
        self.assertEqual(i18n.variants("Transcript"), ["Transcript", "Trascrizione"])

    def test_note_heading_in_any_language(self):
        md = "# Title\n\n## Riassunto\n\nok\n\n## Trascrizione\n\nciao\n"
        self.assertEqual(notes.heading(md, "Transcript"), "## Trascrizione")
        self.assertIsNone(notes.heading(md, "Done by Claude"))


def placeholders(text):
    return [name for _, name, _, _ in string.Formatter().parse(text) if name]


class LanguageTest(unittest.TestCase):
    """LANG, chosen at import: TREDOT_LANG, then ui_language, then the Mac's language."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tredot-test-")
        self.config = os.path.join(self.tmp, "config.json")

    def tearDown(self):
        if os.path.exists(self.config):
            os.remove(self.config)
        os.rmdir(self.tmp)

    def lang(self, ui_language=None, **env):
        with open(self.config, "w") as f:
            json.dump({"ui_language": ui_language} if ui_language else {}, f)
        return run("import i18n; print(i18n.LANG, i18n.t('Approve'))", TREDOT_CONFIG=self.config,
                   **env)

    def test_environment(self):
        self.assertEqual(self.lang(TREDOT_LANG="it"), "it Approva")
        self.assertEqual(self.lang(TREDOT_LANG="it-IT"), "it Approva")

    def test_environment_wins_over_configuration(self):
        self.assertEqual(self.lang("it", TREDOT_LANG="en"), "en Approve")

    def test_configuration(self):
        self.assertEqual(self.lang("it", TREDOT_LANG=None), "it Approva")

    def test_unsupported_is_english(self):
        self.assertEqual(self.lang(TREDOT_LANG="fr"), "en Approve")
        self.assertEqual(self.lang("xx", TREDOT_LANG=None), "en Approve")

    def test_auto_is_a_supported_language(self):
        lang, _ = self.lang("auto", TREDOT_LANG=None).split(" ", 1)
        self.assertIn(lang, i18n.TABLES)

    def test_italian_screen_texts(self):
        out = run("import hook, module, notes; print(hook.NAMES['send_message']);"
                  "print([c[0] for c in hook.options_for({})]); print(notes.TRANSCRIPT)",
                  TREDOT_LANG="it")
        self.assertEqual(out.splitlines(), ["Invia email", "['Sì', 'No', 'In Claude']", "Trascrizione"])


if __name__ == "__main__":
    unittest.main()
