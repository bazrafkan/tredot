// Tredot: the macOS menu-bar app. It shows the module's state and runs the actions (pair, Wi-Fi,
// firmware update, unpair) that the scripts in mac/ also offer.
//
// The real work is done by the Python bridge (bridge.py): the app starts it as a child process,
// restarts it if it stops, and talks to it over the local socket with the same requests as pair.py
// ({"op": "status"}, "pair", "wifi", "update", "unpair"). As a child of the app, the bridge gets the
// app's Bluetooth and local-network permissions (the usage descriptions are in Info.plist).
//
// Built by mac/build_app.py, which bundles everything: Python, the bridge code, the firmware.
// Written in Swift because a status item made from Python (rumps, PyObjC) did not show up in the bar.
//
// It also downloads the Whisper model (Voice, below).

import AppKit
import CryptoKit
import Sparkle

// ---------- paths: inside the app, and the folders from config.py ----------

let plist = Bundle.main.infoDictionary ?? [:]
func setting(_ key: String) -> String { plist[key] as? String ?? "" }
let resources = Bundle.main.resourcePath ?? ""
let pythonPath = resources + "/python/bin/python3"
let bridgeDir = resources + "/bridge"
let appVersion = setting("CFBundleShortVersionString")
let firmwarePath = resources + "/firmware/tredot.ino.bin"

/// A user-facing text in the app's language (en.lproj, it.lproj); the English text is the key
func L(_ english: String) -> String { NSLocalizedString(english, comment: "") }

/// The app's language, passed to the Python children so the bridge's texts match the menu
let appLanguage = Bundle.main.preferredLocalizations.first ?? "en"

/// The app's Python: .pyc files go to ~/Library/Caches, not inside the app (they would break the
/// signature); no user site-packages
func pythonProcess(_ script: String, _ args: [String]) -> Process {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: pythonPath)
    p.arguments = [bridgeDir + "/" + script] + args
    var env = ProcessInfo.processInfo.environment
    env["PYTHONPYCACHEPREFIX"] = NSHomeDirectory() + "/Library/Caches/Tredot/python"
    env["PYTHONNOUSERSITE"] = "1"
    env["TREDOT_LANG"] = appLanguage
    p.environment = env
    return p
}

/// A configuration value (~/.config/tredot/config.json or the default), read at launch: the folders
/// belong to whoever uses the app, not to whoever built it
func configValue(_ key: String, _ fallback: String) -> String {
    let p = pythonProcess("config.py", ["get", key])
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = FileHandle.nullDevice
    guard (try? p.run()) != nil else { return fallback }
    let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    p.waitUntilExit()
    let value = out.trimmingCharacters(in: .whitespacesAndNewlines)
    return p.terminationStatus == 0 && !value.isEmpty ? value : fallback
}

/// integrations.py <action>: the last line of its output is the JSON reply
func integrationsCall(_ action: String) -> [String: Any] {
    let p = pythonProcess("integrations.py", [action])
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = FileHandle.nullDevice
    guard (try? p.run()) != nil else { return ["error": L("The app's Python was not found")] }
    let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    p.waitUntilExit()
    let last = out.split(separator: "\n").last.map(String.init) ?? ""
    return (try? JSONSerialization.jsonObject(with: Data(last.utf8))) as? [String: Any]
        ?? ["error": L("integrations.py did not respond")]
}

let home = NSHomeDirectory()
let loginPlist = home + "/Library/LaunchAgents/ai.tredot.mac.plist"

/// "Launch at Login": launchd opens this app (wherever it is) when the user logs in
func writeLogin() {
    let job: [String: Any] = ["Label": "ai.tredot.mac", "RunAtLoad": true,
                              "ProgramArguments": ["/usr/bin/open", "-g", "-a", Bundle.main.bundlePath]]
    try? FileManager.default.createDirectory(atPath: (loginPlist as NSString).deletingLastPathComponent,
                                             withIntermediateDirectories: true)
    (job as NSDictionary).write(toFile: loginPlist, atomically: true)
}

