import AVFoundation
import CoreAudio
import Foundation
import Testing
@testable import RecorderCore

/// TrackWriter on synthetic audio: segments land by host time and the track is padded to the end.
@Suite("TrackWriter")
struct TrackWriterTests {
    let dir: URL

    init() throws {
        dir = FileManager.default.temporaryDirectory.appending(path: "rc-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    func host(_ t0: UInt64, _ seconds: Double) -> UInt64 {
        seconds >= 0 ? t0 + AudioConvertNanosToHostTime(UInt64(seconds * 1e9))
                     : t0 - AudioConvertNanosToHostTime(UInt64(-seconds * 1e9))
    }

    /// A sine in chunks of `chunk` frames, like IOProc. `jumpAt`: chunk index where mSampleTime jumps by `jump`.
    func push(_ w: TrackWriter, segment: Int32, t0: UInt64, start: Double, rate: Double, seconds: Double,
              chunk: Int = 480, jumpAt: Int? = nil, jump: Double = 0) {
        let buffer = UnsafeMutablePointer<Float>.allocate(capacity: chunk)
        defer { buffer.deallocate() }
        let count = Int(seconds * rate) / chunk
        for i in 0..<count {
            for j in 0..<chunk { buffer[j] = 0.5 * Float(sin(2 * Double.pi * 440 * Double(i * chunk + j) / rate)) }
            let extra = (jumpAt.map { i >= $0 } ?? false) ? jump : 0
            let offset = Double(i * chunk) / rate + extra / rate
            w.ring.push(segment: segment, hostTime: host(t0, start + offset), hostTimeValid: true,
                        sampleTime: Double(i * chunk) + extra, rateScalar: 1, source: buffer, stride: 1, frames: chunk)
        }
    }

    func read(_ url: URL) throws -> (rate: Double, samples: [Float]) {
        // AVAudioFile.read returns frames in batches, so read to the end.
        let file = try AVAudioFile(forReading: url)
        let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 8192)!
        var out: [Float] = []
        while file.framePosition < file.length {
            try file.read(into: buffer)
            if buffer.frameLength == 0 { break }
            out += UnsafeBufferPointer(start: buffer.floatChannelData![0], count: Int(buffer.frameLength))
        }
        return (file.processingFormat.sampleRate, out)
    }

    @Test("segment from 0.5 s: silence before and after, length to the end of recording")
    func padding() throws {
        let t0 = HostClock.now()
        let url = dir.appending(path: "a.caf")
        let w = TrackWriter(url: url, label: "a", t0: t0)
        let seg = w.beginSegment(sampleRate: 48_000, info: [:])
        push(w, segment: seg, t0: t0, start: 0.5, rate: 48_000, seconds: 1)
        let s = w.finish(padToHostTime: host(t0, 2))
        #expect(s.frames == 96_000)
        #expect(s.errors.isEmpty)
        #expect(s.segments.count == 1)
        #expect(s.segments[0].paddedFrames == 24_000)
        #expect(s.segments[0].startFrame == 24_000)
        let (rate, x) = try read(url)
        #expect(rate == 48_000)
        #expect(x.count == 96_000)
        #expect(x[0..<24_000].allSatisfy { $0 == 0 })
        #expect(x[24_000..<72_000].contains { $0 != 0 })
        #expect(x[72_000...].allSatisfy { $0 == 0 })
    }

    @Test("a second segment at another rate lands in place")
    func secondSegmentOtherRate() throws {
        let t0 = HostClock.now()
        let url = dir.appending(path: "b.caf")
        let w = TrackWriter(url: url, label: "b", t0: t0)
        let a = w.beginSegment(sampleRate: 48_000, info: ["n": "1"])
        push(w, segment: a, t0: t0, start: 0, rate: 48_000, seconds: 1)
        let b = w.beginSegment(sampleRate: 16_000, info: ["n": "2"])
        push(w, segment: b, t0: t0, start: 1.5, rate: 16_000, seconds: 1)
        let s = w.finish(padToHostTime: host(t0, 3))
        #expect(s.sampleRate == 48_000)
        #expect(s.segments.count == 2)
        #expect(s.segments[1].startFrame == 72_000)
        #expect(s.segments[1].paddedFrames == 24_000)
        #expect(s.segments[1].sampleRate == 16_000)
        let (_, x) = try read(url)
        #expect(s.errors.isEmpty)
        #expect(x.count == Int(s.frames))
        #expect(abs(x.count - 144_000) < 100)   // the converter tail is a few frames
        // 16 kHz is resampled to 48 kHz, not written as is: 33 chunks of 480 frames = 47,520 frames at 48 kHz
        let active = x[72_000..<min(x.count, 144_000)].lastIndex { abs($0) > 0.01 }.map { $0 - 72_000 } ?? 0
        #expect(abs(active - 47_520) < 50, "audio up to frame \(active)")
    }

    @Test("mSampleTime jump inside a segment: silence at the gap")
    func discontinuity() {
        let t0 = HostClock.now()
        let w = TrackWriter(url: dir.appending(path: "c.caf"), label: "c", t0: t0)
        let seg = w.beginSegment(sampleRate: 48_000, info: [:])
        push(w, segment: seg, t0: t0, start: 0, rate: 48_000, seconds: 1, jumpAt: 50, jump: 960)
        let s = w.finish()
        #expect(s.discontinuities == 1)
        #expect(s.frames == 48_000 + 960)
    }

    @Test("audio before t0 is trimmed")
    func dropLeading() throws {
        let t0 = HostClock.now()
        let url = dir.appending(path: "d.caf")
        let w = TrackWriter(url: url, label: "d", t0: t0)
        let seg = w.beginSegment(sampleRate: 48_000, info: [:])
        push(w, segment: seg, t0: t0, start: -0.1, rate: 48_000, seconds: 1)
        let s = w.finish()
        #expect(s.segments[0].droppedLeadingFrames == 4_800)
        #expect(s.frames == 48_000 - 4_800)
    }

    @Test("no audio: a silent file of the right length")
    func empty() throws {
        let t0 = HostClock.now()
        let url = dir.appending(path: "e.caf")
        let w = TrackWriter(url: url, label: "e", t0: t0)
        let s = w.finish(padToHostTime: host(t0, 1.5), defaultRate: 16_000)
        #expect(s.frames == 24_000)
        #expect(s.segments.isEmpty)
        let (rate, x) = try read(url)
        #expect(rate == 16_000)
        #expect(x.count == 24_000)
        #expect(x.allSatisfy { $0 == 0 })
    }

    @Test("time anchors every 10 s")
    func anchors() {
        let t0 = HostClock.now()
        let w = TrackWriter(url: dir.appending(path: "f.caf"), label: "f", t0: t0, anchorEvery: 0.25)
        let seg = w.beginSegment(sampleRate: 48_000, info: [:])
        push(w, segment: seg, t0: t0, start: 0, rate: 48_000, seconds: 1)
        let s = w.finish()
        #expect(s.segments[0].anchors.count == 4)
        #expect(s.segments[0].anchors[0].frame == 0)
    }
}
