import AVFoundation
import AppKit
import CoreAudio
import Foundation
import RecorderCore

/// Prototype: finds out on a live huddle which Slack processes hold the mic and play audio,
/// and tests Process Tap before it goes into the recorder.
enum Probe {
    static func run(_ args: [String]) -> Never {
        let rest = Array(args.dropFirst())
        switch args.first {
        case "watch": MainActor.assumeIsolated { WatchProbe(args: rest).run() }
        case "tap": MainActor.assumeIsolated { TapProbe(args: rest).run() }
        case "writer-test": WriterTest(args: rest).run()
        case "play": PlayProbe.run(rest)
        default:
            FileHandle.standardError.write(Data("probe: watch | tap | writer-test | play\n".utf8))
            exit(2)
        }
    }

    static var probeRoot: URL {
        FileManager.default.homeDirectoryForCurrentUser.appending(path: "MeetingAssistant/probe")
    }

    static func stamp() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        return f.string(from: Date())
    }

    static func option(_ name: String, _ args: [String]) -> String? {
        guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
        return args[i + 1]
    }

    static func onSignals(_ handler: @escaping @MainActor () -> Void) {
        for sig in [SIGINT, SIGTERM] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { MainActor.assumeIsolated { handler() } }
            source.resume()
            signalSources.append(source)
        }
    }

    nonisolated(unsafe) static var signalSources: [DispatchSourceSignal] = []

    static func describe(devices ids: [AudioObjectID]) -> String {
        ids.isEmpty ? "-" : ids.map { Devices.info(id: $0)?.short ?? "#\($0) (not visible)" }.joined(separator: "; ")
    }

    static func db(_ value: Double) -> String { String(format: "%6.1f", value) }

    static func json<T: Encodable>(_ value: T) -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return (try? encoder.encode(value)) ?? Data()
    }
}

// MARK: - watch

@MainActor
final class WatchProbe {
    private struct Seen {
        var pid: pid_t
        var role: String
        var bundleID: String
        var firstSeen: Date
        var lastSeen: Date?
        var everInput = false
        var everOutput = false
        var appearedDuringCall = false
        var disappearedDuringCall = false
        var inputDevices = Set<String>()
        var outputDevices = Set<String>()
    }

    private let log: LineLog
    private let logURL: URL
    private var known: [AudioObjectID: AudioProcessInfo] = [:]
    private var others: [AudioObjectID: AudioProcessInfo] = [:]
    private var roles: [pid_t: String] = [:]
    private var seen: [AudioObjectID: Seen] = [:]
    private var order: [AudioObjectID] = []
    private var defaultIn: DeviceInfo?
    private var defaultOut: DeviceInfo?
    private var callStart: Date?
    private var calls: [(start: Date, end: Date)] = []
    private var timer: DispatchSourceTimer?
    private let myPID = getpid()

    init(args: [String]) {
        try? FileManager.default.createDirectory(at: Probe.probeRoot, withIntermediateDirectories: true)
        logURL = Probe.probeRoot.appending(path: "watch-\(Probe.stamp()).log")
        log = LineLog(fileURL: logURL)
    }

    func run() -> Never {
        log.raw("probe watch - log: \(logURL.path)")
        log.raw("Printing changes only. Ctrl+C for a summary and exit.\n")
        tick(initial: true)
        let t = DispatchSource.makeTimerSource(queue: .main)
        t.schedule(deadline: .now() + 0.25, repeating: 0.25)
        t.setEventHandler { MainActor.assumeIsolated { self.tick(initial: false) } }
        t.resume()
        timer = t
        Probe.onSignals { self.finish() }
        dispatchMain()
    }

    private func role(_ pid: pid_t) -> String {
        if let r = roles[pid] { return r }
        let r = ProcessArgs.chromiumRole(pid: pid)
        roles[pid] = r
        return r
    }

