"""Builds "Tredot.app": the native (Swift) menu-bar app that keeps the bridge running.

The app (app/Tredot.swift) shows the module's state and runs the actions (pair, Wi-Fi, update);
the Python bridge, which the app starts as a child process, does the work. An app is also needed
for permissions: macOS grants Bluetooth and local network access to the "responsible" app of a
process, and kills processes whose app does not explain why it uses them. Started by a hook, the
bridge would have `claude` as its responsible process, which does not explain it; as a child of
Tredot it uses the usage descriptions below.

    python build_app.py      builds (or rebuilds) the app where it is installed, else in ~/Applications
                             (needs swiftc, uv, and the first time cmake and git, for Whisper)

The app is self-contained: copied to another folder or another Mac it still works. Inside:

    Contents/MacOS/      Tredot (the app), whisper-cli, tredot-video (videos), tredot-speech (macOS
                         speech recognition, until the Whisper model is downloaded)
    Contents/Resources/  python/ (a complete Python with the bridge's libraries), bridge/ (the code
                         in mac/), firmware/ (the latest build, for "Update Firmware"),
                         tredot.mcpb (the Claude Desktop extension, integrations.py)

The Python comes from uv (python-build-standalone): made to be relocated, unlike the system one or
Anaconda's. Its .pyc files go to ~/Library/Caches (Tredot.swift): writing inside the app would break
the signature. Whisper is built from the official sources into a single program with no libraries
to install (Homebrew splits it into pieces it looks for in /opt/homebrew). No paths of the build Mac
end up in the app: folders and model come from config.py at launch.

After changing the code in mac/ or the firmware, run build_app.py again.
"""

import glob
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import tomllib

import build_extension
from config import APP, MODEL_SHA256, MODEL_SIZE, MODEL_URL

# where it is already installed (/Applications, from the .dmg) or ~/Applications: one copy per Mac
TARGET = APP
# the app's version (and the .dmg's, build_dmg.py): the one in pyproject.toml (TREDOT_VERSION: a
# fake newer version, to test updates)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproject.toml"), "rb") as f:
    VERSION = os.environ.get("TREDOT_VERSION") or tomllib.load(f)["project"]["version"]

# App updates (Sparkle, MIT license): the app reads the version list (appcast) from the latest
# GitHub release, downloads the new version and installs it only if it is signed with our key
# (EdDSA: the private key is in the Keychain, account "tredot"; release.py signs). TREDOT_FEED:
# another address, for testing.
RELEASES = "bazrafkan/tredot"  # the GitHub repository whose releases carry the updates
FEED = os.environ.get("TREDOT_FEED") or f"https://github.com/{RELEASES}/releases/latest/download/appcast.xml"
UPDATE_KEY = "3ezHRhj7HRqGPx4PcqVmH0CVWwRY7JNdGUo1sjTSKtI="  # the public key
SPARKLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "sparkle")
SPARKLE_URL = "https://github.com/sparkle-project/Sparkle/releases/download/2.10.0/Sparkle-2.10.0.tar.xz"
PYTHON_VERSION = "3.12"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCE = os.path.join(HERE, "app", "Tredot.swift")
VIDEO = os.path.join(HERE, "app", "Video.swift")  # tredot-video: assembles videos (video.py)
SPEECH = os.path.join(HERE, "app", "Speech.swift")  # tredot-speech: macOS speech recognition (notes.py)
ICON = os.path.join(HERE, "app", "Icon.swift")  # the Tredot icon (three dots on an arc)
# the app's texts per language (Localizable.strings, InfoPlist.strings): app/<lang>.lproj
LOCALIZATIONS = sorted(glob.glob(os.path.join(HERE, "app", "*.lproj")))

# Whisper: pinned version, MIT license. Built once in build/ and copied into the app
WHISPER_TAG = "v1.9.5"
WHISPER_REPO = "https://github.com/ggml-org/whisper.cpp"
WHISPER_SRC = os.path.join(ROOT, "build", "whisper.cpp")
WHISPER_BIN = os.path.join(WHISPER_SRC, "out", "bin", "whisper-cli")

INFO = {
    "CFBundleIdentifier": "ai.tredot.mac",
    "CFBundleName": "Tredot",
    "CFBundleDisplayName": "Tredot",
    "CFBundleDevelopmentRegion": "en",
    "CFBundleLocalizations": ["en", "it"],
    "CFBundleExecutable": "Tredot",
    "CFBundleIconFile": "AppIcon",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": VERSION,
    "CFBundleVersion": VERSION,
    "LSMinimumSystemVersion": "13.0",
    "LSUIElement": True,  # no Dock icon
    "NSBluetoothAlwaysUsageDescription":
        "To talk to the Tredot module without a cable, over Bluetooth.",
    "NSLocalNetworkUsageDescription":
        "To talk to the Tredot module over your home or office Wi-Fi network.",
    "NSBonjourServices": ["_tredot._tcp"],
    "NSSpeechRecognitionUsageDescription":
        "To transcribe the module's voice notes on this Mac until the Whisper model is downloaded.",
    # the Whisper model: the app downloads it and checks its SHA-256
    "TredotModelURL": MODEL_URL,
    "TredotModelSHA256": MODEL_SHA256,
    "TredotModelSize": MODEL_SIZE,
    # Sparkle: which key to verify with, and how often to check (daily); SUFeedURL is added in build()
    "SUPublicEDKey": UPDATE_KEY,
    "SUEnableAutomaticChecks": True,
    "SUScheduledCheckInterval": 86400,
}


