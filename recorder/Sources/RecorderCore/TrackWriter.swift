import AVFAudio
import AudioToolbox
import CoreAudio
import Foundation
import Synchronization

/// Time anchor: which file frame corresponds to which host time.
public struct TimeAnchor: Codable, Sendable {
    public var frame: Int64
    public var sampleTime: Double
    public var hostTime: UInt64
    public var rateScalar: Double

    enum CodingKeys: String, CodingKey {
        case frame, sampleTime = "sample_time", hostTime = "host_time", rateScalar = "rate_scalar"
    }
}

/// A continuous stretch of recording from one device start.
public struct SegmentRecord: Codable, Sendable {
    public var index: Int
    public var startFrame: Int64
    public var hostTime: UInt64
    public var secondsFromT0: Double
    public var sampleRate: Double
    /// Silence inserted before the segment to place it correctly by host time.
    public var paddedFrames: Int64
    /// Frames dropped at the segment start: audio arrived before t0 or before the previous segment ended.
    public var droppedLeadingFrames: Int64
    public var info: [String: String]
    public var anchors: [TimeAnchor]

    enum CodingKeys: String, CodingKey {
        case index, startFrame = "start_frame", hostTime = "host_time", secondsFromT0 = "seconds_from_t0"
        case sampleRate = "sample_rate", paddedFrames = "padded_frames"
        case droppedLeadingFrames = "dropped_leading_frames", info, anchors
    }
}

public struct TrackSummary: Codable, Sendable {
    public var file: String
    public var sampleRate: Double
    public var frames: Int64
    public var seconds: Double
    public var segments: [SegmentRecord]
    public var overflowFrames: Int
    public var discontinuities: Int
    public var overlaps: Int
    public var errors: [String]

    enum CodingKeys: String, CodingKey {
        case file, sampleRate = "sample_rate", frames, seconds, segments
        case overflowFrames = "overflow_frames", discontinuities, overlaps, errors
    }
}

/// Levels over a period (for logs and warnings).
public struct TrackStats: Sendable {
    public var frames = 0
    public var sumSquares = 0.0
    public var peak: Float = 0
    public var chunks = 0
    public var zeroChunks = 0
    public var discontinuities = 0

    public var rmsDB: Double { frames > 0 ? 10 * log10(max(sumSquares / Double(frames), 1e-12)) : -120 }
    public var peakDB: Double { 20 * log10(max(Double(peak), 1e-6)) }
}


/// Writes one track to CAF (mono Float32). Audio arrives through `ring` from IOProc
/// and is written to the file from a separate queue every 50 ms.
///
/// Timeline: file frame 0 is t0. Each segment is placed by the host time of its first
/// frame (silence before it). Within a segment, silence is inserted only if mSampleTime jumps.
/// The file rate is the first segment's rate; segments at other rates are resampled by their own
/// converter (ExtAudioFile can't change rate on the fly), so the file is written through AudioFile.
public final class TrackWriter: @unchecked Sendable {
    public let label: String
    public let url: URL
    public let ring: SampleRing
    public let t0: UInt64

    private let queue: DispatchQueue
    private var timer: DispatchSourceTimer?
    private let specs = Mutex<[Int32: (rate: Double, info: [String: String])]>([:])
    private let nextSegment = Atomic<Int32>(0)
    private let stats = Mutex(TrackStats())
    private let updateSizes: Bool
    private let anchorEvery: Double

    // The state below changes only on `queue`.
    private var file: AudioFileID?
    private var fileRate = 0.0
    /// Frames in the file, including those not yet flushed from `pending`.
    private var framesWritten: Int64 = 0
    private var pending: [Float] = []
    private var segmentRate = 0.0
    private var resampler: Resampler?
    private var currentSegment: Int32 = -1
    private var expectedSampleTime = 0.0
    private var pendingSkip = 0
    private var segments: [SegmentRecord] = []
    private var lastAnchorHost: UInt64 = 0
    private var discontinuities = 0
    private var overlaps = 0
    private var errors: [String] = []
    private let silence: UnsafeMutablePointer<Float>
    private let silenceFrames = 4096

    /// `updateSizes`: update the header's data size on every write. CAF is readable after a crash
    /// even without it (data size -1 means "to end of file"), but this is safer for other readers.
    public init(url: URL, label: String, t0: UInt64, updateSizes: Bool = true, anchorEvery: Double = 10) {
        self.url = url
        self.label = label
        self.t0 = t0
        self.updateSizes = updateSizes
        self.anchorEvery = anchorEvery
        ring = SampleRing()
        queue = DispatchQueue(label: "writer.\(label)", qos: .userInitiated)
        silence = .allocate(capacity: silenceFrames)
        silence.initialize(repeating: 0, count: silenceFrames)
        pending.reserveCapacity(48_000)
    }

    deinit { silence.deallocate() }

