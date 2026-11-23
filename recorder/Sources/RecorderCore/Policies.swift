import Foundation

/// Automatic recording is off until the user turns it on and accepts the consent notice.
public enum RecordingConsent {
    public static func autoRecordActive(stored: Bool?, noticeAccepted: Bool) -> Bool {
        stored == true && noticeAccepted
    }

    /// Auto-recording was left on by an older version: ask before it records anyone.
    public static func needsNoticeAtLaunch(stored: Bool?, noticeAccepted: Bool) -> Bool {
        stored == true && !noticeAccepted
    }
}

/// Which device to record the "Me" track from.
public enum MicPolicy {
    /// Release a Bluetooth mic this many seconds after Slack does, or the headset stays
    /// in call mode (16 kHz). This also rides out the bounce when joining a huddle (Slack takes
    /// and releases the mic twice in the first 3 s).
    public static let releaseDelay = 3.0

    public enum Decision: Equatable, Sendable {
        case keep
        case stop(reason: String)
        case use(DeviceInfo, reason: String)
    }

    /// - slackHoldsMic: some Slack process is recording from the mic
    /// - slackInput: its device, if visible to the recorder
    /// - current: the device being recorded now
    /// - slackReleasedFor: seconds since Slack held the mic (0: holding, infinity: not during this recording)
    public static func decide(slackHoldsMic: Bool, slackInput: DeviceInfo?, current: DeviceInfo?,
                              defaultInput: DeviceInfo?, slackReleasedFor: Double) -> Decision {
        if slackHoldsMic, let s = slackInput {
            return current?.uid == s.uid ? .keep : .use(s, reason: "Slack device")
        }
        if !slackHoldsMic, current != nil, slackReleasedFor < releaseDelay {
            return .keep
        }
        // Never open Bluetooth ourselves: it would switch headphones to headset mode before Slack does.
        if let d = defaultInput, !d.isBluetooth {
            return current?.uid == d.uid ? .keep : .use(d, reason: "default input")
        }
        if let current, current.isBluetooth, !slackHoldsMic {
            return .stop(reason: "Slack released the Bluetooth mic")
        }
        return .keep
    }
}

/// Watches for digital silence: during a call Slack emits faint noise rather than
/// pure zeros, so 10 s of pure zeros means a broken tap or a missing permission.
public struct SilenceWatch: Sendable {
    public let limit: Int
    public private(set) var seconds = 0

    public init(limit: Int = 10) { self.limit = limit }

    /// Call once a second. `expectSound`: audio is expected now; `silent`: only zeros or nothing arrived.
    /// Returns true when the limit is reached (the counter restarts).
    public mutating func feed(expectSound: Bool, silent: Bool) -> Bool {
        guard expectSound, silent else { seconds = 0; return false }
        seconds += 1
        if seconds >= limit { seconds = 0; return true }
        return false
    }
}

/// An output change counts once it has lasted `settle` seconds: while switching, the default
/// output and Slack's device don't change at the same moment.
public struct RouteTracker: Sendable {
    public private(set) var current: RouteDecision?
    private var candidate: RouteDecision?
    private var candidateSince = 0.0
    public let settle: Double

    public init(settle: Double = 1) { self.settle = settle }

    /// Returns a decision once accepted: the first immediately, later ones after `settle`.
    public mutating func feed(_ decision: RouteDecision, at time: Double) -> RouteDecision? {
        guard let current else {
            self.current = decision
            return decision
        }
        if decision.mode == current.mode && decision.deviceUID == current.deviceUID {
            candidate = nil
            return nil
        }
        if candidate?.deviceUID != decision.deviceUID || candidate?.mode != decision.mode {
            candidate = decision
            candidateSince = time
            return nil
        }
        guard time - candidateSince >= settle else { return nil }
        self.current = decision
        candidate = nil
        return decision
    }
}
