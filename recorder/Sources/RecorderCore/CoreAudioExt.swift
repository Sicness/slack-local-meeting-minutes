import CoreAudio
import Foundation

/// 'bltn' and the like from a Core Audio UInt32.
public func fourCC(_ value: UInt32) -> String {
    if value == 0 { return "0" }
    let bytes = [24, 16, 8, 0].map { UInt8((value >> $0) & 0xFF) }
    if bytes.allSatisfy({ $0 >= 0x20 && $0 < 0x7F }) {
        return String(decoding: bytes, as: UTF8.self)
    }
    // Built-in audio reports terminal types as USB Audio codes: 0x0201 mic, 0x0301 speaker, 0x0302 headphones.
    return String(format: "0x%04x", value)
}

/// A readable Core Audio error code: fourCC if it is letters, otherwise a number.
public func describeStatus(_ error: Error) -> String {
    if let e = error as? AudioHardwareError { return "OSStatus \(fourCC(UInt32(bitPattern: e.error))) (\(e.error))" }
    return String(describing: error)
}

public func osStatusCheck(_ status: OSStatus, _ what: String) throws {
    if status != noErr { throw RecorderError.coreAudio(what, status) }
}

public enum RecorderError: Error, CustomStringConvertible {
    case coreAudio(String, OSStatus)
    case message(String)

    public var description: String {
        switch self {
        case let .coreAudio(what, status): "\(what): OSStatus \(fourCC(UInt32(bitPattern: status))) (\(status))"
        case let .message(text): text
        }
    }
}

extension AudioHardwareObject {
    /// AudioObjectID list in a given scope (for example, a process's input devices).
    func objectIDs(_ selector: AudioObjectPropertySelector, scope: AudioObjectPropertyScope) -> [AudioObjectID] {
        let address = PropertyAddress(selector, scope: scope)
        guard hasProperty(address: address), let data = try? propertyData(address: address) else { return [] }
        return data.withUnsafeBytes { Array($0.bindMemory(to: AudioObjectID.self)) }
    }

    func uint32(_ selector: AudioObjectPropertySelector, scope: AudioObjectPropertyScope) -> UInt32? {
        let address = PropertyAddress(selector, scope: scope)
        guard hasProperty(address: address), let data = try? propertyData(address: address),
              data.count >= 4 else { return nil }
        return data.withUnsafeBytes { $0.load(as: UInt32.self) }
    }

    func streamFormat(scope: AudioObjectPropertyScope) -> AudioStreamBasicDescription? {
        let address = PropertyAddress(kAudioDevicePropertyStreamFormat, scope: scope)
        guard hasProperty(address: address), let data = try? propertyData(address: address),
              data.count >= MemoryLayout<AudioStreamBasicDescription>.size else { return nil }
        return data.withUnsafeBytes { $0.load(as: AudioStreamBasicDescription.self) }
    }
}

// MARK: - Host time

public enum HostClock {
    public static func now() -> UInt64 { AudioGetCurrentHostTime() }

    /// Seconds between two host times (may be negative).
    public static func seconds(from start: UInt64, to end: UInt64) -> Double {
        if end >= start {
            return Double(AudioConvertHostTimeToNanos(end - start)) / 1e9
        }
        return -Double(AudioConvertHostTimeToNanos(start - end)) / 1e9
    }
}

extension AudioStreamBasicDescription {
    public var isFloat32: Bool {
        mFormatID == kAudioFormatLinearPCM && (mFormatFlags & kAudioFormatFlagIsFloat) != 0 && mBitsPerChannel == 32
    }

    public var isNonInterleaved: Bool { (mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0 }

    public var short: String {
        let kind = isFloat32 ? "f32" : "\(fourCC(mFormatID))/\(mBitsPerChannel)bit"
        return "\(Int(mSampleRate)) Hz \(mChannelsPerFrame) ch \(kind)\(isNonInterleaved ? " non-int" : "")"
    }

    /// Packed mono Float32, the format of our files.
    public static func monoFloat32(rate: Double) -> AudioStreamBasicDescription {
        AudioStreamBasicDescription(
            mSampleRate: rate, mFormatID: kAudioFormatLinearPCM,
            mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
            mBytesPerPacket: 4, mFramesPerPacket: 1, mBytesPerFrame: 4,
            mChannelsPerFrame: 1, mBitsPerChannel: 32, mReserved: 0)
    }
}
