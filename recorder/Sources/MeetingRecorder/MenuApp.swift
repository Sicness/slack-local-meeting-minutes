import AppKit
import RecorderCore
import ServiceManagement
import UserNotifications

/// Menu bar app. Slack calls are recorded automatically: start when Slack holds the mic longer than
/// `start_after_seconds`, stop when it has released it for `stop_after_seconds`. Manual recording remains.
/// After stopping, `ma process` runs from a queue, never during a call.
@MainActor
final class MenuApp: NSObject, NSApplicationDelegate, NSMenuDelegate, UNUserNotificationCenterDelegate {
    static func run() -> Never {
        let app = NSApplication.shared
        let delegate = MenuApp()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
        exit(0)
    }

    private static let autoRecordKey = "autoRecord"
    private static let noticeAcceptedKey = "recordingNoticeAccepted"
    /// A test instance with its own MA_HOME must not change the real app's choices.
    private let defaults: UserDefaults = {
        let custom = ProcessInfo.processInfo.environment["MA_HOME"] ?? ""
        return custom.isEmpty ? .standard : UserDefaults(suiteName: "local.meetingassistant.recorder.test") ?? .standard
    }()
    private static let discardAction = "discard"
    private static let recordingCategory = "recording"
    /// Contract with the Ollama error in pipeline/src/ma/minutes.py. The Russian text is deliberate: older meta.json files hold it.
    private static let ollamaUnavailableMarkers = ["Ollama is unavailable", "Ollama недоступна"]

    private let log = AppLog()
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var config = RecorderConfig()
    private var session: RecordingSession?
    private var sessionKind: AutoRecordPolicy.Kind?
    private var policy = AutoRecordPolicy()
    private var runner: ProcessingRunner?
    private var ui: UIServer?
    private var waitingForOllama: Set<String> = []
    private var cleanup = CleanupSchedule()
    private var uiTimer: Timer?
    private var uiTries = 0
    private var lastSaved: URL?
    private var configWarnings: [String] = []
    private var setupWarnings: [String] = []   // survive config reloads
    private var timers: [Timer] = []
    private var ticks = 0
    private var lockFD: Int32 = -1
    private var signalSources: [DispatchSourceSignal] = []

    // Menu items that change.
    private let statusLine = NSMenuItem()
    private let detailLine = NSMenuItem()
    private let processingLine = NSMenuItem()
    private let warningsHeader = NSMenuItem()
    private var warningItems: [NSMenuItem] = []
    private let micPermissionItem = NSMenuItem()
    private let capturePermissionItem = NSMenuItem()
    private let notificationsItem = NSMenuItem()
    private var notificationsDenied = false
    private let recordItem = NSMenuItem()
    private let discardItem = NSMenuItem()
    private let autoItem = NSMenuItem()
    private let loginItem = NSMenuItem()
    private let lastItem = NSMenuItem()
    private let openUIItem = NSMenuItem()
    private let openFolderItem = NSMenuItem()
    private let quitItem = NSMenuItem()

    private var autoRecord: Bool {
        get {
            RecordingConsent.autoRecordActive(stored: defaults.object(forKey: Self.autoRecordKey) as? Bool,
                                              noticeAccepted: noticeAccepted)
        }
        set { defaults.set(newValue, forKey: Self.autoRecordKey) }
    }

    private var noticeAccepted: Bool {
        get { defaults.bool(forKey: Self.noticeAcceptedKey) }
        set { defaults.set(newValue, forKey: Self.noticeAcceptedKey) }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        Paths.securePermissions()
        guard acquireSingleInstanceLock() else {
            log.info("recorder already running, exiting")
            NSApp.terminate(nil)
            return
        }
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev"
        log.info("recorder \(version) started, pid \(getpid())")
        reloadConfig()
        setUpProcessing()
        setUpNotifications()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.imagePosition = .imageLeading
        buildMenu()
        statusItem.menu = menu
        refresh()

        for sig in [SIGINT, SIGTERM] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { MainActor.assumeIsolated { NSApp.terminate(nil) } }
            source.resume()
            signalSources.append(source)
        }

        // .common keeps timers running while the menu is open
        let ui = Timer(timeInterval: 1, repeats: true) { _ in MainActor.assumeIsolated { self.refresh() } }
        let watch = Timer(timeInterval: 0.5, repeats: true) { _ in MainActor.assumeIsolated { self.watchTick() } }
        for t in [ui, watch] { RunLoop.main.add(t, forMode: .common) }
        timers = [ui, watch]