    private func line(_ p: AudioProcessInfo) -> String {
        "pid \(p.pid) [\(role(p.pid))] in=\(p.isRunningInput ? 1 : 0) out=\(p.isRunningOutput ? 1 : 0)"
            + "  input: \(Probe.describe(devices: p.inputDevices))  output: \(Probe.describe(devices: p.outputDevices))"
    }

    private func tick(initial: Bool) {
        let now = Date()
        let all = AudioProcesses.all()
        let slack = AudioProcesses.matching(AudioProcesses.slackPrefixes, in: all)
        let inCall = callStart != nil

        for p in slack {
            if let old = known[p.objectID] {
                if old != p { log("  Slack \(line(p))") }
            } else {
                log("+ Slack \(line(p))  \(p.bundleID)")
                seen[p.objectID] = Seen(pid: p.pid, role: role(p.pid), bundleID: p.bundleID, firstSeen: now,
                                        appearedDuringCall: inCall && !initial)
                order.append(p.objectID)
            }
            if var s = seen[p.objectID] {
                s.everInput = s.everInput || p.isRunningInput
                s.everOutput = s.everOutput || p.isRunningOutput
                for id in p.inputDevices { s.inputDevices.insert(Devices.info(id: id)?.name ?? "#\(id)") }
                for id in p.outputDevices { s.outputDevices.insert(Devices.info(id: id)?.name ?? "#\(id)") }
                seen[p.objectID] = s
            }
        }
        let currentIDs = Set(slack.map(\.objectID))
        for (id, old) in known where !currentIDs.contains(id) {
            log("- Slack pid \(old.pid) [\(role(old.pid))] disappeared from Core Audio")
            seen[id]?.lastSeen = now
            if inCall { seen[id]?.disappearedDuringCall = true }
        }
        known = Dictionary(uniqueKeysWithValues: slack.map { ($0.objectID, $0) })

        let micBusy = slack.contains(where: \.isRunningInput)
        if micBusy, callStart == nil {
            callStart = now
            log("> Slack took the microphone")
        } else if !micBusy, let start = callStart {
            calls.append((start, now))
            callStart = nil
            log(String(format: "< Slack released the microphone (held for %.0f s)", now.timeIntervalSince(start)))
        }

        let otherInputs = all.filter { p in
            p.isRunningInput && p.pid != myPID && !AudioProcesses.slackPrefixes.contains { p.bundleID.hasPrefix($0) }
        }
        let otherIDs = Set(otherInputs.map(\.objectID))
        for p in otherInputs where others[p.objectID] == nil {
            log("  another process holds the microphone: pid \(p.pid) \(p.bundleID.isEmpty ? "(no bundle id)" : p.bundleID)")
        }
        for (id, p) in others where !otherIDs.contains(id) {
            log("  another process released the microphone: pid \(p.pid) \(p.bundleID)")
        }
        others = Dictionary(uniqueKeysWithValues: otherInputs.map { ($0.objectID, $0) })

        let din = Devices.defaultInput
        let dout = Devices.defaultOutput
        if din != defaultIn { log("  default input: \(din?.short ?? "-")") }
        if dout != defaultOut { log("  default output: \(dout?.short ?? "-")") }
        defaultIn = din
        defaultOut = dout
    }

    private func finish() {
        timer?.cancel()
        let now = Date()
        if let start = callStart { calls.append((start, now)) }
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        log.raw("\n=== Summary ===")
        log.raw("Periods when Slack held the microphone: " + (calls.isEmpty ? "none"
            : calls.map { "\(f.string(from: $0.start))-\(f.string(from: $0.end)) (\(Int($0.end.timeIntervalSince($0.start))) s)" }
                .joined(separator: ", ")))
        log.raw("Slack processes in Core Audio:")
        for id in order {
            guard let s = seen[id] else { continue }
            var flags: [String] = []
            if s.everInput { flags.append("HELD MICROPHONE") }
            if s.everOutput { flags.append("PLAYED AUDIO") }
            if s.appearedDuringCall { flags.append("appeared during call") }
            if s.disappearedDuringCall { flags.append("disappeared during call") }
            if s.lastSeen != nil, !s.disappearedDuringCall { flags.append("disappeared") }
            log.raw("  pid \(s.pid) [\(s.role)] \(s.bundleID): \(flags.isEmpty ? "quiet" : flags.joined(separator: ", "))")
            if !s.inputDevices.isEmpty { log.raw("      input: \(s.inputDevices.sorted().joined(separator: ", "))") }
            if !s.outputDevices.isEmpty { log.raw("      output: \(s.outputDevices.sorted().joined(separator: ", "))") }
        }
        log.raw("Log: \(logURL.path)")
        exit(0)
    }
}