let cacheDir = configValue("cache_dir", home + "/.cache/tredot")
let notesDir = configValue("notes_dir", home + "/Documents/Tredot/notes")
let socketPath = cacheDir + "/bridge.sock"
let linkNames = ["wifi": "Wi-Fi", "usb": L("cable"), "ble": "Bluetooth"]
let modelPath = configValue("whisper_model", home + "/Library/Application Support/Tredot/ggml-large-v3-turbo-q5_0.bin")
let modelURL = setting("TredotModelURL")
let modelSHA = setting("TredotModelSHA256")
let modelSize = (plist["TredotModelSize"] as? NSNumber)?.int64Value ?? 0

// ---------- the bridge: one JSON request per line on the local socket ----------

func call(_ message: [String: Any], timeout: Double) -> [String: Any]? {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { return nil }
    defer { close(fd) }
    var tv = timeval(tv_sec: Int(timeout), tv_usec: 0)
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    let path = Array(socketPath.utf8)
    withUnsafeMutableBytes(of: &addr.sun_path) { buf in
        for (i, b) in path.prefix(buf.count - 1).enumerated() { buf[i] = b }
    }
    let connected = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
    }
    guard connected == 0, var data = try? JSONSerialization.data(withJSONObject: message) else { return nil }
    data.append(10)
    let sent = data.withUnsafeBytes { write(fd, $0.baseAddress, data.count) }
    guard sent == data.count else { return nil }
    var reply = Data()
    var buf = [UInt8](repeating: 0, count: 4096)
    while reply.last != 10 {
        let n = read(fd, &buf, buf.count)
        if n <= 0 { break }
        reply.append(buf, count: n)
    }
    return (try? JSONSerialization.jsonObject(with: reply)) as? [String: Any]
}

// ---------- helpers ----------

/// The firmware version bundled in the app (build_app.py writes it next to the binary)
func builtFirmware() -> String {
    guard FileManager.default.fileExists(atPath: firmwarePath),
          let text = try? String(contentsOfFile: resources + "/firmware/version", encoding: .utf8)
    else { return "" }
    return text.trimmingCharacters(in: .whitespacesAndNewlines)
}

/// The Mac's Wi-Fi network, if macOS reveals it (without location permission it is redacted)
func macNetwork() -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/sbin/ipconfig")
    p.arguments = ["getsummary", "en0"]
    let pipe = Pipe()
    p.standardOutput = pipe
    guard (try? p.run()) != nil else { return "" }
    p.waitUntilExit()
    let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    for line in out.split(separator: "\n") where line.trimmingCharacters(in: .whitespaces).hasPrefix("SSID : ") {
        let name = line.components(separatedBy: "SSID : ")[1].trimmingCharacters(in: .whitespaces)
        return name == "<redacted>" ? "" : name
    }
    return ""
}

@discardableResult
func alert(_ title: String, _ text: String, ok: String = L("OK"), cancel: String? = nil) -> Bool {
    NSApp.activate(ignoringOtherApps: true)  // an app without a Dock icon: bring the alert to the front
    let box = NSAlert()
    box.messageText = title
    box.informativeText = text
    box.addButton(withTitle: ok)
    if let cancel = cancel { box.addButton(withTitle: cancel) }
    return box.runModal() == .alertFirstButtonReturn
}

/// An alert with a text field (hidden when secure); nil if cancelled
func ask(_ title: String, _ text: String, value: String = "", ok: String, secure: Bool = false) -> String? {
    NSApp.activate(ignoringOtherApps: true)
    let box = NSAlert()
    box.messageText = title
    box.informativeText = text
    box.addButton(withTitle: ok)
    box.addButton(withTitle: L("Cancel"))
    let field = secure ? NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
                       : NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
    field.stringValue = value
    box.accessoryView = field
    box.window.initialFirstResponder = field
    guard box.runModal() == .alertFirstButtonReturn else { return nil }
    return secure ? field.stringValue : field.stringValue.trimmingCharacters(in: .whitespaces)
}

// ---------- the menu-bar icon: the three dots ----------

