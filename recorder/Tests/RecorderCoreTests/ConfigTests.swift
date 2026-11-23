import Foundation
import Testing
@testable import RecorderCore

@Suite("config.toml")
struct ConfigTests {
    @Test("example from architecture.md")
    func architectureExample() {
        let text = """
        obsidian_vault = "~/Documents/Obsidian"   # to confirm
        meetings_folder = "Meetings"
        audio_retention_days = 7

        [recorder]
        bundle_id_prefixes = ["com.tinyspeck.slackmacgap"]
        stop_after_seconds = 15

        [asr]
        model = "mlx-community/whisper-large-v3-turbo"

        [ui]
        host = "127.0.0.1"
        port = 8765
        """
        let (config, warnings) = RecorderConfig.parse(text)
        #expect(warnings.isEmpty)
        #expect(config.bundleIDPrefixes == ["com.tinyspeck.slackmacgap"])
        #expect(config.stopAfterSeconds == 15)
    }

    @Test("no [recorder] section: defaults without warnings")
    func missingSection() {
        let (config, warnings) = RecorderConfig.parse("audio_retention_days = 7\n[asr]\nlanguage = \"ru\"\n")
        #expect(warnings.isEmpty)
        #expect(config == RecorderConfig())
    }

    @Test("multiline array, comments, trailing comma, # inside a string")
    func multilineArray() throws {
        let text = """
        [recorder]
        bundle_id_prefixes = [
          "com.tinyspeck.slackmacgap",  # Slack
          'com.example.app#1',
        ]
        stop_after_seconds = 2.5 # секунды
        """
        let (config, warnings) = RecorderConfig.parse(text)
        #expect(warnings.isEmpty)
        #expect(config.bundleIDPrefixes == ["com.tinyspeck.slackmacgap", "com.example.app#1"])
        #expect(config.stopAfterSeconds == 2.5)
    }

    @Test("complex values in other sections don't interfere")
    func otherSectionsIgnored() {
        let text = """
        [pipeline]
        started = 2026-01-14T10:00:00Z
        point = { x = 1, y = 2 }
        nested = [
          [1, 2],
          [3, 4]
        ]
        [[profiles]]
        name = "a"
        [recorder]
        stop_after_seconds = 20
        """
        let (config, warnings) = RecorderConfig.parse(text)
        #expect(warnings.isEmpty)
        #expect(config.stopAfterSeconds == 20)
    }

    @Test("an error in [recorder]: all defaults and a warning with the line number")
    func syntaxError() {
        let (config, warnings) = RecorderConfig.parse("[recorder]\nstop_after_seconds = 15\nbundle_id_prefixes = [\"a\" \"b\"]\n")
        #expect(config == RecorderConfig())
        #expect(warnings.count == 1)
        #expect(warnings.first?.contains("line 3") == true)
    }

    @Test("unterminated array")
    func unclosedArray() {
        let (_, warnings) = RecorderConfig.parse("[recorder]\nbundle_id_prefixes = [\"a\",\n")
        #expect(warnings.first?.contains("line 2") == true)
    }

    @Test("wrong type: default value for that key")
    func wrongType() {
        let (config, warnings) = RecorderConfig.parse("[recorder]\nbundle_id_prefixes = \"com.x\"\nstop_after_seconds = 30\n")
        #expect(config.bundleIDPrefixes == RecorderConfig().bundleIDPrefixes)
        #expect(config.stopAfterSeconds == 30)
        #expect(warnings.count == 1)
    }

    @Test("CRLF and escape sequences")
    func crlfAndEscapes() throws {
        let values = try MiniTOML.section("recorder", in: "[recorder]\r\nname = \"a\\\"b\\u00e9\"\r\nflag = true\r\n")
        #expect(values["name"] == .string("a\"bé"))
        #expect(values["flag"] == .bool(true))
    }

    @Test("section headers")
    func headers() {
        #expect(MiniTOML.sectionHeader("[recorder]") == "recorder")
        #expect(MiniTOML.sectionHeader("[ recorder ]") == "recorder")
        #expect(MiniTOML.sectionHeader("[[recorder]]") == "[[recorder]]")
        #expect(MiniTOML.sectionHeader("[1, 2]") == nil)
        #expect(MiniTOML.sectionHeader("[\"a\"]") == nil)
    }
}

@Test func languageIsReadFromFileRoot() {
    #expect(RecorderConfig.parse("").config.language == "en")
    #expect(RecorderConfig.parse("language = \"ru\"\n\n[recorder]\nuv = \"/x\"\n").config.language == "ru")
    // [asr] language is the spoken language, not the interface language
    #expect(RecorderConfig.parse("[asr]\nlanguage = \"ru\"\n").config.language == "en")
    let bad = RecorderConfig.parse("language = \"xx\"\n")
    #expect(bad.config.language == "en" && bad.warnings.count == 1)
}

@Test func dataFolderBecomesPrivate() throws {
    let root = FileManager.default.temporaryDirectory.appending(path: "ma-perm-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: root) }
    let folder = root.appending(path: "recordings/r1")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    let file = folder.appending(path: "meta.json")
    try Data("{}".utf8).write(to: file)
    chmod(root.path, 0o755)
    chmod(file.path, 0o644)
    Paths.secure(root)
    func mode(_ url: URL) -> Int { (try? FileManager.default.attributesOfItem(atPath: url.path))?[.posixPermissions] as? Int ?? -1 }
    #expect(mode(root) == 0o700 && mode(folder) == 0o700 && mode(file) == 0o600)
}

@Test func readsConfigCreatedByInit() {
    let text = """
    # Meeting Assistant settings. A missing key uses its default.
    language = "ru"
    me_name = "Алекс"
    obsidian_vault = "/Users/me/Vault"

    [recorder]
    uv = "/opt/homebrew/bin/uv"
    pipeline = "/Users/me/app/pipeline"
    """
    let (config, warnings) = RecorderConfig.parse(text)
    #expect(warnings.isEmpty && config.language == "ru")
    #expect(config.uv == "/opt/homebrew/bin/uv" && config.pipelineDir == "/Users/me/app/pipeline")
}

@Test func uiIdentityProofMatchesServer() {
    #expect(UIServer.proof(token: "test-token", nonce: "test-nonce") == "22127a103f005e5ecdc7d9b5a712a96bb522efb2a291cd5994432f9604acd811")
}