// MARK: - tap

@MainActor
final class TapProbe {
    private let out: URL
    private let seconds: Double
    private let prefixes: [String]
    private let log = LineLog()
    private var t0: UInt64 = 0
    private var bundleTap: TapCapture?
    private var pidTap: TapCapture?
    private var mic: InputCapture?
    private var micWriter: TrackWriter?
    private var writers: [TrackWriter] = []
    private var slackObjects: [AudioObjectID] = []
    private var lastTapState: [String: TapCapture.State] = [:]
    private var lastMicRate = 0.0
    private var timers: [DispatchSourceTimer] = []
    private var events: [String] = []
    private var finished = false
    private var started = Date()

    init(args: [String]) {
        seconds = Double(Probe.option("--seconds", args) ?? "900") ?? 900
        out = Probe.option("--out", args).map { URL(fileURLWithPath: $0) }
            ?? Probe.probeRoot.appending(path: "tap-\(Probe.stamp())")
        prefixes = Probe.option("--bundle-prefix", args).map { [$0] } ?? AudioProcesses.slackPrefixes
    }

    private var bundleIDs: [String] { prefixes.flatMap { [$0, $0 + ".helper"] } }

    private func matching() -> [AudioProcessInfo] {
        AudioProcesses.matching(prefixes).filter { $0.pid != getpid() }
    }

