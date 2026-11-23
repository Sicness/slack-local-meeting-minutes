import Foundation

/// When to start and stop recording on its own. Pure logic: time and "Slack holds the mic" come from outside.
///
/// On a live huddle, Slack takes and releases the mic twice in the first 3 s, so recording starts
/// once the mic is held longer than `startAfter`. Muting in Slack does not release the mic, so recording stops
/// once Slack has not held the mic for `stopAfter`: that is the end of the call.
public struct AutoRecordPolicy: Sendable {
    public enum Kind: Sendable { case auto, manual }
    public enum Action: Equatable, Sendable { case none, start, stop }

    public var startAfter: Double
    public var stopAfter: Double
    public var enabled = true

    private var heldSince: Double?
    private var releasedSince: Double?
    private var callStarted = false
    /// "Don't Record" or a manual stop during a call: no auto-start until this call ends.
    public private(set) var suppressed = false

    public init(startAfter: Double = 3, stopAfter: Double = 15) {
        self.startAfter = startAfter
        self.stopAfter = stopAfter
    }

    /// A call is active: Slack holds the mic or released it less than `stopAfter` ago.
    public var callActive: Bool { callStarted }

    public mutating func suppressUntilCallEnds() {
        if callStarted { suppressed = true }
    }

    public mutating func update(now: Double, slackHoldsMic: Bool, recording: Kind?) -> Action {
        if slackHoldsMic {
            releasedSince = nil
            heldSince = heldSince ?? now
            if now - heldSince! >= startAfter { callStarted = true }
        } else {
            heldSince = nil
            releasedSince = releasedSince ?? now
            if callStarted, now - releasedSince! >= stopAfter {
                callStarted = false
                suppressed = false
            }
        }
        switch recording {
        case nil:
            return enabled && !suppressed && slackHoldsMic && callStarted ? .start : .none
        case .auto:
            return callStarted ? .none : .stop
        case .manual:
            return .none
        }
    }
}

/// The `ma process` queue after recording. Processing loads the GPU, which slows Slack, so during a call
/// no new job starts and a running one is paused (SIGSTOP to its process group).
public struct ProcessingPolicy: Sendable {
    public enum Action: Equatable, Sendable { case none, startNext, pause, resume }

    public static func decide(running: Bool, paused: Bool, callActive: Bool, pending: Int) -> Action {
        if running {
            if callActive && !paused { return .pause }
            if !callActive && paused { return .resume }
            return .none
        }
        return !callActive && pending > 0 ? .startNext : .none
    }
}

/// Recordings never processed yet (for example, the Mac was shut down right after a call).
public enum PendingRecordings {
    public static func find(in root: URL = Paths.recordings, exclude: Set<String> = []) -> [String] {
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: root.path) else { return [] }
        return names.sorted().filter { name in
            guard !exclude.contains(name),
                  let data = try? Data(contentsOf: root.appending(path: name).appending(path: "meta.json")),
                  let meta = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { return false }
            return meta["status"] as? String == "recorded" && meta["pipeline"] == nil
        }
    }
}

/// Daily audio cleanup (`ma cleanup`): whether it is due. The last successful run is the mtime of
/// `~/MeetingAssistant/cleanup.last`; a failed run is retried no sooner than `retryAfter`.
public struct CleanupSchedule: Sendable {
    public var every: TimeInterval = 24 * 3600
    public var retryAfter: TimeInterval = 3600
    public var lastAttempt: Date?

    public init() {}

    public static var stamp: URL { Paths.home.appending(path: "cleanup.last") }

    public static func lastSuccess(at url: URL = stamp) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }

    public static func markSuccess(at url: URL = stamp, now: Date = Date()) {
        try? Data(ISO8601DateFormatter().string(from: now).utf8).write(to: url)
    }

    public func due(lastSuccess: Date?, now: Date) -> Bool {
        if let lastAttempt, now.timeIntervalSince(lastAttempt) < retryAfter { return false }
        guard let lastSuccess else { return true }
        return now.timeIntervalSince(lastSuccess) >= every
    }
}
