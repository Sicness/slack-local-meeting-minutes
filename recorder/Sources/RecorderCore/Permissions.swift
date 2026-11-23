import AVFoundation
import Darwin
import Foundation

public enum PermissionStatus: String, Sendable {
    case granted, denied, unknown
}

/// Audio capture from other apps (kTCCServiceAudioCapture). There is no public API to check it,
/// so, like AudioCap, this calls TCCAccessPreflight / TCCAccessRequest from private TCC.framework.
/// Without permission the tap doesn't fail but returns silence, so the status matters.
public enum AudioCapturePermission {
    private typealias PreflightFn = @convention(c) (CFString, CFDictionary?) -> Int32
    // The block must be @escaping: TCC keeps it until the user answers, otherwise Swift crashes
    // ("closure argument passed as @noescape to Objective-C has escaped").
    private typealias RequestFn = @convention(c) (CFString, CFDictionary?, @escaping @convention(block) (Bool) -> Void) -> Void

    nonisolated(unsafe) private static let tcc: UnsafeMutableRawPointer? =
        dlopen("/System/Library/PrivateFrameworks/TCC.framework/Versions/A/TCC", RTLD_NOW)
    private static let service = "kTCCServiceAudioCapture"

    public static var status: PermissionStatus {
        guard let tcc, let sym = dlsym(tcc, "TCCAccessPreflight") else { return .unknown }
        let preflight = unsafeBitCast(sym, to: PreflightFn.self)
        switch preflight(service as CFString, nil) {
        case 0: return .granted
        case 1: return .denied
        default: return .unknown
        }
    }

    /// Shows the system prompt (if not asked yet) and waits. nil means the API is unavailable or timed out.
    public static func requestBlocking(timeout: TimeInterval = 180) -> Bool? {
        guard let tcc, let sym = dlsym(tcc, "TCCAccessRequest") else { return nil }
        let request = unsafeBitCast(sym, to: RequestFn.self)
        let result = Locked<Bool?>(nil)
        let done = DispatchSemaphore(value: 0)
        request(service as CFString, nil) { granted in
            result.set(granted)
            done.signal()
        }
        _ = done.wait(timeout: .now() + timeout)
        return result.get()
    }
}

public enum MicrophonePermission {
    public static var status: PermissionStatus {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: .granted
        case .denied, .restricted: .denied
        default: .unknown
        }
    }

    public static func requestBlocking(timeout: TimeInterval = 180) -> Bool {
        if status == .granted { return true }
        let result = Locked(false)
        let done = DispatchSemaphore(value: 0)
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            result.set(granted)
            done.signal()
        }
        _ = done.wait(timeout: .now() + timeout)
        return result.get()
    }
}

/// A locked value (for sharing between threads other than the audio thread).
public final class Locked<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Value

    public init(_ value: Value) { self.value = value }
    public func get() -> Value { lock.withLock { value } }
    public func set(_ newValue: Value) { lock.withLock { value = newValue } }
}