/// Three dots on an arc, like the app icon and the module's screen. Hollow: module disconnected;
/// first one filled: connected; all filled: connected and encrypted. The arc spins during an action
/// (pairing, Wi-Fi, firmware). A template image, so macOS tints it for a light or dark bar.
func dotsIcon(filled: Int, angle: CGFloat) -> NSImage {
    let image = NSImage(size: NSSize(width: 20, height: 18), flipped: true) { _ in
        let arc: [(CGFloat, CGFloat)] = [(-0.68, -0.2), (0, 0.32), (0.68, -0.2)]
        let unit: CGFloat = 8, r: CGFloat = 2.3
        NSColor.black.set()
        for (i, p) in arc.enumerated() {
            let x = 10 + (p.0 * cos(angle) - p.1 * sin(angle)) * unit
            let y = 9 + (p.0 * sin(angle) + p.1 * cos(angle)) * unit
            if i < filled {
                NSBezierPath(ovalIn: NSRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r)).fill()
            } else {  // hollow: the ring stays within the same circle
                let ring = NSBezierPath(ovalIn: NSRect(x: x - r + 0.5, y: y - r + 0.5, width: 2 * r - 1, height: 2 * r - 1))
                ring.lineWidth = 1
                ring.stroke()
            }
        }
        return true
    }
    image.isTemplate = true
    return image
}

// ---------- voice: the Whisper model ----------

