import Foundation

/// Where data lives, outside the repository. MA_HOME overrides the root, as in `ma` (for testing).
public enum Paths {
    public static var home: URL {
        if let custom = ProcessInfo.processInfo.environment["MA_HOME"], !custom.isEmpty {
            return URL(fileURLWithPath: (custom as NSString).expandingTildeInPath, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appending(path: "MeetingAssistant", directoryHint: .isDirectory)
    }
    /// Recordings, voiceprints and logs are readable by the owner only (700/600).
    public static func securePermissions() {
        umask(0o077)
        secure(home)
    }

    static func secure(_ root: URL) {
        let fm = FileManager.default
        try? fm.createDirectory(at: root, withIntermediateDirectories: true)
        guard let mode = (try? fm.attributesOfItem(atPath: root.path))?[.posixPermissions] as? Int, mode & 0o077 != 0 else { return }
        if let items = fm.enumerator(at: root, includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey]) {
            for case let url as URL in items {
                let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
                if values?.isSymbolicLink == true { continue }
                chmod(url.path, values?.isDirectory == true ? 0o700 : 0o600)
            }
        }
        chmod(root.path, 0o700)   // last, so an interrupted pass runs again
    }

    public static var recordings: URL { home.appending(path: "recordings", directoryHint: .isDirectory) }
    public static var logs: URL { home.appending(path: "logs", directoryHint: .isDirectory) }
    public static var config: URL { home.appending(path: "config.toml") }
}

/// Recorder settings: the `[recorder]` section of `~/MeetingAssistant/config.toml`.
/// The Python pipeline owns the file; the recorder only reads its own section.
public struct RecorderConfig: Sendable, Equatable {
    /// Processes whose audio goes to remote and whose mic use means a call.
    public var bundleIDPrefixes = ["com.tinyspeck.slackmacgap"]
    /// Auto-stop: seconds Slack must not hold the mic.
    public var stopAfterSeconds = 15.0
    /// Auto-start: seconds Slack must hold the mic (it toggles the mic for 3 s when joining a huddle).
    public var startAfterSeconds = 3.0
    /// Run `ma process` after stopping (queued, never during a call).
    public var processAfterStop = true
    /// Daily, delete audio of processed recordings older than audio_retention_days (`ma cleanup`).
    public var cleanupDaily = true
    /// Paths to uv and pipeline/; defaults are Homebrew and the repository the app was built in.
    public var uv: String?
    public var pipelineDir: String?
    /// Port of the `ma ui` web interface: the `[ui]` section, shared with the pipeline.
    public var uiPort = 8765
    /// UI language: the root `language` key, shared with the pipeline.
    public var language = "en"
    public static let languages = ["en", "ru"]

    public init() {}

    /// No file: defaults without warnings. An error: defaults and a warning.
    public static func load(from url: URL = Paths.config) -> (config: RecorderConfig, warnings: [String]) {
        guard FileManager.default.fileExists(atPath: url.path) else { return (RecorderConfig(), []) }
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            return (RecorderConfig(), ["config.toml is unreadable, using default settings"])
        }
        return parse(text)
    }

