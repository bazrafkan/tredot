"""Cleans up the module's voice before Whisper and in videos, without needing ffmpeg.

The Sense microphone is quiet (level 100-400 instead of a few thousand) and picks up rumble.
The same three steps as ffmpeg's atrim (drop the start-up click), highpass at 80 Hz and dynaudnorm
(adaptive volume), done with numpy on the whole file at once: 30 minutes in a few seconds.
"""

import wave

import numpy as np

SKIP_START = 0.3  # seconds: the PDM microphone clicks when it starts
WINDOW = 0.5  # seconds: the gain is set per window, then smoothed
TARGET = 4000  # voice level (rms) to reach: about -18 dB
MAX_GAIN = 30  # at most x30 (~30 dB): silence must not turn into hiss
QUIET = 60  # below this level a window is silence, and is not raised up to TARGET


def read(path):
    with wave.open(path) as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: needs 16-bit mono audio")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32), w.getframerate()


def write(path, samples, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(samples, -32767, 32767).astype("<i2").tobytes())


FRAME = 4096  # samples per frame: at 16 kHz, one frequency bin every ~4 Hz
HOP = FRAME // 2


def high_pass(x, rate, cutoff=80):
    """Removes rumble below ~80 Hz: overlapping frames, filtered in the frequency domain.

    Each frame is windowed (square-root Hann on the way in and out, so the overlapping frames sum
    back to the signal); frequencies below 60 Hz are zeroed, with a ramp up to 100 Hz. Measured at
    30 Hz: moving averages removed only ~8 dB, ffmpeg ~17; this removes nearly all of it.
    """
    if len(x) < FRAME:
        return x - np.mean(x)
    freqs = np.fft.rfftfreq(FRAME, 1 / rate)
    mask = np.clip((freqs - (cutoff - 20)) / 40, 0, 1)  # 0 below 60 Hz, 1 above 100 Hz
    window = np.sqrt(np.hanning(FRAME + 1)[:FRAME]).astype(np.float32)
    padded = np.concatenate([np.zeros(HOP, np.float32), x, np.zeros(FRAME, np.float32)])
    out = np.zeros_like(padded)
    for start in range(0, len(padded) - FRAME + 1, HOP):  # 30 minutes: ~14,000 frames, ~1 s
        piece = np.fft.irfft(np.fft.rfft(padded[start:start + FRAME] * window) * mask, FRAME)
        out[start:start + FRAME] += piece * window
    return out[HOP:HOP + len(x)]


def level(x, rate):
    """Adaptive volume, like dynaudnorm: one gain per window, smoothed and interpolated."""
    win = int(rate * WINDOW)
    if len(x) < win:
        return x
    count = len(x) // win
    rms = np.sqrt(np.mean(x[:count * win].reshape(count, win) ** 2, axis=1))
    gains = np.where(rms > QUIET, TARGET / np.maximum(rms, 1), TARGET / QUIET)
    gains = np.clip(gains, 1, MAX_GAIN)
    if count >= 3:  # no jumps between windows ("same" with fewer than 3 would change the length)
        gains = np.convolve(gains, np.ones(3) / 3, mode="same")
    centers = np.arange(count) * win + win / 2
    curve = np.interp(np.arange(len(x)), centers, gains)
    y = x * curve
    peak = np.max(np.abs(y)) if len(y) else 0
    return y * (30000 / peak) if peak > 30000 else y  # no clipping on peaks


def clean(src, dst, keep_timing=False):
    """From a module recording to audio ready for Whisper (16 kHz mono, as it arrives).

    keep_timing: the start-up click is silenced instead of cut, so in videos the audio stays in
    sync with the frames (cutting it made the audio run 0.3 s early).
    """
    x, rate = read(src)
    skip = int(SKIP_START * rate)
    if keep_timing:
        x = x.copy()
        x[:skip] = 0
    else:
        x = x[skip:]
    write(dst, level(high_pass(x, rate), rate), rate)
