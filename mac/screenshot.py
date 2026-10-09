"""Screenshots of the module's round screen (SS in the protocol, firmware/tredot/mirror.h).

The module sends what the panel shows as RGB565 pixels; here they become a PNG: round (outside the
circle is transparent, as on the real screen) and doubled to 480x480, so it stays sharp on retina
displays and in the README. PNG is written by hand (zlib + struct): no image library needed.
"""

import os
import struct
import zlib
from datetime import datetime

import numpy as np

SCALE = 2


def rgba(raw, width, height):
    """RGB565 little-endian -> RGBA, transparent outside the circle."""
    pixels = np.frombuffer(raw, dtype="<u2").reshape(height, width).astype(np.uint32)
    red = ((pixels >> 11) & 0x1F) * 255 // 31
    green = ((pixels >> 5) & 0x3F) * 255 // 63
    blue = (pixels & 0x1F) * 255 // 31
    y, x = np.mgrid[0:height, 0:width]
    radius = min(width, height) / 2
    inside = (x - (width - 1) / 2) ** 2 + (y - (height - 1) / 2) ** 2 <= radius ** 2
    alpha = np.where(inside, 255, 0)
    return np.dstack([red, green, blue, alpha]).astype(np.uint8)


def png(image):
    """A PNG file (bytes) from an RGBA array."""
    height, width = image.shape[:2]

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    rows = b"".join(b"\x00" + image[y].tobytes() for y in range(height))  # filter 0 on every row
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9))
            + chunk(b"IEND", b""))


def save(raw, width, height, folder):
    """Saves the screenshot in folder -> its path."""
    image = rgba(raw, width, height).repeat(SCALE, axis=0).repeat(SCALE, axis=1)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, datetime.now().strftime("Screen %Y-%m-%d %H%M%S") + ".png")
    with open(path, "wb") as f:
        f.write(png(image))
    return path
