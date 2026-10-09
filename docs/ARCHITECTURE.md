# Architecture

Tredot has four parts:

| Part | Where | Role |
|---|---|---|
| **Firmware** | `firmware/tredot/` | draws the screen, reads the touch, microphone and camera, and speaks a line protocol over USB, Wi-Fi or BLE |
| **Bridge** | `mac/bridge.py` | the only process that talks to the module. It holds the link, encrypts it and serves everything else on the Mac through a Unix socket |
| **App** | `mac/app/Tredot.swift` | the menu-bar app (`Tredot.app`). It starts and supervises the bridge, provides the macOS permissions, and handles pairing, Wi-Fi, updates and agent setup |
| **Agent integrations** | `mac/hook.py`, `mac/mcp_server.py`, `mac/integrations.py` | the Claude Code plugin (hooks and MCP), the Claude Desktop extension, Gemini CLI (MCP and hooks) |

The module is deliberately simple: it draws what it is told and reports what you touch, say or
frame. Layout, logic and integrations live on the Mac, so most changes don't need new firmware.

```
 ┌──────────── module ────────────┐
 │ XIAO ESP32-S3 Sense + Round    │
 │ Display: screen, touch, mic,   │
 │ camera. firmware/tredot        │
 └───────┬───────────┬────────┬───┘
     USB serial   Wi-Fi TCP   BLE        encrypted lines (PROTOCOL 6)
 ┌───────┴───────────┴────────┴──────────────────────────────────────┐
 │ Tredot.app ── starts ──► bridge.py ◄── ~/.cache/tredot/bridge.sock │
 │                              ▲   ▲   ▲            ▲                │
 │   hook.py (Claude Code,      │   │   │            │                │
 │   Gemini CLI hooks) ─────────┘   │   │   mcp_server.py (any agent) │
 │   notes.py / video.py ───────────┘   └── agent.py (Talk to Claude) │
 └────────────────────────────────────────────────────────────────────┘
```

## Hardware

| Part | Details | Pins |
|---|---|---|
| Seeed XIAO ESP32-S3 Sense | ESP32-S3 (2 cores, 240 MHz, Wi-Fi, BLE), 8 MB flash, 8 MB PSRAM | |
| Microphone (Sense board) | PDM | CLK 42, DATA 41 |
| Camera (Sense board) | OV3660 or OV2640. The firmware handles both. | 10–18, 38–40, 47, 48 |
| Seeed Round Display for XIAO | GC9A01, 240×240, round | SPI 2, 4, 7, 8, 9; backlight 43 |
| Touch | CHSC6X at I2C 0x2E | I2C 5, 6; interrupt 44 |

The camera uses I2C bus 1 and the touch uses bus 0, so their drivers don't collide. The board only
has a U.FL antenna connector. Without the antenna, Wi-Fi was measured at -91 dBm and unusable. With
it, the reading was -60 dBm.

## Firmware

`tredot.ino` is a state machine. The screen is always in one mode: idle, think, tool, done, wait,
ask (a question with options), note, saved, error, recording, menu, photo, video or reply. Claude's
states wait behind the local modes (menu, recording, camera, reply) and come back when those close.

- **Text:** Unifont at 16 px (accents, CJK and symbols). The Mac lays out lines about 22 characters
  wide, and the firmware shortens each line toward the edge of the circle.
- **Touch:** the controller repeats while a finger is down, so a new tap only counts after 150 ms
  without readings. Options take two taps: the first selects, the second confirms.
- **Audio:** a task on the second core reads the microphone continuously into a 2-second ring buffer,
  so audio isn't lost while a camera frame is being sent.
- **Camera:** 640×480 for the viewfinder and video, 1600×1200 for photos (1024×768 over BLE). JPEGs
  travel in 3 kB base64 chunks and are never held as a full copy.
- **Links:** USB serial, `TcpLink` (`wifi_link.h`) and `BleLink` (`ble.h`) are all `Stream`s. The
  rest of the firmware writes to the active `channel` without knowing which link it is. Wi-Fi and
  BLE send through their own tasks, with large queues in PSRAM. A slow radio otherwise froze the
  screen, and they never drop a byte, because half an encrypted frame desynchronizes the Mac.
