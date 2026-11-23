import CoreAudio
import Foundation

/// A recording's `meta.json`. The recorder writes it at start, every 30 s and at stop; pipeline steps then
/// add their fields. Track times are in host time (`host_ticks_per_second` ticks) and seconds from t0.
public struct RecordingMeta: Codable, Sendable {
    public var version = 1
    public var id: String
    /// recording: in progress (or the recorder crashed mid-recording); recorded: finished.
    public var status: String
    public var source = "slack"
    /// manual: "Record Manually"; auto: triggered by Slack's mic use.
    public var trigger: String
    public var startedAt: String
    public var endedAt: String?
    public var durationSeconds: Double
    /// Frame 0 of both tracks is this moment.
    public var t0HostTime: UInt64
    public var hostTicksPerSecond: Double
    public var mode: RecordingMode
    public var modeReason: String
    public var routeChanges: [RouteChange]
    /// When Slack held the mic (seconds from t0).
    public var callPeriods: [Period]
    public var tracks: Tracks
    public var warnings: [Warning]
    public var recorder: RecorderInfo

    public struct RouteChange: Codable, Sendable, Equatable {
        public var atSeconds: Double
        public var mode: RecordingMode
        public var reason: String
        public var deviceUID: String

        enum CodingKeys: String, CodingKey {
            case atSeconds = "at_seconds", mode, reason, deviceUID = "device_uid"
        }
    }

    public struct Period: Codable, Sendable, Equatable {
        public var startSeconds: Double
        public var endSeconds: Double?

        enum CodingKeys: String, CodingKey {
            case startSeconds = "start_seconds", endSeconds = "end_seconds"
        }
    }

    public struct Tracks: Codable, Sendable {
        /// Microphone: "Me" ("Room" in room mode).
        public var mic: TrackSummary
        /// Audio of Slack processes only: "Others".
        public var remote: TrackSummary
    }

    public struct Warning: Codable, Sendable, Equatable {
        public var atSeconds: Double
        public var text: String

        enum CodingKeys: String, CodingKey { case atSeconds = "at_seconds", text }
    }

    public struct RecorderInfo: Codable, Sendable {
        public var version: String
        public var pid: Int32
        public var bundleIDPrefixes: [String]

        enum CodingKeys: String, CodingKey { case version, pid, bundleIDPrefixes = "bundle_id_prefixes" }
    }

    enum CodingKeys: String, CodingKey {
        case version, id, status, source, trigger
        case startedAt = "started_at", endedAt = "ended_at", durationSeconds = "duration_seconds"
        case t0HostTime = "t0_host_time", hostTicksPerSecond = "host_ticks_per_second"
        case mode, modeReason = "mode_reason", routeChanges = "route_changes", callPeriods = "call_periods"
        case tracks, warnings, recorder
    }

    /// Atomic (temp file and rename), so a crash can't leave half-written JSON.
    public func write(to url: URL) throws {
        try Self.encoder.encode(self).write(to: url, options: .atomic)
    }

    public static func read(from url: URL) throws -> RecordingMeta {
        try JSONDecoder().decode(RecordingMeta.self, from: Data(contentsOf: url))
    }

    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes]
        return e
    }()

    /// 2026-01-15T10:15:02.120+00:00
    public static func timestamp(_ date: Date) -> String {
        date.formatted(Date.ISO8601FormatStyle(timeZoneSeparator: .colon, includingFractionalSeconds: true,
                                               timeZone: .current))
    }

    public static var hostTicksPerSecond: Double { AudioGetHostClockFrequency() }
}

public enum RecordingID {
    /// 2026-01-15_1015_ab12: start date and time plus 4 random hex digits.
    public static func make(date: Date = Date()) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd_HHmm"
        return "\(f.string(from: date))_\(String(format: "%04x", UInt16.random(in: 0...UInt16.max)))"
    }
}
