# Releasing

Users get new versions from **Check for Updates…** in the menu, or from the daily automatic check.
Updates use [Sparkle](https://sparkle-project.org) (MIT). The app reads an appcast from the latest
GitHub release, downloads the new version, checks its EdDSA signature, replaces itself and restarts.
New firmware ships inside the app. After an app update, the menu offers to update the module.

## Versions

| What | Where | When to bump |
|---|---|---|
| App (and `.dmg`, appcast, tag `v<version>`) | `version` in `mac/pyproject.toml` | every release |
| Firmware | `#define FIRMWARE` in `firmware/tredot/tredot.ino` | every firmware change |
| Protocol | `#define PROTOCOL` in `tredot.ino` and `PROTOCOL` in `mac/bridge.py` | when a line or field changes in a way the other side wouldn't understand |
| Plugin and extension | `VERSION` in `mac/build_plugin.py` | when `hook.py`, `mcp_server.py`, `module.py`, `config.py` or the hooks change. At launch, the app reinstalls the plugin when this changes |

The app version is written into the bundle and reported by the bridge. When the app finds a bridge
of another version still running, it replaces it.

## Steps

1. Bump the app version in `mac/pyproject.toml`.
2. If the firmware changed, bump `FIRMWARE`, compile with `./firmware.sh` and test it on a module
   (`./firmware.sh update`, or `./firmware.sh flash` over the cable). `build_app.py` bundles
   whatever is in `build/tredot.ino.bin`, so compile before releasing.
3. If the plugin scripts changed, bump `build_plugin.VERSION` and run `python build_plugin.py`.
4. Run the tests: `cd mac && .venv/bin/python -m unittest discover -s tests`.
5. Build, sign and publish:

   ```bash
   cd mac
   .venv/bin/python release.py "What changed, in one or two sentences" --publish
   ```

   Without `--publish`, `release.py` only prepares the release folder under `build/`, so you can
   inspect it first.

What `release.py` does:

1. Builds `Tredot.app` (`build_app.py`) into a release folder.
2. Zips it with `ditto`, which keeps symlinks, permissions and signatures, and signs the zip with
   Sparkle's `sign_update --account tredot`.
3. Writes `appcast.xml` with only this version, its download URL, its signature and the notes.
   Sparkle compares it with the installed version.
4. With `--publish`, it builds the `.dmg` (`build_dmg.py`) and runs `gh release create
   v<version>` on `bazrafkan/tredot` (`build_app.RELEASES`). The release gets the zip, the `.dmg`
   and `appcast.xml`. This requires the GitHub CLI (`gh`), signed in.

## The feed

The app's feed URL (`SUFeedURL`) is

```
https://github.com/bazrafkan/tredot/releases/latest/download/appcast.xml
```

GitHub redirects `latest` to the newest release that is not a draft or a pre-release. Two
consequences:

- Every release must include `appcast.xml`. A release without it breaks update checks for everyone.
- A draft or a pre-release is not offered to users, which is a safe way to stage a release.

## The signing key

Updates are signed with an EdDSA (Ed25519) key:

- The **public** key is `UPDATE_KEY` in `mac/build_app.py`, stored in the app's `Info.plist` as
  `SUPublicEDKey`.
- The **private** key is in the login Keychain under the account **`tredot`**. Sparkle's tools are
  downloaded to `build/sparkle/` by `build_app.py`.

**Back it up.** If the key is lost, every installed copy of Tredot rejects future updates, and users
have to download the `.dmg` by hand. Export it and keep the file in a password manager:

```bash
build/sparkle/bin/generate_keys --account tredot -x tredot-sparkle-key.txt   # export
build/sparkle/bin/generate_keys --account tredot -f tredot-sparkle-key.txt   # import on another Mac
```

Never commit the exported file. Anyone who has it can sign updates that installed apps will accept.

## The .dmg

`build_dmg.py` builds `build/Tredot <version>.dmg`. It contains the app, built in a staging folder
rather than the installed copy, and a link to Applications. The disk uses the app icon. The image is
made with `hdiutil` as a writable disk (for the volume icon), then converted to ULFO (lzfse) and
verified. The `.dmg` is for first installs. Updates go through Sparkle and the zip.

## Testing updates with a local feed

Two environment variables, both read by `build_app.py`, let you test the whole update path without
publishing:

- `TREDOT_FEED`: the feed URL built into the app, in place of GitHub
- `TREDOT_VERSION`: overrides the version from `pyproject.toml`, so you can fake a newer release

```bash
cd mac
# 1. install an app that watches a local feed
TREDOT_FEED=http://127.0.0.1:8000/appcast.xml .venv/bin/python build_app.py

# 2. prepare a "newer" release whose zip is served from the same place (no --publish)
TREDOT_FEED=http://127.0.0.1:8000/appcast.xml TREDOT_VERSION=0.99.0 \
  .venv/bin/python release.py "Test update" --base http://127.0.0.1:8000

# 3. serve the release folder that release.py printed
python3 -m http.server 8000 --directory <release folder>
```

Then choose **Check for Updates…** in the menu. If the test release bundles new firmware, test the
menu's firmware update afterwards. Rebuild the app without the variables when you're done.

## Current limits

- **No Developer ID.** The app is signed ad hoc and not notarized:
  - On first launch, users must allow it in **System Settings > Privacy & Security > Open Anyway**.
  - Each update changes the code identity, so macOS asks again for Bluetooth, Local Network and
    Speech Recognition.

  Developer ID signing and notarization fix both. See the [roadmap](ROADMAP.md).
- **Apple Silicon and macOS 13+ only:** Whisper and the bundled Python are built for arm64.
- **No firmware signature.** Firmware authenticity relies on the encrypted, paired session and the
  SHA-256 check. The image itself isn't signed.
- **Firmware version check.** The menu offers a firmware update whenever the bundled version
  differs from the module's, which includes older versions.
