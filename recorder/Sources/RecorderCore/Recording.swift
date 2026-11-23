import CoreAudio
import Foundation

/// One recording: a `<root>/<id>/` folder with `mic.caf`, `remote.caf` and `meta.json`.
/// Control code lives on the main thread; audio bypasses it (IOProc -> ring -> writer queue).
@MainActor
public final class RecordingSession {
    public struct Status: Sendable {
        public var id: String
        public var elapsed: Double
        public var mode: RecordingMode
        public var modeReason: String
        /// Name of the mic being recorded, or why it isn't.
        public var mic: String
        public var micRunning: Bool
        public var remoteRunning: Bool
        public var slackInCall: Bool
        public var warnings: [String]
    }

    public let id: String
    public let folder: URL
    public let t0: UInt64
    public private(set) var isRunning = false
    /// Levels over the last second (for terminal runs).
    public private(set) var lastSecond = (mic: TrackStats(), remote: TrackStats())
    /// For testing: Slack audio is recorded but not heard.
    public var mutesTappedAudio = false

    private let config: RecorderConfig
    private let log: AppLog
    private let micWriter: TrackWriter
    private let remoteWriter: TrackWriter
    private var meta: RecordingMeta

    /// Tap on the list of Slack processes, updated live (`setDescription`): a bundle id tap
    /// misses processes launched after it was created, and after
    /// a Slack restart all processes are new.
    private var tap: TapCapture?
    private var tappedProcesses = Set<AudioObjectID>()
    private var tapState: TapCapture.State?
    private var tapRetryAt = 0.0

    private var mic: InputCapture?
    private var micRate = 0.0
    private var micNote: String
    private var micRetryAt: [String: Double] = [:]
    private var lastSlackMicHeld: Double?
    private var notedInvisibleSlackMic = false

    private var routes = RouteTracker()
    private var remoteSilence = SilenceWatch()
    private var micSilence = SilenceWatch()
    private var remoteSilenceRebuilt = false
    private var reportedOverflow = 0
    private var slackInCall = false

    private var timer: DispatchSourceTimer?
    private var ticks = 0
    private var activity: NSObjectProtocol?

    private var micURL: URL { folder.appending(path: "mic.caf") }
    private var remoteURL: URL { folder.appending(path: "remote.caf") }
    private var metaURL: URL { folder.appending(path: "meta.json") }

    public init(root: URL = Paths.recordings, config: RecorderConfig, trigger: String = "manual", log: AppLog) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        var id = RecordingID.make()
        while fm.fileExists(atPath: root.appending(path: id).path) { id = RecordingID.make() }
        self.id = id
        folder = root.appending(path: id, directoryHint: .isDirectory)
        try fm.createDirectory(at: folder, withIntermediateDirectories: false)

        self.config = config
        self.log = log
        micNote = L10n.text(.micNotSelected, language: config.language)
        t0 = HostClock.now()
        let startedAt = Date()
        micWriter = TrackWriter(url: folder.appending(path: "mic.caf"), label: "mic", t0: t0)
        remoteWriter = TrackWriter(url: folder.appending(path: "remote.caf"), label: "remote", t0: t0)

