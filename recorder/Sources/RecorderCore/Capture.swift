import CoreAudio
import Foundation

/// Slack process audio: private mono tap -> private aggregate device with only this tap -> IOProc.
/// The aggregate has no real devices, so switching output (headphones/speakers) does not change it.
public final class TapCapture: @unchecked Sendable {
    public enum Target: Sendable, CustomStringConvertible {
        /// By bundle id (macOS 26). `restore`: the tap picks the process up again if it restarts.
        case bundleIDs([String], restore: Bool)
        /// By Core Audio process objects (not pids).
        case processes([AudioObjectID])

        public var description: String {
            switch self {
            case let .bundleIDs(ids, restore): "bundleIDs \(ids) restore=\(restore)"
            case let .processes(ids): "processes \(ids)"
            }
        }
    }

    public struct State: Sendable, Equatable {
        public var tappedProcesses: [AudioObjectID]
        public var bundleIDs: [String]
        public var restoreEnabled: Bool
        public var tapFormat: String
        public var aggregateRate: Double
        public var activeSubtaps: Int
    }

    public let label: String
    public let writer: TrackWriter
    /// Change before `start` / `rebuild`.
    public var target: Target
    public private(set) var startCount = 0
    /// Mute the processes while tapping (for testing: audio is recorded but not heard).
    public var mutesTappedAudio = false
    /// For `.bundleIDs`: extra processes to put in the description (empty by default).
    public var initialProcesses: [AudioObjectID] = []

    private var tap: AudioHardwareTap?
    private var aggregate: AudioHardwareAggregateDevice?
    private var runner: IOProcRunner?

    public init(label: String, target: Target, writer: TrackWriter) {
        self.label = label
        self.target = target
        self.writer = writer
    }

    public var isRunning: Bool { runner?.isRunning ?? false }

    public func start(reason: String) throws {
        let description: CATapDescription
        switch target {
        case let .bundleIDs(ids, restore):
            guard #available(macOS 26, *) else {
                throw RecorderError.message("Tapping by bundle id needs macOS 26")
            }
            description = CATapDescription(monoMixdownOfProcesses: initialProcesses)
            description.bundleIDs = ids
            description.isProcessRestoreEnabled = restore
        case let .processes(ids):
            description = CATapDescription(monoMixdownOfProcesses: ids)
        }
        description.uuid = UUID()
        description.name = "Meeting Recorder (\(label))"
        description.isPrivate = true
        description.muteBehavior = mutesTappedAudio ? .muted : .unmuted

