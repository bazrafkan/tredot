"""The Tredot .dmg: build/Tredot <version>.dmg.

    python build_dmg.py

Inside: the app (built by build_app.py in a separate folder, not the installed one) and an
Applications link to drag it onto; the disk has the app's icon (the three dots). Made with
hdiutil, present on every Mac: first a writable disk (for the icon: .VolumeIcon.icns and SetFile's
"custom icon" flag), then compressed as ULFO (lzfse), which opens on macOS 10.11 and later.

Without signing and notarization, macOS opens a .dmg downloaded from the internet only after
System Settings > Privacy & Security > "Open Anyway".
"""

import os
import shutil
import subprocess

import build_app

ROOT = os.path.dirname(build_app.HERE)
WORK = os.path.join(ROOT, "build", "dmg")
NAME = "Tredot"


def build():
    staging = os.path.join(WORK, NAME)
    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    os.makedirs(staging)
    build_app.build(os.path.join(staging, NAME + ".app"))
    os.symlink("/Applications", os.path.join(staging, "Applications"))
    output = os.path.join(ROOT, "build", f"{NAME} {build_app.VERSION}.dmg")
    if os.path.exists(output):
        os.remove(output)
    shutil.copy2(os.path.join(staging, NAME + ".app", "Contents", "Resources", "AppIcon.icns"),
                 os.path.join(staging, ".VolumeIcon.icns"))
    raw = os.path.join(WORK, "writable.dmg")
    subprocess.run(["hdiutil", "create", "-quiet", "-volname", NAME, "-srcfolder", staging,
                    "-fs", "HFS+", "-format", "UDRW", raw], check=True)
    mount = subprocess.run(["hdiutil", "attach", "-nobrowse", "-noautoopen", raw],
                           check=True, capture_output=True, text=True).stdout.strip().split("\t")[-1]
    try:
        subprocess.run(["SetFile", "-a", "C", mount], check=True)
    finally:
        subprocess.run(["hdiutil", "detach", "-quiet", mount], check=True)
    subprocess.run(["hdiutil", "convert", "-quiet", raw, "-format", "ULFO", "-o", output], check=True)
    subprocess.run(["hdiutil", "verify", "-quiet", output], check=True)
    shutil.rmtree(WORK)
    size = os.path.getsize(output) / 1e6
    print(f"  {output} ({size:.0f} MB)")
    return output


if __name__ == "__main__":
    build()
