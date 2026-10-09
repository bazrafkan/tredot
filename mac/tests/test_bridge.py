"""The real bridge against a fake module: a pseudo-terminal (pty) in place of the USB cable.

Each test starts bridge.py in a temporary folder (its own socket, logs, notes and media), so it
never touches the running bridge or the module on the desk.

    cd mac && .venv/bin/python -m unittest discover tests
"""

import array
import base64
import hashlib
import json
import math
import os
import pty
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tty
import unittest

os.environ["TREDOT_LANG"] = "en"  # the English texts, whatever the Mac's language
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
SEP = "\x1f"

import adpcm  # noqa: E402
import security  # noqa: E402


def red_png(width, height):
    """A red PNG, written by hand: the tests need no image tools."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class FakeModule:
    """The module end of the cable: reads the bridge's lines and sends its own, encrypted if there is a session."""

    def __init__(self):
        self.master, slave = pty.openpty()
        tty.setraw(slave)  # no echo or newline translation, like a real serial port
        self.slave = slave
        self.path = os.ttyname(slave)
        self.splitter = security.Splitter()
        self.queue = []
        self.session = None  # security.Session on the module side
        self.frames = []  # the frames received, as they are: for the replay tests

    def send(self, *fields):
        line = "\t".join(fields)
        self.send_raw(self.session.seal(line) if self.session else (line + "\n").encode())

    def send_raw(self, data):
        os.write(self.master, data)

    def read(self, timeout=5):
        """The next line from the bridge (decrypted if it came in a frame), or None."""
        end = time.monotonic() + timeout
        while not self.queue:
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.master], [], [], left)[0]:
                return None
            for kind, data in self.splitter.feed(os.read(self.master, 65536)):
                if kind == "frame":
                    self.frames.append(data)
                    data = self.session.open(data) if self.session else None
                    if data is None:
                        continue
                self.queue.append(data)
        return self.queue.pop(0)

    def expect(self, prefix, timeout=5):
        """Skips lines up to the first one starting with prefix."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            line = self.read(end - time.monotonic())
            if line is None:
                break
            if line.startswith(prefix):
                return line.split("\t")
        raise AssertionError(f"no {prefix!r} line from the bridge")

    def close(self):
        os.close(self.master)
        os.close(self.slave)


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tredot-test-")
        config = os.path.join(self.tmp, "config.json")
        with open(config, "w") as f:
            json.dump({"notes_dir": os.path.join(self.tmp, "notes"),
                       "media_dir": os.path.join(self.tmp, "media"), "link": "usb"}, f)
        self.module = FakeModule()
        self.env = dict(os.environ, TREDOT_CONFIG=config, TREDOT_CACHE_DIR=self.tmp,
                        TREDOT_PORT=self.module.path)
        self.socket = os.path.join(self.tmp, "bridge.sock")
        self.bridge = subprocess.Popen([sys.executable, os.path.join(HERE, "bridge.py")],
                                       env=self.env, stderr=subprocess.DEVNULL)
        self.addCleanup(self.cleanup)  # even if setUp fails: no test bridges left running
        for _ in range(50):
            if os.path.exists(self.socket):
                break
            time.sleep(0.1)
        # once connected the bridge introduces itself (W: protocol, nonce, language) and sends
        # the current state
        greeting = self.module.read().split("\t")
        self.assertEqual((greeting[:2], len(greeting), greeting[3]), (["W", "6"], 4, "en"))
        self.assertEqual(self.module.expect("S")[:2], ["S", "idle"])

    def cleanup(self):
        self.bridge.kill()
        self.bridge.wait()
        self.module.close()
        shutil.rmtree(self.tmp)

    def call(self, msg, wait=10):
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(wait)
            s.connect(self.socket)
            s.sendall((json.dumps(msg) + "\n").encode())
            return json.loads(s.makefile().readline() or "{}")

    def call_async(self, msg, wait=10):
        """A request that waits for the module: in a thread, the result in box[0]."""
        box = []
        thread = threading.Thread(target=lambda: box.append(self.call(msg, wait)))
        thread.start()
        return thread, box

    # ---------- greeting and version ----------

    def test_hello_with_version(self):
        self.module.send("H", "1", "0.2.0", "ok")
        self.module.expect("S")  # the bridge sends the state again
        self.module.send("I", "cam", "OV3660")
        time.sleep(0.3)
        status = self.call({"op": "status"})
        self.assertTrue(status["connected"])
        self.assertEqual(status["link"], "usb")
        self.assertEqual(status["module"],
                         {"protocol": 1, "firmware": "0.2.0", "mic": "ok", "id": "?", "cam": "OV3660"})

    def test_old_firmware_without_version_still_works(self):
        self.module.send("H")
        self.assertEqual(self.module.expect("S")[:2], ["S", "idle"])
        self.assertEqual(self.call({"op": "status"})["module"]["protocol"], 0)

    def test_newer_firmware_gets_a_warning(self):
        self.module.send("H", "99", "9.9.9", "ok")
        line = self.module.expect("S")
        self.assertEqual(line[1:3], ["error", "Update the Mac app"])
        # and Claude sessions do not cover it
        self.call({"op": "state", "sid": "s1", "state": "think", "session": "test"})
        self.assertIsNone(self.module.read(timeout=0.5))

    # ---------- state ----------

    def test_state_goes_to_screen(self):
        self.call({"op": "state", "sid": "s1", "state": "tool", "title": "Bash",
                   "lines": ["git status"], "session": "project"})
        self.assertEqual(self.module.expect("S"), ["S", "tool", "Bash", "git status", "project"])

    def test_newest_session_wins(self):
        self.call({"op": "state", "sid": "a", "state": "think", "session": "one"})
        self.module.expect("S")
        self.call({"op": "state", "sid": "b", "state": "tool", "title": "Read", "session": "two"})
        self.assertEqual(self.module.expect("S")[4], "two")

    def test_esc_in_claude_clears_thinking(self):
        transcript = os.path.join(self.tmp, "session.jsonl")
        with open(transcript, "w") as f:  # an interruption from an old turn does not count
            f.write('{"type":"user","interruptedMessageId":"old"}\n')
        self.call({"op": "state", "sid": "s1", "state": "think", "session": "project",
                   "transcript": transcript})
        self.assertEqual(self.module.expect("S")[1], "think")
        self.assertIsNone(self.module.read(timeout=2))  # stays on Thinking
        with open(transcript, "a") as f:  # Esc: Claude writes the interruption
            f.write('{"type":"user","message":{"content":[{"type":"text","text":'
                    '"[Request interrupted by user]"}]},"interruptedMessageId":"msg_1"}\n')
        self.assertEqual(self.module.expect("S", timeout=4)[1], "idle")

    def test_fields_are_cleaned(self):
        self.call({"op": "state", "sid": "s1", "state": "tool", "title": "a\tb\nc",
                   "lines": ["x" + SEP + "y"], "session": "s"})
        self.assertEqual(self.module.expect("S")[2:4], ["a b c", "x y"])

    # ---------- permission requests ----------

    def ask(self, timeout=60):
        return self.call_async({"op": "ask", "sid": "s1", "title": "Bash", "lines": ["git push"],
                                "session": "project", "options": ["Yes", "No", "In Claude"],
                                "resumes": [True, False, False], "timeout": timeout})

    def test_ask_answered_on_module(self):
        thread, box = self.ask()
        line = self.module.expect("A")
        self.assertEqual(line[3:], ["Bash", "git push", "project", SEP.join(["Yes", "No", "In Claude"])])
        self.module.send("R", line[1], "0")
        thread.join(5)
        self.assertEqual(box, [{"answer": 0}])
        # "Yes" resumes Claude: the screen goes back to Thinking
        self.assertEqual(self.module.expect("S")[1], "think")

    def test_ask_minus_one_leaves_it_to_claude(self):
        thread, box = self.ask()
        line = self.module.expect("A")
        self.module.send("R", line[1], "-1")
        thread.join(5)
        self.assertEqual(box, [{"answer": None}])

    def test_ask_times_out(self):
        thread, box = self.ask(timeout=1)
        line = self.module.expect("A")
        self.assertEqual(self.module.expect("C", timeout=8), ["C", line[1]])
        thread.join(5)
        self.assertEqual(box, [{"answer": None}])

    def test_asks_queue_one_at_a_time(self):
        first, box1 = self.ask()
        a1 = self.module.expect("A")
        second, box2 = self.ask()
        self.assertIsNone(self.module.read(timeout=0.5))  # the second one waits
        self.module.send("R", a1[1], "1")
        a2 = self.module.expect("A")
        self.assertNotEqual(a1[1], a2[1])
        self.module.send("R", a2[1], "0")
        first.join(5)
        second.join(5)
        self.assertEqual((box1, box2), ([{"answer": 1}], [{"answer": 0}]))

    def test_unknown_answer_id_is_ignored(self):
        thread, box = self.ask()
        self.module.expect("A")
        self.module.send("R", "nosuchid", "0")
        time.sleep(0.3)
        self.assertEqual(box, [])
        self.call({"op": "status"})  # the bridge is still alive

    # ---------- agent: voice, photo, replies ----------

    def test_voice_goes_to_subscriber(self):
        with socket.socket(socket.AF_UNIX) as sub:
            sub.connect(self.socket)
            sub.sendall(b'{"op": "subscribe"}\n')
            time.sleep(0.3)
            pcm = b"\x01\x00" * 1600
            self.module.send("VB", "agent")
            self.module.send("V", base64.b64encode(pcm).decode())
            self.module.send("VE")
            sub.settimeout(5)
            event = json.loads(sub.makefile().readline())
        self.assertEqual(event["event"], "talk")
        self.assertEqual(os.path.getsize(event["wav"]), 44 + len(pcm))  # WAV header + audio

    def test_compressed_voice_over_bluetooth(self):
        tone = array.array("h", (int(8000 * math.sin(i / 8)) for i in range(960)))
        with socket.socket(socket.AF_UNIX) as sub:
            sub.connect(self.socket)
            sub.sendall(b'{"op": "subscribe"}\n')
            time.sleep(0.3)
            self.module.send("VB", "agent")
            for half in (tone[:480], tone[480:]):  # two blocks, like the firmware
                self.module.send("VA", base64.b64encode(adpcm.encode(half.tobytes())).decode())
            self.module.send("VE")
            sub.settimeout(5)
            event = json.loads(sub.makefile().readline())
        self.assertEqual(os.path.getsize(event["wav"]), 44 + 2 * len(tone))

    def test_photo_for_agent(self):
        jpeg = os.path.join(self.tmp, "real.jpg")  # a real photo: the bridge rotates and shrinks it
        png = os.path.join(self.tmp, "real.png")
        with open(png, "wb") as f:
            f.write(red_png(64, 48))
        subprocess.run(["sips", "-s", "format", "jpeg", png, "--out", jpeg], check=True,
                       capture_output=True)
        thread, box = self.call_async({"op": "photo", "prompt": "frame the plant"}, wait=20)
        self.assertEqual(self.module.expect("X"), ["X", "photo", "frame the plant"])
        with open(jpeg, "rb") as f:
            self.module.send("P", base64.b64encode(f.read()).decode())
        thread.join(15)
        self.assertTrue(box[0]["path"].endswith(".jpg"))
        self.assertTrue(base64.b64decode(box[0]["image"]).startswith(b"\xff\xd8"))  # JPEG for the agent

    def test_screenshot(self):
        # the module sends its screen as RGB565; the bridge saves a round PNG, doubled in size
        thread, box = self.call_async({"op": "screenshot"}, wait=30)
        self.assertEqual(self.module.expect("SS"), ["SS"])
        red = (0b11111 << 11).to_bytes(2, "little") * (240 * 240)
        self.module.send("SS", "240", "240", base64.b64encode(red).decode())
        thread.join(15)
        path = box[0]["path"]
        self.assertTrue(path.endswith(".png"))
        size = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                              capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(size[-3:], ["480", "pixelHeight:", "480"])

    def test_busy_module_says_no(self):
        thread, box = self.call_async({"op": "listen", "prompt": "tell me"})
        self.module.expect("X")
        self.module.send("XN")
        thread.join(5)
        self.assertEqual(box, [{"path": None, "text": None}])

    # ---------- security: pairing and encrypted session ----------

    def pair(self, device="XIAO-TEST"):
        """Pairing over the cable, as the firmware does it; then the encrypted session. -> the key."""
        thread, box = self.call_async({"op": "pair", "name": "Test Mac"}, wait=20)
        line = self.module.expect("PAIR\t")
        key = base64.b64decode(line[1])
        self.assertEqual((len(key), line[2]), (32, "Test Mac"))
        self.module.send("PAIRED", device)  # the user tapped Yes on the module
        thread.join(5)
        self.assertEqual(box, [{"device": device, "error": None}])
        self.open_session(key, device)
        return key

    def open_session(self, key, device="XIAO-TEST"):
        greeting = self.module.expect("W\t6\t")
        device_nonce = security.new_nonce()
        self.module.send("H", "4", "0.5.0", "ok", device, security.b64(device_nonce))
        self.module.session = security.Session(key, base64.b64decode(greeting[2]), device_nonce,
                                                side="device")
        self.assertEqual(self.module.expect("S")[1], "idle")  # the state arrives encrypted
        self.assertTrue(self.call({"op": "status"})["secure"])

    def test_gemini_permission_shows_the_tool(self):
        # Gemini asks for permission without saying what for: the bridge shows the previous tool
        self.module.send("H", "4", "0.5.0", "ok", "XIAO-TEST", "-")
        self.module.expect("S")
        self.call({"op": "state", "sid": "g1", "state": "tool", "title": "Shell", "lines": ["npm test"],
                   "session": "test"})
        self.module.expect("S")
        self.call({"op": "state", "sid": "g1", "state": "wait", "title": "Reply in Gemini", "session": "test"})
        line = self.module.expect("S")
        self.assertEqual(line[1:4], ["wait", "Reply in Gemini", "Shell\x1fnpm test"])

    def test_unpaired_module_works_in_clear_on_the_cable(self):
        self.module.send("H", "4", "0.5.0", "ok", "XIAO-TEST", "-")
        self.module.expect("S")  # after the greeting the bridge resends the state: wait for it
        self.call({"op": "state", "sid": "s1", "state": "think", "session": "p"})
        self.assertEqual(self.module.expect("S")[1], "think")
        self.assertFalse(self.call({"op": "status"})["secure"])

    def test_pairing_then_everything_is_encrypted(self):
        self.pair()
        before = len(self.module.frames)
        self.call({"op": "state", "sid": "s1", "state": "tool", "title": "Bash", "session": "p"})
        self.assertEqual(self.module.expect("S")[1:3], ["tool", "Bash"])
        self.assertGreater(len(self.module.frames), before)  # it came in a frame

    def test_pairing_refused_on_module(self):
        thread, box = self.call_async({"op": "pair", "name": "Mac"}, wait=20)
        self.module.expect("PAIR\t")
        self.module.send("PAIRNO")
        thread.join(5)
        self.assertEqual(box, [{"device": None, "error": "refused on the module"}])

    def ask_secure(self):
        thread, box = self.ask()
        return thread, box, self.module.expect("A")

    def test_forged_yes_is_rejected(self):
        key = self.pair()
        thread, box, line = self.ask_secure()
        # 1. a plaintext "Yes", as sent by someone without the key
        self.module.session, session = None, self.module.session
        self.module.send("R", line[1], "0")
        # 2. a "Yes" encrypted with the wrong key
        wrong = security.Session(security.new_key(), b"x" * 16, b"y" * 16, side="device")
        self.module.send_raw(wrong.seal(f"R\t{line[1]}\t0"))
        time.sleep(0.5)
        self.assertEqual(box, [])  # neither got through
        # 3. the real module answers "No": it gets through
        self.module.session = session
        self.module.send("R", line[1], "1")
        thread.join(5)
        self.assertEqual(box, [{"answer": 1}])
        self.assertTrue(key)

    def test_replayed_answer_is_rejected(self):
        self.pair()
        thread, box, line = self.ask_secure()
        recorded = self.module.session.seal(f"R\t{line[1]}\t0")  # recorded by an eavesdropper
        self.module.send_raw(recorded)
        thread.join(5)
        self.assertEqual(box, [{"answer": 0}])
        # the same answer replayed to a new request must not count
        thread2, box2, line2 = self.ask_secure()
        self.module.send_raw(recorded)
        time.sleep(0.5)
        self.assertEqual(box2, [])
        self.module.send("R", line2[1], "1")
        thread2.join(5)
        self.assertEqual(box2, [{"answer": 1}])

    def test_unsolicited_unpaired_does_not_forget_the_key(self):
        self.pair()
        self.module.session, session = None, self.module.session
        self.module.send("UNPAIRED", "XIAO-TEST")  # plaintext, unsolicited
        time.sleep(0.3)
        with open(os.path.join(self.tmp, "pairings.json")) as f:
            pairings = json.load(f)
        self.assertIn("XIAO-TEST", pairings)
        self.module.session = session

    def test_out_of_step_session_reopens(self):
        # bridge and module on different keys (seen on the cable: stale greetings read at once):
        # after 3 rejected frames the bridge starts over with W, and they understand each other again
        key = self.pair()
        self.module.session = security.Session(key, b"x" * 16, b"y" * 16, side="device")
        for _ in range(3):
            self.module.send("I", "wifi", "off")
        self.module.session = None  # as in pair(): what arrives is read later, with the new key
        self.open_session(key)
        self.assertTrue(self.call({"op": "status"})["key_ok"])

    def test_different_key_asks_to_pair_again(self):
        # the module was paired again (another key): reopening is not enough, the menu says so
        self.pair()
        other = security.new_key()
        for _ in range(2):
            self.module.session = security.Session(other, b"x" * 16, b"y" * 16, side="device")
            for _ in range(3):
                self.module.send("I", "wifi", "off")
            greeting = self.module.expect("W\t6\t")
            nonce = security.new_nonce()
            self.module.send("H", "4", "0.5.0", "ok", "XIAO-TEST", security.b64(nonce))
            self.module.session = security.Session(other, base64.b64decode(greeting[2]), nonce, side="device")
        time.sleep(0.3)
        self.assertFalse(self.call({"op": "status"})["key_ok"])

    def test_module_paired_to_another_mac(self):
        self.module.send("H", "4", "0.5.0", "ok", "XIAO-OTHER", security.b64(security.new_nonce()))
        time.sleep(0.3)
        self.assertFalse(self.call({"op": "status"})["secure"])

    def test_module_restart_opens_a_new_session(self):
        key = self.pair()
        self.module.session = None
        self.module.send("H", "4", "0.5.0", "ok", "XIAO-TEST", "boot")  # it restarted
        self.open_session(key)

    def test_pairing_file_is_private(self):
        self.pair()
        mode = os.stat(os.path.join(self.tmp, "pairings.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    # ---------- firmware update ----------

    def serve_update(self, refuse=False):
        """The fake module receives the firmware like the real one: -> (bytes received, max chunks unacked)."""
        received, acked, worst = bytearray(), 0, 0
        size = None
        while True:
            line = self.module.read(timeout=10)
            if line is None:
                raise AssertionError("the bridge stopped sending")
            f = line.split("\t")
            if f[0] == "U":
                size, digest = int(f[1]), f[2]
                self.module.send("UK", "0")
            elif f[0] == "u":
                received += base64.b64decode(f[1])
                worst = max(worst, (len(received) - acked + 3071) // 3072)
                if len(received) - acked >= 6144 or len(received) == size:  # ack every 2 chunks
                    acked = len(received)
                    self.module.send("UK", str(acked))
            elif f[0] == "UE":
                ok = not refuse and hashlib.sha256(received).hexdigest() == digest
                self.module.send(*(["UD"] if ok else ["UN", "hash mismatch"]))
                return bytes(received), worst
            elif f[0] == "UA":
                return bytes(received), worst

    def test_update_sends_the_firmware(self):
        self.pair()
        firmware = os.urandom(50000)
        path = os.path.join(self.tmp, "firmware.bin")
        with open(path, "wb") as f:
            f.write(firmware)
        thread, box = self.call_async({"op": "update", "path": path}, wait=60)
        received, worst = self.serve_update()
        thread.join(10)
        self.assertEqual(box, [{"error": None}])
        self.assertEqual(received, firmware)
        self.assertLessEqual(worst, 3)  # never more than 3 chunks unacked

    def test_update_refused_by_module(self):
        self.pair()
        path = os.path.join(self.tmp, "firmware.bin")
        with open(path, "wb") as f:
            f.write(os.urandom(5000))
        thread, box = self.call_async({"op": "update", "path": path}, wait=60)
        self.serve_update(refuse=True)
        thread.join(10)
        self.assertEqual(box, [{"error": "hash mismatch"}])

    def test_update_needs_pairing(self):
        self.module.send("H", "5", "0.7.0", "ok", "XIAO-TEST", "-")
        self.module.expect("S")
        path = os.path.join(self.tmp, "firmware.bin")
        with open(path, "wb") as f:
            f.write(b"x")
        self.assertIn("not paired", self.call({"op": "update", "path": path})["error"])

    def test_reply_is_paged(self):
        self.call({"op": "show", "title": "Claude", "text": "A reply long enough to wrap "
                   "over several lines of the round screen."})
        line = self.module.expect("M")
        self.assertEqual(line[1], "Claude")
        self.assertTrue(all(len(r) <= 21 for r in line[2].split(SEP)))


if __name__ == "__main__":
    unittest.main()
