"""Bridge between the coding agents' hooks and the Tredot module.

It keeps the link to the module open (only one process can own the serial port) and listens to
the hooks on a Unix socket. Hooks send it one JSON line:
  {"op": "state", "sid": "...", "state": "think", "title": "...", "lines": [...], "session": "..."}
  {"op": "ask", "title": "...", "lines": [...], "session": "...", "timeout": 60,
   "options": ["Yes", "No", ...], "resumes": [true, false, ...]}
For "ask" it replies {"answer": <option index>}, or {"answer": null} if the module is disconnected
or the time ran out: the hook then lets the request appear in Claude.
"resumes" says, for each option, whether Claude goes back to work after it is chosen.

For the agent (agent.py) there are also:
  {"op": "subscribe"}                 kept open: the bridge pushes {"event": "talk", "wav": ...}
                                      (to the latest subscriber; if it leaves, to the previous one)
  {"op": "show", "title": ..., "text": ...}       a paged reply on the screen
  {"op": "photo", "prompt": ...}      -> {"path": <jpg> | null}   a photo framed by the user
  {"op": "listen", "prompt": ...}     -> {"path": <wav> | null}   a spoken answer

The first hook that cannot find it starts it; it keeps running until stopped.
"""

import base64
import hashlib
import json
import logging
import os
import subprocess
import sys
import socket
import socketserver
import threading
import time
import uuid
import wave
from base64 import b64decode
from datetime import datetime

import adpcm
import links
import screenshot
import security
from config import CACHE, MEDIA, NOTES, SOCKET
from hook import plain, wrap
from i18n import LANG, t
from notes import invented, transcribe

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(CACHE, "bridge.log")
# The protocol version this bridge speaks (PROTOCOL in the firmware), announced in the H greeting.
# An H without fields is version 0, same lines.
# 2: the bridge introduces itself with W as soon as it connects (needed with several links).
# 3: compressed VA audio (IMA ADPCM) when the module is on Bluetooth.
# 4: pairing and encrypted lines (security.py); W and H carry the session nonces.
# 5: firmware update from the bridge (U, u, UE, UA; the module answers UK, UD, UN).
# W also carries the UI language (en, it) as its last field: firmware that does not know it
# ignores it.
PROTOCOL = 6
RESYNC = 3  # encrypted frames rejected in a row before reopening the session (received)
# The Mac-side version: inside the app build_app.py writes it to the "VERSION" file, in the source
# tree it comes from pyproject.toml. The app compares it with its own: a bridge from an older
# version still running after an update gets closed (op "quit") and replaced.
try:
    with open(os.path.join(HERE, "VERSION"), encoding="utf-8") as _f:
        VERSION = _f.read().strip()
except OSError:
    try:
        import tomllib
        with open(os.path.join(HERE, "pyproject.toml"), "rb") as _f:
            VERSION = tomllib.load(_f)["project"]["version"]
    except (OSError, KeyError, ValueError):
        VERSION = "?"
OTA_CHUNK = 3072  # bytes per u line: 4 kB in base64, well below the module's longest line
# Chunks in flight without an ack. Cable: 3, the module receives into 16 kB. Wi-Fi: 8, TCP has its
# own flow control, and with 3 the acks were the bottleneck (32 kB/s instead of ~90).
OTA_WINDOW = {"usb": 3, "wifi": 8}


SEP = "\x1f"  # separates list items inside a field


def clean(text, limit=200):
    """No tabs, newlines or separators inside a protocol field."""
    text = " ".join(str(text or "").replace(SEP, " ").split())
    return text[:limit]


def clean_list(items, limit=4):
    return SEP.join(clean(i, 120) for i in list(items or [])[:limit])


# Silence after which a session counts as stopped: an interrupted Claude (Esc, Ctrl+C) sends no
# Stop, and without a deadline its "Thinking" would stay on the screen forever.
STALE = {"think": 180, "tool": 900, "wait": 900, "note": 600}
# Esc in Claude sends no event, but writes the interruption to the session transcript: the bridge
# watches the transcripts of the sessions it shows as working.
WORKING = ("think", "tool", "wait")
INTERRUPT = ('"interruptedMessageId"', "[Request interrupted by user")


# Plaintext lines accepted while the encrypted session is open: the greeting (the module restarted
# or answers W), pairing replies (pairing happens in plaintext, over the cable) and diagnostics (#).
PLAIN_OK = ("H", "PAIRED", "PAIRNO", "UNPAIRED", "#")
NOT_PAIRED = t("the module is not paired or not connected")


def line_allowed(line, session):
    return session is None or line.startswith(PLAIN_OK)