def sparkle():
    """Sparkle (pinned version) in build/sparkle: the framework and its tools (sign_update)."""
    if not os.path.exists(os.path.join(SPARKLE, "Sparkle.framework")):
        os.makedirs(SPARKLE, exist_ok=True)
        archive = os.path.join(SPARKLE, "sparkle.tar.xz")
        subprocess.run(["curl", "-fsSL", "-o", archive, SPARKLE_URL], check=True)
        subprocess.run(["tar", "xf", archive, "-C", SPARKLE], check=True)
        os.remove(archive)
    return SPARKLE


def whisper():
    """whisper-cli as a single program: Metal with embedded shaders, no libraries outside macOS.
    Apple Silicon, macOS 13 or later."""
    if os.path.exists(WHISPER_BIN):
        return WHISPER_BIN
    if not os.path.exists(WHISPER_SRC):
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", WHISPER_TAG, WHISPER_REPO,
                        WHISPER_SRC], check=True)
    out = os.path.join(WHISPER_SRC, "out")
    subprocess.run(["cmake", "-S", WHISPER_SRC, "-B", out, "-DCMAKE_BUILD_TYPE=Release",
                    "-DBUILD_SHARED_LIBS=OFF", "-DGGML_METAL=ON", "-DGGML_METAL_EMBED_LIBRARY=ON",
                    "-DGGML_NATIVE=OFF", "-DGGML_BLAS=ON", "-DWHISPER_BUILD_TESTS=OFF",
                    "-DWHISPER_BUILD_SERVER=OFF", "-DWHISPER_SDL2=OFF",
                    "-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0", "-DCMAKE_OSX_ARCHITECTURES=arm64"],
                   check=True, capture_output=True)
    subprocess.run(["cmake", "--build", out, "-j", str(os.cpu_count() or 4), "--target", "whisper-cli"],
                   check=True, capture_output=True)
    return WHISPER_BIN


# What the bridge does not use is removed from the copied Python (tests, Tk GUI, C headers):
# from ~66 MB to ~35
PRUNE = ["include", "share", "lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*", "lib/libtcl*",
         "lib/libtk*", "lib/python*/test", "lib/python*/idlelib", "lib/python*/tkinter",
         "lib/python*/turtledemo", "lib/python*/ensurepip", "lib/python*/lib2to3",
         "lib/python*/config-*", "lib/python*/lib-dynload/_tkinter*", "lib/python*/EXTERNALLY-MANAGED",
         "bin/pip*", "bin/idle*"]
# and from the packages: the copy of Claude Code inside claude-agent-sdk (224 MB; agent.py uses
# the installed one), PyObjC's tests, and pip (removed after it has installed the rest)
PRUNE_PACKAGES = ["claude_agent_sdk/_bundled", "PyObjCTest", "pip"]