- **Partitions** (`partitions.csv`): two 3.94 MB OTA slots, no SPIFFS. The firmware is about 3.5 MB.

Building has two quirks, both handled by `firmware.sh`. Arduino's `ctags` only exists for Intel
Macs, so `tools/ctags` is an empty stub and the firmware declares every function before using it.
The camera also needs PSRAM, so the FQBN sets `PSRAM=opi`.

`PROTOCOL` and `FIRMWARE` at the top of `tredot.ino` are reported in the `H` greeting. Bump
`PROTOCOL` whenever a line or field changes in a way an older bridge wouldn't understand. If the
module's protocol is newer than the bridge's, the bridge shows "update the bridge" on the screen.

## The bridge

`bridge.py` runs as a child of `Tredot.app`, which restarts it if it exits. If the bridge isn't
running, a hook launches the app with `open -g`. The bridge:

- opens the best available link (`links.py`) and moves to a better one when it appears;
- serves JSON-line requests on `~/.cache/tredot/bridge.sock` (`status`, `state`, `ask`, `show`,
  `photo`, `listen`, `subscribe`, `pair`, `unpair`, `wifi`, `update`, `text`, `quit`, …);
- keeps a separate state for each agent session and shows the one with the latest event. Claude
  Code sends no event when you press Esc, so the bridge watches the session transcript for the
  interruption. A session that has been silent for 3 minutes is treated as stopped (15 minutes
  while a tool runs or Claude is waiting for you);
- saves recordings, photos and video frames, and launches `notes.py`, `video.py` and `agent.py`.

When the app starts, it compares its version with the running bridge's version (`status` returns
`bridge` and `pid`). If they differ, it sends `quit` and starts its own, so an old bridge doesn't
survive an update.

Other files in `mac/`:

| File | Role |
|---|---|
| `config.py` | the configuration (`~/.config/tredot/config.json` merged over defaults), app and data paths |
| `links.py` | transports: `SerialLink`, `TcpLink`, `BleLink` (bleak), discovery and priority |
| `security.py` | pairings, session keys, frame sealing and parsing |
| `hook.py` | hook entry point for Claude Code and Gemini CLI |
| `mcp_server.py`, `module.py` | the MCP server and the client calls it makes to the bridge (standard library only) |
| `notes.py`, `audio.py` | voice notes: cleaning, transcription, summary |
| `video.py` | assembles video from frames and audio |
| `agent.py` | the Talk to Claude agent (Claude Agent SDK) |
| `adpcm.py` | IMA ADPCM decoder for audio over BLE |
| `integrations.py`, `agent_settings.py` | connect agents from the menu, edit agent settings files |
| `pair.py`, `wifi.py`, `update_firmware.py` | terminal equivalents of the menu items |
| `build_app.py`, `build_dmg.py`, `build_plugin.py`, `build_extension.py`, `release.py` | packaging (see [RELEASING.md](RELEASING.md)) |

## Links

| Link | Transport | Carries |
|---|---|---|
| USB | serial at 115200 on `/dev/cu.usbmodem*` | everything |
| Wi-Fi | TCP port 7777. The module advertises `_tredot._tcp` over Bonjour/mDNS, and the bridge remembers addresses in `~/.cache/tredot/addresses.json` | everything |
| BLE | one GATT service: an RX characteristic for Mac-to-module writes, TX notifications back | everything except video. Audio is compressed (`VA`, IMA ADPCM) and photos drop to 1024×768 |

**Priority for normal traffic is Wi-Fi, then USB, then BLE.** The cable is mostly there for power,
so unplugging it should interrupt nothing. If the active link drops, the bridge moves to the next
one. When a better link becomes available, it opens the new link before closing the old one. An
address that no longer answers never blocks the cable. The `link` key can force a single transport.

**Pairing and firmware updates prefer USB, then Wi-Fi, and never use BLE.** If the cable is
present, the bridge switches to it for those operations. Over Wi-Fi, an update only starts when
the module's signal is better than -80 dBm.

Measured details:

- BLE on macOS gives about 10–16 kB/s. Raw audio needs 43 kB/s, while ADPCM needs about 11 kB/s.
- When the bridge connects to the module over Wi-Fi, the module stops advertising over BLE. The two
  share the radio.
- The module picks the strongest access point at boot and roams if the signal stays below -80 dBm
  for 30 seconds.