    public static func parse(_ text: String) -> (config: RecorderConfig, warnings: [String]) {
        var config = RecorderConfig()
        let values: [String: TOMLValue]
        do {
            values = try MiniTOML.section("recorder", in: text)
        } catch {
            return (config, ["config.toml, \(error), using default recorder settings"])
        }
        var warnings: [String] = []

        if let v = values["bundle_id_prefixes"] {
            if case let .array(items) = v,
               case let strings = items.compactMap({ if case let .string(s) = $0, !s.isEmpty { s } else { nil } }),
               !strings.isEmpty, strings.count == items.count {
                config.bundleIDPrefixes = strings
            } else {
                warnings.append("config.toml: bundle_id_prefixes must be a non-empty list of strings, using the default")
            }
        }
        for (key, path) in [("stop_after_seconds", \RecorderConfig.stopAfterSeconds),
                            ("start_after_seconds", \RecorderConfig.startAfterSeconds)] {
            guard let v = values[key] else { continue }
            if case let .number(n) = v, n > 0 {
                config[keyPath: path] = n
            } else {
                warnings.append("config.toml: \(key) must be a positive number, using the default")
            }
        }
        for (key, path) in [("process_after_stop", \RecorderConfig.processAfterStop),
                            ("cleanup_daily", \RecorderConfig.cleanupDaily)] {
            guard let v = values[key] else { continue }
            if case let .bool(b) = v { config[keyPath: path] = b }
            else { warnings.append("config.toml: \(key) must be true or false, using the default") }
        }
        if let ui = try? MiniTOML.section("ui", in: text), case let .number(n)? = ui["port"], n >= 1024, n < 65536 {
            config.uiPort = Int(n)
        }
        if let root = try? MiniTOML.section(nil, in: text), let v = root["language"] {
            if case let .string(s) = v, languages.contains(s) { config.language = s }
            else { warnings.append("config.toml: language must be en or ru, using the default") }
        }
        for (key, path) in [("uv", \RecorderConfig.uv), ("pipeline", \RecorderConfig.pipelineDir)] {
            guard let v = values[key] else { continue }
            if case let .string(s) = v, !s.isEmpty { config[keyPath: path] = s }
            else { warnings.append("config.toml: \(key) must be a non-empty string, using the default") }
        }
        return (config, warnings)
    }
}

public enum TOMLValue: Equatable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case array([TOMLValue])
}

/// The TOML subset the recorder needs: `[section]`, `key = value`, strings `"..."` and `'...'`,
/// numbers, `true`/`false`, arrays (including multiline), `#` comments.
/// Only the requested section is parsed; others are skipped even if they use more of TOML.
public enum MiniTOML {
    public struct ParseError: Error, Equatable, CustomStringConvertible {
        public let line: Int
        public let message: String
        public var description: String { "line \(line): \(message)" }
    }

