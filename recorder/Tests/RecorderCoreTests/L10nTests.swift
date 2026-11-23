import Foundation
import Testing
@testable import RecorderCore

@Suite("localization")
struct L10nTests {
    private func placeholders(_ text: String) -> Int {
        text.components(separatedBy: "%@").count - 1
    }

    @Test("en and ru have identical key sets that cover every key")
    func keySets() {
        let all = Set(L10n.Key.allCases)
        #expect(Set(L10n.en.keys) == all)
        #expect(Set(L10n.ru.keys) == all)
    }

    @Test("every key has the same number of placeholders in both languages")
    func placeholdersMatch() {
        for key in L10n.Key.allCases {
            #expect(placeholders(L10n.en[key] ?? "") == placeholders(L10n.ru[key] ?? ""), "\(key)")
        }
    }

    @Test("default language is English")
    func defaultLanguage() {
        #expect(L10n.defaultLanguage == "en")
        #expect(RecorderConfig().language == "en")
    }

    @Test("lookup formats arguments and falls back to English")
    func lookup() {
        #expect(L10n.text(.menuQuit, language: "en") == "Quit")
        #expect(L10n.text(.menuQuit, language: "ru") == L10n.ru[.menuQuit])
        #expect(L10n.text(.menuQuit, language: "de") == "Quit")
        #expect(L10n.text(.menuShowLast, language: "en", args: ["a%@b"]) == "Show Last Recording (a%@b)")
    }
}