    public func start() {
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + 0.05, repeating: 0.05)
        t.setEventHandler { [weak self] in self?.drain() }
        t.resume()
        timer = t
    }

    /// New segment: call before starting IOProc and pass the number to `SampleRing.push`.
    public func beginSegment(sampleRate: Double, info: [String: String]) -> Int32 {
        let id = nextSegment.wrappingAdd(1, ordering: .relaxed).oldValue
        specs.withLock { $0[id] = (sampleRate, info) }
        return id
    }

    /// Levels since the last call (and reset).
    public func takeStats() -> TrackStats {
        stats.withLock { s in
            let copy = s
            s = TrackStats()
            return copy
        }
    }

    public var overflowFrames: Int { ring.overflowFrames }

    /// Writes the remainder and closes the file. Devices must already be stopped.
    /// `padToHostTime`: pad with silence up to this moment so a recording's tracks have equal length;
    /// if there was no audio at all, the file is created at `defaultRate` and is all silence.
    public func finish(padToHostTime end: UInt64? = nil, defaultRate: Double = 48_000) -> TrackSummary {
        queue.sync {
            timer?.cancel()
            timer = nil
            drain()
            flushResampler()
            if let end {
                if file == nil { openFile(rate: defaultRate) }
                let target = Int64((HostClock.seconds(from: t0, to: end) * fileRate).rounded())
                if file != nil, target > framesWritten { appendSilence(fileFrames: Int(target - framesWritten)) }
            }
            flushPending()
            if let file {
                let status = AudioFileClose(file)
                if status != noErr { errors.append("AudioFileClose: \(fourCC(UInt32(bitPattern: status)))") }
                self.file = nil
            }
            return summaryLocked()
        }
    }

    /// Current state (for meta.json during recording).
    public func snapshot() -> TrackSummary { queue.sync { summaryLocked() } }

    /// Segments so far.
    public func segmentsSnapshot() -> [SegmentRecord] { queue.sync { segments } }

    private func summaryLocked() -> TrackSummary {
        TrackSummary(
            file: url.lastPathComponent, sampleRate: fileRate, frames: framesWritten,
            seconds: fileRate > 0 ? Double(framesWritten) / fileRate : 0,
            segments: segments, overflowFrames: ring.overflowFrames,
            discontinuities: discontinuities, overlaps: overlaps, errors: errors)
    }

    // MARK: - Writer queue

    private func drain() {
        ring.drain { header, samples in process(header, samples) }
        flushPending()
    }

    private func process(_ header: ChunkHeader, _ samples: UnsafeBufferPointer<Float>) {
        if header.segment != currentSegment {
            guard let spec = specs.withLock({ $0[header.segment] }) else { return }
            startSegment(header, rate: spec.rate, info: spec.info)
        }

        // A gap inside the segment (the device lost frames): fill with silence.
        let delta = header.sampleTime - expectedSampleTime
        if delta > 0.5 {
            discontinuities += 1
            stats.withLock { $0.discontinuities += 1 }
            appendSegmentSilence(frames: Int(delta.rounded()))
        } else if delta < -0.5 {
            overlaps += 1
        }
        expectedSampleTime = header.sampleTime + Double(header.frames)

        if !header.isContinuation, header.hostTimeValid,
           lastAnchorHost == 0 || HostClock.seconds(from: lastAnchorHost, to: header.hostTime) >= anchorEvery {
            lastAnchorHost = header.hostTime
            if !segments.isEmpty {
                segments[segments.count - 1].anchors.append(
                    TimeAnchor(frame: framesWritten, sampleTime: header.sampleTime,
                               hostTime: header.hostTime, rateScalar: header.rateScalar))
            }
        }

        var sumSquares = 0.0
        var peak: Float = 0
        for v in samples {
            sumSquares += Double(v * v)
            peak = max(peak, abs(v))
        }
        stats.withLock {
            $0.frames += samples.count
            $0.sumSquares += sumSquares
            $0.peak = max($0.peak, peak)
            $0.chunks += 1
            if peak == 0 { $0.zeroChunks += 1 }
        }

        var start = 0
        if pendingSkip > 0 {
            start = min(pendingSkip, samples.count)
            pendingSkip -= start
        }
        if start < samples.count, let base = samples.baseAddress {
            appendSegment(base + start, frames: samples.count - start)
        }
    }

    private func startSegment(_ header: ChunkHeader, rate: Double, info: [String: String]) {
        flushResampler()
        if file == nil { openFile(rate: rate) }
        segmentRate = rate
        resampler = rate == fileRate ? nil : Resampler(from: rate, to: fileRate)
        if rate != fileRate, resampler == nil { errors.append("no converter \(rate) -> \(fileRate)") }

        let seconds = HostClock.seconds(from: t0, to: header.hostTime)
        let target = Int64((seconds * fileRate).rounded())
        var padded: Int64 = 0
        var dropped: Int64 = 0
        if target > framesWritten {
            padded = target - framesWritten
            appendSilence(fileFrames: Int(padded))
        } else if target < framesWritten {
            dropped = framesWritten - target
            pendingSkip = Int((Double(dropped) * rate / fileRate).rounded())
        }
        segments.append(SegmentRecord(
            index: segments.count, startFrame: framesWritten, hostTime: header.hostTime,
            secondsFromT0: seconds, sampleRate: rate, paddedFrames: padded,
            droppedLeadingFrames: dropped, info: info, anchors: []))
        currentSegment = header.segment
        expectedSampleTime = header.sampleTime
        lastAnchorHost = 0
    }

    // MARK: - File output

    /// Segment frames (at its rate): written directly or through the converter.
    private func appendSegment(_ pointer: UnsafePointer<Float>, frames: Int) {
        if let resampler {
            resampler.convert(pointer, frames: frames) { appendFile($0, frames: $1) }
        } else {
            appendFile(pointer, frames: frames)
        }
    }

    /// Silence inside a segment takes the same path, so it can't interleave with the converter's tail.
    private func appendSegmentSilence(frames: Int) {
        var left = frames
        while left > 0 {
            let n = min(left, silenceFrames)
            appendSegment(silence, frames: n)
            left -= n
        }
    }

    /// Silence at the file rate (between segments and at the end).
    private func appendSilence(fileFrames: Int) {
        var left = fileFrames
        while left > 0 {
            let n = min(left, silenceFrames)
            appendFile(silence, frames: n)
            left -= n
        }
    }

    private func flushResampler() {
        resampler?.flush { appendFile($0, frames: $1) }
        resampler = nil
    }

    private func appendFile(_ pointer: UnsafePointer<Float>, frames: Int) {
        guard file != nil, frames > 0 else { return }
        pending.append(contentsOf: UnsafeBufferPointer(start: pointer, count: frames))
        framesWritten += Int64(frames)
    }

    /// One write call per queue pass (every 50 ms), not per buffer.
    private func flushPending() {
        guard let file, !pending.isEmpty else { return }
        let byteOffset = (framesWritten - Int64(pending.count)) * 4
        var bytes = UInt32(pending.count * 4)
        let status = pending.withUnsafeBytes { AudioFileWriteBytes(file, false, byteOffset, &bytes, $0.baseAddress!) }
        if status != noErr, errors.count < 20 {
            errors.append("AudioFileWriteBytes: \(fourCC(UInt32(bitPattern: status)))")
        }
        pending.removeAll(keepingCapacity: true)
    }

    private func openFile(rate: Double) {
        var format = AudioStreamBasicDescription.monoFloat32(rate: rate)
        var id: AudioFileID?
        let status = AudioFileCreateWithURL(url as CFURL, kAudioFileCAFType, &format, .eraseFile, &id)
        guard status == noErr, let id else {
            errors.append("AudioFileCreateWithURL: \(fourCC(UInt32(bitPattern: status)))")
            return
        }
        file = id
        fileRate = rate
        if updateSizes {
            var deferSizes: UInt32 = 0
            AudioFileSetProperty(id, kAudioFilePropertyDeferSizeUpdates, UInt32(MemoryLayout<UInt32>.size), &deferSizes)
        }
    }
}

