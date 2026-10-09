"""IMA ADPCM: decodes the compressed voice the module sends over Bluetooth (VA lines).

The format is the one in firmware/tredot/adpcm.h: each block starts with 3 bytes of state
(predicted sample as int16 LE, step index), then 4 bits per sample, the first in the low nibble.
"""

import array
import struct

STEPS = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66,
    73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408,
    449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630,
    9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767]
INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


def decode(block):
    """One ADPCM block -> 16-bit little-endian PCM (bytes)."""
    if len(block) < 3:
        return b""
    predicted = struct.unpack_from("<h", block)[0]
    index = min(block[2], 88)
    out = array.array("h")
    for byte in block[3:]:
        for code in (byte & 0x0F, byte >> 4):
            step = STEPS[index]
            delta = step >> 3
            if code & 4:
                delta += step
            if code & 2:
                delta += step >> 1
            if code & 1:
                delta += step >> 2
            predicted += -delta if code & 8 else delta
            predicted = max(-32768, min(32767, predicted))
            index = max(0, min(88, index + INDEX[code]))
            out.append(predicted)
    return out.tobytes()


def encode(pcm, predicted=0, index=0):
    """16-bit PCM -> one ADPCM block, like the firmware does: used by the tests (fake module)."""
    samples = array.array("h", pcm)
    out = bytearray(struct.pack("<hB", predicted, index))
    codes = []
    for sample in samples:
        step = STEPS[index]
        diff, code = sample - predicted, 0
        if diff < 0:
            code, diff = 8, -diff
        delta = step >> 3
        if diff >= step:
            code |= 4
            diff -= step
            delta += step
        if diff >= step >> 1:
            code |= 2
            diff -= step >> 1
            delta += step >> 1
        if diff >= step >> 2:
            code |= 1
            delta += step >> 2
        predicted += -delta if code & 8 else delta
        predicted = max(-32768, min(32767, predicted))
        index = max(0, min(88, index + INDEX[code]))
        codes.append(code)
    for i in range(0, len(codes) - 1, 2):
        out.append(codes[i] | codes[i + 1] << 4)
    return bytes(out)
