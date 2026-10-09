"""Who transcribes (notes.transcribe): Whisper if the model is there, otherwise macOS speech
recognition (tredot-speech) while the app downloads it. Two fake scripts stand in for the real tools."""

import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

os.environ["TREDOT_LANG"] = "en"  # the English texts, whatever the Mac's language
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio  # noqa: E402
import notes  # noqa: E402


def script(path, body):
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class SpeechTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.wav = os.path.join(self.tmp, "note.wav")
        audio.write(self.wav, np.zeros(16000, np.float32), 16000)
        self.model = os.path.join(self.tmp, "model.bin")
        # fake whisper-cli: writes the text where -of says (the last two arguments: -of <base>)
        self.whisper = script(os.path.join(self.tmp, "whisper-cli"),
                              'for a; do last=$a; done; echo "  from whisper " > "$last.txt"')
        self.speech = script(os.path.join(self.tmp, "tredot-speech"), 'echo "from  macOS"')
        self.tools = {"whisper-cli": self.whisper, "tredot-speech": self.speech}

    def transcribe(self):
        with mock.patch.object(notes, "MODEL", self.model), \
             mock.patch.object(notes, "tool", lambda name: self.tools.get(name, name)):
            return notes.transcribe(self.wav)

    def test_whisper_when_model_is_there(self):
        open(self.model, "w").close()
        self.assertEqual(self.transcribe(), "from whisper")

    def test_macos_while_model_downloads(self):
        self.assertEqual(self.transcribe(), "from macOS")

    def test_no_model_and_no_app(self):
        self.tools["tredot-speech"] = "tredot-speech"  # not found: tool() returns the bare name
        with self.assertRaisesRegex(RuntimeError, "Whisper model"):
            self.transcribe()

    def test_auto_language_is_the_tredot_language(self):
        # Whisper's own detection fails on short notes: "auto" means the UI language
        with mock.patch.dict(notes.CONFIG, {"language": "auto"}), mock.patch.object(notes, "LANG", "it"):
            self.assertEqual(notes.speech_language(), "it")
        with mock.patch.dict(notes.CONFIG, {"language": "fr"}), mock.patch.object(notes, "LANG", "it"):
            self.assertEqual(notes.speech_language(), "fr")

    def fake_whisper_hearing(self, detected):
        """A Whisper that, left to detect, claims `detected`; it writes the language it was given."""
        script(self.whisper, 'lang=""; prev=""; for a; do [ "$prev" = "-l" ] && lang=$a; prev=$a; last=$a; done\n'
               f'[ "$lang" = auto ] && echo "auto-detected language: {detected} (p = 0.97)" >&2 && lang={detected}\n'
               'echo "text in $lang" > "$last.txt"')
        open(self.model, "w").close()

    def transcriptions(self, lang="it"):
        with mock.patch.object(notes, "MODEL", self.model), mock.patch.object(notes, "LANG", lang), \
             mock.patch.dict(notes.CONFIG, {"language": "auto"}), \
             mock.patch.object(notes, "tool", lambda name: self.tools.get(name, name)):
            return notes.transcriptions(self.wav)

    def test_detected_tredot_language_is_one_pass(self):
        self.fake_whisper_hearing("it")
        self.assertEqual(self.transcriptions(), [("it", "text in it")])

    def test_other_language_gives_both_for_the_model_to_pick(self):
        # Whisper can be confidently wrong on short notes (Italian heard as French): both go on
        self.fake_whisper_hearing("fr")
        candidates = self.transcriptions()
        self.assertEqual(candidates, [("it", "text in it"), ("fr", "text in fr")])
        for_agent = notes.for_agent(candidates)
        self.assertIn("[it] text in it", for_agent)
        self.assertIn("[fr] text in fr", for_agent)

    def test_macos_refuses(self):
        script(self.speech, 'echo "speech recognition not allowed" >&2; exit 2')
        with self.assertRaisesRegex(RuntimeError, "not allowed"):
            self.transcribe()


if __name__ == "__main__":
    unittest.main()