/// The Whisper model (574 MB) is not in the bundle: the app downloads it on first launch, with the
/// progress in the menu, and installs it only if its SHA-256 matches. Until then voice notes are
/// transcribed by macOS speech recognition (tredot-speech, notes.py).
final class Voice: NSObject, URLSessionDownloadDelegate {
    enum State { case ready, downloading(Int64), checking, failed(String) }
    private(set) var state: State = .failed("")
    var changed: () -> Void = {}
    private var resumeData: Data?  // after an interruption the download resumes where it stopped
    private lazy var session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)

    var ready: Bool { FileManager.default.fileExists(atPath: modelPath) }

    func start() {
        if ready { set(.ready); return }
        guard let url = URL(string: modelURL) else { set(.failed(L("no download address"))); return }
        let folder = URL(fileURLWithPath: (modelPath as NSString).deletingLastPathComponent)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let free = (try? folder.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage ?? Int64.max
        if free < modelSize * 2 {  // the download and its copy, for a moment
            set(.failed(String(format: L("needs %@ MB of free disk space"), "\(modelSize * 2 / 1_000_000)")))
            return
        }
        set(.downloading(0))
        let task = resumeData.map { session.downloadTask(withResumeData: $0) } ?? session.downloadTask(with: url)
        resumeData = nil
        task.resume()
    }

    private func set(_ s: State) {
        DispatchQueue.main.async { self.state = s; self.changed() }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData _: Int64,
                    totalBytesWritten written: Int64, totalBytesExpectedToWrite _: Int64) {
        set(.downloading(written))
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {
        if let http = downloadTask.response as? HTTPURLResponse, http.statusCode != 200 {
            set(.failed(String(format: L("the server answered %@"), "\(http.statusCode)")))
            return
        }
        // the file at location is deleted as soon as this function returns: move it right away
        let part = modelPath + ".part"
        try? FileManager.default.removeItem(atPath: part)
        guard (try? FileManager.default.moveItem(at: location, to: URL(fileURLWithPath: part))) != nil else {
            set(.failed(L("could not save it")))
            return
        }
        set(.checking)
        if sha256(part) == modelSHA, (try? FileManager.default.moveItem(atPath: part, toPath: modelPath)) != nil {
            set(.ready)
        } else {
            try? FileManager.default.removeItem(atPath: part)
            set(.failed(L("the downloaded file is not the right one")))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let error = error as NSError? else { return }
        resumeData = error.userInfo[NSURLSessionDownloadTaskResumeData] as? Data
        set(.failed(L("connection interrupted")))
    }

    /// The file's SHA-256, in 8 MB chunks (not all in memory)
    private func sha256(_ path: String) -> String {
        guard let file = FileHandle(forReadingAtPath: path) else { return "" }
        defer { try? file.close() }
        var hash = SHA256()
        while let chunk = try? file.read(upToCount: 8 << 20), !chunk.isEmpty { hash.update(data: chunk) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

// ---------- the app ----------

final class TredotApp: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    var rows: [NSMenuItem] = []
    var pairItem = NSMenuItem(), wifiItem = NSMenuItem(), updateItem = NSMenuItem()
    var unpairItem = NSMenuItem(), loginItem = NSMenuItem(), voiceItem = NSMenuItem()
    var codeItem = NSMenuItem(), desktopItem = NSMenuItem(), geminiItem = NSMenuItem()
    var agents: [String: Any] = [:]  // integrations.py status
    var spin: CGFloat = 0  // the arc's angle during an action
    let voice = Voice()
    // Sparkle: checks once a day on its own, and when asked from the menu (Info.plist: SUFeedURL)
    let updater = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
    var bridge: Process?
    var status: [String: Any] = [:]
    var busy: String?  // the running action, in words: "Pairing…"

    func applicationDidFinishLaunching(_ note: Notification) {
        editMenu()
        item.button?.image = dotsIcon(filled: 0, angle: 0)
        Timer.scheduledTimer(withTimeInterval: 1.0 / 15, repeats: true) { [weak self] _ in
            guard let self = self, self.busy != nil else { return }
            self.spin += 0.15
            self.drawIcon()
        }
        let menu = NSMenu()
        menu.autoenablesItems = false  // refresh() enables and disables the items
        rows = (0..<3).map { _ in add(menu, "…", nil) }
        voiceItem = add(menu, "…", #selector(retryVoice))
        menu.addItem(.separator())
        pairItem = add(menu, L("Pair This Mac…"), #selector(pair))
        wifiItem = add(menu, L("Connect to Wi-Fi…"), #selector(wifi))
        updateItem = add(menu, L("Update Firmware"), #selector(update))
        menu.addItem(.separator())
        codeItem = add(menu, L("Connect to Claude Code"), #selector(connectCode))
        desktopItem = add(menu, L("Install in Claude Desktop…"), #selector(connectDesktop))
        geminiItem = add(menu, L("Connect to Gemini CLI"), #selector(connectGemini))
        menu.addItem(.separator())
        unpairItem = add(menu, L("Unpair…"), #selector(unpair))
        loginItem = add(menu, L("Launch at Login"), #selector(toggleLogin))
        menu.addItem(.separator())
        let check = NSMenuItem(title: L("Check for Updates…"),
                               action: #selector(SPUStandardUpdaterController.checkForUpdates(_:)), keyEquivalent: "")
        check.target = updater
        menu.addItem(check)
        menu.addItem(.separator())
        add(menu, L("Take a Screenshot of the Module"), #selector(takeScreenshot))
        add(menu, L("Open Notes"), #selector(openNotes))
        add(menu, L("Open Logs"), #selector(openLogs))
        menu.addItem(.separator())
        add(menu, L("Quit"), #selector(quit))
        item.menu = menu
        menu.delegate = self  // on every open: which agents are connected
        Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
        tick()
        fixLogin()
        voice.changed = { [weak self] in self?.refresh() }
        voice.start()
        // the stable link to this app's Python and an up-to-date plugin (integrations.py)
        DispatchQueue.global().async {
            _ = integrationsCall("refresh")
            self.checkAgents()
        }
    }

    func menuWillOpen(_ menu: NSMenu) { DispatchQueue.global().async { self.checkAgents() } }

    func checkAgents() {
        let s = integrationsCall("status")
        DispatchQueue.main.async { self.agents = s; self.refresh() }
    }

    /// An invisible Edit menu: in an app without a Dock icon, ⌘V, ⌘C, ⌘X, ⌘A, ⌘Z reach text fields
    /// only this way (without it the Wi-Fi name and password could not be pasted)
    func editMenu() {
        let edit = NSMenu(title: L("Edit"))
        edit.addItem(withTitle: L("Undo"), action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: L("Cut"), action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: L("Copy"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: L("Paste"), action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: L("Select All"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let main = NSMenu()
        let holder = NSMenuItem()
        holder.submenu = edit
        main.addItem(holder)
        NSApp.mainMenu = main
    }

    @discardableResult
    func add(_ menu: NSMenu, _ title: String, _ action: Selector?) -> NSMenuItem {
        let it = NSMenuItem(title: title, action: action, keyEquivalent: "")
        it.target = self
        it.isEnabled = action != nil
        menu.addItem(it)
        return it
    }

    // ---------- the bridge, always running ----------

    func ensureBridge() {
        if bridge?.isRunning == true { return }
        if let running = call(["op": "status"], timeout: 1) {
            // one is already running: fine if it is our version. An older one, left running after an
            // app update, is asked to quit; if it does not, its process is killed
            if running["bridge"] as? String == appVersion { return }
            _ = call(["op": "quit"], timeout: 2)
            Thread.sleep(forTimeInterval: 0.5)
            if call(["op": "status"], timeout: 1) != nil {
                let kill = Process()
                kill.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
                kill.arguments = ["-f", bridgeDir + "/bridge.py"]
                try? kill.run()
                kill.waitUntilExit()
                Thread.sleep(forTimeInterval: 0.5)
            }
        }
        let p = pythonProcess("bridge.py", [])
        try? FileManager.default.createDirectory(atPath: cacheDir, withIntermediateDirectories: true)
        let errPath = cacheDir + "/bridge.err"
        if !FileManager.default.fileExists(atPath: errPath) {
            FileManager.default.createFile(atPath: errPath, contents: nil)
        }
        if let err = FileHandle(forWritingAtPath: errPath) {
            err.seekToEndOfFile()
            p.standardError = err
        }
        p.standardOutput = FileHandle.nullDevice
        try? p.run()
        bridge = p
    }

    // ---------- state, every second ----------

    func tick() {
        DispatchQueue.global().async {
            self.ensureBridge()
            let s = call(["op": "status"], timeout: 1) ?? [:]
            DispatchQueue.main.async { self.status = s; self.refresh() }
        }
    }

    func refresh() {
        let module = status["module"] as? [String: Any] ?? [:]
        let link = status["link"] as? String
        let connected = status["connected"] as? Bool ?? false
        // encrypted, but with a key the module does not have (it was paired again elsewhere): not a
        // working link, so the menu offers to re-pair
        let keyOk = status["key_ok"] as? Bool ?? true
        let secure = (status["secure"] as? Bool ?? false) && keyOk
        let firmware = module["firmware"] as? String ?? "?"
        let built = builtFirmware()
        drawIcon()
        rows[0].title = connected ? String(format: L("%@ · firmware %@"), module["id"] as? String ?? L("Module"), firmware)
                                  : L("Module not connected")
        let via = linkNames[link ?? ""] ?? link ?? "?"
        rows[1].title = !connected ? L("Searching over Wi-Fi, cable and Bluetooth…")
                      : !keyOk ? L("Key differs from the module: re-pair with the cable")
                      : String(format: secure ? L("Connected via %@ · encrypted") : L("Connected via %@ · not paired"), via)
        if let wifi = module["wifi"] as? String {
            rows[2].title = busy ?? String(format: L("Module Wi-Fi: %@"), wifiText(wifi))
        } else {
            rows[2].title = busy ?? L("Wi-Fi not set up")
        }
        let idle = busy == nil
        let cable = ((try? FileManager.default.contentsOfDirectory(atPath: "/dev")) ?? [])
            .contains { $0.hasPrefix("cu.usbmodem") }
        // already paired: nothing to do (to pair again: unpair, then pair)
        pairItem.isEnabled = idle && cable && !secure
        let again = keyOk ? L("Pair This Mac…") : L("Re-pair This Mac…")
        pairItem.title = secure ? L("✓ Paired with This Mac")
                       : cable ? again : String(format: L("%@ (needs the cable)"), again)
        wifiItem.isEnabled = idle && secure
        let canUpdate = idle && secure && !built.isEmpty && built != firmware && link != "ble"
        updateItem.isEnabled = canUpdate
        updateItem.title = canUpdate ? String(format: L("Update Firmware to %@"), built)
                         : (!built.isEmpty && built == firmware ? L("Firmware Up to Date") : L("Update Firmware"))
        unpairItem.isEnabled = idle && secure
        loginItem.state = FileManager.default.fileExists(atPath: loginPlist) ? .on : .off
        agentItem(codeItem, "claude_code", "Claude Code", todo: L("Connect to Claude Code"))
        agentItem(desktopItem, "claude_desktop", "Claude Desktop", todo: L("Install in Claude Desktop…"))
        agentItem(geminiItem, "gemini", "Gemini CLI", todo: L("Connect to Gemini CLI"))
        if agents["extension"] as? Bool == false { desktopItem.isEnabled = false }
        voiceItem.isEnabled = false
        switch voice.state {
        case .ready:
            voiceItem.title = L("Voice: Whisper, on this Mac")
        case .downloading(let done):
            let percent = modelSize > 0 ? done * 100 / modelSize : 0
            voiceItem.title = String(format: L("Downloading voice model: %@%% (%@ of %@ MB)"),
                                     "\(percent)", "\(done / 1_000_000)", "\(modelSize / 1_000_000)")
        case .checking:
            voiceItem.title = L("Downloading voice model: checking the file…")
        case .failed(let why):
            voiceItem.title = String(format: L("Voice model not downloaded: %@. Retry"), why)
            voiceItem.isEnabled = true
        }
    }

    /// The module's Wi-Fi state (protocol tokens, wifi_link.h) in the app's language; the reason
    /// after "error: " and the IP address are shown as they come
    func wifiText(_ state: String) -> String {
        if state.hasPrefix("error: ") { return String(format: L("error: %@"), String(state.dropFirst(7))) }
        return ["off", "connecting", "address"].contains(state) ? L(state) : state
    }

    func drawIcon() {
        let connected = status["connected"] as? Bool ?? false
        let secure = status["secure"] as? Bool ?? false
        item.button?.image = dotsIcon(filled: connected && secure ? 3 : connected ? 1 : 0,
                                      angle: busy != nil ? spin : 0)
    }

    /// The progress of the running action, shown in the menu (callable from any thread)
    func step(_ text: String) {
        DispatchQueue.main.async {
            if self.busy != nil { self.busy = text; self.refresh() }
        }
    }

    /// A long action in the background; when it ends, an alert with the outcome
    func background(_ label: String, _ job: @escaping () -> (String, String)) {
        busy = label
        refresh()
        DispatchQueue.global().async {
            let (title, text) = job()
            DispatchQueue.main.async {
                self.busy = nil
                self.refresh()
                alert(title, text)
            }
        }
    }

    // ---------- actions ----------

    @objc func pair() {
        alert(L("Pairing"), L("The module will show “Pair?”: tap “Yes” twice within 60 seconds. "
                              + "From then on everything is encrypted."))
        let name = Host.current().localizedName ?? "Mac"
        background(L("Pairing…")) {
            let r = call(["op": "pair", "name": name], timeout: 90) ?? [:]
            if let id = r["device"] as? String {
                return (L("Paired"), String(format: L("%@ is paired with this Mac. You can now connect it to Wi-Fi."), id))
            }
            return (L("Not paired"), r["error"] as? String ?? L("the bridge is not responding"))
        }
    }

    @objc func wifi() {
        guard let ssid = ask(L("Module Wi-Fi"), L("Network name (the module only sees 2.4 GHz networks):"),
                             value: macNetwork(), ok: L("Next")), !ssid.isEmpty,
              let password = ask(L("Module Wi-Fi"), String(format: L("Password for %@ (not stored on this Mac):"), ssid),
                                 ok: L("Connect"), secure: true)
        else { return }
        background(L("Connecting to Wi-Fi…")) {
            let r = call(["op": "wifi", "ssid": ssid, "password": password], timeout: 10) ?? [:]
            if let error = r["error"] as? String { return (L("Wi-Fi not set up"), error) }
            var state = ""
            self.step(String(format: L("Looking for “%@”…"), ssid))
            for _ in 0..<60 {  // the module reports its progress with I wifi lines (wifi_link.h)
                Thread.sleep(forTimeInterval: 0.5)
                let module = call(["op": "status"], timeout: 1)?["module"] as? [String: Any]
                state = module?["wifi"] as? String ?? ""
                if state == "address" { self.step(String(format: L("Joined “%@”, getting an address…"), ssid)) }
                if !state.isEmpty && !["connecting", "address", "off"].contains(state) { break }
            }
            if state.first?.isNumber == true {
                return (L("Wi-Fi connected"), String(format: L("The module is on %@ (%@)."), ssid, state))
            }
            if state.hasPrefix("error: ") {  // the module knows why (wifi_link.h)
                let why = String(state.dropFirst(7))
                return (L("Wi-Fi not connected"),
                        String(format: L("%@ Network: “%@”."), why.prefix(1).uppercased() + why.dropFirst(), ssid))
            }
            return (L("Wi-Fi not connected"),
                    String(format: L("The module could not join “%@” within 30 seconds. Check the name (including "
                                     + "capitals and spaces) and the password, and that the network is 2.4 GHz."), ssid))
        }
    }

    @objc func update() {
        let version = builtFirmware()
        let current = (status["module"] as? [String: Any])?["firmware"] as? String ?? "?"
        guard alert(L("Update the firmware?"),
                    String(format: L("The module goes from %@ to %@. At the end, confirm on the module by tapping "
                                     + "“Yes” twice. If anything goes wrong before then, the current firmware stays."),
                           current, version),
                    ok: L("Update"), cancel: L("Cancel")) else { return }
        background(L("Updating the firmware…")) {
            let r = call(["op": "update", "path": firmwarePath, "version": version], timeout: 900) ?? [:]
            if r.keys.contains("error"), let error = r["error"] as? String {
                return (L("Not updated"), String(format: L("%@. The previous firmware stays."), error))
            }
            if r.isEmpty { return (L("Not updated"), L("The bridge did not respond. The previous firmware stays.")) }
            return (L("Updated"), String(format: L("The module restarts with firmware %@."), version))
        }
    }

    @objc func unpair() {
        guard alert(L("Unpair the module?"), L("The module forgets this Mac and the Wi-Fi network, and will "
                                               + "only work over the cable until you pair it again."),
                    ok: L("Unpair"), cancel: L("Cancel"))
        else { return }
        background(L("Unpairing…")) {
            let ok = call(["op": "unpair"], timeout: 15)?["ok"] as? Bool ?? false
            return ok ? (L("Unpaired"), L("The module now works only over the cable."))
                      : (L("The module did not respond"), L("Try again with the module connected."))
        }
    }

    /// "Launch at Login" turned on by another copy of the app (e.g. one in ~/Applications before
    /// installing from the .dmg): point it at this one
    func fixLogin() {
        guard let job = NSDictionary(contentsOfFile: loginPlist) as? [String: Any],
              let args = job["ProgramArguments"] as? [String], args.last != Bundle.main.bundlePath
        else { return }
        var fixed = job
        fixed["ProgramArguments"] = ["/usr/bin/open", "-g", "-a", Bundle.main.bundlePath]
        (fixed as NSDictionary).write(toFile: loginPlist, atomically: true)
    }

    @objc func toggleLogin() {
        let fm = FileManager.default
        if fm.fileExists(atPath: loginPlist) {
            try? fm.removeItem(atPath: loginPlist)
        } else {
            writeLogin()
        }
        refresh()
    }

    /// An agent's menu item: ✓ when connected (still clickable: connects again), disabled if missing
    func agentItem(_ it: NSMenuItem, _ key: String, _ name: String, todo: String) {
        let s = agents[key] as? [String: Any] ?? [:]
        let present = s["present"] as? Bool ?? false
        let connected = s["connected"] as? Bool ?? false
        it.title = !present ? String(format: L("%@ is not installed"), name)
                 : connected ? String(format: L("✓ Connected to %@"), name) : todo
        it.isEnabled = busy == nil && present
    }

    func connect(_ action: String, _ label: String, _ title: String) {
        background(label) {
            let r = integrationsCall(action)
            DispatchQueue.global().async { self.checkAgents() }
            if let error = r["error"] as? String { return (L("Not connected"), error) }
            return (title, r["ok"] as? String ?? L("Done."))
        }
    }

    @objc func connectCode() { connect("claude-code", L("Connecting Claude Code…"), L("Claude Code connected")) }
    @objc func connectDesktop() { connect("claude-desktop", L("Opening Claude Desktop…"), L("Almost done")) }
    @objc func connectGemini() { connect("gemini", L("Connecting Gemini CLI…"), L("Gemini CLI connected")) }

    @objc func retryVoice() { voice.start() }

    /// What the module's screen shows, as a PNG in the media folder, opened right away
    @objc func takeScreenshot() {
        DispatchQueue.global().async {
            let reply = call(["op": "screenshot"], timeout: 30) ?? [:]
            DispatchQueue.main.async {
                if let path = reply["path"] as? String {
                    NSWorkspace.shared.open(URL(fileURLWithPath: path))
                } else {
                    alert(L("No screenshot"), reply["error"] as? String ?? L("the bridge is not responding"))
                }
            }
        }
    }

    @objc func openNotes() { NSWorkspace.shared.open(URL(fileURLWithPath: notesDir)) }
    @objc func openLogs() { NSWorkspace.shared.open(URL(fileURLWithPath: cacheDir)) }

    @objc func quit() {
        bridge?.terminate()  // only the bridge we started; one started by a hook stops on its own
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // menu bar only, no Dock icon
let delegate = TredotApp()
app.delegate = delegate
app.run()