/// Resamples one segment of mono Float32. The converter's tail comes out in `flush`.
final class Resampler {
    private let converter: AVAudioConverter
    private let input: AVAudioPCMBuffer
    private let output: AVAudioPCMBuffer
    private let inputCapacity = 4096

    init?(from inRate: Double, to outRate: Double) {
        guard let inFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: inRate, channels: 1, interleaved: false),
              let outFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: outRate, channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: inFormat, to: outFormat),
              let input = AVAudioPCMBuffer(pcmFormat: inFormat, frameCapacity: AVAudioFrameCount(inputCapacity)),
              let output = AVAudioPCMBuffer(pcmFormat: outFormat,
                                            frameCapacity: AVAudioFrameCount(Double(inputCapacity) * outRate / inRate) + 4096)
        else { return nil }
        converter.sampleRateConverterQuality = AVAudioQuality.high.rawValue
        converter.primeMethod = .normal   // no delay: the segment start stays in place
        self.converter = converter
        self.input = input
        self.output = output
    }

    func convert(_ pointer: UnsafePointer<Float>, frames: Int, sink: (UnsafePointer<Float>, Int) -> Void) {
        var offset = 0
        while offset < frames {
            let n = min(inputCapacity, frames - offset)
            input.floatChannelData![0].update(from: pointer + offset, count: n)
            input.frameLength = AVAudioFrameCount(n)
            run(endOfStream: false, sink: sink)
            offset += n
        }
    }

    func flush(sink: (UnsafePointer<Float>, Int) -> Void) {
        run(endOfStream: true, sink: sink)
    }

    /// The converter calls the input block synchronously inside convert, so nonisolated(unsafe) is safe here.
    private func run(endOfStream: Bool, sink: (UnsafePointer<Float>, Int) -> Void) {
        nonisolated(unsafe) let input = self.input
        nonisolated(unsafe) var supplied = false
        while true {
            output.frameLength = 0
            var error: NSError?
            let status = converter.convert(to: output, error: &error) { _, s in
                if endOfStream { s.pointee = .endOfStream; return nil }
                if supplied { s.pointee = .noDataNow; return nil }
                supplied = true
                s.pointee = .haveData
                return input
            }
            if output.frameLength > 0 { sink(output.floatChannelData![0], Int(output.frameLength)) }
            // haveData: the output buffer is full and the converter still has frames.
            if status != .haveData || output.frameLength == 0 { return }
        }
    }
}
