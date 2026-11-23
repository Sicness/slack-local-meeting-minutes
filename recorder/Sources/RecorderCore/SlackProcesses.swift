import CoreAudio
import Darwin
import Foundation

/// Snapshot of a Core Audio client (a process working with audio).
public struct AudioProcessInfo: Sendable, Equatable {
    public let objectID: AudioObjectID
    public let pid: pid_t
    public let bundleID: String
    public let isRunningInput: Bool
    public let isRunningOutput: Bool
    public let inputDevices: [AudioObjectID]
    public let outputDevices: [AudioObjectID]
}

public enum AudioProcesses {
    public static let slackPrefixes = ["com.tinyspeck.slackmacgap"]

    public static func all() -> [AudioProcessInfo] {
        let processes = (try? AudioHardwareSystem.shared.processes) ?? []
        return processes.compactMap { p in
            guard let pid = try? p.pid else { return nil }
            return AudioProcessInfo(
                objectID: p.id, pid: pid,
                bundleID: ((try? p.bundleID) ?? nil) ?? "",
                isRunningInput: (try? p.isRunningInput) ?? false,
                isRunningOutput: (try? p.isRunningOutput) ?? false,
                inputDevices: p.objectIDs(kAudioProcessPropertyDevices, scope: kAudioObjectPropertyScopeInput),
                outputDevices: p.objectIDs(kAudioProcessPropertyDevices, scope: kAudioObjectPropertyScopeOutput))
        }
    }

    public static func matching(_ prefixes: [String], in list: [AudioProcessInfo]? = nil) -> [AudioProcessInfo] {
        (list ?? all()).filter { p in prefixes.contains { p.bundleID.hasPrefix($0) } }
    }
}

// MARK: - Process arguments (Chromium helper type)

public enum ProcessArgs {
    /// A process's argv via sysctl KERN_PROCARGS2 (works for processes of the same user).
    public static func arguments(pid: pid_t) -> [String] {
        var mib: [Int32] = [CTL_KERN, KERN_PROCARGS2, pid]
        var size = 0
        guard sysctl(&mib, 3, nil, &size, nil, 0) == 0, size > MemoryLayout<Int32>.size else { return [] }
        var buf = [UInt8](repeating: 0, count: size)
        guard sysctl(&mib, 3, &buf, &size, nil, 0) == 0 else { return [] }
        let argc = Int(buf.withUnsafeBytes { $0.load(as: Int32.self) })
        var i = MemoryLayout<Int32>.size
        while i < size && buf[i] != 0 { i += 1 }   // executable path
        while i < size && buf[i] == 0 { i += 1 }   // padding
        var args: [String] = []
        while args.count < argc && i < size {
            let start = i
            while i < size && buf[i] != 0 { i += 1 }
            args.append(String(decoding: buf[start..<i], as: UTF8.self))
            i += 1
        }
        return args
    }

    /// "main", "renderer", "gpu-process" or a utility subtype ("audio.mojom.AudioService").
    public static func chromiumRole(pid: pid_t) -> String {
        let args = arguments(pid: pid)
        func value(_ key: String) -> String? {
            args.first { $0.hasPrefix(key) }.map { String($0.dropFirst(key.count)) }
        }
        guard let type = value("--type=") else { return args.isEmpty ? "?" : "main" }
        if type == "utility", let sub = value("--utility-sub-type=") { return sub }
        return type
    }
}