    func run() -> Never {
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        try? Data("\(getpid())".utf8).write(to: out.appending(path: "pid"))
        NSApplication.shared.setActivationPolicy(.accessory)
        Probe.onSignals { self.finish(reason: "signal") }

        log.raw("probe tap - \(Bundle.main.bundleIdentifier ?? "no bundle id"), pid \(getpid())")
        log.raw("Folder: \(out.path)")
        log.raw("Microphone: \(MicrophonePermission.status.rawValue), audio capture: \(AudioCapturePermission.status.rawValue)")
        if Bundle.main.bundleIdentifier == nil {
            log.raw("WARNING: not running as an .app, so Terminal gets the permissions. Use scripts/probe.sh tap.")
        }

        DispatchQueue.global().async {
            let micOK = MicrophonePermission.requestBlocking()
            let captureOK: Bool? = AudioCapturePermission.status == .granted ? true : AudioCapturePermission.requestBlocking()
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self.begin(micOK: micOK, captureOK: captureOK) }
            }
        }
        NSApplication.shared.run()
        exit(0)
    }

    private func event(_ text: String) {
        log(text)
        events.append(String(format: "%7.2f  ", Date().timeIntervalSince(started)) + text)
    }

    private func begin(micOK: Bool, captureOK: Bool?) {
        event("permissions: microphone \(micOK ? "yes" : "NO"), audio capture \(captureOK.map { $0 ? "yes" : "NO" } ?? "unknown")"
              + " (preflight: \(AudioCapturePermission.status.rawValue))")
        started = Date()
        t0 = HostClock.now()

        let bundleWriter = TrackWriter(url: out.appending(path: "remote-bundle.caf"), label: "bundle", t0: t0)
        let pidWriter = TrackWriter(url: out.appending(path: "remote-pid.caf"), label: "pid", t0: t0)
        let micWriter = TrackWriter(url: out.appending(path: "mic.caf"), label: "mic", t0: t0)
        writers = [bundleWriter, pidWriter, micWriter]
        self.micWriter = micWriter
        writers.forEach { $0.start() }

        let bundle = TapCapture(label: "bundle", target: .bundleIDs(bundleIDs, restore: true),
                                writer: bundleWriter)
        let slack = matching()
        do {
            try bundle.start(reason: "start")
            event("tap bundle: started (bundleIDs only)")
        } catch {
            event("tap bundle: ERROR with an empty process list \(describeStatus(error)), trying bundleIDs + current processes")
            bundle.initialProcesses = slack.map(\.objectID)
            do { try bundle.start(reason: "start, bundleIDs + processes"); event("tap bundle: started (bundleIDs + processes)") }
            catch { event("tap bundle: ERROR \(describeStatus(error))") }
        }
        bundleTap = bundle

        slackObjects = slack.map(\.objectID)
        let pid = TapCapture(label: "pid", target: .processes(slackObjects), writer: pidWriter)
        do { try pid.start(reason: "start"); event("tap pid: started on \(slack.map { "\($0.pid)" })") }
        catch { event("tap pid: ERROR \(describeStatus(error))") }
        pidTap = pid

        for tap in [bundle, pid] {
            if let s = tap.state() { event("tap \(tap.label): \(describe(s))"); lastTapState[tap.label] = s }
        }

        updateMic(slack: slack)

        let fast = DispatchSource.makeTimerSource(queue: .main)
        fast.schedule(deadline: .now() + 0.25, repeating: 0.25)
        fast.setEventHandler { MainActor.assumeIsolated { self.fastTick() } }
        let slow = DispatchSource.makeTimerSource(queue: .main)
        slow.schedule(deadline: .now() + 1, repeating: 1)
        slow.setEventHandler { MainActor.assumeIsolated { self.secondTick() } }
        let stop = DispatchSource.makeTimerSource(queue: .main)
        stop.schedule(deadline: .now() + seconds)
        stop.setEventHandler { MainActor.assumeIsolated { self.finish(reason: "\(Int(self.seconds)) s elapsed") } }
        for t in [fast, slow, stop] { t.resume() }
        timers = [fast, slow, stop]
        log.raw("\nEvery second: track level (RMS / peak, dBFS), zeros = all-zero buffers, out/in = Slack pids playing audio / holding the mic.\n")
    }

    private func describe(_ s: TapCapture.State) -> String {
        let pids = s.tappedProcesses.map { id in
            AudioProcesses.all().first { $0.objectID == id }.map { "\($0.pid)" } ?? "obj\(id)"
        }
        return "processes \(pids) bundleIDs \(s.bundleIDs) restore=\(s.restoreEnabled) format \(s.tapFormat)"
            + " aggregate \(Int(s.aggregateRate)) Hz subtaps \(s.activeSubtaps)"
    }

    /// The device Slack records from; before a call, the default input unless it is Bluetooth.
    private func desiredMic(slack: [AudioProcessInfo]) -> (DeviceInfo, String)? {
        for p in slack where p.isRunningInput {
            for id in p.inputDevices {
                if let d = Devices.info(id: id), d.inputChannels > 0 { return (d, "Slack device (pid \(p.pid))") }
            }
            if !p.inputDevices.isEmpty { event("microphone: Slack device \(p.inputDevices) is not visible to this process") }
        }
        return nil
    }

    private func updateMic(slack: [AudioProcessInfo]) {
        guard let micWriter else { return }
        var choice = desiredMic(slack: slack)
        if choice == nil, mic == nil, let def = Devices.defaultInput {
            if def.isBluetooth {
                return  // wait for Slack to open the Bluetooth mic itself
            }
            choice = (def, "default input")
        }
        guard let (device, reason) = choice else { return }
        if let mic, mic.device.uid == device.uid { return }
        mic?.stop()
        let capture = InputCapture(device: device, writer: micWriter)
        do {
            try capture.start(reason: reason)
            mic = capture
            lastMicRate = device.nominalRate
            event("microphone: \(device.short) - \(reason), format \(capture.format?.short ?? "?")")
        } catch {
            mic = nil
            event("microphone: ERROR on \(device.short): \(describeStatus(error))")
        }
    }

    private func fastTick() {
        let slack = matching()
        let ids = slack.map(\.objectID)

        if Set(ids) != Set(slackObjects), let pidTap {
            let added = Set(ids).subtracting(slackObjects), removed = Set(slackObjects).subtracting(ids)
            slackObjects = ids
            do {
                try pidTap.updateProcesses(ids)
                event("tap pid: setDescription ok (+\(added.sorted()) -\(removed.sorted())) -> \(pidTap.state().map(describe) ?? "?")")
            } catch {
                event("tap pid: setDescription ERROR \(describeStatus(error))")
            }
        }

        for tap in [bundleTap, pidTap].compactMap({ $0 }) {
            guard let s = tap.state() else { continue }
            let old = lastTapState[tap.label]
            if let old, old.tapFormat != s.tapFormat || old.aggregateRate != s.aggregateRate {
                event("tap \(tap.label): format \(old.tapFormat) -> \(s.tapFormat), aggregate \(Int(old.aggregateRate)) -> \(Int(s.aggregateRate)) Hz, rebuilding")
                do { try tap.rebuild(reason: "format change") } catch { event("tap \(tap.label): rebuild ERROR \(describeStatus(error))") }
                lastTapState[tap.label] = tap.state()
            } else if old != s {
                event("tap \(tap.label): \(describe(s))")
                lastTapState[tap.label] = s
            }
        }

        if let mic {
            let info = Devices.info(id: mic.device.id)
            if info == nil || info?.isAlive == false {
                event("microphone: device \(mic.device.name) disappeared")
                mic.stop()
                self.mic = nil
            } else if let info, info.nominalRate != lastMicRate {
                event("microphone: sample rate \(Int(lastMicRate)) -> \(Int(info.nominalRate)) Hz, restarting")
                mic.stop()
                self.mic = nil
            }
        }
        updateMic(slack: slack)
    }

    private func secondTick() {
        let slack = matching()
        let outs = slack.filter(\.isRunningOutput).map { "\($0.pid)" }.joined(separator: ",")
        let ins = slack.filter(\.isRunningInput).map { "\($0.pid)" }.joined(separator: ",")
        var parts: [String] = []
        for w in writers {
            let s = w.takeStats()
            parts.append("\(w.label) \(Probe.db(s.rmsDB))/\(Probe.db(s.peakDB)) zeros \(s.zeroChunks)/\(s.chunks)"
                         + (s.discontinuities > 0 ? " discontinuities \(s.discontinuities)" : "")
                         + (w.overflowFrames > 0 ? " OVERFLOW \(w.overflowFrames)" : ""))
        }
        log("\(parts.joined(separator: " | ")) | out \(outs.isEmpty ? "-" : outs) in \(ins.isEmpty ? "-" : ins)")

        let permissions = "microphone \(MicrophonePermission.status.rawValue), audio capture \(AudioCapturePermission.status.rawValue)"
        if permissions != lastPermissions {
            if !lastPermissions.isEmpty { event("permissions changed: \(permissions)") }
            lastPermissions = permissions
        }
    }

    private var lastPermissions = ""

    private struct Summary: Encodable {
        var reason: String
        var bundleID: String?
        var permissions: [String: String]
        var tracks: [String: TrackSummary]
        var events: [String]
    }

    private func finish(reason: String) {
        guard !finished else { return }
        finished = true
        timers.forEach { $0.cancel() }
        bundleTap?.stop()
        pidTap?.stop()
        mic?.stop()
        var tracks: [String: TrackSummary] = [:]
        for w in writers { tracks[w.label] = w.finish() }
        let summary = Summary(
            reason: reason, bundleID: Bundle.main.bundleIdentifier,
            permissions: ["microphone": MicrophonePermission.status.rawValue,
                          "audio_capture": AudioCapturePermission.status.rawValue],
            tracks: tracks, events: events)
        try? Probe.json(summary).write(to: out.appending(path: "summary.json"))
        try? FileManager.default.removeItem(at: out.appending(path: "pid"))
        log.raw("\n=== Done (\(reason)) ===")
        for (label, t) in tracks.sorted(by: { $0.key < $1.key }) {
            log.raw(String(format: "  %@: %@ - %.1f s, %d Hz, %d segments, %d discontinuities, %d overflow frames%@",
                           label, t.file, t.seconds, Int(t.sampleRate), t.segments.count, t.discontinuities,
                           t.overflowFrames, t.errors.isEmpty ? "" : ", errors: \(t.errors.joined(separator: "; "))"))
        }
        log.raw("Summary: \(out.appending(path: "summary.json").path)")
        exit(0)
    }
}