- Only one Mac at a time can be connected over Wi-Fi. A TCP client that doesn't open an encrypted
  session within 5 seconds is dropped.
- Some office networks isolate clients from each other. BLE is the fallback for that case.

## Pairing and encryption

**Pairing (once, over USB only).** The cable shows that the Mac and the module are on the same
desk. The Mac generates a random 32-byte key and sends `PAIR <key> <mac name>`. The module asks on
screen whether to pair with that Mac and needs a two-tap confirmation. It stores the key in NVS
(namespace `tredot`). The Mac stores it in `~/.config/tredot/pairings.json` (mode 0600), with one
entry per module. BLE advertising only starts once the module is paired. `UNPAIR` also makes the
module forget its Wi-Fi network.

**Session (every connection, any link):**

```
Mac    -> W <protocol> <mac nonce> <language>
module -> H <protocol> <firmware> <mic> <id> <device nonce | "-" unpaired | "boot">
```

`<language>` (`en`, `it`) is the Mac's UI language: the module stores it (NVS namespace `ui`) and
draws its own texts in it, also after a restart.

Each side derives two keys with HKDF-SHA256 from the pairing key, using `mac nonce ‖ device nonce`
(16 random bytes each) as the salt. The info labels are `tredot m2d` (Mac to module) and
`tredot d2m` (module to Mac), giving one key per direction.

**Frames.** After `H`, every line travels as a binary AES-256-GCM frame:

```
0x00 | length (3 bytes) | counter (8 bytes) | ciphertext | tag (16 bytes)
```

The GCM nonce is the direction (`m2d\0` or `d2m\0`) followed by the counter, and the 12-byte header
is authenticated as associated data. Text lines never start with `0x00`, so frames and plaintext
can share one stream.

**Replay protection.** Counters only go up. A frame with an old counter is dropped, as is a frame
that was modified, forged or recorded from another session. After 3 rejected frames in a row, the
bridge reopens the session, which recovers from a desynchronized stream.

**Rules.** While a session is open, both sides accept only encrypted lines. The only plaintext
lines are `H`, `PAIRED`, `PAIRNO` and `UNPAIRED`. An `R` (approval) only counts if it arrives
encrypted, so a "Yes" injected without the key is rejected. The tests check this, along with
replayed frames, frames under a wrong key and an unsolicited `UNPAIRED`. An unpaired module works
only over USB, in plaintext. Wi-Fi credentials (`N`) and updates (`U…`) require an encrypted
session.

**Known limits.** The module's flash is not encrypted, so someone holding the module can read its
key. On the Mac the key is in a file rather than in the Keychain. Both are on the
[roadmap](ROADMAP.md).

## Line protocol (PROTOCOL 6)

Lines are UTF-8 with TAB-separated fields. Lists inside a field (text lines, options) are separated
by `0x1F`. The Mac does all text layout.

**Mac to module**

| Line | Meaning |
|---|---|
| `W <protocol> <nonce>` | the bridge connected here. This becomes the active link, and the module answers with `H` |
| `S <state> <title> <lines> <session>` | status screen. State is one of `idle think tool done wait note saved error` |
| `A <id> <seconds> <title> <lines> <session> <options>` | a question or permission request with options and a timeout |
| `C <id>` | the request was answered elsewhere or timed out |
| `L <lines>` | Claude's text as it appears, shown under "Thinking…" |
| `M <title> <lines>` | an agent reply, paged. The module acknowledges it with `K` |
| `X photo <instruction>` / `X listen <question>` | the agent wants a photo or a spoken answer |
| `PAIR <key> <name>` | pairing request (USB only) |
| `UNPAIR` | remove the pairing (encrypted only) |
| `N <ssid> <password>` | Wi-Fi credentials, base64. Empty values mean forget (encrypted only) |
| `U <bytes> <sha256> <version>` | start a firmware update |
| `u <base64>` | one 3 kB firmware chunk |
| `UE` | end of firmware. The module verifies it and asks for confirmation |
| `UA` | abort the update |

**Module to Mac**

