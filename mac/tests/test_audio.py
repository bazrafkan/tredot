"""Voice cleanup (audio.py), which replaces ffmpeg."""

import math
import os
import sys
import tempfile
import unittest

import numpy as np

os.environ["TREDOT_LANG"] = "en"  # the English texts, whatever the Mac's language
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio  # noqa: E402

RATE = 16000


def tone(freq, seconds, amplitude):
    t = np.arange(int(RATE * seconds)) / RATE
    return amplitude * np.sin(2 * math.pi * freq * t)


class AudioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src, self.dst = os.path.join(self.tmp, "in.wav"), os.path.join(self.tmp, "out.wav")

    def rms(self, x):
        return float(np.sqrt(np.mean(x ** 2)))

    def test_quiet_voice_gets_louder(self):
        audio.write(self.src, tone(300, 3, 300), RATE)  # like the Sense microphone: quiet
        audio.clean(self.src, self.dst)
        out, _ = audio.read(self.dst)
        self.assertGreater(self.rms(out), 2000)
        self.assertLessEqual(np.max(np.abs(out)), 32767)

    def test_rumble_goes_away(self):
        audio.write(self.src, tone(30, 3, 3000) + tone(800, 3, 300), RATE)  # loud rumble + voice
        x, _ = audio.read(self.src)
        filtered = audio.high_pass(x, RATE)
        low = np.abs(np.fft.rfft(filtered))[int(30 * 3)]   # 30 Hz (3 s: one bin every 1/3 Hz)
        voice = np.abs(np.fft.rfft(filtered))[int(800 * 3)]
        self.assertLess(low, voice)  # it was 10 times louder, now below the voice

    def test_trim_or_keep_timing(self):
        audio.write(self.src, tone(300, 2, 300), RATE)
        audio.clean(self.src, self.dst)
        self.assertEqual(len(audio.read(self.dst)[0]), int(RATE * (2 - audio.SKIP_START)))
        audio.clean(self.src, self.dst, keep_timing=True)  # videos: same length, start silenced
        out, _ = audio.read(self.dst)
        self.assertEqual(len(out), RATE * 2)
        self.assertLess(np.max(np.abs(out[:int(RATE * 0.25)])), 50)

    def test_short_recording(self):
        # under 1.3 s (0.3 trimmed at the start): one or two windows, where smoothing got the length wrong
        for seconds in (0.5, 1.0, 1.2):
            audio.write(self.src, tone(300, seconds, 200), RATE)
            audio.clean(self.src, self.dst)
            out, _ = audio.read(self.dst)
            self.assertEqual(len(out), int(RATE * seconds) - int(RATE * audio.SKIP_START))

    def test_silence_stays_silence(self):
        audio.write(self.src, np.zeros(RATE * 2), RATE)
        audio.clean(self.src, self.dst)
        self.assertEqual(np.max(np.abs(audio.read(self.dst)[0])), 0)


if __name__ == "__main__":
    unittest.main()
