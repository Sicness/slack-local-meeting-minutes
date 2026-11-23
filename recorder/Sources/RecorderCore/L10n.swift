import Foundation

/// User-facing strings. `language` comes from `RecorderConfig.language`; missing keys fall back to English.
/// Placeholders are always `%@`, arguments are strings.
public enum L10n {
    public static let defaultLanguage = "en"

    public enum Key: String, CaseIterable, Sendable {
        // Menu
        case menuWarnings, menuMicDenied, menuCaptureDenied, menuNotificationsOff
        case menuDiscard, menuAutoRecord, menuLaunchAtLogin, menuLaunchNeedsApproval
        case menuOpenUI, menuOpenFolder, menuQuit, menuStopAndSave, menuRecordManually, menuShowLast
        // Status lines
        case statusRecordingCall, statusRecording, modeRoom, modeHeadphones, statusDetail
        case slackInCall, slackNotInCall, slackAudioNotRecording
        case statusNotRecordingThisCall, statusWaiting, statusNotRecording
        case processingCleanup, processingItem, processingPaused, processingQueued
        // Notifications
        case notifyRecordingTitle, notifyRecordingBody, notifyDiscardAction
        case notifyOllamaTitle, notifyOllamaBody, notifyFailedTitle, notifyFailedSeeLog
        case notifyMinutesReady, notifyTranscriptReady
        // Alerts
        case noticeTitle, noticeText, noticeAccept, noticeDecline
        case alertUIPortBusyTitle, alertUIPortBusyText
        case alertStartFailed, alertDiscardTitle, alertDiscardText, alertDelete, alertCancel
        case alertLoginFailed, alertNoPipelineTitle, alertNoPipelineText
        case alertUIStartFailed, alertUITimedOut, alertUISeeLog
        // Recording warnings
        case warnNoPipeline, warnNoMic, warnNoCapture, warnTapFailed, warnRemoteZeros, warnRemoteStillZeros
        case warnMicZeros, warnOverflow, warnMicFailed
        // Microphone state
        case micNotSelected, micWaitingBluetooth, micDeviceGone, micRateChanged, micError
    }

    public static func text(_ key: Key, language: String, args: [String] = []) -> String {
        let table = language == "ru" ? ru : en
        let template = table[key] ?? en[key] ?? key.rawValue
        return args.isEmpty ? template : String(format: template, arguments: args.map { $0 as NSString })
    }