| Line | Meaning |
|---|---|
| `H <protocol> <firmware> <mic> <id> <session>` | greeting, always plaintext. `mic` is `ok` or `no`. `session` is the device nonce, `-` (unpaired) or `boot` (just started, so the Mac restarts with `W`) |
| `PAIRED <id>` / `PAIRNO` / `UNPAIRED <id>` | result of `PAIR` / `UNPAIR` |
| `I cam <sensor>` | camera sensor, reported the first time the camera starts |
| `I wifi <state>` | `off`, `connecting`, `address`, `<IP>` or `error: <reason>` |
| `R <id> <index>` | confirmed option (0 is the first). `-1` means it was never shown, so the agent's own dialog decides |
| `T <x> <y>` | raw touch, for diagnostics |
| `VB [agent]` | recording starts. `agent` means Talk to Claude, otherwise it is a voice note |
| `V <base64>` | PCM audio: 16 kHz, 16-bit, mono |
| `VA <base64>` | IMA ADPCM audio, sent instead of `V` over BLE |
| `VE` | recording ends |
| `P <base64>` | a JPEG photo |
| `MB` / `F <ms> <base64>` / `ME` | video start, a JPEG frame with its timestamp, video end. Audio arrives as `V` lines |
| `XN` | busy: can't take a photo or listen right now |
| `K <lines>` | the `M` reply arrived complete |
| `UK <bytes>` | update ready, or bytes received so far |
| `UD` / `UN <reason>` | update installed / not installed |

## Firmware updates (OTA)

The bridge sends firmware over USB or Wi-Fi, inside the encrypted session, so only the paired Mac
can update the module. BLE is refused because 3.5 MB would take about 5 minutes.

1. `U` carries the size, the SHA-256 and the version. The module refuses if it is recording, if the
   link is BLE, or if the Wi-Fi signal is weak. Otherwise it prepares the inactive OTA slot.
2. `u` chunks of 3 kB follow, with flow control through `UK`. Over USB the bridge keeps 3 chunks in
   flight, over Wi-Fi 8. A full update takes about 50 seconds over USB.
3. On `UE` the module checks the SHA-256 and asks **Install?** on screen, showing the new and the
   current version (60 seconds to answer).
4. **Yes** sets the new slot as the boot slot, sends `UD` and restarts. **No**, a timeout, a dropped
   link or a hash mismatch sends `UN <reason>`, and the current firmware stays.
5. The new firmware boots in a pending state. It marks itself valid once it has opened an
   encrypted session with the Mac, or after 2 minutes without a crash. If it crashes before that,
   the bootloader rolls back to the previous slot. The Arduino core normally marks every image
   valid at boot, and the firmware overrides that.

The app bundles the last compiled firmware (`Contents/Resources/firmware/`) with its version. The
menu offers the update when that version differs from the module's.

`./firmware.sh flash` is the cable path for development. It writes the bootloader, partition table,
`otadata` and the app with esptool, but not the whole 8 MB image, which would erase NVS and with it
the pairing. While it runs, a `flashing` marker in the cache folder keeps the bridge away from the
port.

## Voice

1. The module streams audio while you speak (`V`, or `VA` over BLE). The bridge writes a 16 kHz
   `.wav`.
2. **Checks:** flat or silent audio becomes a "mic muted?" error rather than an empty note. Whisper
   tends to invent text on silence, so known phrases it produces there are filtered out.
3. **Cleaning** (`audio.py`, numpy, no ffmpeg): the first 0.3 s is dropped because the PDM
   microphone clicks at start. A high-pass filter at 80 Hz is applied in the frequency domain. Gain
   then adapts in 0.5 s windows toward an RMS of about 4000 (about -18 dB), capped at +30 dB, and
   quiet windows aren't boosted. The Sense microphone is quiet (levels of 100–400), which is why
   this step matters.
4. **Transcription:** `whisper-cli` from whisper.cpp v1.9.5. It is built by `build_app.py` as a
   single static binary with Metal and its shaders embedded, then bundled in the app. The model is
   `ggml-large-v3-turbo-q5_0.bin` (574 MB, MIT). The app downloads it from Hugging Face on first
   launch, shows progress in the menu, resumes after interruptions, and moves it into place only if
   its SHA-256 matches. `vocabulary` is passed as a prompt, and `language` defaults to `auto`.