// MARK: - writer-test

/// A sine wave through the same ring and TrackWriter: tests CAF after kill -9 without permissions.
struct WriterTest {
    let seconds: Double
    let out: URL
    let deferSizes: Bool

    init(args: [String]) {
        seconds = Double(Probe.option("--seconds", args) ?? "20") ?? 20
        out = Probe.option("--out", args).map { URL(fileURLWithPath: $0) }
            ?? Probe.probeRoot.appending(path: "writer-test-\(Probe.stamp())")
        deferSizes = args.contains("--defer-sizes")
    }

    func run() -> Never {
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        let t0 = HostClock.now()
        let writer = TrackWriter(url: out.appending(path: "test.caf"), label: "test", t0: t0, updateSizes: !deferSizes)
        writer.start()
        let rate = 48_000.0, frames = 512
        let segment = writer.beginSegment(sampleRate: rate, info: ["kind": "sine"])
        let buffer = UnsafeMutablePointer<Float>.allocate(capacity: frames)
        var sampleTime = 0.0
        let begin = Date()
        print("writer-test: \(out.path)/test.caf, pid \(getpid()), deferSizes=\(deferSizes)")
        // Sample count follows elapsed time because usleep is imprecise.
        while Date().timeIntervalSince(begin) < seconds {
            let due = Date().timeIntervalSince(begin) * rate
            while sampleTime + Double(frames) <= due {
                for i in 0..<frames {
                    buffer[i] = 0.25 * Float(sin(2 * Double.pi * 440 * (sampleTime + Double(i)) / rate))
                }
                writer.ring.push(segment: segment, hostTime: HostClock.now(), hostTimeValid: true,
                                 sampleTime: sampleTime, rateScalar: 1, source: buffer, stride: 1, frames: frames)
                sampleTime += Double(frames)
            }
            usleep(5_000)
        }
        let summary = writer.finish()
        print(String(decoding: Probe.json(summary), as: UTF8.self))
        exit(0)
    }
}

// MARK: - play

/// Plays a file on the default output. Launched via `open -n`, this is another process
/// with the recorder's bundle id, which tests the bundle id tap without Slack.
enum PlayProbe {
    static func run(_ args: [String]) -> Never {
        guard let path = args.first, let player = try? AVAudioPlayer(contentsOf: URL(fileURLWithPath: path)) else {
            FileHandle.standardError.write(Data("probe play FILE [--volume 0...1]\n".utf8))
            exit(2)
        }
        player.volume = Float(Probe.option("--volume", args) ?? "1") ?? 1
        player.prepareToPlay()
        player.play()
        print("play: pid \(getpid()), \(String(format: "%.1f", player.duration)) s")
        let end = Date().addingTimeInterval(player.duration + 0.3)
        while Date() < end { RunLoop.current.run(until: Date().addingTimeInterval(0.1)) }
        exit(0)
    }
}
