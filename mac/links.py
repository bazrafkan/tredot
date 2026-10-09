"""The links to the module ("transports"): USB cable, Bluetooth LE and Wi-Fi.

They all have the same shape, so the bridge does not care which one it is using:
  find()     -> a transport ready to open, or None if the module is not reachable this way
  open()     -> True if connected
  read()     -> the bytes received (b"" if nothing arrives shortly); LinkLost if the link dropped
  write(b)   -> LinkLost if the link dropped
  close()
  name       "usb", "ble", "wifi": seen by the rest of the bridge (logs, status)
"""

import asyncio
import glob
import json
import logging
import os
import select
import socket
import threading
import time

import serial

from config import CACHE, CONFIG

BAUD = 115200
# firmware.sh creates it while flashing: the bridge must not grab the USB port (hooks keep
# restarting it). Older than 5 minutes means a flash that went wrong.
FLASHING = os.path.join(CACHE, "flashing")


def flashing():
    try:
        return time.time() - os.path.getmtime(FLASHING) < 300
    except OSError:
        return False


class LinkLost(Exception):
    """The link dropped: the bridge closes it and looks for another."""


class SerialLink:
    name = "usb"

    def __init__(self, path):
        self.path = path
        self.port = None

    @staticmethod
    def find():
        if flashing():
            return None
        wanted = os.environ.get("TREDOT_PORT")
        if wanted:
            return SerialLink(wanted)
        ports = sorted(glob.glob("/dev/cu.usbmodem*"))
        return SerialLink(ports[0]) if ports else None

    def open(self):
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = self.path, BAUD, 0.2
        # DTR/RTS low: some combinations reset the ESP32-S3
        s.dtr = False
        s.rts = False
        try:
            s.open()
        except serial.SerialException as e:
            logging.info("opening %s failed: %s", self.path, e)
            return False
        # drop what the module wrote while nobody was listening (old H greetings): read now, each
        # greeting would restart the session and leave the bridge on a different key than the
        # module (seen: every answer rejected, firmware updates never answered)
        s.reset_input_buffer()
        self.port = s
        return True

    def read(self):
        try:
            # everything available (photos and video arrive in hundreds of kB), or wait for 1 byte
            return self.port.read(max(1, self.port.in_waiting))
        except (serial.SerialException, OSError) as e:
            raise LinkLost(str(e)) from e

    def write(self, data):
        try:
            self.port.write(data)
        except (serial.SerialException, OSError) as e:
            raise LinkLost(str(e)) from e

    def close(self):
        try:
            self.port.close()
        except Exception:
            pass

    def __str__(self):
        return self.path


# ---------- Bluetooth ----------

# Same as firmware/tredot/ble.h
BLE_SERVICE = "6f1c0001-3b5e-4c47-9a21-7d2f5e8a4c10"
BLE_RX = "6f1c0002-3b5e-4c47-9a21-7d2f5e8a4c10"  # Mac -> module
BLE_TX = "6f1c0003-3b5e-4c47-9a21-7d2f5e8a4c10"  # module -> Mac

_loop = None


def ble_loop():
    """bleak needs asyncio: its own event loop, in its own thread, for the life of the bridge."""
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, name="ble", daemon=True).start()
    return _loop


def ble_run(coro, timeout):
    return asyncio.run_coroutine_threadsafe(coro, ble_loop()).result(timeout)


class BleLink:
    name = "ble"

    def __init__(self, device):
        self.device = device
        self.client = None
        self.rx = bytearray()
        self.arrived = threading.Condition()
        self.lost = False

    @staticmethod
    def find():
        from bleak import BleakScanner  # imported here so tests and the cable never load it
        try:
            device = ble_run(BleakScanner.find_device_by_filter(
                lambda d, adv: BLE_SERVICE in adv.service_uuids, timeout=4), 10)
        except Exception as e:  # Bluetooth off, or permission denied
            logging.info("Bluetooth scan failed: %s", e)
            return None
        return BleLink(device) if device else None

    def open(self):
        from bleak import BleakClient

        async def connect():
            client = BleakClient(self.device, disconnected_callback=lambda _: self.drop())
            await client.connect(timeout=10)
            await client.start_notify(BLE_TX, self.received)
            return client
        try:
            self.client = ble_run(connect(), 20)
        except Exception as e:
            logging.info("Bluetooth connection failed: %s", e)
            return False
        logging.info("Bluetooth: %d-byte packets", self.client.mtu_size - 3)
        return True

    def received(self, _, data):
        with self.arrived:
            self.rx += data
            self.arrived.notify()

    def drop(self):
        with self.arrived:
            self.lost = True
            self.arrived.notify()

    def read(self):
        with self.arrived:
            if not self.rx and not self.lost:
                self.arrived.wait(0.2)
            if self.lost:
                raise LinkLost("Bluetooth disconnected")
            data, self.rx = bytes(self.rx), bytearray()
            return data

    def write(self, data):
        if self.lost:
            raise LinkLost("Bluetooth disconnected")
        size = self.client.mtu_size - 3

        async def send():
            # with response: without it, on macOS a write sometimes never returns (seen: the
            # bridge stalled for 30 s). Few lines go from the Mac to the module, so it is cheap.
            for i in range(0, len(data), size):
                await self.client.write_gatt_char(BLE_RX, data[i:i + size], response=True)
        began = time.monotonic()
        try:
            ble_run(send(), 10)
        except Exception as e:
            logging.info("Bluetooth write failed after %.1f s (%d bytes): %r",
                         time.monotonic() - began, len(data), e)
            self.drop()
            raise LinkLost(repr(e)) from e

    def close(self):
        if self.client:
            try:
                ble_run(self.client.disconnect(), 5)
            except Exception:
                pass

    def __str__(self):
        return f"{self.device.name} {self.device.address}"


