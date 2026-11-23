import Foundation
import Testing
@testable import RecorderCore

/// Runs the policy in 0.5 s steps: (second, Slack holds the mic) -> action at each step.
private func run(_ p: inout AutoRecordPolicy, from: Double, to: Double, holds: Bool,
                 recording: inout AutoRecordPolicy.Kind?) -> [(Double, AutoRecordPolicy.Action)] {
    var out: [(Double, AutoRecordPolicy.Action)] = []
    var t = from
    while t < to {
        let a = p.update(now: t, slackHoldsMic: holds, recording: recording)
        if a == .start { recording = .auto }
        if a == .stop { recording = nil }
        if a != .none { out.append((t, a)) }
        t += 0.5
    }
    return out
}

@Suite("automatic call recording")
struct AutoRecordTests {
    @Test("bounce on joining a huddle doesn't start recording, a call starts it after 3 s")
    func startDebounce() {
        var p = AutoRecordPolicy(startAfter: 3, stopAfter: 15)
        var rec: AutoRecordPolicy.Kind? = nil
        // Slack takes and releases the mic twice in the first 3 s
        #expect(run(&p, from: 0, to: 1, holds: true, recording: &rec).isEmpty)
        #expect(run(&p, from: 1, to: 1.5, holds: false, recording: &rec).isEmpty)
        #expect(run(&p, from: 1.5, to: 2.5, holds: true, recording: &rec).isEmpty)
        #expect(run(&p, from: 2.5, to: 3, holds: false, recording: &rec).isEmpty)
        #expect(!p.callActive)
        let started = run(&p, from: 3, to: 10, holds: true, recording: &rec)
        #expect(started.map(\.1) == [.start])
        #expect(started.first?.0 == 6)
        #expect(p.callActive && rec == .auto)
    }

    @Test("a short mic pause doesn't stop recording, 15 s does")
    func stopAfterRelease() {
        var p = AutoRecordPolicy(startAfter: 3, stopAfter: 15)
        var rec: AutoRecordPolicy.Kind? = nil
        _ = run(&p, from: 0, to: 10, holds: true, recording: &rec)
        #expect(run(&p, from: 10, to: 20, holds: false, recording: &rec).isEmpty)   // 10 s is still the call
        _ = run(&p, from: 20, to: 30, holds: true, recording: &rec)
        let stopped = run(&p, from: 30, to: 60, holds: false, recording: &rec)
        #expect(stopped.map(\.1) == [.stop] && stopped.first?.0 == 45)
        #expect(!p.callActive && rec == nil)
        #expect(run(&p, from: 60, to: 70, holds: false, recording: &rec).isEmpty)
    }

    @Test("Don't Record lasts until the call ends; the next call is recorded")
    func suppress() {
        var p = AutoRecordPolicy(startAfter: 3, stopAfter: 15)
        var rec: AutoRecordPolicy.Kind? = nil
        _ = run(&p, from: 0, to: 5, holds: true, recording: &rec)
        #expect(rec == .auto)
        p.suppressUntilCallEnds()
        rec = nil   // recording deleted
        #expect(run(&p, from: 5, to: 30, holds: true, recording: &rec).isEmpty)
        #expect(run(&p, from: 30, to: 40, holds: false, recording: &rec).isEmpty)   // pause < 15 s is the same call
        #expect(run(&p, from: 40, to: 45, holds: true, recording: &rec).isEmpty)
        _ = run(&p, from: 45, to: 61, holds: false, recording: &rec)               // the call ended
        #expect(!p.suppressed)
        #expect(run(&p, from: 61, to: 70, holds: true, recording: &rec).map(\.1) == [.start])
    }

    @Test("manual recording doesn't stop itself; disabled auto-record doesn't start")
    func manualAndDisabled() {
        var p = AutoRecordPolicy(startAfter: 3, stopAfter: 15)
        var rec: AutoRecordPolicy.Kind? = .manual
        #expect(run(&p, from: 0, to: 10, holds: true, recording: &rec).isEmpty)
        #expect(run(&p, from: 10, to: 60, holds: false, recording: &rec).isEmpty)
        #expect(rec == .manual)
        var q = AutoRecordPolicy(startAfter: 3, stopAfter: 15)
        q.enabled = false
        var none: AutoRecordPolicy.Kind? = nil
        #expect(run(&q, from: 0, to: 20, holds: true, recording: &none).isEmpty)
        #expect(q.callActive)                       // the call is still seen, so processing waits
    }

    @Test("suppressing outside a call does nothing")
    func suppressOutsideCall() {
        var p = AutoRecordPolicy()
        p.suppressUntilCallEnds()
        #expect(!p.suppressed)
    }
}

