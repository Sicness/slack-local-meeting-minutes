"""Translations for everything the user reads: web UI, notes, speaker labels.

Logs, CLI output and developer errors are English only.
"""

import re

_UNNAMED = re.compile(r"(?:Voice|Голос) \d+")

CATALOG: dict[str, dict[str, str]] = {
    "en": {
        # speakers
        "me": "Me", "others": "Others", "room": "Room", "unknown": "Unknown", "voice": "Voice {n}",
        # notes
        "note_meeting": "Meeting", "note_transcript": "Transcript", "summary": "Summary.",
        "decisions": "Decisions", "action_items": "Action items",
        "open_questions": "Open questions and concerns", "topics": "Discussion",
        "unassigned": "unassigned", "no_due": "no due date", "full_transcript": "Full transcript",
        "footer": "minutes: {model}, verify against the timestamp links", "minutes_link": "Minutes",
        "duration_h": "{h} h {m} min", "duration_m": "{m} min",
        "transcript_title": "Transcript {id}",
        "transcript_hint": "“Voice N” is a permanent voice number; set a name with `ma voices name N <name>`. "
                           "(?) marks low-confidence speech.",
        # navigation and tables
        "nav_recordings": "Recordings", "nav_voices": "Voices", "col_when": "When", "col_meeting": "Meeting",
        "col_length": "Length", "col_people": "Participants", "col_state": "Status",
        "col_voice": "Voice", "col_name": "Name", "col_meetings": "Meetings", "col_speech": "Speech",
        "col_listen": "Play", "col_same": "Same person as…",
        "no_recordings": "No recordings yet. Start one from the menu bar, or turn on automatic recording there.",
        "link_minutes": "minutes", "link_transcript": "transcript", "link_minutes_obsidian": "minutes in Obsidian",
        # recording page
        "meeting_on": "Meeting {date}", "meeting_voices": "Voices in this meeting",
        "no_voices": "No other speakers in this recording, or voices are not separated yet.",
        "audio_gone": "The audio has been deleted (kept for {days} days). You can't play it, but you can still assign names.",
        "meetings_n": "meetings: {n}", "speech_t": "speech {t}", "listen_at": "Play {t}", "who": "Who is this",
        "name_placeholder": "Name", "save": "Save", "other_voice": "This is a different voice",
        "choose": "— choose —", "new_voice": "New voice", "move": "Move", "processing": "Processing",
        "rerender_notes": "Re-render notes", "restart_from": "Restart processing from step", "run": "Run",
        "steps": "Steps:", "gpu_note": "Processing uses the GPU, so avoid running it during a call.",
        # voices page
        "voices_intro": "A voice number is permanent. A name applies to every meeting: transcripts and Obsidian notes update automatically.",
        "no_voices_yet": "No voices yet.", "name_for": "Name for {label}", "merge_with": "Merge {label} with",
        "merge": "Merge",
        # states
        "state_busy": "processing", "state_failed": "failed at {step}", "state_recording": "recording",
        "state_done": "done", "state_wait": "waiting",
        # how a voice was matched
        "decision_matched": "recognized", "decision_new": "new voice",
        "decision_split": "same person, split by the diarizer", "decision_short": "little speech, recognized",
        "decision_short-near": "little speech, similar to a neighbor", "decision_unknown": "not identified",
        "decision_manual": "assigned manually",
        # API errors shown in the UI
        "err_no_recording": "no such recording", "err_cut": "could not extract the audio",
        "err_audio_gone": "the audio of this recording has been deleted (kept for {days} days)",
        "err_no_voice": "no such voice in this meeting",
        "err_not_ready": "the recording is not processed yet or is being processed",
        "err_no_step": "no such step", "err_busy": "the recording is already being processed",
        # browser messages
        "js_failed": "Failed: {error}", "js_rerendered": "notes re-rendered: {n}", "js_skipped": ", skipped {n}",
        "js_name_saved": "Name saved, {rest}", "js_pick_voice": "Choose whose voice this is",
        "js_moved": "Voice moved, notes re-rendered", "js_pick": "Choose a voice", "js_merged": "Voices merged, {rest}",
        "js_started": "Processing started. Refresh the page in a couple of minutes",
        "js_notes": "Notes re-rendered", "js_no_audio": "Audio unavailable", "js_deleted": "Deleted",
        # deletion
        "delete_voice": "Delete", "delete_recording": "Delete recording",
        "delete_voice_confirm": "Erase this voiceprint and name? The speech stays in transcripts as Unknown. This cannot be undone.",
        "delete_recording_confirm": "Permanently delete this recording, its voice data and its Obsidian notes? Text you added to the notes is kept. This cannot be undone.",
    },
    "ru": {
        "me": "Я", "others": "Остальные", "room": "Комната", "unknown": "Неизвестный", "voice": "Голос {n}",
        "note_meeting": "Встреча", "note_transcript": "Расшифровка", "summary": "Коротко.",
        "decisions": "Решения", "action_items": "Задачи",
        "open_questions": "Открытые вопросы и консерны", "topics": "О чём говорили",
        "unassigned": "не назначено", "no_due": "срок не назван", "full_transcript": "Полная расшифровка",
        "footer": "протокол: {model}, проверяйте по ссылкам на время", "minutes_link": "Протокол",
        "duration_h": "{h} ч {m} мин", "duration_m": "{m} мин",
        "transcript_title": "Расшифровка {id}",
        "transcript_hint": "«Голос N» — постоянный номер голоса, имя назначается `ma voices name N <имя>`. "
                           "(?) — распознано неуверенно.",
        "nav_recordings": "Записи", "nav_voices": "Голоса", "col_when": "Когда", "col_meeting": "Встреча",
        "col_length": "Длина", "col_people": "Участники", "col_state": "Состояние",
        "col_voice": "Голос", "col_name": "Имя", "col_meetings": "Встреч", "col_speech": "Речь",
        "col_listen": "Послушать", "col_same": "Это тот же человек, что…",
        "no_recordings": "Записей пока нет. Начните запись из строки меню или включите там автозапись.",
        "link_minutes": "протокол", "link_transcript": "расшифровка", "link_minutes_obsidian": "протокол в Obsidian",
        "meeting_on": "Встреча {date}", "meeting_voices": "Голоса встречи",
        "no_voices": "Собеседников на этой записи нет или голоса ещё не разделены.",
        "audio_gone": "Звук записи уже удалён (хранится {days} дн.) — послушать нельзя, имена назначать можно.",
        "meetings_n": "встреч: {n}", "speech_t": "речь {t}", "listen_at": "Послушать {t}", "who": "Кто это",
        "name_placeholder": "Имя", "save": "Сохранить", "other_voice": "Это другой голос",
        "choose": "— выбрать —", "new_voice": "Новый голос", "move": "Перенести", "processing": "Обработка",
        "rerender_notes": "Перерисовать заметки", "restart_from": "Перезапустить обработку с шага", "run": "Запустить",
        "steps": "Шаги:", "gpu_note": "Обработка занимает GPU — во время звонка лучше не запускать.",
        "voices_intro": "Номер голоса постоянный. Имя применяется ко всем встречам: расшифровки и заметки в Obsidian перерисуются сами.",
        "no_voices_yet": "Голосов пока нет.", "name_for": "Имя для {label}", "merge_with": "Объединить {label} с",
        "merge": "Объединить",
        "state_busy": "обрабатывается", "state_failed": "ошибка на шаге {step}", "state_recording": "идёт запись",
        "state_done": "готово", "state_wait": "ждёт обработки",
        "decision_matched": "узнан", "decision_new": "новый голос",
        "decision_split": "тот же человек, разделитель раздвоил", "decision_short": "мало речи, узнан",
        "decision_short-near": "мало речи, похож на соседа", "decision_unknown": "не опознан",
        "decision_manual": "назначен вручную",
        "err_no_recording": "нет такой записи", "err_cut": "не удалось вырезать звук",
        "err_audio_gone": "звук этой записи уже удалён (хранится {days} дн.)",
        "err_no_voice": "нет такого голоса во встрече",
        "err_not_ready": "запись ещё не обработана или её сейчас обрабатывают",
        "err_no_step": "нет такого шага", "err_busy": "запись уже обрабатывается",
        "js_failed": "Не вышло: {error}", "js_rerendered": "заметок перерисовано: {n}", "js_skipped": ", пропущено {n}",
        "js_name_saved": "Имя сохранено, {rest}", "js_pick_voice": "Выберите, чей это голос",
        "js_moved": "Голос перенесён, заметки перерисованы", "js_pick": "Выберите голос", "js_merged": "Голоса объединены, {rest}",
        "js_started": "Обработка запущена — обновите страницу через пару минут",
        "js_notes": "Заметки перерисованы", "js_no_audio": "Звук недоступен", "js_deleted": "Удалено",
        "delete_voice": "Удалить", "delete_recording": "Удалить запись",
        "delete_voice_confirm": "Удалить отпечаток голоса и имя? Речь останется в расшифровках как «Неизвестный». Отменить нельзя.",
        "delete_recording_confirm": "Удалить запись, данные её голосов и заметки Obsidian? Текст, который вы дописали в заметки, сохранится. Отменить нельзя.",
    },
}


def t(language: str, key: str, **values) -> str:
    table = CATALOG.get(language, CATALOG["en"])
    text = table.get(key, CATALOG["en"][key])
    return text.format(**values) if values else text


def voice_label(language: str, voice_id: int) -> str:
    return t(language, "voice", n=voice_id)


def is_unnamed(name: str) -> bool:
    """A numbered voice in any language; older transcripts store the Russian label."""
    return _UNNAMED.fullmatch(name) is not None


def browser_strings(language: str) -> dict[str, str]:
    return {k: t(language, k) for k in CATALOG["en"] if k.startswith("js_")}