        let emptyTrack = TrackSummary(file: "", sampleRate: 0, frames: 0, seconds: 0, segments: [],
                                      overflowFrames: 0, discontinuities: 0, overlaps: 0, errors: [])
        meta = RecordingMeta(
            id: id, status: "recording", trigger: trigger,
            startedAt: RecordingMeta.timestamp(startedAt), endedAt: nil, durationSeconds: 0,
            t0HostTime: t0, hostTicksPerSecond: RecordingMeta.hostTicksPerSecond,
            mode: .headphones, modeReason: "not determined yet", routeChanges: [], callPeriods: [],
            tracks: .init(mic: emptyTrack, remote: emptyTrack), warnings: [],
            recorder: .init(version: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev",
                            pid: getpid(), bundleIDPrefixes: config.bundleIDPrefixes))
    }

    /// Seconds from t0.
    public var elapsed: Double { HostClock.seconds(from: t0, to: HostClock.now()) }

    public func start() {
        guard !isRunning else { return }
        isRunning = true
        activity = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiated, .idleSystemSleepDisabled], reason: "Recording a call")
        micWriter.start()
        remoteWriter.start()
        log.info("recording \(id): started (\(meta.trigger)), \(folder.path)")

        if MicrophonePermission.status == .denied {
            warn(.warnNoMic)
        }
        if AudioCapturePermission.status == .denied {
            warn(.warnNoCapture)
        }

        let slack = slackProcesses()
        updateCall(slack)
        startTap(reason: "start")
        checkMic(slack)
        updateRoute(slack)
        writeMeta()

        let t = DispatchSource.makeTimerSource(queue: .main)
        t.schedule(deadline: .now() + 0.25, repeating: 0.25)
        t.setEventHandler { [weak self] in MainActor.assumeIsolated { self?.tick() } }
        t.resume()
        timer = t
    }

    /// Stops. `save: false` deletes the whole folder. Returns the saved recording's folder.
    @discardableResult
    public func stop(save: Bool) -> URL? {
        guard isRunning else { return nil }
        isRunning = false
        timer?.cancel()
        timer = nil
        tap?.stop()
        mic?.stop()
        mic = nil
        let end = HostClock.now()
        let endSeconds = HostClock.seconds(from: t0, to: end)
        if slackInCall, !meta.callPeriods.isEmpty { meta.callPeriods[meta.callPeriods.count - 1].endSeconds = endSeconds }

        // Both tracks are padded with silence to a common end, so their lengths match.
        meta.tracks.mic = micWriter.finish(padToHostTime: end, defaultRate: 16_000)
        meta.tracks.remote = remoteWriter.finish(padToHostTime: end, defaultRate: 16_000)
        if let activity { ProcessInfo.processInfo.endActivity(activity) }
        activity = nil

        guard save else {
            try? FileManager.default.removeItem(at: folder)
            log.info("recording \(id): stopped and deleted")
            return nil
        }
        meta.status = "recorded"
        meta.endedAt = RecordingMeta.timestamp(Date())
        meta.durationSeconds = endSeconds
        writeMeta(final: true)
        log.info(String(format: "recording %@: saved, %.1f s, mic %d segments, remote %d segments", id, endSeconds,
                        meta.tracks.mic.segments.count, meta.tracks.remote.segments.count))
        return folder
    }

    public var status: Status {
        let texts = meta.warnings.map(\.text)
        return Status(
            id: id, elapsed: isRunning ? elapsed : meta.durationSeconds,
            mode: meta.mode, modeReason: meta.modeReason,
            mic: mic.map { $0.device.name } ?? micNote, micRunning: mic != nil,
            remoteRunning: tap?.isRunning ?? false, slackInCall: slackInCall,
            warnings: Array(texts.suffix(5)))
    }

    // MARK: - Polling

    private func tick() {
        guard isRunning else { return }
        ticks += 1
        let slack = slackProcesses()
        updateCall(slack)
        checkTap(slack)
        checkMic(slack)
        updateRoute(slack)
        if ticks % 4 == 0 { secondTick() }
        if ticks % 120 == 0 { writeMeta() }
    }

    /// Slack processes, excluding the recorder itself (in case the prefix matches it too).
    private func slackProcesses() -> [AudioProcessInfo] {
        let me = getpid()
        return AudioProcesses.matching(config.bundleIDPrefixes).filter { $0.pid != me }
    }

    private func updateCall(_ slack: [AudioProcessInfo]) {
        let holding = slack.contains(where: \.isRunningInput)
        let now = elapsed
        if holding { lastSlackMicHeld = now }
        guard holding != slackInCall else { return }
        slackInCall = holding
        if holding {
            meta.callPeriods.append(.init(startSeconds: now, endSeconds: nil))
            let pids = slack.filter(\.isRunningInput).map { "\($0.pid)" }.joined(separator: ",")
            log.info(String(format: "recording %@: Slack took the microphone (pid %@) at %.1f s", id, pids, now))
        } else if !meta.callPeriods.isEmpty {
            meta.callPeriods[meta.callPeriods.count - 1].endSeconds = now
            log.info(String(format: "recording %@: Slack released the microphone at %.1f s", id, now))
        }
    }

    // MARK: - Slack audio (remote)

    private func startTap(reason: String) {
        let ids = slackProcesses().map(\.objectID)
        let capture = tap ?? TapCapture(label: "remote", target: .processes(ids), writer: remoteWriter)
        capture.target = .processes(ids)
        tappedProcesses = Set(ids)
        capture.mutesTappedAudio = mutesTappedAudio
        tap = capture
        do {
            try capture.start(reason: reason)
            tapState = capture.state()
            log.info("recording \(id): tap (\(reason)): \(tapState.map(describe) ?? "?")")
        } catch {
            tapRetryAt = elapsed + 5
            let detail = describeStatus(error)
            if slackProcesses().isEmpty {
                log.info("\(L10n.text(.warnTapFailed, language: L10n.defaultLanguage, args: [detail])) (Slack isn't running, will retry)")
            } else {
                warn(.warnTapFailed, detail)
            }
        }
    }

    private func rebuildTap(_ reason: String) {
        log.info("recording \(id): rebuilding tap: \(reason)")
        tap?.stop()
        startTap(reason: reason)
    }

    private func describe(_ s: TapCapture.State) -> String {
        let all = AudioProcesses.all()
        let pids = s.tappedProcesses.map { id in all.first { $0.objectID == id }.map { "\($0.pid)" } ?? "#\(id)" }
        return "pid \(pids) format \(s.tapFormat) aggregate \(Int(s.aggregateRate)) Hz"
    }

    private func checkTap(_ slack: [AudioProcessInfo]) {
        guard let tap, tap.isRunning else {
            if elapsed >= tapRetryAt { startTap(reason: "retry") }
            return
        }
        // The set of Slack processes changed (new helper, Slack restart): update the live tap's list.
        let ids = Set(slack.map(\.objectID))
        if ids != tappedProcesses {
            let added = slack.filter { !tappedProcesses.contains($0.objectID) }.map(\.pid)
            let removed = tappedProcesses.subtracting(ids).count
            do {
                try tap.updateProcesses(slack.map(\.objectID))
                tappedProcesses = ids
                log.info("recording \(id): tap: Slack processes +\(added) -\(removed)")
            } catch {
                rebuildTap("setDescription failed (\(describeStatus(error)))")
                return
            }
        }
        guard let state = tap.state() else {
            rebuildTap("tap disappeared")
            return
        }
        if let old = tapState, old.tapFormat != state.tapFormat || old.aggregateRate != state.aggregateRate {
            rebuildTap("format \(old.tapFormat) -> \(state.tapFormat), aggregate \(Int(old.aggregateRate)) -> \(Int(state.aggregateRate)) Hz")
            return
        }
        tapState = state
    }

    // MARK: - Microphone (mic)

    private func checkMic(_ slack: [AudioProcessInfo]) {
        if let mic {
            let info = Devices.info(id: mic.device.id)
            if info == nil || info?.isAlive == false {
                log.info("recording \(id): microphone \"\(mic.device.name)\" disappeared")
                stopMic(note: tr(.micDeviceGone))
            } else if let info, info.nominalRate != micRate {
                log.info("recording \(id): microphone \"\(mic.device.name)\" changed rate \(Int(micRate)) -> \(Int(info.nominalRate)) Hz")
                stopMic(note: tr(.micRateChanged))
            }
        }

        let holding = slack.contains(where: \.isRunningInput)
        var slackInput: DeviceInfo?
        if holding {
            search: for p in slack where p.isRunningInput {
                for deviceID in p.inputDevices {
                    if let d = Devices.info(id: deviceID), d.inputChannels > 0 { slackInput = d; break search }
                }
            }
            if slackInput == nil, !notedInvisibleSlackMic {
                notedInvisibleSlackMic = true
                log.warn("recording \(id): Slack records from a device the recorder can't see, using the default input")
            }
        }
        let releasedFor = holding ? 0 : lastSlackMicHeld.map { elapsed - $0 } ?? .infinity
        let decision = MicPolicy.decide(slackHoldsMic: holding, slackInput: slackInput, current: mic?.device,
                                        defaultInput: Devices.defaultInput, slackReleasedFor: releasedFor)
        switch decision {
        case .keep:
            if mic == nil, let d = Devices.defaultInput, d.isBluetooth, !holding {
                micNote = tr(.micWaitingBluetooth)
            }
        case let .stop(reason):
            log.info("recording \(id): microphone \"\(mic?.device.name ?? "?")\" released: \(reason)")
            stopMic(note: tr(.micWaitingBluetooth))
        case let .use(device, reason):
            if let retry = micRetryAt[device.uid], elapsed < retry { return }
            stopMic(note: micNote)
            startMic(device, reason: reason)
        }
    }

    private func startMic(_ device: DeviceInfo, reason: String) {
        let capture = InputCapture(device: device, writer: micWriter)
        do {
            try capture.start(reason: reason)
            mic = capture
            micRate = device.nominalRate
            micRetryAt[device.uid] = nil
            micSilence = SilenceWatch()
            log.info("recording \(id): microphone \(device.short): \(reason), \(capture.format?.short ?? "?")")
        } catch {
            micRetryAt[device.uid] = elapsed + 5
            micNote = tr(.micError, device.name)
            warn(.warnMicFailed, device.name, describeStatus(error))
        }
    }

    private func stopMic(note: String) {
        mic?.stop()
        mic = nil
        micNote = note
    }

    // MARK: - Headphones or speakers

    private func updateRoute(_ slack: [AudioProcessInfo]) {
        var device: DeviceInfo?
        search: for p in slack where p.isRunningOutput {
            for deviceID in p.outputDevices {
                if let d = Devices.info(id: deviceID), d.outputChannels > 0 { device = d; break search }
            }
        }
        guard let device = device ?? Devices.defaultOutput else { return }
        let first = routes.current == nil
        guard let decision = routes.feed(OutputRoute.classify(device), at: elapsed) else { return }
        if first {
            meta.mode = decision.mode
            meta.modeReason = decision.reason
        } else {
            meta.routeChanges.append(.init(atSeconds: elapsed, mode: decision.mode, reason: decision.reason,
                                           deviceUID: decision.deviceUID))
        }
        log.info("recording \(id): mode \(decision.mode.rawValue): \(decision.reason)")
    }

    // MARK: - Once a second

    private func secondTick() {
        let m = micWriter.takeStats()
        let r = remoteWriter.takeStats()
        lastSecond = (m, r)

        let remoteSilent = r.chunks == 0 || r.zeroChunks == r.chunks
        if !remoteSilent { remoteSilenceRebuilt = false }
        if tap?.isRunning == true, remoteSilence.feed(expectSound: slackInCall, silent: remoteSilent) {
            if !remoteSilenceRebuilt {
                remoteSilenceRebuilt = true
                warn(.warnRemoteZeros)
                rebuildTap("digital silence during call")
            } else {
                warn(.warnRemoteStillZeros)
            }
        }
        if mic != nil, micSilence.feed(expectSound: true, silent: m.chunks == 0 || m.zeroChunks == m.chunks) {
            warn(.warnMicZeros)
        }
        let overflow = micWriter.overflowFrames + remoteWriter.overflowFrames
        if overflow > reportedOverflow {
            reportedOverflow = overflow
            warn(.warnOverflow)
        }
    }

    // MARK: - meta.json and warnings

    private func tr(_ key: L10n.Key, _ args: String...) -> String {
        L10n.text(key, language: config.language, args: args)
    }

    /// The menu and meta.json get the UI language; the log stays in English.
    private func warn(_ key: L10n.Key, _ args: String...) {
        let text = L10n.text(key, language: config.language, args: args)
        if meta.warnings.contains(where: { $0.text == text }) { return }
        meta.warnings.append(.init(atSeconds: (elapsed * 10).rounded() / 10, text: text))
        log.warn("recording \(id): \(L10n.text(key, language: L10n.defaultLanguage, args: args))")
    }

    private func writeMeta(final: Bool = false) {
        if !final {
            meta.durationSeconds = elapsed
            meta.tracks.mic = micWriter.snapshot()
            meta.tracks.remote = remoteWriter.snapshot()
        }
        do { try meta.write(to: metaURL) } catch { log.error("recording \(id): failed to write meta.json: \(error)") }
    }
}
