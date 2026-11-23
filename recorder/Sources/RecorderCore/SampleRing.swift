import CoreAudio
import Foundation
import Synchronization

/// Header of an audio chunk from one IOProc call (or part of it, if the call exceeds a slot).
public struct ChunkHeader: Sendable {
    public var segment: Int32
    public var frames: Int32
    public var hostTime: UInt64
    public var sampleTime: Double
    public var rateScalar: Double
    public var hostTimeValid: Bool
    /// Continues the previous IOProc call: hostTime refers to the call's start, not this chunk.
    public var isContinuation: Bool
}

/// Single-producer, single-consumer ring between the audio thread and the file writer queue.
/// The writer (IOProc) takes no locks and allocates no memory.
public final class SampleRing: @unchecked Sendable {
    public let slotFrames: Int
    public let slotCount: Int
    private let samples: UnsafeMutablePointer<Float>
    private let headers: UnsafeMutablePointer<ChunkHeader>
    private let head = Atomic<Int>(0)   // next slot to write
    private let tail = Atomic<Int>(0)   // next slot to read
    private let overflow = Atomic<Int>(0)

    /// By default 1024 slots x 2048 frames, about 40 s of audio at 48 kHz with 512-frame calls.
    public init(slotFrames: Int = 2048, slotCount: Int = 1024) {
        self.slotFrames = slotFrames
        self.slotCount = slotCount
        samples = .allocate(capacity: slotFrames * slotCount)
        samples.initialize(repeating: 0, count: slotFrames * slotCount)
        headers = .allocate(capacity: slotCount)
        headers.initialize(repeating: ChunkHeader(segment: 0, frames: 0, hostTime: 0, sampleTime: 0,
                                                  rateScalar: 1, hostTimeValid: false, isContinuation: false),
                           count: slotCount)
    }

    deinit {
        samples.deallocate()
        headers.deallocate()
    }

    /// Frames lost to overflow (the reader fell behind).
    public var overflowFrames: Int { overflow.load(ordering: .relaxed) }

    /// Called from IOProc. Takes one channel from `source` with step `stride` (for interleaved channels).
    public func push(segment: Int32, hostTime: UInt64, hostTimeValid: Bool, sampleTime: Double, rateScalar: Double,
                     source: UnsafePointer<Float>, stride: Int, frames: Int) {
        var offset = 0
        while offset < frames {
            let count = min(slotFrames, frames - offset)
            let h = head.load(ordering: .relaxed)
            if h - tail.load(ordering: .acquiring) >= slotCount {
                overflow.wrappingAdd(frames - offset, ordering: .relaxed)
                return
            }
            let slot = h % slotCount
            let dst = samples + slot * slotFrames
            var src = source + offset * stride
            for i in 0..<count {
                dst[i] = src.pointee
                src += stride
            }
            headers[slot] = ChunkHeader(segment: segment, frames: Int32(count), hostTime: hostTime,
                                        sampleTime: sampleTime + Double(offset), rateScalar: rateScalar,
                                        hostTimeValid: hostTimeValid, isContinuation: offset > 0)
            head.store(h + 1, ordering: .releasing)
            offset += count
        }
    }

    /// Called from the writer queue: delivers everything buffered, in order.
    public func drain(_ body: (ChunkHeader, UnsafeBufferPointer<Float>) -> Void) {
        let t = tail.load(ordering: .relaxed)
        let h = head.load(ordering: .acquiring)
        guard h > t else { return }
        for i in t..<h {
            let slot = i % slotCount
            let header = headers[slot]
            body(header, UnsafeBufferPointer(start: samples + slot * slotFrames, count: Int(header.frames)))
        }
        tail.store(h, ordering: .releasing)
    }
}

/// IOProc on a device (real or tap aggregate): puts channel 0 of the first input buffer into the ring.
public final class IOProcRunner: @unchecked Sendable {
    public let deviceID: AudioObjectID
    private var procID: AudioDeviceIOProcID?

    public init(deviceID: AudioObjectID) {
        self.deviceID = deviceID
    }

    public var isRunning: Bool { procID != nil }

    public func start(ring: SampleRing, segment: Int32) throws {
        var id: AudioDeviceIOProcID?
        // nil queue: the block runs directly on the real-time audio thread.
        let status = AudioDeviceCreateIOProcIDWithBlock(&id, deviceID, nil) { inNow, inInputData, inInputTime, _, _ in
            let list = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inInputData))
            guard list.count > 0 else { return }
            let buffer = list[0]
            let channels = Int(buffer.mNumberChannels)
            guard let data = buffer.mData, channels > 0 else { return }
            let frames = Int(buffer.mDataByteSize) / (MemoryLayout<Float>.size * channels)
            guard frames > 0 else { return }
            let ts = inInputTime.pointee
            let hostValid = ts.mFlags.contains(.hostTimeValid)
            ring.push(segment: segment,
                      hostTime: hostValid ? ts.mHostTime : inNow.pointee.mHostTime,
                      hostTimeValid: hostValid,
                      sampleTime: ts.mSampleTime,
                      rateScalar: ts.mFlags.contains(.rateScalarValid) ? ts.mRateScalar : 1,
                      source: data.assumingMemoryBound(to: Float.self), stride: channels, frames: frames)
        }
        try osStatusCheck(status, "AudioDeviceCreateIOProcIDWithBlock")
        guard let id else { throw RecorderError.message("IOProc was not created") }
        let startStatus = AudioDeviceStart(deviceID, id)
        if startStatus != noErr {
            AudioDeviceDestroyIOProcID(deviceID, id)
            try osStatusCheck(startStatus, "AudioDeviceStart")
        }
        procID = id
    }

    public func stop() {
        guard let id = procID else { return }
        AudioDeviceStop(deviceID, id)
        AudioDeviceDestroyIOProcID(deviceID, id)
        procID = nil
    }
}
