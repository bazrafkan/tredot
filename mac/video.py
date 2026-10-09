"""Video from the module: timestamped JPEG frames + audio.wav -> an .mp4

The bridge runs it when a recording ends: video.py <work folder>. The module sends frames as fast
as it can, not at a fixed rate: each frame lasts until the next one's timestamp, so the video
stays in sync with the audio. The work folder is removed when done. The encoding is done by
tredot-video (macOS AVFoundation), so no ffmpeg is needed.
"""

import os
import shutil
import subprocess
import sys
import wave
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio  # noqa: E402
from config import APP  # noqa: E402
from hook import send, wrap  # noqa: E402
from i18n import t  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def video_tool():
    """tredot-video inside the app (built there by build_app.py), or the one in build/ for development."""
    inside = os.path.join(APP, "Contents", "MacOS", "tredot-video")
    return inside if os.path.exists(inside) else os.path.join(ROOT, "build", "tredot-video")


def show(state, title="", lines=()):
    try:
        send({"op": "state", "state": state, "title": title, "lines": list(lines)}, wait=2)
    except Exception:
        pass


def main(work):
    with open(os.path.join(work, "times.txt")) as f:
        frames = [(name, int(ms)) for name, ms in (line.split() for line in f if line.strip())]
    if len(frames) < 2:
        shutil.rmtree(work)
        show("error", t("Empty video"), wrap(t("no frames received"), 22, 3))
        return

    sound = os.path.join(work, "audio.wav")
    with wave.open(sound) as w:
        has_audio = w.getnframes() > 1600
    cleaned = os.path.join(work, "clean.wav")
    if has_audio:  # same cleanup as voice notes, without shifting the audio against the frames
        audio.clean(sound, cleaned, keep_timing=True)
    name = os.path.basename(work).lstrip(".")
    out = os.path.join(os.path.dirname(work), name + ".mp4")
    # tredot-video (app/Video.swift): hardware H.264, AAC, and the quarter turn for the sideways sensor
    subprocess.run([video_tool(), work, cleaned if has_audio else "-", out], check=True)
    shutil.rmtree(work)
    # no screen on success: the module has already gone back on its own


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as e:
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {sys.argv[1:]}: {e!r}", file=sys.stderr)
        show("error", t("Video failed"), [str(e)[:60]])
