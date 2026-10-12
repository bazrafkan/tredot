"""The bridge over Wi-Fi: a fake module on TCP (localhost) in place of the one on the network."""

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

os.environ["TREDOT_LANG"] = "en"  # the English texts, whatever the Mac's language
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import security  # noqa: E402


class WifiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tredot-test-")
        self.server = socket.create_server(("127.0.0.1", 0))
        port = self.server.getsockname()[1]
        config = os.path.join(self.tmp, "config.json")
        with open(config, "w") as f:
            json.dump({"notes_dir": self.tmp, "media_dir": self.tmp, "link": "wifi"}, f)
        self.key = security.new_key()
        with open(os.path.join(self.tmp, "pairings.json"), "w") as f:
            json.dump({"XIAO-TEST": {"key": security.b64(self.key), "name": "test"}}, f)
        env = dict(os.environ, TREDOT_CONFIG=config, TREDOT_CACHE_DIR=self.tmp,
                   TREDOT_WIFI=f"127.0.0.1:{port}")
        self.bridge = subprocess.Popen([sys.executable, os.path.join(HERE, "bridge.py")], env=env,
                                       stderr=subprocess.DEVNULL)
        self.addCleanup(self.cleanup)
        self.server.settimeout(10)
        self.conn, _ = self.server.accept()
        self.conn.settimeout(5)
        self.splitter = security.Splitter()
        self.queue = []

    def cleanup(self):
        self.bridge.kill()
        self.bridge.wait()
        self.server.close()
        shutil.rmtree(self.tmp)

    def next(self, session=None, encrypted=False):
        """The next line; with encrypted, only those that came in an encrypted frame."""
        while True:
            while not self.queue:
                for kind, data in self.splitter.feed(self.conn.recv(65536)):
                    self.queue.append((kind, session.open(data) if kind == "frame" else data))
            kind, line = self.queue.pop(0)
            if kind == "frame" or not encrypted:
                return line

    def status(self):
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(os.path.join(self.tmp, "bridge.sock"))
            s.sendall(b'{"op": "status"}\n')
            return json.loads(s.makefile().readline())

    def test_encrypted_session_over_wifi(self):
        greeting = self.next()
        while not greeting.startswith("W\t"):
            greeting = self.next()
        nonce = security.new_nonce()
        self.conn.sendall(f"H\t4\t0.6.0\tok\tXIAO-TEST\t{security.b64(nonce)}\n".encode())
        session = security.Session(self.key, base64.b64decode(greeting.split("\t")[2]), nonce,
                                    side="device")
        line = self.next(session, encrypted=True)
        while line is None or not line.startswith("S\t"):
            line = self.next(session, encrypted=True)
        self.assertEqual(line.split("\t")[1], "idle")  # the state arrives encrypted, over TCP
        status = self.status()
        self.assertEqual((status["link"], status["secure"]), ("wifi", True))

    def test_reconnects_when_the_module_closes(self):
        first = self.next()
        self.conn.close()  # the module restarted, or lost the network
        self.conn, _ = self.server.accept()  # the bridge comes back on its own
        self.conn.settimeout(5)
        self.splitter, self.queue = security.Splitter(), []
        second = self.next()
        self.assertTrue(first.startswith("W\t8\t") and second.startswith("W\t8\t"))
        self.assertTrue(first.endswith("\ten") and second.endswith("\ten"))  # the language
        self.assertNotEqual(first, second)  # a new nonce: a new session


if __name__ == "__main__":
    unittest.main()