class Ask:
    def __init__(self, sid, title, lines, session, options, resumes, timeout):
        self.sid = sid
        self.id = uuid.uuid4().hex[:8]
        self.title, self.lines, self.session = title, lines, session
        self.options, self.resumes = options, resumes
        self.deadline = time.monotonic() + timeout
        self.answer = None
        self.done = threading.Event()

    def line(self):
        left = max(1, int(self.deadline - time.monotonic()))
        return "\t".join(["A", self.id, str(left), self.title, self.lines, self.session,
                          self.options])


class Device:
    """The module, over any link (links.py), with automatic reconnection."""

    def __init__(self):
        self.link = None
        self.lock = threading.RLock()  # close() can be called from inside write()
        # One line at a time to the module: several threads write (hooks, agent, bridge). Two
        # concurrent Bluetooth writes both hang (seen); on the cable two lines could interleave.
        self.write_lock = threading.Lock()
        self.state_line = "S\tidle"  # the screen shown now (outside of requests)
        self.sessions = {}  # sid -> (state, S line, time): each agent session on its own
        self.visible = None  # sid of the session on the screen
        self.asks = []  # queued: only the first one is ever on the screen
        self.recording = None  # .wav file of the voice note being recorded
        self.recording_path = None
        self.video = None  # work folder of the video being recorded: frames + audio
        self.message, self.text = None, ""  # the Claude message being streamed
        self.recording_for_agent = False
        self.agents = []  # voice subscribers (agent.py, the MCP channel): the latest wins
        self.agent_queue = []  # events that arrived before the agent was ready
        self.agent_started = -99  # when the agent was last launched
        self.pending = {}  # "photo" / "listen" requested by the agent -> [Event, result]
        self.info = {}  # from the H greeting and I lines: protocol, firmware, mic, camera
        self.transcripts = {}  # sid -> [.jsonl transcript, bytes already read]
        self.splitter = security.Splitter()
        self.session = None  # security.Session when the module is paired: everything encrypted
        self.mac_nonce = b""
        self.pairing = None  # pairing in progress: [Event, key, module id or None]
        self.ota = None  # firmware update in progress: {"cond", "acked", "result"}
        self.forced = None  # a transport to use instead of the usual order (use_cable)
        self.hellos = 0  # count of H greetings: tells when the module answered on a link
        self.rejected = 0  # encrypted frames rejected in a row: at RESYNC the session reopens
        self.resyncs = 0  # sessions reopened with no frame getting through: different keys
        self.last_refresh = self.last_watch = self.last_upgrade = 0

    @property
    def connected(self):
        return self.link is not None

    def open(self, link=None):
        """The first link that actually opens, in order (or link, if already found)."""
        for candidate in [link] if link else links.candidates(only=self.forced):
            if candidate.open():
                self.attach(candidate)
                return True
        return False

    def attach(self, link):
        """An open link becomes the one in use: W greeting and state."""
        self.link = link
        self.splitter = security.Splitter()
        logging.info("connected via %s (%s)", link.name, link)
        self.greet()
        self.resend()  # for firmware without the encrypted session (protocol < 4)

    def greet(self):
        """W: the module answers with an H greeting on this link and, if paired, opens the session.
        The last field is the language the module's own texts follow."""
        self.rejected = 0
        with self.write_lock:
            self.session = None
            self.mac_nonce = security.new_nonce()
        self.write(f"W\t{PROTOCOL}\t{security.b64(self.mac_nonce)}\t{LANG}")

    def close(self):
        if self.link:
            self.link.close()
        self.link = None
        self.session = None
        logging.info("module disconnected")
        self.end_note()  # link lost mid note or video: keep what was recorded
        self.end_video()
        # whoever waits for an answer must not hang: everything goes back to Claude
        with self.lock:
            for ask in self.asks:
                ask.answer = None
                ask.done.set()
            self.asks.clear()

    def write(self, line, plain=False):
        """One line to the module: encrypted if there is a session (plain: in clear anyway, for PAIR)."""
        if line[:2] in ("S\t", "L\t", "A\t", "C\t", "M\t", "X\t"):
            logging.info("-> %s", line.replace("\t", " | ")[:90])
        with self.write_lock:
            link = self.link
            if not link:
                return
            try:
                if self.session and not plain:
                    link.write(self.session.seal(line))
                else:
                    link.write((line + "\n").encode("utf-8"))
                return
            except links.LinkLost as e:
                logging.info("link lost: %s", e)
        self.close()  # outside write_lock: close() ends the requests, which write

    def resend(self):
        with self.lock:
            if self.asks:
                self.write(self.asks[0].line())
            else:
                self.write(self.state_line)

    def set_state(self, sid, state, title, lines, session, transcript=None):
        with self.lock:
            before = self.sessions.get(sid)
            if state == "wait" and not lines and before and before[0] == "tool":
                # Gemini asks for permission in its terminal without saying what for: it is the
                # tool just announced (BeforeTool comes before the question). Show that instead.
                _, _, tool_title, tool_lines, _ = before[1].split("\t")
                lines = SEP.join([tool_title] + [x for x in tool_lines.split(SEP) if x])
        line = "\t".join(["S", state, title, lines, session])
        with self.lock:
            if transcript and os.path.isfile(transcript):
                # from here on: interruptions already written belong to earlier turns
                self.transcripts[sid] = [transcript, os.path.getsize(transcript)]
            self.sessions[sid] = (state, line, time.monotonic())
            self.refresh()

    def watch_interrupts(self):
        """Removes from the screen the sessions stopped with Esc (see INTERRUPT)."""
        with self.lock:
            working = [sid for sid, (state, _, _) in self.sessions.items()
                       if state in WORKING and sid in self.transcripts]
        for sid in working:
            entry = self.transcripts[sid]
            try:
                with open(entry[0], "rb") as f:
                    f.seek(entry[1])
                    new = f.read()
            except OSError:
                continue
            new = new[:new.rfind(b"\n") + 1]  # a partial line is read again next time
            entry[1] += len(new)
            if any(mark.encode() in new for mark in INTERRUPT):
                logging.info("session %s interrupted in Claude", sid[:8])
                with self.lock:
                    self.sessions.pop(sid, None)
                    self.refresh()

    def refresh(self):
        """The screen shows the session with the latest event, after dropping stale ones."""
        with self.lock:
            if self.info.get("protocol", 0) > PROTOCOL:
                return  # keep the "update the bridge" warning
            now = time.monotonic()
            for sid, (state, _, at) in list(self.sessions.items()):
                if now - at > STALE.get(state, float("inf")):
                    logging.info("session %s idle for %ds: removed", sid[:8], now - at)
                    del self.sessions[sid]
            newest = max(self.sessions.items(), key=lambda kv: kv[1][2], default=None)
            self.visible = newest[0] if newest else None
            line = newest[1][1] if newest else "S\tidle"
            if line != self.state_line:
                self.state_line = line
                if not self.asks:
                    self.write(line)

    def add_text(self, sid, message, delta):
        """Claude's text as it streams: the module shows the last three lines."""
        if sid != self.visible:
            return  # text from another session: not the one on the screen
        if message != self.message:
            self.message, self.text = message, ""
        self.text += delta
        lines = wrap(plain(self.text), 22, 999)[-3:]
        self.write("L\t" + clean_list(lines))

    def add_ask(self, ask):
        with self.lock:
            self.asks.append(ask)
            if len(self.asks) == 1:
                self.write(ask.line())

    def finish_ask(self, ask, answer, tell_device):
        with self.lock:
            if ask not in self.asks:
                return
            head = self.asks[0] is ask
            self.asks.remove(ask)
            ask.answer = answer
            ask.done.set()
            if answer is not None and ask.resumes[answer]:
                # decided on the module: Claude resumes, no prompt to look at
                line = "\t".join(["S", "think", "", "", ask.session])
                self.sessions[ask.sid] = ("think", line, time.monotonic())
                self.state_line = line
            if head:
                if tell_device:
                    self.write("C\t" + ask.id)
                self.write(self.asks[0].line() if self.asks else self.state_line)

    def received(self, kind, data):
        """One piece from the module: a plaintext line or an encrypted frame (security.Splitter)."""
        if kind == "frame":
            line = self.session.open(data) if self.session else None
            if line is None:
                logging.warning("frame rejected (%d bytes): wrong key, modified or replayed",
                                len(data))
                self.rejected += 1
                if self.rejected == RESYNC:
                    # bridge and module on different keys: start over instead of staying deaf.
                    # If nothing gets through even then, the module's key is not ours (paired
                    # again elsewhere): the menu offers to pair again (status key_ok)
                    self.resyncs += 1
                    logging.warning("%d frames rejected in a row: reopening the session", RESYNC)
                    self.greet()
                return
            self.rejected = self.resyncs = 0
            self.on_line(line)
        elif line_allowed(data, self.session):
            self.on_line(data)
        elif "\t" in data:  # anything else (chip boot output on serial) is noise
            logging.warning("plaintext line ignored, the session is encrypted: %s", data[:40])

    def on_line(self, line):
        parts = line.split("\t")
        if parts[0] == "H":
            self.hello(parts[1:])
        elif parts[0] in ("PAIRED", "PAIRNO", "UNPAIRED"):
            self.paired(parts)
        elif line.startswith("#"):  # firmware measurements (DIAG): go to the log
            logging.info("module %s", line)
        elif parts[0] in ("UK", "UD", "UN") and self.ota:
            with self.ota["cond"]:
                if parts[0] == "UK" and len(parts) > 1 and parts[1].isdigit():
                    self.ota["acked"] = int(parts[1])
                else:
                    self.ota["result"] = "ok" if parts[0] == "UD" else (parts[1] if len(parts) > 1 else "?")
                self.ota["cond"].notify_all()
        elif parts[0] == "I" and len(parts) >= 3:
            self.info[parts[1]] = parts[2]
            logging.info("module reports: %s = %s", parts[1], parts[2])
            if parts[1] == "wifi" and parts[2][:1].isdigit() and self.info.get("id"):
                links.remember(self.info["id"], parts[2])  # to find it at once next time
        elif parts[0] == "R" and len(parts) >= 3:
            with self.lock:
                ask = next((a for a in self.asks if a.id == parts[1]), None)
            logging.info("answer %s -> %s", parts[1], parts[2])
            if ask and parts[2].isdigit() and int(parts[2]) < len(ask.resumes):
                self.finish_ask(ask, int(parts[2]), tell_device=False)
            elif ask:  # -1: the module is recording, the prompt in Claude decides
                self.finish_ask(ask, None, tell_device=False)
        elif parts[0] in ("V", "VA") and len(parts) >= 2:
            target = self.video["audio"] if self.video else self.recording
            if target:
                data = b64decode(parts[1])
                target.writeframes(adpcm.decode(data) if parts[0] == "VA" else data)
        elif parts[0] == "F" and len(parts) >= 3 and self.video:
            self.add_frame(int(parts[1]), b64decode(parts[2]))
        elif parts[0] == "P" and len(parts) >= 2:
            self.save_photo(b64decode(parts[1]))
        elif parts[0] == "SS" and len(parts) >= 4:
            self.resolve("screenshot", (int(parts[1]), int(parts[2]), b64decode(parts[3])))
        elif parts[0] == "MB":
            self.start_video()
        elif parts[0] == "ME":
            self.end_video()
        elif parts[0] == "VB":
            self.start_note()
            self.recording_for_agent = len(parts) > 1 and parts[1] == "agent"
        elif parts[0] == "XN":  # the module was busy: the agent gets no photo or voice
            for kind in ("photo", "listen"):
                self.resolve(kind, None)
        elif parts[0] == "VE":
            self.end_note()
        elif parts[0] == "K":
            logging.info("reply shown on the module: %s lines", parts[1] if len(parts) > 1 else "?")
        elif parts[0] == "T":
            logging.info("touch %s", parts[1:])

    def upgrade(self):
        """Switches to a better link than the one in use (links.TRANSPORTS order), but only when
        nothing is in progress."""
        with self.lock:
            busy = (self.asks or self.recording or self.video or self.pending or self.forced
                    or self.ota or self.pairing)
        if busy or not self.link:
            return
        # open the new one first, and leave the old one only if the new one really works
        for better in links.candidates(better_than=self.link.name):
            if better.open():
                logging.info("switching from %s to %s", self.link.name, better.name)
                self.close()
                self.attach(better)
                return

    def use_cable(self, need_session=True, timeout=15):
        """For pairing and updates: the cable, if present, even while Wi-Fi is in use.
        True if now on the cable (and, with need_session, with the encrypted session). The caller
        resets self.forced = None when done."""
        if self.link and self.link.name == "usb":  # already on the cable
            return bool(self.session) or not need_session
        if not links.SerialLink.find():
            return False
        logging.info("switching to the cable (pairing or update)")
        seen = self.hellos
        self.forced = links.SerialLink
        self.close()  # the run loop reopens, on the cable
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            link = self.link
            if link and link.name == "usb" and self.hellos > seen and (self.session or not need_session):
                return True
            time.sleep(0.1)
        return False

    def hello(self, fields):
        """H <protocol> <firmware> <mic> [<id> <session>]: who the module is, whether we match.

        session: the module's nonce (paired: the encrypted session opens), "-" (not paired:
        plaintext, cable only) or "boot" (it just started: start over with W).
        """
        version = int(fields[0]) if fields and fields[0].isdigit() else 0
        self.hellos += 1
        kept = {k: v for k, v in self.info.items() if k in ("cam", "wifi")}  # from I lines
        self.info = {"protocol": version,
                     "firmware": fields[1] if len(fields) > 1 else "?",
                     "mic": fields[2] if len(fields) > 2 else "?",
                     "id": fields[3] if len(fields) > 3 else "?"}
        self.info.update(kept)
        logging.info("module started: protocol %d, firmware %s, mic %s",
                     version, self.info["firmware"], self.info["mic"])
        nonce = fields[4] if len(fields) > 4 else "-"
        if nonce == "boot":
            self.greet()  # restarted: a new session, which it opens by answering with H
            return
        if nonce != "-":
            key = security.key_for(self.info["id"])
            if not key:
                logging.warning("%s is paired with another Mac: to use it here, pair it over the cable "
                                "(mac/pair.py)", self.info["id"])
                return
            with self.write_lock:
                self.session = security.Session(key, self.mac_nonce, base64.b64decode(nonce))
            logging.info("encrypted session with %s", self.info["id"])
        elif version >= 4:
            logging.info("%s is not paired: cable only, in plaintext (mac/pair.py)",
                         self.info["id"])
        if version > PROTOCOL:
            # lines this bridge may not understand: better to say so than to fail silently
            logging.warning("firmware newer than the bridge (protocol %d > %d)", version, PROTOCOL)
            with self.lock:
                self.state_line = "\t".join(["S", "error", t("Update the Mac app"), clean_list(
                    [t("the firmware uses"), t("protocol {version},", version=version),
                     t("the bridge {protocol}", protocol=PROTOCOL)]), ""])
        self.resend()

    def pair(self, name, timeout=75):
        """Pairing over the cable: the module asks for a confirming tap. -> (module id or None, error)."""
        try:
            if not self.use_cable(need_session=False):
                return None, t("needs the USB cable")
            return self._pair(name, timeout)
        finally:
            self.forced = None

    def _pair(self, name, timeout):
        waiting = [threading.Event(), security.new_key(), None]
        self.pairing = waiting
        self.write(f"PAIR\t{security.b64(waiting[1])}\t{clean(name, 30)}", plain=True)
        if not waiting[0].wait(timeout):
            self.pairing = None
            return None, t("no answer from the module")
        return waiting[2], None if waiting[2] else t("refused on the module")

    def set_wifi(self, ssid, password):
        """Network name and password to the module: encrypted only (N is never logged)."""
        if not self.session:
            return NOT_PAIRED
        self.info.pop("wifi", None)  # the module reports the outcome with an I wifi line
        self.write("\t".join(["N", security.b64(ssid.encode()), security.b64(password.encode())]))
        return None

    def update(self, firmware, version=""):
        """New firmware to the module: over the cable if present, otherwise Wi-Fi; always
        encrypted. -> None if done, or the reason it failed."""
        try:
            self.use_cable()  # no cable: stay where we are
            return self._update(firmware, version)
        finally:
            self.forced = None

    def _update(self, firmware, version):
        if not self.session:
            return NOT_PAIRED
        if self.link.name == "ble":
            return t("not over Bluetooth (too slow): connect it to Wi-Fi or with the cable")
        state = {"cond": threading.Condition(), "acked": -1, "result": None}
        self.ota = state

        def wait(until, timeout):
            with state["cond"]:
                state["cond"].wait_for(lambda: until() or state["result"] or not self.session, timeout)
            return state["result"] or (None if until() else t("the module stopped responding"))

        try:
            began = time.monotonic()
            self.write(f"U\t{len(firmware)}\t{hashlib.sha256(firmware).hexdigest()}\t{clean(version, 20)}")
            error = wait(lambda: state["acked"] == 0, 20)
            if error:
                return error
            for start in range(0, len(firmware), OTA_CHUNK):
                window = OTA_WINDOW.get(self.link.name if self.link else "usb", 3)
                ahead = lambda: start - state["acked"] < window * OTA_CHUNK  # noqa: E731
                error = wait(ahead, 30)
                if error:
                    self.write("UA")
                    return error
                self.write("u\t" + security.b64(firmware[start:start + OTA_CHUNK]))
                if start % (OTA_CHUNK * 100) == 0:
                    logging.info("update: %d%%", start * 100 // len(firmware))
            error = wait(lambda: state["acked"] == len(firmware), 30)
            if error:
                return error
            self.write("UE")
            # the module asks for confirmation on screen (60 s), then installs and answers UD or UN
            error = wait(lambda: False, 90)
            logging.info("update %s in %.0f s via %s", "done" if error == "ok" else error,
                         time.monotonic() - began, self.link.name if self.link else "?")
            if error == "ok":
                self.close()  # the module restarts: close now, reconnect as soon as it is back
            return None if error == "ok" else error
        finally:
            self.ota = None

    def unpair(self, timeout=10):
        if not self.session:
            return False
        waiting = [threading.Event(), "unpair", None]
        self.pairing = waiting
        self.write("UNPAIR")  # encrypted: only the paired Mac can ask
        waiting[0].wait(timeout)
        return waiting[2] is not None

    def paired(self, parts):
        """Pairing replies. They arrive in plaintext, so they count only if we asked: otherwise
        anyone could make the Mac forget the key with an UNPAIRED."""
        waiting, self.pairing = self.pairing, None
        if not waiting:
            logging.warning("unsolicited %s: ignored", parts[0])
            return
        if parts[0] == "PAIRED" and isinstance(waiting[1], bytes) and len(parts) > 1:
            security.save_pairing(parts[1], waiting[1], socket.gethostname())
            logging.info("paired with %s", parts[1])
            waiting[2] = parts[1]
            self.greet()  # and open the encrypted session right away
        elif parts[0] == "UNPAIRED" and waiting[1] == "unpair" and len(parts) > 1:
            security.forget_pairing(parts[1])
            logging.info("unpaired from %s", parts[1])
            waiting[2] = parts[1]
            self.greet()
        else:
            logging.info("pairing refused on the module")
        waiting[0].set()

    def start_note(self):
        self.end_note(process=False)
        os.makedirs(NOTES, exist_ok=True)
        path = os.path.join(NOTES, datetime.now().strftime("%Y-%m-%d %H%M%S") + ".wav")
        self.recording = wave.open(path, "wb")
        self.recording.setnchannels(1)
        self.recording.setsampwidth(2)
        self.recording.setframerate(16000)
        self.recording_path = path
        logging.info("recording to %s", path)

    def end_note(self, process=True):
        if not self.recording:
            return
        self.recording.close()
        self.recording = None
        logging.info("recording ended")
        if process and self.recording_for_agent:
            # either the answer to the agent's "listen", or a new utterance for it
            if not self.resolve("listen", self.recording_path):
                # transcribed here, in its own thread: subscribers (agent, channel) get the text
                threading.Thread(target=self.talk, args=(self.recording_path,), daemon=True).start()
        elif process:
            subprocess.Popen(
                [sys.executable, os.path.join(HERE, "notes.py"), self.recording_path],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(CACHE, "notes.log"), "a"),
            )

    # ---------- agent ----------

    def resolve(self, kind, result):
        waiting = self.pending.pop(kind, None)
        if waiting:
            waiting[1] = result
            waiting[0].set()
        return waiting is not None

    def request(self, kind, line, timeout):
        """Asks the module for a photo or a spoken answer for the agent and waits for the result."""
        waiting = [threading.Event(), None]
        self.pending[kind] = waiting
        self.write(line)
        if not waiting[0].wait(timeout):
            self.pending.pop(kind, None)
        return waiting[1]

    def to_agent(self, event):
        while self.agents:
            try:
                self.agents[-1].write((json.dumps(event) + "\n").encode())
                self.agents[-1].flush()
                return
            except OSError:
                self.agents.pop()
        self.agent_queue.append(event)
        if time.monotonic() - self.agent_started < 60:
            return  # already starting: the event waits in the queue
        self.agent_started = time.monotonic()
        logging.info("starting the agent")
        subprocess.Popen(
            [sys.executable, os.path.join(HERE, "agent.py")],
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=open(os.path.join(CACHE, "agent.log"), "a"),
        )

    def talk(self, wav):
        self.to_agent({"event": "talk", "wav": wav, "text": hear(wav)})

    def show_reply(self, title, text):
        lines = []
        for para in str(text or "").split("\n"):
            lines += wrap(plain(para), 21, 999) if para.strip() else ([""] if lines else [])
        self.write("\t".join(["M", clean(title, 30), clean_list(lines[:40], 40)]))

    def save_photo(self, jpeg):
        os.makedirs(MEDIA, exist_ok=True)
        path = os.path.join(MEDIA, datetime.now().strftime("%Y-%m-%d %H%M%S") + ".jpg")
        raw = os.path.join(MEDIA, "." + os.path.basename(path))
        with open(raw, "wb") as f:
            f.write(jpeg)
        logging.info("photo %s (%d kB)", path, len(jpeg) // 1024)
        self.resolve("photo", path)  # if the agent asked for it
        # the sensor is mounted sideways: rotate it in the background so the link does not wait.
        # sips ships with every Mac; -r 90 = a quarter turn clockwise
        subprocess.Popen(
            ["sh", "-c", 'sips -r 90 -s formatOptions 90 "$0" --out "$1" >/dev/null && rm "$0"', raw, path],
            start_new_session=True, stdout=subprocess.DEVNULL,
            stderr=open(os.path.join(CACHE, "video.log"), "a"),
        )

    def start_video(self):
        self.end_video(process=False)
        work = os.path.join(MEDIA, "." + datetime.now().strftime("%Y-%m-%d %H%M%S"))
        os.makedirs(work, exist_ok=True)
        audio = wave.open(os.path.join(work, "audio.wav"), "wb")
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        self.video = {"dir": work, "audio": audio, "n": 0,
                      "times": open(os.path.join(work, "times.txt"), "w")}
        logging.info("video to %s", work)

    def add_frame(self, ms, jpeg):
        v = self.video
        v["n"] += 1
        with open(os.path.join(v["dir"], f"{v['n']:06d}.jpg"), "wb") as f:
            f.write(jpeg)
        v["times"].write(f"{v['n']:06d}.jpg {ms}\n")

    def end_video(self, process=True):
        v, self.video = self.video, None
        if not v:
            return
        v["audio"].close()
        v["times"].close()
        logging.info("video ended: %d frames", v["n"])
        if process:
            subprocess.Popen(
                [sys.executable, os.path.join(HERE, "video.py"), v["dir"]],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(CACHE, "video.log"), "a"),
            )

    def run(self):
        if links.TcpLink in links.allowed():
            try:
                links.finder()  # start looking for the module on the network, in the background
            except Exception as e:
                logging.info("network search did not start: %s", e)
        while True:
            if not self.link and not self.open():
                time.sleep(1)
                continue
            try:
                chunk = self.link.read()
            except links.LinkLost as e:
                logging.info("link lost: %s", e)
                self.close()
                continue
            if chunk and not self.splitter.pending():
                began = time.monotonic()  # to measure the speed of large lines
            for kind, data in self.splitter.feed(chunk):
                if len(data) > 20000 and self.link:
                    secs = max(time.monotonic() - began, 0.001)
                    logging.info("%d kB line in %.1f s via %s: %d kB/s", len(data) // 1024,
                                 secs, self.link.name, len(data) / 1024 / secs)
                self.received(kind, data)
                began = time.monotonic()
            # expired requests go back to Claude; stale sessions leave the screen
            now = time.monotonic()
            if now - self.last_refresh > 5:
                self.last_refresh = now
                self.refresh()
            if now - self.last_watch > 1:
                self.last_watch = now
                self.watch_interrupts()
            if now - self.last_upgrade > 10:
                self.last_upgrade = now
                self.upgrade()
            with self.lock:
                expired = [a for a in self.asks if a.deadline <= now]
            for ask in expired:
                self.finish_ask(ask, None, tell_device=True)