5. **Fallback:** while the model is missing, `tredot-speech` (`Speech.swift`) transcribes with macOS
   speech recognition in on-device-only mode. With `auto`, it uses the Mac's current locale. It runs
   inside the app bundle, so the permission prompt is the app's.

From there:

- **Voice note** (`notes.py`): `claude -p` turns the transcript into Markdown (`# <title>`,
  `## Summary`, `## Key points`, `## To do`), and the transcript is appended under `## Transcript`.
  The `.wav` is renamed after the title. If the note explicitly asks the assistant to do something,
  such as adding an event to the calendar, a second `claude -p` runs with Google Calendar tools only.
  Reads are free, and writes need approval on the module.
- **Talk to Claude:** the bridge pushes `{"event": "talk", "wav": ...}` to the most recent
  `subscribe`r. That is a `talk.sh` channel session if one is open. Otherwise it is `agent.py`, a
  Claude Agent SDK client that uses your Claude Code login, with the module tools in-process. It
  keeps one conversation per day and writes a diary in the notes folder.

## Photos and video

**Photos:** taken at 1600×1200 (1024×768 over BLE). The sensor is mounted sideways, so the bridge
rotates the JPEG with `sips` and saves it to `media_dir` at 1200×1600. A photo returned to an agent
is downscaled to 1280 px at most.

**Video:** the module sends 640×480 frames whenever it can, each with its timestamp (`F <ms>`),
plus audio. The rate is about 8 fps over USB and about 7 over Wi-Fi. Over Wi-Fi, if more than
160 kB of frames are queued, new frames go only to the viewfinder, so voice and screen don't stall.
The bridge stores the frames, a `times.txt` and the audio. `video.py` cleans the audio and calls
`tredot-video` (`Video.swift`), which uses AVFoundation with the Mac's hardware H.264 encoder and
AAC. Each frame lasts until the next frame's timestamp, which keeps the audio in sync. Frames are
rotated a quarter turn clockwise, and the result is a 480×640 `.mp4` in `media_dir`. Video doesn't
run over BLE.

## Agent integrations

The design favors standard MCP. The Claude Code hooks are an extra: if they change, the MCP tools
still work.

### MCP server

`mcp_server.py` is a hand-written JSON-RPC stdio server that uses only the Python standard library,
so any Python 3.9+ can run it. Its tools are `approve`, `ask`, `show`, `listen`, `photo` and
`status`. They call the bridge through `module.py`. `approve` waits up to 2 minutes and returns
`approved`, `denied` or `no answer`.

With `TREDOT_CHANNEL=1` the server runs as a Claude Code *channel*: it exposes no tools and instead
pushes Talk to Claude transcripts into the session (`talk.sh`). Custom channels are a research
preview. They load only with `--dangerously-load-development-channels`, and Claude Code accepts
them only from a server that negotiates MCP revision 2025-06-18, so that is what the server
negotiates.

### Claude Code: plugin `tredot`