        guard let tap = try AudioHardwareSystem.shared.makeProcessTap(description: description) else {
            throw RecorderError.message("makeProcessTap returned nil")
        }
        self.tap = tap
        do {
            let composition: [String: Any] = [
                kAudioAggregateDeviceNameKey: "Meeting Recorder Tap (\(label))",
                kAudioAggregateDeviceUIDKey: "local.meetingassistant.tap.\(label).\(UUID().uuidString)",
                kAudioAggregateDeviceIsPrivateKey: 1,
                kAudioAggregateDeviceIsStackedKey: 0,
                kAudioAggregateDeviceSubDeviceListKey: [[String: Any]](),
                kAudioAggregateDeviceTapListKey: [[
                    kAudioSubTapUIDKey: try tap.uid,
                    kAudioSubTapDriftCompensationKey: 0,
                ]],
            ]
            guard let aggregate = try AudioHardwareSystem.shared.makeAggregateDevice(description: composition) else {
                throw RecorderError.message("makeAggregateDevice returned nil")
            }
            self.aggregate = aggregate

            let format = inputFormat(of: aggregate) ?? (try? tap.format)
            guard let format, format.isFloat32 else {
                throw RecorderError.message("Unexpected tap format: \(format?.short ?? "none")")
            }
            let segment = writer.beginSegment(sampleRate: format.mSampleRate, info: [
                "kind": "tap", "target": target.description, "reason": reason,
                "tap_format": ((try? tap.format)?.short) ?? "?", "aggregate_format": format.short,
            ])
            let runner = IOProcRunner(deviceID: aggregate.id)
            try runner.start(ring: writer.ring, segment: segment)
            self.runner = runner
            startCount += 1
        } catch {
            stop()
            throw error
        }
    }

    /// Tear down strictly in reverse order: IOProc, aggregate, tap.
    public func stop() {
        runner?.stop()
        runner = nil
        if let aggregate { try? AudioHardwareSystem.shared.destroyAggregateDevice(aggregate) }
        aggregate = nil
        if let tap { try? AudioHardwareSystem.shared.destroyProcessTap(tap) }
        tap = nil
    }

    /// Recreate the tap and aggregate entirely (new segment).
    public func rebuild(reason: String) throws {
        stop()
        try start(reason: reason)
    }

    /// `.processes` only: change the process list of a live tap without recreating it.
    public func updateProcesses(_ ids: [AudioObjectID]) throws {
        guard case .processes = target, let tap else { return }
        target = .processes(ids)
        let description = try tap.description
        description.processes = ids
        try tap.setDescription(description)
    }

    public func state() -> State? {
        guard let tap, let aggregate else { return nil }
        let description = try? tap.description
        var bundleIDs: [String] = []
        var restoreEnabled = false
        if #available(macOS 26, *) {
            bundleIDs = description?.bundleIDs ?? []
            restoreEnabled = description?.isProcessRestoreEnabled ?? false
        }
        return State(
            tappedProcesses: description?.processes ?? [],
            bundleIDs: bundleIDs,
            restoreEnabled: restoreEnabled,
            tapFormat: ((try? tap.format)?.short) ?? "?",
            aggregateRate: (try? aggregate.nominalSampleRate) ?? 0,
            activeSubtaps: ((try? aggregate.activeSubtaps) ?? []).count)
    }
}

/// Microphone: IOProc directly on the input device. Sample rate, buffer size and hog mode stay untouched,
/// so Slack recording from the same device is unaffected.
public final class InputCapture: @unchecked Sendable {
    public let device: DeviceInfo
    public let writer: TrackWriter
    public private(set) var format: AudioStreamBasicDescription?
    private var runner: IOProcRunner?

    public init(device: DeviceInfo, writer: TrackWriter) {
        self.device = device
        self.writer = writer
    }

    public var isRunning: Bool { runner?.isRunning ?? false }

    public func start(reason: String) throws {
        let hw = AudioHardwareDevice(id: device.id)
        guard let format = inputFormat(of: hw), format.isFloat32 else {
            throw RecorderError.message("Unexpected input format on \(device.name): \(inputFormat(of: hw)?.short ?? "none")")
        }
        self.format = format
        let streamLatency = ((try? hw.streams) ?? []).first { (try? $0.direction) == .input }.flatMap { try? $0.latency } ?? 0
        let segment = writer.beginSegment(sampleRate: format.mSampleRate, info: [
            "kind": "input", "device": device.name, "uid": device.uid, "transport": device.transport,
            "format": format.short, "reason": reason,
            "latency_frames": String(((try? hw.inputLatency) ?? 0) + streamLatency),
            "safety_offset_frames": String((try? hw.inputSafetyOffset) ?? 0),
        ])
        let runner = IOProcRunner(deviceID: device.id)
        try runner.start(ring: writer.ring, segment: segment)
        self.runner = runner
    }

    public func stop() {
        runner?.stop()
        runner = nil
    }
}

/// Format of the device's first input stream (the tap stream on a tap aggregate).
func inputFormat(of device: AudioHardwareDevice) -> AudioStreamBasicDescription? {
    let streams = (try? device.streams) ?? []
    if let stream = streams.first(where: { (try? $0.direction) == .input }), let format = try? stream.virtualFormat {
        return format
    }
    return device.streamFormat(scope: kAudioObjectPropertyScopeInput)
}