    static let en: [Key: String] = [
        .menuWarnings: "Warnings:",
        .menuMicDenied: "No microphone access — Open Settings…",
        .menuCaptureDenied: "No audio capture access — Open Settings…",
        .menuNotificationsOff: "Notifications are off (no “Don’t Record” action) — Open Settings…",
        .menuDiscard: "Stop Without Saving…",
        .menuAutoRecord: "Record Slack Calls Automatically",
        .menuLaunchAtLogin: "Launch at Login",
        .menuLaunchNeedsApproval: "Launch at Login — Approve in Settings…",
        .menuOpenUI: "Open Recordings and Voices…",
        .menuOpenFolder: "Open Recordings Folder",
        .menuQuit: "Quit",
        .menuStopAndSave: "Stop and Save",
        .menuRecordManually: "Record Manually",
        .menuShowLast: "Show Last Recording (%@)",

        .statusRecordingCall: "Recording call %@ · %@",
        .statusRecording: "Recording %@ · %@",
        .modeRoom: "room",
        .modeHeadphones: "headphones",
        .statusDetail: "Microphone: %@ · Slack: %@",
        .slackInCall: "in call",
        .slackNotInCall: "not in call",
        .slackAudioNotRecording: "Slack audio not recording",
        .statusNotRecordingThisCall: "Not recording this call",
        .statusWaiting: "Waiting for a Slack call",
        .statusNotRecording: "Not recording",
        .processingCleanup: "Cleaning up old audio",
        .processingItem: "Processing: %@",
        .processingPaused: " — paused during call",
        .processingQueued: "%@ queued",

        .notifyRecordingTitle: "Recording Slack call",
        .notifyRecordingBody: "Transcript and minutes will be made on this Mac after the call.",
        .notifyDiscardAction: "Don’t Record",
        .notifyOllamaTitle: "Ollama isn’t running",
        .notifyOllamaBody: "The transcript is ready. Minutes will follow once Ollama starts — retrying every 15 minutes.",
        .notifyFailedTitle: "Processing failed",
        .notifyFailedSeeLog: "see pipeline.log",
        .notifyMinutesReady: "Minutes ready",
        .notifyTranscriptReady: "Transcript ready",

        .alertStartFailed: "Couldn’t start recording",
        .alertDiscardTitle: "Delete this recording?",
        .alertDiscardText: "Recording will stop and its files will be permanently deleted. No new recording will start until this call ends.",
        .alertDelete: "Delete",
        .alertCancel: "Cancel",
        .noticeTitle: "Recording other people requires their consent",
        .noticeText: "This app records everyone on the call and keeps voiceprints to recognize people in later meetings. Many countries and US states require you to tell participants or get their consent, and voiceprints can count as biometric data. You are responsible for following the law and your organization's rules.",
        .noticeAccept: "I Understand",
        .noticeDecline: "Not Now",
        .alertUIPortBusyTitle: "Another program is using the web interface port",
        .alertUIPortBusyText: "Port %@ is taken. Close that program, or set a different port in [ui] in config.toml.",
        .alertLoginFailed: "Couldn’t change Launch at Login",
        .alertNoPipelineTitle: "uv or pipeline/ not found",
        .alertNoPipelineText: "Set uv and pipeline under [recorder] in ~/MeetingAssistant/config.toml.",
        .alertUIStartFailed: "Couldn’t start the web interface",
        .alertUITimedOut: "The web interface didn’t start",
        .alertUISeeLog: "Details: ~/MeetingAssistant/logs/ui.log",

        .warnNoPipeline: "uv or pipeline/ not found — processing after recording is off (set uv and pipeline under [recorder])",
        .warnNoMic: "No microphone access: the “Me” track will be empty. Settings → Privacy → Microphone",
        .warnNoCapture: "No audio capture access: the Slack track will be empty. Settings → Privacy → Screen & System Audio Recording",
        .warnTapFailed: "Couldn’t start capturing Slack audio: %@",
        .warnRemoteZeros: "Slack audio: 10 s of digital silence during the call — rebuilt the tap",
        .warnRemoteStillZeros: "Slack audio is still digital silence. Check the audio recording permission in Settings",
        .warnMicZeros: "Microphone has been digitally silent for 10 s — check the microphone permission",
        .warnOverflow: "Disk can’t keep up with recording: some audio was lost",
        .warnMicFailed: "Microphone “%@” failed to start: %@",

        .micNotSelected: "not selected yet",
        .micWaitingBluetooth: "waiting for Slack (default is Bluetooth)",
        .micDeviceGone: "device disconnected",
        .micRateChanged: "sample rate changed",
        .micError: "error: %@",
    ]

