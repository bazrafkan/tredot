// IMA ADPCM: voice in a quarter of the space, for Bluetooth (too slow for raw PCM).
//
// Every block is independent: 3 bytes of state (predicted sample int16 LE, step index), then
// 4 bits per sample, the first sample in the low nibble. A lost block does not spoil the next ones.
// The decoder is in mac/adpcm.py.
#pragma once
#include <Arduino.h>

const int16_t ADPCM_STEPS[89] = {
  7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66,
  73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408,
  449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
  2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630,
  9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
const int8_t ADPCM_INDEX[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

struct Adpcm {
  int predicted = 0;
  int index = 0;

  uint8_t nibble(int16_t sample) {
    int step = ADPCM_STEPS[index];
    int diff = sample - predicted;
    uint8_t code = 0;
    if (diff < 0) { code = 8; diff = -diff; }
    int delta = step >> 3;
    if (diff >= step) { code |= 4; diff -= step; delta += step; }
    step >>= 1;
    if (diff >= step) { code |= 2; diff -= step; delta += step; }
    step >>= 1;
    if (diff >= step) { code |= 1; delta += step; }
    predicted += (code & 8) ? -delta : delta;
    predicted = constrain(predicted, -32768, 32767);
    index = constrain(index + ADPCM_INDEX[code], 0, 88);
    return code;
  }

  // n samples (even) -> 3 + n/2 bytes in out
  size_t encode(const int16_t *pcm, size_t n, uint8_t *out) {
    out[0] = predicted & 0xFF;
    out[1] = (predicted >> 8) & 0xFF;
    out[2] = index;
    for (size_t i = 0; i + 1 < n; i += 2)
      out[3 + i / 2] = nibble(pcm[i]) | (nibble(pcm[i + 1]) << 4);
    return 3 + n / 2;
  }
};
