# Roadmap

## Direction

The core of Tredot is **approval**. The screen shows the exact action ("`git push origin main`",
"send this email to …") and you tap Yes or No, away from the keyboard. Voice, photos and talking to
the agent are secondary until real use shows they matter. Four principles guide the work:

- **MCP first.** The product must work through standard MCP alone. Claude Code hooks and channels are
  extras, kept in the plugin, so changes there don't break the product.
- **"No" is the safe default.** No answer, an error or a lost link never approves anything, and
  options need two taps.
- **No terminal for users.** Users install a `.dmg` and use a menu, with no Python, Homebrew or
  esptool.
- **Local first.** Tredot runs no servers. Data stays on the user's Mac.

## Done

- **Foundations:** a single configuration file, no machine-specific paths, a protocol version in the
  greeting, and automated tests with a fake module.
- **Links:** USB, Wi-Fi (mDNS discovery, roaming between access points, error reasons in the menu)
  and BLE (ADPCM audio). The bridge switches between them automatically.
- **Security:** pairing over the cable with on-screen confirmation, AES-256-GCM on every link with
  per-session keys and replay protection, and approvals accepted only when encrypted.
- **OTA firmware updates** over USB or Wi-Fi, with on-screen confirmation and automatic rollback.
- **Menu-bar app** (Swift) that runs the bridge and handles pairing, Wi-Fi, firmware updates, agent
  setup and start at login.
- **Agents:** English MCP tools including `approve`, a Claude Code plugin with tap approvals, a
  Claude Desktop extension, and Gemini CLI with status on screen.
- **Self-contained package:** Python, Whisper, the video muxer and the speech fallback inside the
  app. No ffmpeg (replaced by `sips`, numpy and AVFoundation). The Whisper model downloads on first
  launch and is checked with SHA-256. The app ships as a `.dmg`.
- **App updates** through Sparkle, signed with EdDSA and published on GitHub Releases.

## Next

**Release quality**

- [ ] **Clean-Mac test:** with a new macOS user and only the `.dmg`, approve an action in Claude
  Code, Claude Desktop and Gemini CLI, and transcribe a voice note.
- [ ] **Developer ID signing and notarization,** so there's no Gatekeeper override and permissions
  survive updates.
- [ ] Test **Cursor, Codex CLI and VS Code** with the MCP server.
- [ ] Remove the duplicate tools that appear in Claude Desktop's Code tab when both the plugin and
  the extension are installed.

**Firmware updates**

- [ ] **Firmware signature verification** on the module, so only images we signed install.
- [ ] Offer **only newer** firmware. Today the menu offers any bundled version that differs from
  the module's.
- [ ] **Notify** when a module update is available, instead of only showing it in the menu.

**Security**

- [ ] Keep the Mac's pairing key in the macOS Keychain.
- [ ] Enable flash encryption on the module, so a stolen module doesn't reveal its key.
- [ ] Add a factory reset from the module itself.

**Module**

- [ ] Smoother video: binary frames instead of base64, and capture in a separate task.
- [x] The screen and backlight turn off after 30 seconds of inactivity (firmware 1.2.0).
- [ ] Larger touch targets for lists.
- [ ] Battery: check that a LiPo on the Round Display charges over USB and that the firmware can
  read its level.
- [ ] Re-measure BLE throughput with the antenna attached.
- [ ] Set up Wi-Fi without a paired Mac.

**Later, if needed**

- [ ] MCP over local HTTP, only when an agent that matters requires it, with a token and an origin
  check. A server on `localhost` is reachable from web pages, which is a real risk for a device that
  approves actions.
- [ ] Windows and Intel Macs, if pilot users ask for them.

## Pilot

A pilot with 5–10 people who use agents every day, including some who don't use Claude, will decide
what the product is. Each pilot module is the same development boards in a 3D-printed case. What
to measure, recorded locally and shared only with consent:

- approvals made on the module versus on the computer
- who still uses it after 4 weeks
- how much voice, photos and Talk to Claude are used
- how often the answer is "No", and why

If approvals dominate and most people keep using it, the next step is the hardware product. If voice
and photos dominate, the product needs rethinking.

## Hardware product

Only if the pilot supports it:

- [ ] A custom board around a pre-certified ESP32-S3 module (same firmware), with a round screen,
  touch and microphone. The camera stays only if the pilot shows it's used.
- [ ] An enclosure, USB-C power and optionally a battery.
- [ ] A sound for "approval needed", which is likely more useful than voice output.
- [ ] Certifications for sale: CE/RED including its cybersecurity requirements (EN 18031), the Cyber
  Resilience Act, RoHS/WEEE and GPSR in the EU, and FCC in the US.
