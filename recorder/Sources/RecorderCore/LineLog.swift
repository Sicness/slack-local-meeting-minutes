import Foundation

/// Timestamped lines to stdout and, if set, a file. Unbuffered, because the log is read with tail -f.
public final class LineLog: @unchecked Sendable {
    private let lock = NSLock()
    private let file: FileHandle?
    private let formatter: DateFormatter

    public init(fileURL: URL? = nil) {
        if let fileURL {
            FileManager.default.createFile(atPath: fileURL.path, contents: nil)
            file = try? FileHandle(forWritingTo: fileURL)
        } else {
            file = nil
        }
        formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss.SSS"
    }

    public func callAsFunction(_ text: String) {
        lock.withLock { emit("\(formatter.string(from: Date()))  \(text)\n") }
    }

    /// Without a timestamp (headers, summaries).
    public func raw(_ text: String) {
        lock.withLock { emit(text + "\n") }
    }

    private func emit(_ line: String) {
        let data = Data(line.utf8)
        FileHandle.standardOutput.write(data)
        file?.write(data)
    }
}
