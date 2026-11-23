import Darwin
import CryptoKit
import Foundation

/// Runs `uv run --project <pipeline> ma process <id>` in its own process group, so it can be paused
/// as a whole (uv, python, ma-diarize). Output goes to ~/MeetingAssistant/logs/process.log.
/// Also runs the daily `ma cleanup`: after queued recordings and never during a call.
public final class ProcessingRunner {
    /// Job id of the cleanup in `current` and in the `tick` result.
    public static let cleanupID = "ma cleanup"

    public struct Job: Sendable, Equatable {
        public let id: String
        public let pid: pid_t
    }

    public private(set) var queue: [String] = []
    public private(set) var current: Job?
    public private(set) var paused = false
    /// Deferred retries (Ollama not running): recording -> when to queue it again.
    public private(set) var retries: [String: Date] = [:]
    /// Audio cleanup is waiting for its turn.
    public private(set) var cleanupPending = false
    private let uv: String
    private let pipeline: String
    private let log: AppLog

    public init(uv: String, pipeline: String, log: AppLog) {
        self.uv = uv
        self.pipeline = pipeline
        self.log = log
    }

    public func enqueue(_ id: String) {
        guard current?.id != id, !queue.contains(id) else { return }
        queue.append(id)
        log.info("processing: \(id) queued (\(queue.count))")
    }

    public func requestCleanup() {
        guard !cleanupPending, current?.id != Self.cleanupID else { return }
        cleanupPending = true
        log.info("audio cleanup: queued")
    }

    /// Retry processing later: `ma process` resumes from the failed step.
    public func retryLater(_ id: String, after seconds: TimeInterval) {
        retries[id] = Date().addingTimeInterval(seconds)
        log.info("processing: \(id) will retry in \(Int(seconds / 60)) min")
    }

    /// Call once a second. Returns a finished job and its exit code.
    public func tick(callActive: Bool, now: Date = Date()) -> (id: String, status: Int32)? {
        for (id, at) in retries where at <= now {
            retries[id] = nil
            enqueue(id)
        }
        var finished: (String, Int32)?
        if let job = current {
            var status: Int32 = 0
            if waitpid(job.pid, &status, WNOHANG) == job.pid {
                let code: Int32 = (status & 0x7f) == 0 ? (status >> 8) & 0xff : 128 + (status & 0x7f)
                log.info("processing: \(job.id) exited with code \(code)")
                current = nil
                paused = false
                finished = (job.id, code)
            }
        }
        switch ProcessingPolicy.decide(running: current != nil, paused: paused, callActive: callActive, pending: queue.count + (cleanupPending ? 1 : 0)) {
        case .startNext: startNext()
        case .pause:
            if let job = current, kill(-job.pid, SIGSTOP) == 0 {
                paused = true
                log.info("processing: \(job.id) paused, call in progress")
            }
        case .resume:
            if let job = current, kill(-job.pid, SIGCONT) == 0 {
                paused = false
                log.info("processing: \(job.id) resumed, call ended")
            }
        case .none: break
        }
        return finished.map { (id: $0.0, status: $0.1) }
    }

    /// On recorder exit, don't kill processing: just unpause it so it can finish.
    public func detach() {
        if let job = current, paused { kill(-job.pid, SIGCONT) }
    }

