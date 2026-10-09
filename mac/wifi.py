"""Connects the module to a Wi-Fi network: name and password travel encrypted (pairing required).

    python wifi.py            asks for the network (the Mac's by default) and the password
    python wifi.py status     which network the module is on
    python wifi.py forget     the module leaves the Wi-Fi and forgets the network

You type the password here, in the terminal: only dots show (one per character) and it is not
kept on the Mac. The module stores it in its memory. The ESP32-S3 only sees 2.4 GHz networks.
"""

import getpass
import re
import subprocess
import sys
import termios
import time
import tty

import hook
import module


def mac_network():
    """The name of the network the Mac is on, if macOS tells."""
    try:
        out = subprocess.run(["ipconfig", "getsummary", "en0"], capture_output=True, text=True,
                             timeout=5).stdout
        match = re.search(r"^\s*SSID : (.+)$", out, re.M)
        if match and match.group(1).strip() != "<redacted>":
            return match.group(1).strip()
        out = subprocess.run(["networksetup", "-getairportnetwork", "en0"], capture_output=True,
                             text=True, timeout=5).stdout
        match = re.search(r"Network: (.+)$", out.strip())
        return match.group(1).strip() if match else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def masked_input(prompt):
    """Like getpass, but with a dot per character, so you see how much you typed."""
    if not sys.stdin.isatty():
        return getpass.getpass(prompt)
    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    typed = []
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                break
            if ch == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
            if ch in ("\x7f", "\b"):  # backspace
                if typed:
                    typed.pop()
                    sys.stdout.write("\b \b")
            elif ch >= " ":
                typed.append(ch)
                sys.stdout.write("•")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write("\n")
    return "".join(typed)


def module_wifi():
    return (module.call({"op": "status"}).get("module") or {}).get("wifi")


def main():
    hook.bridge()
    command = sys.argv[1] if len(sys.argv) > 1 else "connect"
    status = module.call({"op": "status"})
    if command == "status":
        print(f"Module Wi-Fi: {module_wifi() or 'not set up'}  ·  "
              f"the bridge reaches it via {status.get('link') or 'nothing'}")
        return
    if not status.get("secure"):
        sys.exit("The module must be paired and connected (python pair.py, over the USB cable).")
    if command == "forget":
        ssid, password = "", ""
    elif command == "connect":
        default = mac_network()
        ssid = input(f"Wi-Fi network{f' [{default}]' if default else ''}: ").strip() or default
        if not ssid:
            sys.exit("The network name is required.")
        password = masked_input(f"Password for {ssid}: ")
    else:
        sys.exit(__doc__)
    error = module.call({"op": "wifi", "ssid": ssid, "password": password}).get("error")
    password = None  # no copies lying around
    if error:
        sys.exit(f"Not sent: {error}.")
    if not ssid:
        print("The module forgot the network.")
        return
    print(f"Looking for {ssid}…")
    joined = False
    for _ in range(60):  # the module reports its progress (wifi_link.h)
        time.sleep(0.5)
        state = module_wifi()
        if state == "address" and not joined:
            joined = True
            print("Joined, getting an address…")
        if state and state not in ("connecting", "address"):
            break
    if state and state[0].isdigit():
        print(f"Connected: {state}. The bridge switches to Wi-Fi by itself if you unplug the cable.")
    elif state and state.startswith("error: "):
        sys.exit(f"Not connected: {state[7:]}")
    else:
        sys.exit(f"Not connected ({state or 'no answer'}): check name and password, "
                 "and that the network is 2.4 GHz.")


if __name__ == "__main__":
    main()
