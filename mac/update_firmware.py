"""Updates the module's firmware through the bridge, over Wi-Fi or USB cable (not Bluetooth: too slow).

    python update_firmware.py [build/tredot.ino.bin]

The module writes the new firmware to the other OTA slot, checks its hash and restarts. If the
new firmware crashes before talking to the Mac, the module rolls back to the previous one.
Used by ./firmware.sh update.
"""

import glob
import os
import re
import sys
import time

import hook
import module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def firmware_version():
    return (module.call({"op": "status"}).get("module") or {}).get("firmware")


def new_version():
    """The version of the compiled firmware, from tredot.ino (#define FIRMWARE)."""
    try:
        with open(os.path.join(ROOT, "firmware", "tredot", "tredot.ino"), encoding="utf-8") as f:
            match = re.search(r'#define FIRMWARE "([^"]+)"', f.read())
        return match.group(1) if match else ""
    except OSError:
        return ""


def main():
    path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                           os.path.join(ROOT, "build", "tredot.ino.bin"))
    hook.bridge()
    status = module.call({"op": "status"})
    before = firmware_version()
    route = "over the USB cable" if glob.glob("/dev/cu.usbmodem*") else f"via {status.get('link')}"
    print(f"Updating {(status.get('module') or {}).get('id', 'the module')} {route} "
          f"({os.path.getsize(path) // 1024} kB, now firmware {before})…")
    began = time.monotonic()
    version = new_version()
    print(f"At the end, tap \"Yes\" twice on the module to install {version}.")
    error = module.call({"op": "update", "path": path, "version": version}, 900).get("error")
    if error:
        sys.exit(f"Not updated: {error}. The previous firmware is unchanged.")
    print(f"Sent in {time.monotonic() - began:.0f} s. The module is restarting…")
    for _ in range(60):  # until the module comes back with the new firmware
        time.sleep(1)
        status = module.call({"op": "status"})
        version = (status.get("module") or {}).get("firmware")
        if status.get("secure") and version and version != before:
            print(f"Done: firmware {version}, via {status.get('link')}, encrypted.")
            return
    sys.exit("The module has not reconnected yet: check its screen.")


if __name__ == "__main__":
    main()
