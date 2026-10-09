"""A new Tredot version for existing users: the app finds it with "Check for Updates…".

    python release.py "What changed, in a sentence or two"            prepares build/release/<version>/
    python release.py "What changed" --publish                        and publishes it on GitHub (gh)

Before: bump the version in pyproject.toml (and FIRMWARE in tredot.ino if the firmware changed,
compiled with ./firmware.sh: it ships inside the app). Then this script:

  1. builds the app (build_app.py) and the .dmg (build_dmg.py, for first-time installs);
  2. zips the app and signs the zip with our key (Sparkle's sign_update: the private key is in the
     Keychain, account "tredot"; without a valid signature the app refuses the update);
  3. writes appcast.xml, the list the app reads: this version, where to download it, the signature;
  4. with --publish: a GitHub release v<version> with the zip, the .dmg and appcast.xml. The app
     always reads the appcast of the latest release (build_app.FEED), so that is all it takes.

New firmware ships with the app: after the update the menu offers "Update Firmware".
"""

import argparse
import email.utils
import html
import os
import re
import shutil
import subprocess

import build_app
import build_dmg

WORK = os.path.join(build_app.ROOT, "build", "release")


def appcast(version, url, signature, notes):
    """The version list with only the latest one: enough for Sparkle (it compares with its own)."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Tredot</title>
    <item>
      <title>Tredot {version}</title>
      <pubDate>{email.utils.formatdate(usegmt=True)}</pubDate>
      <sparkle:version>{version}</sparkle:version>
      <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>{build_app.INFO["LSMinimumSystemVersion"]}</sparkle:minimumSystemVersion>
      <description><![CDATA[<p>{html.escape(notes)}</p>]]></description>
      <enclosure url="{html.escape(url)}" type="application/octet-stream" {signature}/>
    </item>
  </channel>
</rss>
"""


def prepare(notes, base_url):
    version = build_app.VERSION
    folder = os.path.join(WORK, version)
    if os.path.exists(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    built = os.path.join(folder, "Tredot.app")
    build_app.build(built)
    archive = os.path.join(folder, f"Tredot-{version}.zip")
    # ditto, not zip: keeps the symlinks, permissions and signatures inside the app
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", built, archive], check=True)
    shutil.rmtree(built)
    signed = subprocess.run([os.path.join(build_app.SPARKLE, "bin", "sign_update"), "--account", "tredot", archive],
                            check=True, capture_output=True, text=True).stdout.strip()
    if not re.search(r'sparkle:edSignature="[^"]+" length="\d+"', signed):
        raise RuntimeError("signing failed: " + signed)
    url = f"{base_url}/Tredot-{version}.zip"
    with open(os.path.join(folder, "appcast.xml"), "w", encoding="utf-8") as f:
        f.write(appcast(version, url, signed, notes))
    return version, folder, archive


def main():
    parser = argparse.ArgumentParser(description="Prepare (and optionally publish) a Tredot release.")
    parser.add_argument("notes", help="what changed, in a sentence or two (shown to people updating)")
    parser.add_argument("--publish", action="store_true", help="create the GitHub release (gh)")
    parser.add_argument("--base", help="where the zips will be hosted (default: the GitHub release)")
    args = parser.parse_args()
    version = build_app.VERSION
    base = args.base or f"https://github.com/{build_app.RELEASES}/releases/download/v{version}"
    version, folder, archive = prepare(args.notes, base)
    print(f"  {folder}: signed zip and appcast.xml")
    if args.publish:
        dmg = build_dmg.build()
        subprocess.run(["gh", "release", "create", f"v{version}", "--repo", build_app.RELEASES,
                        "--title", f"Tredot {version}", "--notes", args.notes,
                        archive, dmg, os.path.join(folder, "appcast.xml")], check=True)
        print(f"  published: https://github.com/{build_app.RELEASES}/releases/tag/v{version}")


if __name__ == "__main__":
    main()