    public static func section(_ name: String?, in text: String) throws(ParseError) -> [String: TOMLValue] {
        var result: [String: TOMLValue] = [:]
        var current: String? = nil   // nil is the file root
        var pending = ""             // multiline value
        var pendingLine = 0

        for (index, raw) in text.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline).enumerated() {
            let lineNo = index + 1
            let line = stripComment(String(raw)).trimmingCharacters(in: .whitespacesAndNewlines)

            if !pending.isEmpty {
                pending += "\n" + line
                if isBalanced(pending) {
                    try assign(pending, line: pendingLine, into: &result)
                    pending = ""
                }
                continue
            }
            if line.isEmpty { continue }
            if let header = sectionHeader(line) {
                current = header
                continue
            }
            guard current == name else { continue }
            if isBalanced(line) {
                try assign(line, line: lineNo, into: &result)
            } else {
                pending = line
                pendingLine = lineNo
            }
        }
        if !pending.isEmpty { throw ParseError(line: pendingLine, message: "unterminated array or string") }
        return result
    }

    /// `[name]` gives name; `[[name]]` (array of tables) keeps the brackets so it can't match a plain section.
    static func sectionHeader(_ line: String) -> String? {
        guard line.hasPrefix("["), line.hasSuffix("]") else { return nil }
        let isArray = line.hasPrefix("[[") && line.hasSuffix("]]")
        let inner = line.dropFirst(isArray ? 2 : 1).dropLast(isArray ? 2 : 1).trimmingCharacters(in: .whitespaces)
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "_-."))
        guard !inner.isEmpty, inner.unicodeScalars.allSatisfy(allowed.contains) else { return nil }
        return isArray ? "[[\(inner)]]" : inner
    }

    /// Strips a `# comment`, leaving `#` inside strings alone.
    static func stripComment(_ line: String) -> String {
        var quote: Character? = nil
        var escaped = false
        for (i, c) in zip(line.indices, line) {
            if let q = quote {
                if escaped { escaped = false }
                else if c == "\\" && q == "\"" { escaped = true }
                else if c == q { quote = nil }
            } else if c == "\"" || c == "'" {
                quote = c
            } else if c == "#" {
                return String(line[..<i])
            }
        }
        return line
    }

    /// Array brackets are closed and strings are terminated.
    static func isBalanced(_ text: String) -> Bool {
        var depth = 0
        var quote: Character? = nil
        var escaped = false
        for c in text {
            if let q = quote {
                if escaped { escaped = false }
                else if c == "\\" && q == "\"" { escaped = true }
                else if c == q { quote = nil }
            } else if c == "\"" || c == "'" { quote = c }
            else if c == "[" { depth += 1 }
            else if c == "]" { depth -= 1 }
        }
        return depth <= 0 && quote == nil
    }

    private static func assign(_ statement: String, line: Int, into result: inout [String: TOMLValue]) throws(ParseError) {
        guard let eq = statement.firstIndex(of: "=") else {
            throw ParseError(line: line, message: "expected \"key = value\"")
        }
        var key = statement[..<eq].trimmingCharacters(in: .whitespaces)
        if key.count >= 2, let f = key.first, f == "\"" || f == "'", key.last == f {
            key = String(key.dropFirst().dropLast())
        }
        guard !key.isEmpty else { throw ParseError(line: line, message: "empty key") }
        var parser = ValueParser(text: Array(statement[statement.index(after: eq)...]), line: line)
        let value = try parser.value()
        parser.skipSpace()
        guard parser.atEnd else { throw ParseError(line: line, message: "unexpected text after value of \"\(key)\"") }
        result[key] = value
    }

    private struct ValueParser {
        let text: [Character]
        let line: Int
        var pos = 0

        var atEnd: Bool { pos >= text.count }

        mutating func skipSpace() {
            while pos < text.count, text[pos].isWhitespace { pos += 1 }
        }

        func fail(_ message: String) -> ParseError { ParseError(line: line, message: message) }

        mutating func value() throws(ParseError) -> TOMLValue {
            skipSpace()
            guard pos < text.count else { throw fail("missing value") }
            switch text[pos] {
            case "\"": return .string(try basicString())
            case "'": return .string(try literalString())
            case "[": return try array()
            default: return try scalar()
            }
        }

        mutating func basicString() throws(ParseError) -> String {
            pos += 1
            var out = ""
            while pos < text.count {
                let c = text[pos]
                pos += 1
                if c == "\"" { return out }
                if c == "\\" {
                    guard pos < text.count else { break }
                    let e = text[pos]
                    pos += 1
                    switch e {
                    case "n": out.append("\n")
                    case "t": out.append("\t")
                    case "r": out.append("\r")
                    case "\"": out.append("\"")
                    case "\\": out.append("\\")
                    case "u", "U":
                        let n = e == "u" ? 4 : 8
                        guard pos + n <= text.count, let code = UInt32(String(text[pos..<pos + n]), radix: 16),
                              let scalar = Unicode.Scalar(code) else { throw fail("invalid \\u in string") }
                        out.unicodeScalars.append(scalar)
                        pos += n
                    default: throw fail("unknown escape sequence \\\(e)")
                    }
                } else {
                    out.append(c)
                }
            }
            throw fail("unterminated string")
        }

        mutating func literalString() throws(ParseError) -> String {
            pos += 1
            guard let end = text[pos...].firstIndex(of: "'") else { throw fail("unterminated string") }
            let s = String(text[pos..<end])
            pos = end + 1
            return s
        }

        mutating func array() throws(ParseError) -> TOMLValue {
            pos += 1
            var items: [TOMLValue] = []
            while true {
                skipSpace()
                guard pos < text.count else { throw fail("unterminated array") }
                if text[pos] == "]" { pos += 1; return .array(items) }
                items.append(try value())
                skipSpace()
                guard pos < text.count else { throw fail("unterminated array") }
                if text[pos] == "," { pos += 1 }
                else if text[pos] != "]" { throw fail("expected a comma in array") }
            }
        }

        mutating func scalar() throws(ParseError) -> TOMLValue {
            let start = pos
            while pos < text.count, !text[pos].isWhitespace, text[pos] != ",", text[pos] != "]" { pos += 1 }
            let token = String(text[start..<pos])
            if token == "true" { return .bool(true) }
            if token == "false" { return .bool(false) }
            let digits = token.replacingOccurrences(of: "_", with: "")
            if let n = Double(digits), digits.allSatisfy({ $0.isNumber || "+-.eE".contains($0) }) {
                return .number(n)
            }
            throw fail("unsupported value \"\(token)\"")
        }
    }
}
