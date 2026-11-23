import Foundation
import Testing
@testable import RecorderCore

@Suite("meta.json")
struct MetaTests {
    func sample() -> RecordingMeta {
        let track = TrackSummary(file: "mic.caf", sampleRate: 16_000, frames: 16_000, seconds: 1,
                                 segments: [SegmentRecord(index: 0, startFrame: 0, hostTime: 10, secondsFromT0: 0,
                                                          sampleRate: 16_000, paddedFrames: 0, droppedLeadingFrames: 0,
                                                          info: ["device": "BT Headset"],
                                                          anchors: [TimeAnchor(frame: 0, sampleTime: 0, hostTime: 10, rateScalar: 1)])],
                                 overflowFrames: 0, discontinuities: 0, overlaps: 0, errors: [])
        return RecordingMeta(
            id: "2026-01-15_1015_ab12", status: "recorded", trigger: "manual",
            startedAt: RecordingMeta.timestamp(Date()), endedAt: nil, durationSeconds: 1,
            t0HostTime: 10, hostTicksPerSecond: 24_000_000, mode: .headphones, modeReason: "Bluetooth: BT Headset",
            routeChanges: [.init(atSeconds: 0.5, mode: .room, reason: "built-in speakers", deviceUID: "x")],
            callPeriods: [.init(startSeconds: 0.2, endSeconds: nil)],
            tracks: .init(mic: track, remote: track), warnings: [.init(atSeconds: 1, text: "w")],
            recorder: .init(version: "0.1", pid: 1, bundleIDPrefixes: ["com.tinyspeck.slackmacgap"]))
    }

    @Test("snake_case keys, as the pipeline expects")
    func keys() throws {
        let data = try RecordingMeta.encoder.encode(sample())
        let json = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        for key in ["version", "id", "status", "source", "trigger", "started_at", "duration_seconds", "t0_host_time",
                    "host_ticks_per_second", "mode", "mode_reason", "route_changes", "call_periods", "tracks", "warnings"] {
            #expect(json[key] != nil, "missing key \(key)")
        }
        #expect(json["mode"] as? String == "headphones")
        let mic = try #require((json["tracks"] as? [String: Any])?["mic"] as? [String: Any])
        #expect(mic["sample_rate"] as? Double == 16_000)
        let segment = try #require((mic["segments"] as? [[String: Any]])?.first)
        #expect(segment["start_frame"] != nil && segment["seconds_from_t0"] != nil)
        let anchor = try #require((segment["anchors"] as? [[String: Any]])?.first)
        #expect(Set(anchor.keys) == ["frame", "sample_time", "host_time", "rate_scalar"])
        let change = try #require((json["route_changes"] as? [[String: Any]])?.first)
        #expect(change["at_seconds"] as? Double == 0.5)
    }

    @Test("timestamp with milliseconds and time zone")
    func timestamp() {
        let s = RecordingMeta.timestamp(Date())
        #expect(s.wholeMatch(of: /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}([+-]\d{2}:\d{2}|Z)/) != nil, "\(s)")
    }

    @Test("write and read")
    func roundTrip() throws {
        let url = FileManager.default.temporaryDirectory.appending(path: "meta-\(UUID().uuidString).json")
        defer { try? FileManager.default.removeItem(at: url) }
        try sample().write(to: url)
        let back = try RecordingMeta.read(from: url)
        #expect(back.id == "2026-01-15_1015_ab12")
        #expect(back.tracks.mic.segments.first?.info["device"] == "BT Headset")
        #expect(back.routeChanges.first?.mode == .room)
    }

    @Test("recording id: date_time_hex")
    func recordingID() {
        let id = RecordingID.make()
        #expect(id.wholeMatch(of: /\d{4}-\d{2}-\d{2}_\d{4}_[0-9a-f]{4}/) != nil, "\(id)")
    }
}
