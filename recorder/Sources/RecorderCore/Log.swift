import Foundation
import os

/// Recorder log: `~/MeetingAssistant/logs/recorder.log` (appended, rotated to `.1` at 5 MB)
/// and the system log (`log stream --predicate 'subsystem == "local.meetingassistant.recorder"'`).
public final class AppLog: @unchecked Sendable {
    public enum Level: String { case info, warn, error }

    private let lock = NSLock()
    private let file: FileHandle?
    private let echo: Bool
    private let logger = Logger(subsystem: "local.meetingassistant.recorder", category: "recorder")
    private let formatter: DateFormatter

    /// `echo`: also write to stdout (when run from a terminal).
    public init(fileURL: URL? = Paths.logs.appending(path: "recorder.log"), echo: Bool = false) {
        self.echo = echo
        formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss.SSS"
        guard let fileURL else { file = nil; return }
        let fm = FileManager.default
        try? fm.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let size = (try? fm.attributesOfItem(atPath: fileURL.path))?[.size] as? Int, size > 5_000_000 {
            let old = fileURL.appendingPathExtension("1")
            try? fm.removeItem(at: old)
            try? fm.moveItem(at: fileURL, to: old)
        }
        // O_APPEND: lines from the menu app and terminal `record` don't overwrite each other.
        let fd = open(fileURL.path, O_WRONLY | O_APPEND | O_CREAT, 0o644)
        file = fd >= 0 ? FileHandle(fileDescriptor: fd, closeOnDealloc: true) : nil
    }

    public func info(_ text: String) { write(.info, text) }
    public func warn(_ text: String) { write(.warn, text) }
    public func error(_ text: String) { write(.error, text) }

    private func write(_ level: Level, _ text: String) {
        switch level {
        case .info: logger.info("\(text, privacy: .public)")
        case .warn: logger.warning("\(text, privacy: .public)")
        case .error: logger.error("\(text, privacy: .public)")
        }
        lock.withLock {
            let line = "\(formatter.string(from: Date())) [\(level.rawValue)] \(text)\n"
            let data = Data(line.utf8)
            file?.write(data)
            if echo { FileHandle.standardOutput.write(data) }
        }
    }
}
