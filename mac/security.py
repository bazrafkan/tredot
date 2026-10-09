"""Pairing and encryption between the Mac and the module. The C side is firmware/tredot/security.h.

Pairing (once, cable only: the cable proves the Mac and the module are on the same desk): the Mac
picks a random 32-byte key and sends it with PAIR; the module asks for a confirming tap and stores
it. The Mac keeps it in pairings.json next to the configuration (readable only by you), one per
module.

Session (on every connection, over any transport):
  Mac    -> W <protocol> <Mac nonce>
  module -> H <protocol> <firmware> <mic> <id> <module nonce>
From then on every line travels in an encrypted, authenticated binary frame (AES-256-GCM) with the
session keys (HKDF from the pairing key and both nonces, one per direction) and an ever-increasing
counter: a forged, modified, replayed or old-session line is dropped.

The frame: 0x00, length (3 bytes), counter (8 bytes), ciphertext, tag (16 bytes).
Text lines never start with 0x00, so both can share the same stream.
"""

import base64
import json
import os
import secrets
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from config import FILE as CONFIG_FILE

PAIRINGS = os.path.join(os.path.dirname(CONFIG_FILE), "pairings.json")
MARK = 0x00
HEADER = 12  # mark, length, counter
TAG = 16
MAX_LINE = 1 << 22  # 4 MB: more than any photo


def b64(data):
    return base64.b64encode(data).decode()


def new_key():
    return secrets.token_bytes(32)


def new_nonce():
    return secrets.token_bytes(16)


def _derive(key, salt, info):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(key)


class Session:
    """The keys of one connection: "m2d" is outgoing on the Mac and incoming on the module."""

    def __init__(self, key, mac_nonce, device_nonce, side="mac"):
        salt = mac_nonce + device_nonce
        m2d, d2m = _derive(key, salt, b"tredot m2d"), _derive(key, salt, b"tredot d2m")
        self.out_key, self.in_key = (m2d, d2m) if side == "mac" else (d2m, m2d)
        self.out_dir = b"m2d\0" if side == "mac" else b"d2m\0"
        self.in_dir = b"d2m\0" if side == "mac" else b"m2d\0"
        self.sent = 0
        self.received = -1

    def seal(self, line):
        """A line (without newline) -> the encrypted frame."""
        data = line.encode("utf-8") if isinstance(line, str) else line
        header = struct.pack(">B", MARK) + len(data).to_bytes(3, "big") + struct.pack(">Q", self.sent)
        sealed = AESGCM(self.out_key).encrypt(self.out_dir + header[4:], data, header)
        self.sent += 1
        return header + sealed

    def open(self, frame):
        """The frame -> the line, or None if forged, modified or already seen."""
        header, sealed = frame[:HEADER], frame[HEADER:]
        counter = struct.unpack(">Q", header[4:])[0]
        if counter <= self.received:
            return None  # replayed: someone is resending recorded lines
        try:
            data = AESGCM(self.in_key).decrypt(self.in_dir + header[4:], sealed, header)
        except InvalidTag:
            return None
        self.received = counter
        return data.decode("utf-8", "replace")


class Splitter:
    """Splits the byte stream into text lines and encrypted frames as they arrive."""

    def __init__(self):
        self.buf = bytearray()
        self.scanned = 0  # no newline up to here: a plaintext photo is a ~400 kB line

    def feed(self, chunk):
        """Returns the complete [("line", text) | ("frame", bytes)]; the rest waits for more data."""
        self.buf += chunk
        out = []
        while self.buf:
            if self.buf[0] == MARK:
                if len(self.buf) < HEADER:
                    break
                size = HEADER + int.from_bytes(self.buf[1:4], "big") + TAG
                if size - HEADER - TAG > MAX_LINE:
                    del self.buf[:1]  # not a real frame: skip one byte
                    continue
                if len(self.buf) < size:
                    break
                out.append(("frame", bytes(self.buf[:size])))
                del self.buf[:size]
            else:
                cut = self.buf.find(b"\n", self.scanned)
                if cut < 0:
                    self.scanned = len(self.buf)
                    break
                out.append(("line", self.buf[:cut].decode("utf-8", "replace").strip()))
                del self.buf[:cut + 1]
                self.scanned = 0
        return out

    def pending(self):
        return len(self.buf)


# ---------- saved pairings ----------

def load_pairings():
    try:
        with open(PAIRINGS, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def key_for(device):
    entry = load_pairings().get(device)
    return base64.b64decode(entry["key"]) if entry else None


def _write(pairings):
    os.makedirs(os.path.dirname(PAIRINGS), exist_ok=True)
    tmp = PAIRINGS + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # readable only by you
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(pairings, f, indent=2)
    os.replace(tmp, PAIRINGS)


def save_pairing(device, key, name):
    pairings = load_pairings()
    pairings[device] = {"key": b64(key), "name": name}
    _write(pairings)


def forget_pairing(device):
    pairings = load_pairings()
    if pairings.pop(device, None) is not None:
        _write(pairings)
