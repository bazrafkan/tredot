"""Voice compression over Bluetooth: what comes out resembles what goes in."""

import array
import math
import os
import sys
import unittest

os.environ["TREDOT_LANG"] = "en"  # the English texts, whatever the Mac's language
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adpcm  # noqa: E402


def snr(original, decoded):
    """Signal-to-noise ratio in dB: above ~20 a voice stays clearly intelligible."""
    signal = sum(x * x for x in original)
    noise = sum((x - y) ** 2 for x, y in zip(original, decoded)) or 1
    return 10 * math.log10(signal / noise)


class AdpcmTest(unittest.TestCase):
    def test_round_trip(self):
        # fake "speech": two tones with changing volume
        pcm = array.array("h", (int(6000 * math.sin(i / 7) * math.sin(i / 900) + 2000 * math.sin(i / 3))
                                for i in range(4800)))
        decoded = array.array("h", adpcm.decode(adpcm.encode(pcm.tobytes())))
        self.assertEqual(len(decoded), len(pcm))
        self.assertGreater(snr(pcm, decoded), 20)

    def test_quarter_size(self):
        pcm = array.array("h", [0] * 480).tobytes()
        self.assertEqual(len(adpcm.encode(pcm)), 3 + 240)  # 960 bytes -> 243

    def test_blocks_are_independent(self):
        pcm = array.array("h", (int(9000 * math.sin(i / 5)) for i in range(960)))
        first = adpcm.encode(pcm[:480].tobytes())
        # the second block starts from the encoder's last state, written in its header
        state = adpcm.decode(first)
        second = adpcm.encode(pcm[480:].tobytes(), predicted=array.array("h", state)[-1])
        self.assertEqual(len(adpcm.decode(second)), 960)

    def test_short_block(self):
        self.assertEqual(adpcm.decode(b"\x00"), b"")


if __name__ == "__main__":
    unittest.main()