Choosing **Connect to Claude Code** writes a marketplace to
`~/Library/Application Support/Tredot/marketplace` (Claude Code wants a folder that doesn't move),
then runs `claude plugin marketplace add` and `claude plugin install tredot@tredot`. When the app
starts, it updates the plugin if its version changed. The repository also contains the same plugin
(`plugin/`, `.claude-plugin/`), generated by `build_plugin.py` and run with the system `python3`.

Hooks (`plugin/hooks/hooks.json`, all running `hook.py`):

| Event | On the module | Timeout |
|---|---|---|
| `UserPromptSubmit` | Thinking… | 5 s |
| `PreToolUse` | tool name and description | 5 s |
| `PostToolUse` | back to Thinking… | 5 s |
| `PermissionRequest` | the request with options. Your choice goes back to Claude | 90 s |
| `Notification` (`permission_prompt`) | Reply in Claude | 5 s |
| `Stop` | Done, plus the start of the reply | 5 s |
| `StopFailure` | the API error | 5 s |
| `MessageDisplay` | Claude's text under Thinking… | 3 s |

`PermissionRequest` flow: `hook.py` sends an `ask` to the bridge with the options taken from
Claude's own `permission_suggestions`: **Yes**, at most one "Yes, always / all edits / add folder"
rule, **No** and **In Claude**. The module shows it for `ask_timeout` seconds (60 by default). The
hook then prints a `PermissionRequest` decision: `allow` (with the rule, if chosen) or `deny` with
`interrupt`. For **In Claude**, a timeout or a missing module, the hook prints nothing and Claude
Code's normal dialog appears.

The hook's rule is that if anything goes wrong (bridge down, module unplugged, an exception), it
exits 0 silently. The module can never block Claude.

### Claude Desktop: extension `tredot.mcpb`

`build_extension.py` builds the `.mcpb` (a zip with `manifest.json`, without npx) into the app's
`Resources`. Choosing **Install in Claude Desktop…** opens it in Claude Desktop, which asks you to
confirm. The extension runs the same MCP server with
`${HOME}/Library/Application Support/Tredot/python3`. Claude Desktop gets the tools. Status and
tap approval of Claude Desktop's own permission prompts aren't available because it has no hooks.

### Gemini CLI

Choosing **Connect to Gemini CLI** writes to `~/.gemini/settings.json`:

- `mcpServers.tredot` with a 300-second timeout, because `approve` can wait for a person;
- hooks `BeforeAgent`, `BeforeTool`, `AfterTool`, `AfterAgent` and `Notification`, all running
  `hook.py`. The hook maps them to the Claude Code events, so the module shows the same status
  screens.

**Why Gemini approvals stay in its terminal:** Gemini CLI's hooks can't answer a permission prompt.
The `ToolPermission` notification only reports that one is open. The module therefore shows "Reply
in Gemini" with the tool waiting, and you answer in the terminal. Gemini can still call the
`approve` MCP tool before a consequential action, which does go through the module.

### Connecting from the app

`integrations.py` runs with the app's Python. Its actions are `status`, `claude-code`,
`claude-desktop`, `gemini` and `refresh`. Each prints JSON (`ok` or `error`, and for `status`, the
`present`, `connected`, `version` and `extension` flags). `refresh` runs at every app launch.

Agents launch the hook and the MCP server with a Python interpreter. Without the developer tools, a
Mac has no `python3`, so the agents use **`~/Library/Application Support/Tredot/python3`**. This is
a symlink to the Python inside the app, refreshed at every launch, so moving or updating the app
doesn't break the agents' settings.

## App bundle

```
Tredot.app/Contents/
  MacOS/          Tredot (menu-bar app), whisper-cli, tredot-video, tredot-speech
  Frameworks/     Sparkle.framework (updates)
  Resources/      python/     relocatable CPython 3.12 (uv's python-build-standalone) with the
                              bridge's packages from uv.lock; tests, Tk, pip removed
                  bridge/     a copy of mac/*.py plus a version file
                  firmware/   tredot.ino.bin and its version
                  tredot.mcpb, AppIcon.icns
  Info.plist      ai.tredot.mac, LSUIElement, usage strings (Bluetooth, Local Network, Speech
                  Recognition), Bonjour _tredot._tcp, Whisper model URL/SHA-256/size, Sparkle feed
                  and public key
```

- **Why an app:** macOS grants Bluetooth and Local Network to the *responsible* app of a process. A
  bridge started by a hook would have `claude` as its responsible process and would be killed on
  its first Bluetooth access. As a child of `Tredot.app`, it gets the app's permissions.
- The app is self-contained. Copied to another folder or another Mac, it works, because folders
  and the model path come from `config.py` at runtime. Python writes `.pyc` files to
  `~/Library/Caches/Tredot`, because writing inside the bundle would break its signature.
- The app is signed ad hoc (Sparkle first, then the app), because without any signature macOS
  doesn't remember the permissions. **Launch at Login** is a LaunchAgent,
  `~/Library/LaunchAgents/ai.tredot.mac.plist`.
- The app has no Dock icon, and an accessory app doesn't receive ⌘V in text fields without an Edit
  menu. The app therefore installs an invisible Edit menu so the Wi-Fi password can be pasted.

## Files on the Mac

| What | Where |
|---|---|
| Configuration, pairings | `~/.config/tredot/` (`config.json`, `pairings.json`) |
| Socket, logs, known addresses | `~/.cache/tredot/` |
| Whisper model, `python3` link, plugin marketplace | `~/Library/Application Support/Tredot/` |
| Notes, photos, videos | `notes_dir`, `media_dir` (under `~/Documents/Tredot/` by default) |
