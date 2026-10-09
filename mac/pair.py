"""Pairs the module with this Mac, or removes the pairing (see security.py).

    python pair.py            pair: over the USB cable, then confirm with two taps on "Yes" on the module
    python pair.py unpair     remove the pairing: the module then works over the cable only
    python pair.py status     how the module is connected, and whether the session is encrypted
"""

import subprocess
import sys

import hook
import module


def mac_name():
    try:
        return subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "Mac"


def main():
    hook.bridge()  # starts the bridge if it is not running
    command = sys.argv[1] if len(sys.argv) > 1 else "pair"
    status = module.call({"op": "status"})
    if command == "status":
        info = status.get("module") or {}
        print(f"connected: {status.get('link') or 'no'}  ·  module: {info.get('id', '?')}  ·  "
              f"firmware {info.get('firmware', '?')}  ·  "
              f"{'encrypted' if status.get('secure') else 'not encrypted'}")
    elif command == "unpair":
        if not status.get("secure"):
            sys.exit("The module is not paired with this Mac (or not connected).")
        ok = module.call({"op": "unpair"}, 15).get("ok")
        print("Pairing removed: the module now works over the USB cable only." if ok
              else "The module did not answer.")
    elif command == "pair":
        if status.get("link") != "usb":
            sys.exit("Connect the module to the Mac with the USB cable: pairing only works that way.")
        print("On the module: tap \"Yes\" twice to pair it with this Mac (60 seconds)…")
        reply = module.call({"op": "pair", "name": mac_name()}, 90)
        if reply.get("device"):
            print(f"Paired with {reply['device']}: everything is encrypted from now on, "
                  "and Bluetooth is on.")
        else:
            sys.exit(f"Not paired: {reply.get('error')}.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