def python(resources):
    """Python and the bridge's libraries (the versions in uv.lock) in Contents/Resources/python."""
    subprocess.run(["uv", "python", "install", PYTHON_VERSION], check=True, capture_output=True)
    found = subprocess.run(["uv", "python", "find", "--managed-python", PYTHON_VERSION],
                           check=True, capture_output=True, text=True).stdout.strip()
    # base_prefix, not prefix: inside the project uv may find the project's virtual environment,
    # whose bin/python is only a link to the installation on this Mac
    prefix = subprocess.run([found, "-c", "import sys; print(sys.base_prefix)"],
                            check=True, capture_output=True, text=True).stdout.strip()
    home = os.path.join(resources, "python")
    shutil.copytree(prefix, home, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    self_contained(home)
    for pattern in PRUNE:
        for path in glob.glob(os.path.join(home, pattern)):
            shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.remove(path)
    exe = os.path.join(home, "bin", "python" + PYTHON_VERSION)
    with tempfile.NamedTemporaryFile("w", suffix=".txt") as req:
        subprocess.run(["uv", "export", "--project", HERE, "--frozen", "--no-dev", "--no-hashes",
                        "--no-emit-project", "--no-header", "--quiet", "-o", req.name], check=True)
        subprocess.run(["uv", "pip", "install", "--quiet", "--python", exe, "--break-system-packages",
                        "-r", req.name], check=True)
    site = glob.glob(os.path.join(home, "lib", "python*", "site-packages"))[0]
    for name in PRUNE_PACKAGES:
        shutil.rmtree(os.path.join(site, name), ignore_errors=True)
    # the packages' command-line scripts (pyserial-ports, f2py...): they embed this Mac's paths,
    # and the bridge does not use them
    for path in glob.glob(os.path.join(home, "bin", "*")):
        if not os.path.basename(path).startswith("python"):
            os.remove(path)
    return exe


def self_contained(folder):
    """Stop the build if a link inside the bundled Python points outside it: the app would work on
    this Mac and fail on every other one."""
    for root, dirs, files in os.walk(folder):
        for name in dirs + files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                target = os.path.realpath(path)
                if not target.startswith(os.path.realpath(folder) + os.sep):
                    raise RuntimeError(f"{path} points outside the app: {target}")


def icon(resources):
    """AppIcon.icns: Icon.swift draws the PNGs at every size, iconutil packs them."""
    work = os.path.join(ROOT, "build", "icon")
    if os.path.exists(work):
        shutil.rmtree(work)
    iconset = os.path.join(work, "AppIcon.iconset")
    tool = os.path.join(work, "icon")
    os.makedirs(work)
    subprocess.run(["swiftc", "-O", "-o", tool, ICON], check=True, capture_output=True)
    subprocess.run([tool, iconset], check=True)
    output = os.path.join(resources, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", output], check=True)
    return output


def firmware(resources):
    """The latest compiled firmware (./firmware.sh) and its version, for "Update Firmware"."""
    binary = os.path.join(ROOT, "build", "tredot.ino.bin")
    if not os.path.exists(binary):
        return None
    with open(os.path.join(ROOT, "firmware", "tredot", "tredot.ino"), encoding="utf-8") as f:
        version = re.search(r'#define FIRMWARE "([^"]+)"', f.read()).group(1)
    folder = os.path.join(resources, "firmware")
    os.makedirs(folder)
    shutil.copy2(binary, os.path.join(folder, "tredot.ino.bin"))
    with open(os.path.join(folder, "version"), "w") as f:
        f.write(version + "\n")
    return version


def build(app=TARGET):
    contents = os.path.join(app, "Contents")
    resources = os.path.join(contents, "Resources")
    if os.path.exists(app):
        shutil.rmtree(app)
    os.makedirs(os.path.join(contents, "MacOS"))
    os.makedirs(resources)
    frameworks = os.path.join(contents, "Frameworks")
    shutil.copytree(os.path.join(sparkle(), "Sparkle.framework"), os.path.join(frameworks, "Sparkle.framework"),
                    symlinks=True)
    subprocess.run(["swiftc", "-O", "-F", SPARKLE, "-framework", "Sparkle",
                    "-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks",
                    "-o", os.path.join(contents, "MacOS", "Tredot"), SOURCE], check=True)
    subprocess.run(["swiftc", "-O", "-o", os.path.join(contents, "MacOS", "tredot-video"), VIDEO],
                   check=True)
    subprocess.run(["swiftc", "-O", "-o", os.path.join(contents, "MacOS", "tredot-speech"), SPEECH],
                   check=True)
    shutil.copy2(whisper(), os.path.join(contents, "MacOS", "whisper-cli"))
    python(resources)
    bridge = os.path.join(resources, "bridge")
    os.makedirs(bridge)
    for source in glob.glob(os.path.join(HERE, "*.py")):
        if os.path.basename(source) != "build_app.py":
            shutil.copy2(source, bridge)
    shutil.copytree(os.path.join(HERE, "vendor"), os.path.join(bridge, "vendor"))  # force-graph (MIT)
    with open(os.path.join(bridge, "VERSION"), "w") as f:  # bridge.VERSION
        f.write(VERSION + "\n")
    version = firmware(resources)
    icon(resources)
    for folder in LOCALIZATIONS:
        shutil.copytree(folder, os.path.join(resources, os.path.basename(folder)))
    build_extension.build(os.path.join(resources, "tredot.mcpb"))
    with open(os.path.join(contents, "Info.plist"), "wb") as f:
        plistlib.dump(dict(INFO, CFBundleShortVersionString=VERSION, CFBundleVersion=VERSION,
                           SUFeedURL=FEED), f)
    # ad-hoc signature: without one macOS does not remember the Bluetooth and local network
    # permissions. Sparkle first, with its helpers inside (Autoupdate, Updater.app, XPC services)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", os.path.join(frameworks, "Sparkle.framework")],
                   check=True, capture_output=True)
    subprocess.run(["codesign", "--force", "--sign", "-", app], check=True, capture_output=True)
    size = subprocess.run(["du", "-sh", app], capture_output=True, text=True).stdout.split()[0]
    print(f"  {app} ({size}, firmware {version or 'not built'})")


if __name__ == "__main__":
    build()
