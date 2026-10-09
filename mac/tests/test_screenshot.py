"""The module's screen as a PNG (screenshot.py): colors, the round mask, a valid file."""

import os
import sys
import unittest
import zlib

os.environ["TREDOT_LANG"] = "en"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import screenshot  # noqa: E402


def rgb565(red, green, blue):
    return ((red >> 3) << 11 | (green >> 2) << 5 | blue >> 3).to_bytes(2, "little")


class ScreenshotTest(unittest.TestCase):
    def test_colors_and_round_mask(self):
        image = screenshot.rgba(rgb565(255, 128, 0) * (240 * 240), 240, 240)
        self.assertEqual(tuple(image[120, 120]), (255, 129, 0, 255))  # 6 bits of green: 128 -> 129
        self.assertEqual(image[0, 0, 3], 0)  # the corners are outside the round screen
        self.assertEqual(image[120, 0, 3], 255)  # the edge is inside

    def test_png_is_valid(self):
        data = screenshot.png(screenshot.rgba(rgb565(0, 0, 255) * 16, 4, 4))
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        length = int.from_bytes(data[33:37], "big")  # IDAT after the 8-byte signature and IHDR
        rows = zlib.decompress(data[41:41 + length])
        self.assertEqual(len(rows), 4 * (1 + 4 * 4))


if __name__ == "__main__":
    unittest.main()
