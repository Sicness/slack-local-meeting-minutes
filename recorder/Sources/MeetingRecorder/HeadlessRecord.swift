import AppKit
import RecorderCore

/// `MeetingRecorder record ...`: the same recording as the menu, from the terminal, for testing.
/// Run as an .app via `scripts/record.sh`, otherwise Terminal gets the permissions.
@MainActor
final class HeadlessRecord {
    private let seconds: Double?
    private let root: URL
    private let prefixes: [String]?
    private let mute: Bool
    private let pidFile: URL?
    private let log = AppLog(echo: true)
    private var session: RecordingSession?
    private var signalSources: [DispatchSourceSignal] = []
    private var timers: [DispatchSourceTimer] = []

    init(args: [String]) {
        func option(_ name: String) -> String? {
            guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
            return args[i + 1]
        }
        seconds = option("--seconds").flatMap(Double.init)
        root = option("--root").map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) } ?? Paths.recordings
        let p = args.indices.filter { args[$0] == "--bundle-prefix" && $0 + 1 < args.count }.map { args[$0 + 1] }
        prefixes = p.isEmpty ? nil : p
        mute = args.contains("--mute")
        pidFile = option("--pid-file").map { URL(fileURLWithPath: $0) }
    }

    func run() -> Never {
        NSApplication.shared.setActivationPolicy(.accessory)
        if let pidFile { try? Data("\(getpid())".utf8).write(to: pidFile) }
        for sig in [SIGINT, SIGTERM] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { MainActor.assumeIsolated { self.finish("signal") } }
            source.resume()
            signalSources.append(source)
        }
        if Bundle.main.bundleIdentifier == nil {
            log.warn("not running as an .app, so Terminal gets the permissions; use scripts/record.sh")
        }
        DispatchQueue.global(qos: .userInitiated).async {
            _ = MicrophonePermission.requestBlocking()
            if AudioCapturePermission.status == .unknown { _ = AudioCapturePermission.requestBlocking() }
            DispatchQueue.main.async { MainActor.assumeIsolated { self.begin() } }
        }
        NSApplication.shared.run()
        exit(0)
    }

    private func begin() {
        log.info("permissions: microphone \(MicrophonePermission.status.rawValue), audio capture \(AudioCapturePermission.status.rawValue)")
        var (config, warnings) = RecorderConfig.load()
        warnings.forEach { log.warn($0) }
        if let prefixes { config.bundleIDPrefixes = prefixes }
        do {
            let s = try RecordingSession(root: root, config: config, trigger: "manual", log: log)
            s.mutesTappedAudio = mute
            s.start()
            session = s
            print("Folder: \(s.folder.path)")
        } catch {
            log.error("failed to start recording: \(error)")
            exit(1)
        }

        let levels = DispatchSource.makeTimerSource(queue: .main)
        levels.schedule(deadline: .now() + 1.02, repeating: 1)
        levels.setEventHandler { MainActor.assumeIsolated { self.printLevels() } }
        levels.resume()
        timers.append(levels)
        if let seconds {
            let stop = DispatchSource.makeTimerSource(queue: .main)
            stop.schedule(deadline: .now() + seconds)
            stop.setEventHandler { MainActor.assumeIsolated { self.finish("\(Int(seconds)) s elapsed") } }
            stop.resume()
            timers.append(stop)
        }
    }

    private func printLevels() {
        guard let s = session else { return }
        let (m, r) = s.lastSecond
        let st = s.status
        print(String(format: "%7.1f s  mic %6.1f/%6.1f zeros %3d/%-3d | remote %6.1f/%6.1f zeros %3d/%-3d | %@ | microphone %@ | Slack %@",
                     st.elapsed, m.rmsDB, m.peakDB, m.zeroChunks, m.chunks, r.rmsDB, r.peakDB, r.zeroChunks, r.chunks,
                     st.mode.rawValue, st.mic, st.slackInCall ? "in call" : "-"))
    }

    private func finish(_ reason: String) {
        timers.forEach { $0.cancel() }
        if let folder = session?.stop(save: true) {
            print("Done (\(reason)): \(folder.path)")
        }
        session = nil
        if let pidFile { try? FileManager.default.removeItem(at: pidFile) }
        exit(0)
    }
}