    private func startNext() {
        let id: String
        let args: [String]
        if !queue.isEmpty {
            id = queue.removeFirst()
            args = [uv, "run", "--project", pipeline, "ma", "process", id]
        } else if cleanupPending {
            cleanupPending = false
            id = Self.cleanupID
            args = [uv, "run", "--project", pipeline, "ma", "cleanup"]
        } else {
            return
        }
        try? FileManager.default.createDirectory(at: Paths.logs, withIntermediateDirectories: true)
        var attr = posix_spawnattr_t(bitPattern: 0)
        posix_spawnattr_init(&attr)
        posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP))
        posix_spawnattr_setpgroup(&attr, 0)
        var files = posix_spawn_file_actions_t(bitPattern: 0)
        posix_spawn_file_actions_init(&files)
        posix_spawn_file_actions_addopen(&files, 0, "/dev/null", O_RDONLY, 0)
        let logPath = Paths.logs.appending(path: "process.log").path
        posix_spawn_file_actions_addopen(&files, 1, logPath, O_WRONLY | O_CREAT | O_APPEND, 0o644)
        posix_spawn_file_actions_adddup2(&files, 1, 2)
        defer {
            posix_spawnattr_destroy(&attr)
            posix_spawn_file_actions_destroy(&files)
        }
        // Launched from Finder there is no Terminal PATH; ma uses full paths for ffmpeg and other tools.
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        let cArgs = args.map { strdup($0) } + [nil]
        let cEnv = env.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer { (cArgs + cEnv).forEach { free($0) } }
        var pid: pid_t = 0
        let rc = posix_spawn(&pid, uv, &files, &attr, cArgs, cEnv)
        if rc != 0 {
            log.error("processing: failed to launch \(uv): \(String(cString: strerror(rc)))")
            return
        }
        current = Job(id: id, pid: pid)
        log.info("processing: \(id) started, pid \(pid)")
    }
}

/// The `ma ui` web interface: started on first open and kept running while the recorder runs.
public final class UIServer {
    private var process: Process?
    public let port: Int

    public init(port: Int) { self.port = port }

    public var baseURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }

    /// Address with the session token; never write it to a log.
    public var url: URL {
        guard let token else { return baseURL }
        return URL(string: "\(baseURL.absoluteString)?token=\(token)") ?? baseURL
    }

    public var token: String? {
        let path = Paths.home.appending(path: "ui.token")
        let value = (try? String(contentsOf: path, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
        return value?.isEmpty == false ? value : nil
    }

    public static func proof(token: String, nonce: String) -> String {
        let code = HMAC<SHA256>.authenticationCode(for: Data(nonce.utf8), using: SymmetricKey(data: Data(token.utf8)))
        return code.map { String(format: "%02x", $0) }.joined()
    }

    /// The process on the port knows the token, so it is our server and may receive the link.
    public static func verify(baseURL: URL, token: String?) async -> Bool {
        guard let token else { return false }
        let nonce = UUID().uuidString
        guard let url = URL(string: "\(baseURL.absoluteString)ma-identity?nonce=\(nonce)") else { return false }
        let request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 2)
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200 else { return false }
        return String(data: data, encoding: .utf8) == proof(token: token, nonce: nonce)
    }

    /// Something is listening on the port on 127.0.0.1.
    public var isUp: Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(port).bigEndian)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        return withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0 }
        }
    }

    public func start(uv: String, pipeline: String, log: AppLog) throws {
        if let process, process.isRunning { return }
        try? FileManager.default.createDirectory(at: Paths.logs, withIntermediateDirectories: true)
        let logURL = Paths.logs.appending(path: "ui.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        let out = try FileHandle(forWritingTo: logURL)
        out.seekToEndOfFile()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: uv)
        p.arguments = ["run", "--project", pipeline, "ma", "ui"]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        p.environment = env
        p.standardOutput = out
        p.standardError = out
        try p.run()
        process = p
        log.info("web interface: started, pid \(p.processIdentifier), \(baseURL.absoluteString)")
    }

    public func stop() {
        if let process, process.isRunning { process.terminate() }
        process = nil
    }
}

/// Where to find uv and pipeline/: from `[recorder]` or next to the app (recorder/build/MeetingRecorder.app -> repository).
public enum PipelineLocation {
    public static func pipelineDir(config: RecorderConfig, bundle: URL = Bundle.main.bundleURL) -> String? {
        if let p = config.pipelineDir { return (p as NSString).expandingTildeInPath }
        let repo = bundle.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let dir = repo.appending(path: "pipeline")
        return FileManager.default.fileExists(atPath: dir.appending(path: "pyproject.toml").path) ? dir.path : nil
    }

    public static func uv(config: RecorderConfig) -> String? {
        if let u = config.uv { return (u as NSString).expandingTildeInPath }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return ["/opt/homebrew/bin/uv", "/usr/local/bin/uv", "\(home)/.local/bin/uv", "\(home)/.cargo/bin/uv"]
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }
}