        requestPermissions()
        let stored = defaults.object(forKey: Self.autoRecordKey) as? Bool
        if RecordingConsent.needsNoticeAtLaunch(stored: stored, noticeAccepted: noticeAccepted) {
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.autoRecord = self.ensureRecordingNotice()
                    self.policy.enabled = self.autoRecord
                    self.refresh()
                }
            }
        }
    }

    /// Shown once, before the first recording of any kind.
    private func ensureRecordingNotice() -> Bool {
        if noticeAccepted { return true }
        NSApp.activate()
        let a = NSAlert()
        a.alertStyle = .warning
        a.messageText = tr(.noticeTitle)
        a.informativeText = tr(.noticeText)
        a.addButton(withTitle: tr(.noticeAccept))
        a.addButton(withTitle: tr(.noticeDecline))
        let accepted = a.runModal() == .alertFirstButtonReturn
        if accepted { noticeAccepted = true }
        log.info("recording consent notice: \(accepted ? "accepted" : "declined")")
        return accepted
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        // Quit, restart or log out: the recording is saved and processed on next launch.
        if let session {
            lastSaved = session.stop(save: true)
            self.session = nil
        }
        runner?.detach()
        ui?.stop()
        log.info("recorder exiting")
        return .terminateNow
    }

    private func reloadConfig() {
        let (c, warnings) = RecorderConfig.load()
        let languageChanged = c.language != config.language
        config = c
        configWarnings = warnings + setupWarnings
        warnings.forEach { log.warn($0) }
        policy.startAfter = c.startAfterSeconds
        policy.stopAfter = c.stopAfterSeconds
        policy.enabled = autoRecord
        if languageChanged { applyLanguage() }
    }

    private func tr(_ key: L10n.Key, _ args: String...) -> String {
        L10n.text(key, language: config.language, args: args)
    }

    /// The config is reloaded before each recording, so the language can change while running.
    private func applyLanguage() {
        guard statusItem != nil else { return }   // first launch: buildMenu and setUpNotifications set the text
        setMenuTitles()
        registerNotificationCategories()
        refresh()
    }

    // MARK: - Slack call and processing queue

    private func watchTick() {
        ticks += 1
        let me = getpid()
        let holds = AudioProcesses.matching(config.bundleIDPrefixes).contains { $0.pid != me && $0.isRunningInput }
        let now = ProcessInfo.processInfo.systemUptime
        switch policy.update(now: now, slackHoldsMic: holds, recording: session == nil ? nil : sessionKind) {
        case .start:
            log.info("Slack call: mic held longer than \(Int(config.startAfterSeconds)) s, starting recording")
            startRecording(trigger: "auto")
        case .stop:
            log.info("Slack call: mic free for \(Int(config.stopAfterSeconds)) s, stopping recording")
            stopRecording()
        case .none:
            break
        }
        if ticks % 2 == 0, let runner {
            // During any recording or call, processing doesn't start and stays paused.
            if config.cleanupDaily, ticks % 120 == 0,
               cleanup.due(lastSuccess: CleanupSchedule.lastSuccess(), now: Date()) {
                cleanup.lastAttempt = Date()
                runner.requestCleanup()
            }
            if let done = runner.tick(callActive: policy.callActive || session != nil) {
                if done.id == ProcessingRunner.cleanupID {
                    if done.status == 0 { CleanupSchedule.markSuccess() }
                    else { log.warn("audio cleanup failed (code \(done.status)), retrying in an hour, see process.log") }
                } else {
                    notifyProcessed(id: done.id, status: done.status)
                }
            }
        }
    }

    private func setUpProcessing() {
        guard config.processAfterStop || config.cleanupDaily else {
            log.info("processing and daily cleanup are off")
            return
        }
        guard let uv = PipelineLocation.uv(config: config), let pipeline = PipelineLocation.pipelineDir(config: config) else {
            let text = tr(.warnNoPipeline)
            log.warn(L10n.text(.warnNoPipeline, language: L10n.defaultLanguage))
            setupWarnings.append(text)
            configWarnings.append(text)
            return
        }
        let r = ProcessingRunner(uv: uv, pipeline: pipeline, log: log)
        if config.processAfterStop {
            for id in PendingRecordings.find() { r.enqueue(id) }
        } else {
            log.info("processing after recording is off (process_after_stop = false)")
        }
        runner = r
        log.info("processing after recording: \(uv) -> \(pipeline)")
    }

    // MARK: - Recording

    private func startRecording(trigger: String) {
        reloadConfig()
        do {
            let s = try RecordingSession(config: config, trigger: trigger, log: log)
            s.start()
            session = s
            sessionKind = trigger == "auto" ? .auto : .manual
            if trigger == "auto" {
                notify(id: "rec-\(s.id)", title: tr(.notifyRecordingTitle),
                       body: tr(.notifyRecordingBody),
                       category: Self.recordingCategory, info: ["recording": s.id])
            }
        } catch {
            log.error("failed to start recording: \(error)")
            if trigger == "manual" { alert(tr(.alertStartFailed), "\(error)") }
            else { policy.suppressUntilCallEnds() }   // don't retry every half second
        }
        refresh()
    }

    private func stopRecording(save: Bool = true) {
        guard let session else { return }
        let saved = session.stop(save: save)
        if save { lastSaved = saved }
        if saved != nil, config.processAfterStop { runner?.enqueue(session.id) }
        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: ["rec-\(session.id)"])
        self.session = nil
        sessionKind = nil
        refresh()
    }

    // MARK: - Notifications

    private func setUpNotifications() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        registerNotificationCategories()
        let log = self.log
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            log.info("notifications: \(granted ? "allowed" : "not allowed")" + (error.map { " (\($0.localizedDescription))" } ?? ""))
            DispatchQueue.main.async { MainActor.assumeIsolated { self.notificationsDenied = !granted; self.refresh() } }
        }
    }

    private func registerNotificationCategories() {
        let discard = UNNotificationAction(identifier: Self.discardAction, title: tr(.notifyDiscardAction), options: [.destructive])
        UNUserNotificationCenter.current().setNotificationCategories([
            UNNotificationCategory(identifier: Self.recordingCategory, actions: [discard], intentIdentifiers: [])
        ])
    }

    private func notify(id: String, title: String, body: String, category: String? = nil, info: [String: String] = [:]) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.userInfo = info
        if let category { content.categoryIdentifier = category }
        let log = self.log
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: id, content: content, trigger: nil)) { error in
            if let error { log.warn("notification not shown: \(error.localizedDescription)") }
        }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .list]
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse) async {
        let action = response.actionIdentifier
        let info = response.notification.request.content.userInfo
        let recording = info["recording"] as? String
        let note = info["note"] as? String
        await MainActor.run { self.handleNotification(action: action, recording: recording, note: note) }
    }

    private func handleNotification(action: String, recording: String?, note: String?) {
        if action == Self.discardAction, let recording, let session, session.id == recording {
            log.info("recording \(recording): Don't Record, deleting and not recording until the call ends")
            policy.suppressUntilCallEnds()
            stopRecording(save: false)
            return
        }
        if action == UNNotificationDefaultActionIdentifier, let note,
           var comps = URLComponents(string: "obsidian://open") {
            comps.queryItems = [URLQueryItem(name: "path", value: note)]
            if let url = comps.url { NSWorkspace.shared.open(url) }
        }
    }

    /// Processing finished: when minutes are ready, notify with a link to the Obsidian note.
    private func notifyProcessed(id: String, status: Int32) {
        let meta = (try? Data(contentsOf: Paths.recordings.appending(path: id).appending(path: "meta.json")))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        if status != 0 {
            let steps = meta["pipeline"] as? [String: [String: Any]] ?? [:]
            let failed = steps.first { $0.value["status"] as? String == "failed" }
            let error = failed?.value["error"] as? String ?? ""
            // Ollama isn't running: the transcript is ready, minutes come later. Notify once.
            if failed?.key == "minutes", Self.ollamaUnavailableMarkers.contains(where: { error.contains($0) }) {
                runner?.retryLater(id, after: 15 * 60)
                if waitingForOllama.insert(id).inserted {
                    notify(id: "done-\(id)", title: tr(.notifyOllamaTitle),
                           body: tr(.notifyOllamaBody))
                }
                return
            }
            let reason = failed.map { "\($0.key): \(String(error.prefix(120)))" } ?? tr(.notifyFailedSeeLog)
            notify(id: "done-\(id)", title: tr(.notifyFailedTitle), body: reason)
            return
        }
        waitingForOllama.remove(id)
        let vault = ((meta["pipeline"] as? [String: Any])?["notes"] as? [String: Any])
            .flatMap { ($0["result"] as? [String: Any])?["vault"] as? String }
        let notes = meta["notes"] as? [String: Any]
        guard let vault, let rel = (notes?["meeting"] as? String) ?? (notes?["transcript"] as? String) else { return }
        let path = URL(fileURLWithPath: vault).appending(path: rel).path
        let name = (rel as NSString).lastPathComponent.replacingOccurrences(of: ".md", with: "")
        let topic = name.components(separatedBy: " — ").dropFirst().joined(separator: " — ")
        notify(id: "done-\(id)", title: tr(notes?["meeting"] is String ? .notifyMinutesReady : .notifyTranscriptReady),
               body: topic.isEmpty ? name : topic, info: ["note": path])
    }

    // MARK: - Permissions

    private func requestPermissions() {
        DispatchQueue.global(qos: .userInitiated).async {
            let mic = MicrophonePermission.requestBlocking()
            var capture = AudioCapturePermission.status
            if capture == .unknown, let granted = AudioCapturePermission.requestBlocking() {
                capture = granted ? .granted : .denied
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    let captureText = switch capture { case .granted: "yes"; case .denied: "no"; case .unknown: "unknown" }
                    self.log.info("permissions: microphone \(mic ? "yes" : "no"), audio capture \(captureText)")
                    self.refresh()
                }
            }
        }
    }

    // MARK: - Menu

    private func buildMenu() {
        menu.delegate = self
        menu.autoenablesItems = false
        for item in [statusLine, detailLine, processingLine, warningsHeader] { item.isEnabled = false }
        setMenuTitles()

        micPermissionItem.target = self
        micPermissionItem.action = #selector(openMicSettings)
        capturePermissionItem.target = self
        capturePermissionItem.action = #selector(openCaptureSettings)
        notificationsItem.target = self
        notificationsItem.action = #selector(openNotificationSettings)

        recordItem.target = self
        recordItem.action = #selector(toggleRecording)
        discardItem.target = self
        discardItem.action = #selector(discardRecording)
        autoItem.target = self
        autoItem.action = #selector(toggleAuto)
        loginItem.target = self
        loginItem.action = #selector(toggleLoginItem)

        openUIItem.action = #selector(openUI)
        openUIItem.keyEquivalent = "o"
        openUIItem.target = self
        openFolderItem.action = #selector(openRecordings)
        openFolderItem.target = self
        lastItem.target = self
        lastItem.action = #selector(revealLast)
        quitItem.action = #selector(quit)
        quitItem.keyEquivalent = "q"
        quitItem.target = self

        for item in [statusLine, detailLine, processingLine, warningsHeader, micPermissionItem, capturePermissionItem,
                     notificationsItem, .separator(), recordItem, discardItem, .separator(), autoItem, loginItem, .separator(),
                     openUIItem, openFolderItem, lastItem, .separator(), quitItem] {
            menu.addItem(item)
        }
    }

    /// Titles that `refresh` doesn't update.
    private func setMenuTitles() {
        warningsHeader.title = tr(.menuWarnings)
        micPermissionItem.title = tr(.menuMicDenied)
        capturePermissionItem.title = tr(.menuCaptureDenied)
        notificationsItem.title = tr(.menuNotificationsOff)
        discardItem.title = tr(.menuDiscard)
        autoItem.title = tr(.menuAutoRecord)
        loginItem.title = tr(.menuLaunchAtLogin)
        openUIItem.title = tr(.menuOpenUI)
        openFolderItem.title = tr(.menuOpenFolder)
        quitItem.title = tr(.menuQuit)
    }

    func menuWillOpen(_ menu: NSMenu) {
        refresh()
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let denied = settings.authorizationStatus == .denied
            DispatchQueue.main.async { MainActor.assumeIsolated { self.notificationsDenied = denied; self.refresh() } }
        }
    }

    private func refresh() {
        guard statusItem != nil else { return }
        let micDenied = MicrophonePermission.status == .denied
        let captureDenied = AudioCapturePermission.status == .denied
        micPermissionItem.isHidden = !micDenied
        capturePermissionItem.isHidden = !captureDenied
        notificationsItem.isHidden = !notificationsDenied

        var warnings = configWarnings
        if let session {
            let s = session.status
            statusLine.title = tr(sessionKind == .auto ? .statusRecordingCall : .statusRecording, Self.clock(s.elapsed),
                                  tr(s.mode == .room ? .modeRoom : .modeHeadphones))
            detailLine.title = tr(.statusDetail, s.mic, tr(s.slackInCall ? .slackInCall : .slackNotInCall))
                + (s.remoteRunning ? "" : " · " + tr(.slackAudioNotRecording))
            detailLine.isHidden = false
            warnings += s.warnings
            recordItem.title = tr(.menuStopAndSave)
            discardItem.isHidden = false
        } else {
            statusLine.title = autoRecord ? tr(policy.suppressed ? .statusNotRecordingThisCall : .statusWaiting)
                                          : tr(.statusNotRecording)
            detailLine.isHidden = true
            recordItem.title = tr(.menuRecordManually)
            discardItem.isHidden = true
        }
        if let runner, runner.current != nil || !runner.queue.isEmpty {
            let current = runner.current.map {
                ($0.id == ProcessingRunner.cleanupID ? tr(.processingCleanup) : tr(.processingItem, $0.id))
                    + (runner.paused ? tr(.processingPaused) : "")
            }
            let queued = runner.queue.isEmpty ? nil : tr(.processingQueued, "\(runner.queue.count)")
            processingLine.title = [current, queued].compactMap { $0 }.joined(separator: " · ")
            processingLine.isHidden = false
        } else {
            processingLine.isHidden = true
        }
        autoItem.state = autoRecord ? .on : .off
        let login = SMAppService.mainApp.status
        loginItem.state = login == .enabled ? .on : .off
        loginItem.title = login == .requiresApproval ? tr(.menuLaunchNeedsApproval)
                                                     : tr(.menuLaunchAtLogin)

        warningItems.forEach { menu.removeItem($0) }
        warningItems = warnings.map { text in
            let item = NSMenuItem(title: "  " + text, action: nil, keyEquivalent: "")
            item.isEnabled = false
            return item
        }
        var index = menu.index(of: warningsHeader) + 1
        for item in warningItems {
            menu.insertItem(item, at: index)
            index += 1
        }
        warningsHeader.isHidden = warnings.isEmpty

        if let lastSaved {
            lastItem.title = tr(.menuShowLast, lastSaved.lastPathComponent)
            lastItem.isHidden = false
        } else {
            lastItem.isHidden = true
        }

        let button = statusItem.button
        if let session {
            button?.image = Self.symbol("record.circle.fill", color: .systemRed)
            button?.title = " " + Self.clock(session.status.elapsed)
            button?.font = .monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)
        } else {
            let problem = micDenied || captureDenied || !configWarnings.isEmpty
            button?.image = Self.symbol(problem ? "waveform.badge.exclamationmark" : "waveform", color: nil)
            button?.title = ""
        }
        button?.toolTip = "Meeting Recorder — \(statusLine.title)"
    }

    private static func symbol(_ name: String, color: NSColor?) -> NSImage? {
        let image = NSImage(systemSymbolName: name, accessibilityDescription: "Meeting Recorder")
            ?? NSImage(systemSymbolName: "waveform", accessibilityDescription: "Meeting Recorder")
        guard let color else {
            image?.isTemplate = true
            return image
        }
        return image?.withSymbolConfiguration(.init(paletteColors: [color]))
    }

    static func clock(_ seconds: Double) -> String {
        let s = Int(seconds)
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
                         : String(format: "%02d:%02d", s / 60, s % 60)
    }

    // MARK: - Actions

    @objc private func toggleRecording() {
        if session != nil {
            // Manual stop during a call: no auto-start until it ends
            policy.suppressUntilCallEnds()
            stopRecording()
        } else if ensureRecordingNotice() {
            startRecording(trigger: "manual")
        }
    }

    @objc private func discardRecording() {
        guard let session else { return }
        NSApp.activate()
        let a = NSAlert()
        a.messageText = tr(.alertDiscardTitle)
        a.informativeText = tr(.alertDiscardText)
        a.addButton(withTitle: tr(.alertDelete))
        a.addButton(withTitle: tr(.alertCancel))
        a.buttons.first?.hasDestructiveAction = true
        guard a.runModal() == .alertFirstButtonReturn, self.session === session else { return }
        policy.suppressUntilCallEnds()
        stopRecording(save: false)
    }

    @objc private func toggleAuto() {
        let turnOn = !autoRecord
        if turnOn, !ensureRecordingNotice() { return }
        autoRecord = turnOn
        policy.enabled = autoRecord
        log.info("automatic call recording: \(autoRecord ? "on" : "off")")
        refresh()
    }

    @objc private func toggleLoginItem() {
        let service = SMAppService.mainApp
        do {
            switch service.status {
            case .enabled:
                try service.unregister()
                log.info("launch at login off")
            case .requiresApproval:
                SMAppService.openSystemSettingsLoginItems()
            default:
                try service.register()
                log.info("launch at login: \(service.status == .enabled ? "on" : "awaiting approval in Settings")")
                if service.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
            }
        } catch {
            log.error("launch at login: \(error.localizedDescription)")
            alert(tr(.alertLoginFailed), error.localizedDescription)
        }
        refresh()
    }

    /// Web interface: if `ma ui` isn't listening yet, start it and wait up to 20 s.
    @objc private func openUI() {
        let server = ui ?? UIServer(port: config.uiPort)
        ui = server
        if server.isUp {
            openVerified(server)
            return
        }
        guard let uv = PipelineLocation.uv(config: config), let pipeline = PipelineLocation.pipelineDir(config: config) else {
            alert(tr(.alertNoPipelineTitle), tr(.alertNoPipelineText))
            return
        }
        do {
            try server.start(uv: uv, pipeline: pipeline, log: log)
        } catch {
            alert(tr(.alertUIStartFailed), "\(error)")
            return
        }
        uiTries = 0
        uiTimer?.invalidate()
        let t = Timer(timeInterval: 0.3, repeats: true) { _ in MainActor.assumeIsolated { self.pollUI() } }
        RunLoop.main.add(t, forMode: .common)
        uiTimer = t
    }

    /// The link carries the session token: only our own server may receive it.
    private func openVerified(_ server: UIServer) {
        let base = server.baseURL, token = server.token
        Task { @MainActor in
            if await UIServer.verify(baseURL: base, token: token) {
                NSWorkspace.shared.open(server.url)
            } else {
                alert(tr(.alertUIPortBusyTitle), tr(.alertUIPortBusyText, "\(server.port)"))
            }
        }
    }

    private func pollUI() {
        uiTries += 1
        guard let ui else { uiTimer?.invalidate(); return }
        if ui.isUp {
            uiTimer?.invalidate()
            openVerified(ui)
        } else if uiTries > 66 {
            uiTimer?.invalidate()
            alert(tr(.alertUITimedOut), tr(.alertUISeeLog))
        }
    }

    @objc private func openRecordings() {
        try? FileManager.default.createDirectory(at: Paths.recordings, withIntermediateDirectories: true)
        NSWorkspace.shared.open(Paths.recordings)
    }

    @objc private func revealLast() {
        guard let lastSaved else { return }
        NSWorkspace.shared.activateFileViewerSelecting([lastSaved])
    }

    @objc private func openMicSettings() {
        openURL("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
    }

    @objc private func openCaptureSettings() {
        openURL("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")
    }

    @objc private func openNotificationSettings() {
        openURL("x-apple.systempreferences:com.apple.Notifications-Settings.extension?id=local.meetingassistant.recorder")
    }

    @objc private func quit() { NSApp.terminate(nil) }

    private func openURL(_ string: String) {
        if let url = URL(string: string) { NSWorkspace.shared.open(url) }
    }

    private func alert(_ title: String, _ text: String) {
        NSApp.activate()
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.runModal()
    }

    /// Only one menu bar instance is allowed. The `record` and `probe` modes don't take the lock.
    private func acquireSingleInstanceLock() -> Bool {
        try? FileManager.default.createDirectory(at: Paths.home, withIntermediateDirectories: true)
        let fd = open(Paths.home.appending(path: "recorder.lock").path, O_CREAT | O_RDWR, 0o644)
        guard fd >= 0 else { return true }
        if flock(fd, LOCK_EX | LOCK_NB) != 0 {
            close(fd)
            return false
        }
        lockFD = fd
        return true
    }
}