# ---------- Wi-Fi ----------

SERVICE = "_tredot._tcp.local."  # as announced by the firmware (wifi_link.h)
PORT = 7777
# The last address of each module: an mDNS lookup takes 0.3 to 6 s, so at start-up that address
# is tried first. If it changed, the attempt fails within 2 s and the search goes on.
KNOWN = os.path.join(CACHE, "addresses.json")
# An address that did not answer is not retried for a minute: each attempt blocks the bridge for
# 2-5 s, and while on the cable Wi-Fi is retried every 10 s (bridge.Device.upgrade).
_failed = {}  # address -> when it failed


def resting(address):
    return time.monotonic() - _failed.get(address, -999) < 60


def remember(device, address):
    try:
        with open(KNOWN) as f:
            known = json.load(f)
    except (OSError, ValueError):
        known = {}
    if known.get(device) != address:
        known[device] = address
        with open(KNOWN, "w") as f:
            json.dump(known, f)


def remembered():
    try:
        with open(KNOWN) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Finder:
    """Browses for modules on the network (mDNS) in the background for the life of the bridge,
    so the address is ready when needed."""

    def __init__(self):
        from zeroconf import ServiceBrowser, Zeroconf  # imported here so tests and the cable never load it
        self.found = {}  # module id -> (address, port, service name)
        self.zc = Zeroconf()
        self.browser = ServiceBrowser(self.zc, SERVICE, handlers=[self.changed])

    def changed(self, zeroconf, service_type, name, state_change):
        from zeroconf import ServiceStateChange
        if state_change == ServiceStateChange.Removed:
            self.found = {k: v for k, v in self.found.items() if v[2] != name}
            return
        info = zeroconf.get_service_info(service_type, name, timeout=3000)
        if not info or not info.parsed_addresses():
            return
        device = (info.properties.get(b"id") or b"").decode()
        self.found[device] = (info.parsed_addresses()[0], info.port, name)
        remember(device, info.parsed_addresses()[0])
        logging.info("on the network: %s at %s:%d", device, info.parsed_addresses()[0], info.port)


_finder = None


def finder():
    global _finder
    if _finder is None:
        _finder = Finder()
    return _finder


class TcpLink:
    name = "wifi"

    def __init__(self, address, port, quick=False):
        self.address, self.port = address, port
        self.sock = None
        self.quick = quick  # remembered address, not seen now: try it only briefly

    @staticmethod
    def find():
        wanted = os.environ.get("TREDOT_WIFI")  # host:port, for tests
        if wanted:
            host, port = wanted.rsplit(":", 1)
            return TcpLink(host, int(port))
        from security import key_for
        try:
            found = list(finder().found.items())
        except Exception as e:  # no network or permission denied: try the other transports
            logging.info("network search failed: %s", e)
            return None
        for device, (address, port, _) in found:
            if key_for(device) and not resting(address):  # only a module paired with this Mac
                return TcpLink(address, port)
        for device, address in remembered().items():  # not found by the search yet
            if key_for(device) and not resting(address):
                return TcpLink(address, PORT, quick=True)
        return None

    def open(self):
        try:
            self.sock = socket.create_connection((self.address, self.port), timeout=2 if self.quick else 5)
        except OSError as e:
            logging.info("Wi-Fi connection to %s failed: %s", self, e)
            _failed[self.address] = time.monotonic()
            return False
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # send small lines at once
        # Module restarted or gone from the network: without keepalive this is noticed only on
        # the next write (measured: a minute). With it, in ~10 s.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for option, value in (("TCP_KEEPALIVE", 4), ("TCP_KEEPINTVL", 2), ("TCP_KEEPCNT", 3)):
            if hasattr(socket, option):
                self.sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, option), value)
        self.sock.setblocking(True)
        return True

    def read(self):
        try:
            if not select.select([self.sock], [], [], 0.2)[0]:
                return b""
            data = self.sock.recv(65536)
        except OSError as e:
            raise LinkLost(str(e)) from e
        if not data:
            raise LinkLost("the module closed the Wi-Fi connection")
        return data

    def write(self, data):
        try:
            self.sock.settimeout(10)
            self.sock.sendall(data)
        except OSError as e:
            raise LinkLost(str(e)) from e

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def __str__(self):
        return f"{self.address}:{self.port}"


# In order of preference: the first that finds the module wins. Wi-Fi first: the cable is mostly
# for power, and unplugging it must not interrupt anything (a request, a note). Pairing and
# firmware updates use the cable instead, when present (bridge.Device.use_cable).
# The "link" config key picks just one ("usb", "wifi", "ble"); the default is "auto".
TRANSPORTS = {"wifi": TcpLink, "usb": SerialLink, "ble": BleLink}


def allowed():
    choice = CONFIG.get("link", "auto")
    return list(TRANSPORTS.values()) if choice == "auto" else [TRANSPORTS[choice]]


def find(better_than=None):
    """The first transport that finds the module; with better_than, only those preferred to it."""
    return next(candidates(better_than), None)


def candidates(better_than=None, only=None):
    """The transports that find the module, in order of preference (lazily: finding is costly).
    The caller tries to open each and moves on if one fails: a remembered Wi-Fi address may no
    longer answer, and must not prevent using the cable."""
    for transport in [only] if only else allowed():
        if better_than and transport.name == better_than:
            return
        link = transport.find()
        if link:
            yield link