@Suite("processing queue")
struct ProcessingTests {
    @Test("during a call don't start and pause; resume afterwards")
    func decide() {
        #expect(ProcessingPolicy.decide(running: false, paused: false, callActive: false, pending: 1) == .startNext)
        #expect(ProcessingPolicy.decide(running: false, paused: false, callActive: true, pending: 1) == .none)
        #expect(ProcessingPolicy.decide(running: false, paused: false, callActive: false, pending: 0) == .none)
        #expect(ProcessingPolicy.decide(running: true, paused: false, callActive: true, pending: 0) == .pause)
        #expect(ProcessingPolicy.decide(running: true, paused: true, callActive: true, pending: 0) == .none)
        #expect(ProcessingPolicy.decide(running: true, paused: true, callActive: false, pending: 3) == .resume)
        #expect(ProcessingPolicy.decide(running: true, paused: false, callActive: false, pending: 3) == .none)
    }

    @Test("a deferred retry is queued on time and doesn't run during a call")
    func retry() {
        let r = ProcessingRunner(uv: "/usr/bin/false", pipeline: "/tmp", log: AppLog(fileURL: nil))
        let now = Date()
        r.retryLater("x", after: 60)
        _ = r.tick(callActive: true, now: now.addingTimeInterval(30))
        #expect(r.queue.isEmpty)
        _ = r.tick(callActive: true, now: now.addingTimeInterval(61))
        #expect(r.queue == ["x"] && r.retries.isEmpty && r.current == nil)
    }

    @Test("unprocessed recordings: recorded without pipeline")
    func pending() throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "ma-pending-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        func make(_ id: String, _ meta: [String: Any]) throws {
            let dir = root.appending(path: id)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try JSONSerialization.data(withJSONObject: meta).write(to: dir.appending(path: "meta.json"))
        }
        try make("a", ["status": "recorded"])
        try make("b", ["status": "recorded", "pipeline": ["prepare": ["status": "done"]]])
        try make("c", ["status": "recording"])
        try make("d", ["status": "recorded"])
        #expect(PendingRecordings.find(in: root) == ["a", "d"])
        #expect(PendingRecordings.find(in: root, exclude: ["d"]) == ["a"])
    }

    @Test("[recorder]: start, processing, paths")
    func config() {
        let (c, w) = RecorderConfig.parse("""
        [recorder]
        start_after_seconds = 5
        stop_after_seconds = 20
        process_after_stop = false
        uv = "~/bin/uv"
        pipeline = "/x/pipeline"
        """)
        #expect(w.isEmpty)
        #expect(c.startAfterSeconds == 5 && c.stopAfterSeconds == 20 && !c.processAfterStop)
        #expect(c.uv == "~/bin/uv" && c.pipelineDir == "/x/pipeline")
        let (d, w2) = RecorderConfig.parse("[recorder]\nstart_after_seconds = -1\nprocess_after_stop = \"yes\"\nuv = 3\n")
        #expect(d.startAfterSeconds == 3 && d.processAfterStop && d.uv == nil && w2.count == 3)
    }

    @Test("pipeline next to the app: recorder/build/X.app -> repository/pipeline")
    func location() {
        let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let app = repo.appending(path: "recorder/build/MeetingRecorder.app")
        #expect(PipelineLocation.pipelineDir(config: RecorderConfig(), bundle: app) == repo.appending(path: "pipeline").path)
        var c = RecorderConfig()
        c.pipelineDir = "/elsewhere"
        #expect(PipelineLocation.pipelineDir(config: c, bundle: app) == "/elsewhere")
    }
}

@Suite("daily audio cleanup")
struct CleanupTests {
    @Test("once a day; retry an hour after failure; the stamp is the file mtime")
    func schedule() throws {
        var s = CleanupSchedule()
        let now = Date()
        #expect(s.due(lastSuccess: nil, now: now))                                       // never run
        #expect(!s.due(lastSuccess: now.addingTimeInterval(-23 * 3600), now: now))
        #expect(s.due(lastSuccess: now.addingTimeInterval(-25 * 3600), now: now))
        s.lastAttempt = now.addingTimeInterval(-600)                                     // just failed
        #expect(!s.due(lastSuccess: nil, now: now))
        #expect(s.due(lastSuccess: nil, now: now.addingTimeInterval(3600)))
        let url = FileManager.default.temporaryDirectory.appending(path: "ma-cleanup-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: url) }
        #expect(CleanupSchedule.lastSuccess(at: url) == nil)
        CleanupSchedule.markSuccess(at: url)
        let last = try #require(CleanupSchedule.lastSuccess(at: url))
        #expect(abs(last.timeIntervalSinceNow) < 5)
    }

    @Test("cleanup waits for the call to end")
    func runner() {
        // Without a call, tick would launch a process, so this only checks the wait.
        let r = ProcessingRunner(uv: "/usr/bin/false", pipeline: "/tmp", log: AppLog(fileURL: nil))
        r.requestCleanup()
        r.requestCleanup()
        #expect(r.cleanupPending)
        _ = r.tick(callActive: true)
        #expect(r.cleanupPending && r.current == nil)
    }

    @Test("[recorder] cleanup_daily")
    func config() {
        #expect(RecorderConfig().cleanupDaily)
        let (c, w) = RecorderConfig.parse("[recorder]\ncleanup_daily = false\n")
        #expect(!c.cleanupDaily && w.isEmpty)
        let (d, w2) = RecorderConfig.parse("[recorder]\ncleanup_daily = 1\n")
        #expect(d.cleanupDaily && w2.count == 1)
    }
}