    static let ru: [Key: String] = [
        .menuWarnings: "Предупреждения:",
        .menuMicDenied: "Нет доступа к микрофону — открыть Настройки…",
        .menuCaptureDenied: "Нет доступа к записи звука — открыть Настройки…",
        .menuNotificationsOff: "Уведомления выключены (нет «Не записывать») — открыть Настройки…",
        .menuDiscard: "Остановить и не сохранять…",
        .menuAutoRecord: "Записывать звонки Slack сам",
        .menuLaunchAtLogin: "Запускать при входе в систему",
        .menuLaunchNeedsApproval: "Запускать при входе — разрешите в Настройках…",
        .menuOpenUI: "Открыть записи и голоса…",
        .menuOpenFolder: "Открыть папку записей",
        .menuQuit: "Выйти",
        .menuStopAndSave: "Остановить и сохранить",
        .menuRecordManually: "Записать вручную",
        .menuShowLast: "Показать последнюю запись (%@)",

        .statusRecordingCall: "Записываю звонок %@ · %@",
        .statusRecording: "Запись %@ · %@",
        .modeRoom: "комната",
        .modeHeadphones: "наушники",
        .statusDetail: "Микрофон: %@ · Slack: %@",
        .slackInCall: "в звонке",
        .slackNotInCall: "не в звонке",
        .slackAudioNotRecording: "звук Slack не пишется",
        .statusNotRecordingThisCall: "Не записываю этот звонок",
        .statusWaiting: "Жду звонок Slack",
        .statusNotRecording: "Не записываю",
        .processingCleanup: "Очистка старого аудио",
        .processingItem: "Обработка: %@",
        .processingPaused: " — пауза, идёт звонок",
        .processingQueued: "в очереди %@",

        .notifyRecordingTitle: "Записываю звонок Slack",
        .notifyRecordingBody: "Расшифровка и протокол — после звонка, на этом Mac.",
        .notifyDiscardAction: "Не записывать",
        .notifyOllamaTitle: "Ollama не запущена",
        .notifyOllamaBody: "Расшифровка готова, протокол сделаю, когда запустится Ollama — пробую раз в 15 минут.",
        .notifyFailedTitle: "Обработка записи не удалась",
        .notifyFailedSeeLog: "см. pipeline.log",
        .notifyMinutesReady: "Протокол готов",
        .notifyTranscriptReady: "Расшифровка готова",

        .alertStartFailed: "Не удалось начать запись",
        .alertDiscardTitle: "Удалить эту запись?",
        .alertDiscardText: "Запись остановится, а её файлы удалятся без возможности восстановления. До конца этого звонка новая запись не начнётся.",
        .alertDelete: "Удалить",
        .alertCancel: "Отмена",
        .noticeTitle: "Для записи других людей нужно их согласие",
        .noticeText: "Приложение записывает всех участников звонка и хранит отпечатки голосов, чтобы узнавать людей на следующих встречах. Во многих странах участников нужно предупредить или получить их согласие, а отпечатки голоса могут считаться биометрическими данными. За соблюдение закона и правил вашей организации отвечаете вы.",
        .noticeAccept: "Понятно",
        .noticeDecline: "Не сейчас",
        .alertUIPortBusyTitle: "Порт веб-интерфейса занят другой программой",
        .alertUIPortBusyText: "Порт %@ занят. Закройте эту программу или укажите другой порт в разделе [ui] файла config.toml.",
        .alertLoginFailed: "Не удалось изменить запуск при входе",
        .alertNoPipelineTitle: "Не нашёл uv или pipeline/",
        .alertNoPipelineText: "Укажите [recorder] uv и pipeline в ~/MeetingAssistant/config.toml.",
        .alertUIStartFailed: "Не удалось запустить веб-интерфейс",
        .alertUITimedOut: "Веб-интерфейс не запустился",
        .alertUISeeLog: "Подробности — ~/MeetingAssistant/logs/ui.log",

        .warnNoPipeline: "не нашёл uv или pipeline/ — обработка после записи выключена (укажите [recorder] uv и pipeline)",
        .warnNoMic: "Нет доступа к микрофону: дорожка «Я» будет пустой. Настройки → Конфиденциальность → Микрофон",
        .warnNoCapture: "Нет доступа к записи звука: дорожка Slack будет пустой. Настройки → Конфиденциальность → Запись экрана и системного звука",
        .warnTapFailed: "Не удалось включить запись звука Slack: %@",
        .warnRemoteZeros: "Звук Slack: 10 с ровных нулей во время звонка — пересоздал tap",
        .warnRemoteStillZeros: "Звук Slack по-прежнему ровные нули. Проверьте разрешение «Запись звука» в Настройках",
        .warnMicZeros: "Микрофон 10 с отдаёт ровные нули — проверьте разрешение на микрофон",
        .warnOverflow: "Запись не успевает на диск: часть звука потеряна",
        .warnMicFailed: "Микрофон «%@» не запустился: %@",

        .micNotSelected: "ещё не выбран",
        .micWaitingBluetooth: "ждёт Slack (по умолчанию Bluetooth)",
        .micDeviceGone: "устройство пропало",
        .micRateChanged: "смена частоты",
        .micError: "ошибка: %@",
    ]
}