device = Device()


def hear(wav):
    """The transcript of a recording, or None if nothing can be heard."""
    try:
        text = transcribe(wav)
    except Exception as e:
        logging.info("transcription failed: %s", e)
        return None
    return None if not text or invented(text) else text


def small_photo(path):
    """The photo (rotated in the background by save_photo) at most 1280 px, in base64: for agents."""
    for _ in range(50):  # wait for the rotated photo
        if os.path.exists(path):
            break
        time.sleep(0.1)
    small = path[:-4] + ".agent.jpg"
    try:
        # at most 1280 px per side (sips -Z), without enlarging smaller ones
        size = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                              capture_output=True, text=True, check=True).stdout.split()
        largest = max(int(size[i + 1]) for i, word in enumerate(size) if word.startswith("pixel"))
        resize = ["-Z", "1280"] if largest > 1280 else []
        subprocess.run(["sips", *resize, "-s", "formatOptions", "75", path, "--out", small],
                       check=True, capture_output=True)
        with open(small, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except (OSError, subprocess.CalledProcessError) as e:
        logging.info("photo for the agent failed: %s", e)
        return None
    finally:
        if os.path.exists(small):
            os.remove(small)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            msg = json.loads(self.rfile.readline() or b"{}")
        except json.JSONDecodeError:
            return
        title = clean(msg.get("title"), 40)
        lines = clean_list(msg.get("lines"))
        session = clean(msg.get("session"), 30)
        sid = str(msg.get("sid") or "module")  # the module's own notes and videos: a separate "session"
        if msg.get("op") == "state":
            device.set_state(sid, clean(msg.get("state"), 10), title, lines, session,
                             msg.get("transcript"))
            self.reply({"ok": True})
        elif msg.get("op") == "text":
            device.add_text(sid, msg.get("message"), str(msg.get("delta") or ""))
            self.reply({"ok": True})
        elif msg.get("op") == "ask":
            options = list(msg.get("options") or [])[:4]
            if not device.connected or not options:
                self.reply({"answer": None})
                return
            resumes = list(msg.get("resumes") or [False] * len(options))
            ask = Ask(sid, title, lines, session, clean_list(options), resumes,
                      float(msg.get("timeout", 60)))
            device.add_ask(ask)
            # if the hook dies (Claude closes it) the request leaves the screen
            self.connection.settimeout(0.5)
            while not ask.done.wait(0.5):
                try:
                    if self.connection.recv(1, socket.MSG_PEEK) == b"":
                        device.finish_ask(ask, None, tell_device=True)
                except socket.timeout:
                    pass
                except OSError:
                    device.finish_ask(ask, None, tell_device=True)
            self.reply({"answer": ask.answer})
        elif msg.get("op") == "subscribe":
            device.agents.append(self.wfile)
            logging.info("voice subscribers: %d", len(device.agents))
            for event in device.agent_queue:
                device.to_agent(event)
            device.agent_queue.clear()
            self.connection.settimeout(None)
            try:
                while self.rfile.readline():  # stays open while the agent lives
                    pass
            except OSError:
                pass
            if self.wfile in device.agents:
                device.agents.remove(self.wfile)
            logging.info("voice subscriber left: %d", len(device.agents))
        elif msg.get("op") == "talk":  # an already recorded utterance, e.g. from the Mac
            device.to_agent({"event": "talk", "wav": str(msg.get("wav"))})
            self.reply({"ok": True})
        elif msg.get("op") == "show":
            device.show_reply(msg.get("title") or "Claude", msg.get("text"))
            self.reply({"ok": True})
        elif msg.get("op") in ("photo", "listen"):
            kind = msg["op"]
            if not device.connected:
                self.reply({"path": None})
                return
            line = "\t".join(["X", kind,
                              clean(msg.get("prompt"), 40)])
            path = device.request(kind, line, float(msg.get("timeout", 120)))
            # The bridge transcribes and shrinks the photo, so the clients (MCP server, plugin,
            # extensions) stay plain Python with no dependencies and no Whisper
            if kind == "listen":
                self.reply({"path": path, "text": hear(path) if path else None})
            else:
                self.reply({"path": path, "image": small_photo(path) if path else None})
        elif msg.get("op") == "screenshot":
            if not device.connected:
                self.reply({"path": None, "error": t("the module is not connected")})
                return
            shot = device.request("screenshot", "SS", 20)
            if not shot:
                self.reply({"path": None, "error": t("no answer from the module")})
                return
            width, height, raw = shot
            path = screenshot.save(raw, width, height, MEDIA)
            logging.info("screenshot %s", path)
            self.reply({"path": path})
        elif msg.get("op") == "status":
            link = device.link
            self.reply({"connected": device.connected, "link": link and link.name,
                        "port": link and str(link), "secure": device.session is not None,
                        "key_ok": device.resyncs < 2,  # 2 sessions reopened without a good frame
                        "protocol": PROTOCOL, "bridge": VERSION, "pid": os.getpid(), "module": device.info})
        elif msg.get("op") == "quit":
            # an app of another version takes over: close the link and exit
            self.reply({"ok": True})
            logging.info("closed by the app (new version)")
            device.close()
            threading.Timer(0.3, lambda: os._exit(0)).start()
        elif msg.get("op") == "pair":
            device_id, error = device.pair(clean(msg.get("name") or socket.gethostname(), 30))
            self.reply({"device": device_id, "error": error})
        elif msg.get("op") == "unpair":
            self.reply({"ok": device.unpair()})
        elif msg.get("op") == "update":
            try:
                with open(str(msg.get("path")), "rb") as f:
                    firmware = f.read()
            except OSError as e:
                self.reply({"error": t("firmware not found: {error}", error=e)})
                return
            self.reply({"error": device.update(firmware, str(msg.get("version") or ""))})
        elif msg.get("op") == "wifi":
            self.reply({"error": device.set_wifi(str(msg.get("ssid") or ""),
                                                 str(msg.get("password") or ""))})

    def reply(self, obj):
        try:
            self.wfile.write((json.dumps(obj) + "\n").encode())
        except OSError:
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    os.makedirs(CACHE, exist_ok=True)
    logging.basicConfig(filename=LOG, level=logging.INFO, format="%(asctime)s %(message)s")
    if os.path.exists(SOCKET):
        try:
            socket.socket(socket.AF_UNIX).connect(SOCKET)
            return  # a bridge is already running
        except OSError:
            os.unlink(SOCKET)
    server = Server(SOCKET, Handler)
    os.chmod(SOCKET, 0o600)
    threading.Thread(target=device.run, daemon=True).start()
    logging.info("bridge started, pid %s", os.getpid())
    server.serve_forever()


if __name__ == "__main__":
    main()
