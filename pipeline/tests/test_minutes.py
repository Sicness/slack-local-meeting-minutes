"""Minutes (with a fake Ollama) and Obsidian notes."""

import json

import pytest

from ma import minutes, notes
from ma.config import Config
from ma.glossary import Glossary

MINUTES = {"title": "Запуск @v2 и QR", "summary": "@me и @v2 договорились.",
           "decisions": [{"at": "0:00:05", "text": "Катим в четверг", "who": ["@me", "@v9"]}],
           "action_items": [{"at": "[00:00:21]", "owner": "@v2", "task": "Дописать лимиты", "due": "до среды"},
                            {"at": "", "owner": "@v7", "task": "Написать FAQ", "due": ""}],
           "open_questions": ["Кто дежурит у @v3?"],
           "topics": [{"title": "Лимиты", "points": ["@v2 рассказал про лимиты."]}]}


def transcript_doc():
    u = lambda s, who, text, c=0.9: {"start": s, "end": s + 2, "speaker": who, "track": "remote", "text": text,
                                     "confidence": c, "words": []}
    return {"version": 2, "recording_id": "rec", "speakers": {"@me": "Я", "@v2": "Алиса", "@v3": "Голос 3"},
            "utterances": [u(1, "@me", "привет"), u(4, "@v2", "давайте в четверг"), u(6, "@v2", "да"),
                           u(20, "@v3", "я возьму", 0.4), u(30, "@me", "ок")]}


def recording(tmp_path, duration=600):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "transcript.json").write_text(json.dumps(transcript_doc(), ensure_ascii=False))
    meta = {"id": "2026-01-15_1151_abcd", "started_at": "2026-01-15T11:51:20.093+00:00", "duration_seconds": duration,
            "source": "slack"}
    return folder, meta


def fake_ollama(monkeypatch, replies):
    calls = []

    def post(cfg, body, timeout=1800):
        calls.append(body)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(minutes, "_post", post)
    return calls


def ok(m=MINUTES):
    return {"done_reason": "stop", "message": {"content": json.dumps(m, ensure_ascii=False)},
            "prompt_eval_count": 100, "eval_count": 50}


# --- minutes ---

def test_minutes_retry_without_thinking_and_clean(tmp_path, monkeypatch):
    folder, meta = recording(tmp_path)
    calls = fake_ollama(monkeypatch, [{"done_reason": "length", "message": {"content": "{"}}, ok()])
    res = minutes.run(folder, meta, Config(language="ru"), Glossary(terms=["Jira"]))
    assert [c["think"] for c in calls] == [True, False] and res["think"] is False
    body = calls[0]
    assert body["format"] == minutes.SCHEMA and body["keep_alive"] == 0
    assert "Jira" in body["messages"][0]["content"] and "@v2 — Алиса" in body["messages"][1]["content"]
    assert "@v3 — имя неизвестно" in body["messages"][1]["content"]
    assert "[00:00:20] @v3: я возьму" in body["messages"][1]["content"]
    m = json.loads((folder / "minutes.json").read_text())["minutes"]
    assert m["decisions"][0] == {"at": "00:00:05", "text": "Катим в четверг", "who": ["@me"]}   # foreign label removed
    assert m["action_items"][0]["at"] == "00:00:21" and m["action_items"][1]["owner"] == ""


def test_minutes_short_meeting_and_errors(tmp_path, monkeypatch):
    folder, meta = recording(tmp_path, duration=60)
    (folder / "minutes.json").write_text("{}")
    fake_ollama(monkeypatch, [])
    assert minutes.run(folder, meta, Config(), Glossary()) == {"skipped": "short"}
    assert not (folder / "minutes.json").exists()
    meta["duration_seconds"] = 600
    fake_ollama(monkeypatch, [minutes.MinutesError("Ollama is unavailable")])
    with pytest.raises(minutes.MinutesError, match="unavailable"):
        minutes.run(folder, meta, Config(), Glossary())
    fake_ollama(monkeypatch, [{"done_reason": "length", "message": {"content": ""}}] * 2)
    with pytest.raises(minutes.MinutesError, match="length"):
        minutes.run(folder, meta, Config(), Glossary())


def test_context_size():
    assert minutes.context_size("а" * 100, "б" * 100, 16384) == 32768
    assert minutes.context_size("", "в" * 100_000, 16384) == 65536


def test_ollama_down_message():
    # the Swift recorder matches "Ollama is unavailable" to schedule retries
    with pytest.raises(minutes.MinutesError, match=r"^Ollama is unavailable at http://127\.0\.0\.1:9 .*Ollama\.app"):
        minutes._post(Config(ollama_url="http://127.0.0.1:9"), {}, timeout=2)


# --- notes ---

def test_names_links_and_file_names():
    names = notes.names_for(transcript_doc(), Config(me_name="Alex"))
    assert notes.sub("@me, @v2 и @v3; @v99", names) == "[[Alex]], [[Алиса]] и Голос 3; @v99"
    meta = {"started_at": "2026-01-15T11:51:20+00:00"}
    assert notes.note_name(meta, 'QR: "лимиты" / #1 [черновик]') == "2026-01-15 1151 — QR лимиты 1 черновик"
    assert notes.note_name({"id": "2026-01-15_1030_beef"}, "  ", language="ru") == "2026-01-15 1030 — Встреча"
    paras = notes.paragraphs(transcript_doc())
    assert [p["start"] for p in paras] == [1, 4, 20, 30] and paras[2]["text"].endswith("(?)")
    assert notes.time_link("00:00:06", paras, "T/x") == " ([[T/x#^t000004|00:00:06]])"
    assert notes.time_link("", paras, "T/x") == ""


def test_notes_write_and_rerender_keeps_user_edits(tmp_path):
    folder, meta = recording(tmp_path)
    cleaned, _ = minutes.clean(json.loads(json.dumps(MINUTES)), set(transcript_doc()["speakers"]))
    (folder / "minutes.json").write_text(json.dumps({"model": "qwen", "minutes": cleaned}, ensure_ascii=False))
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = Config(obsidian_vault=str(vault), language="ru")
    res = notes.run(folder, meta, cfg)
    mnote, tnote = vault / res["meeting"], vault / res["transcript"]
    assert res["meeting"] == "Meetings/2026-01-15 1151 — Запуск Алиса и QR.md"
    text = mnote.read_text()
    assert "[[Алиса]] — Дописать лимиты — до среды" in text and "не назначено — Написать FAQ — срок не назван" in text
    assert '([[Transcripts/2026-01-15 1151 — Запуск Алиса и QR#^t000004|00:00:05]])' in text
    assert 'participants: ["[[Me]]", "[[Алиса]]", "Голос 3"]' in text and "@v" not in text
    assert "^t000020" in tnote.read_text() and "Голос 3:** я возьму (?)" in tnote.read_text()

    # The user added text outside the block and a key of their own; voice 3 got a name: re-render
    mnote.write_text(text.replace("---\n\n", "status: done\n---\n\nМои заметки\n\n", 1) + "\nПосле блока\n")
    doc = transcript_doc()
    doc["speakers"]["@v3"] = "Виктор"
    (folder / "transcript.json").write_text(json.dumps(doc, ensure_ascii=False))
    res2 = notes.run(folder, meta, cfg)
    assert res2 == res                                          # file name does not change
    text = mnote.read_text()
    assert "status: done" in text and "Мои заметки" in text and text.rstrip().endswith("После блока")
    assert '"[[Виктор]]"' in text and "Кто дежурит у [[Виктор]]?" in text

    # block removed by hand: leave the note alone
    mnote.write_text("моё")
    notes.run(folder, meta, cfg)
    assert mnote.read_text() == "моё"


def test_notes_short_meeting_only_transcript(tmp_path):
    folder, meta = recording(tmp_path, duration=60)
    vault = tmp_path / "vault"
    vault.mkdir()
    res = notes.run(folder, meta, Config(obsidian_vault=str(vault), language="ru"))
    assert res["meeting"] is None and (vault / res["transcript"]).exists()
    assert "Протокол:" not in (vault / res["transcript"]).read_text()


def test_notes_need_vault(tmp_path):
    folder, meta = recording(tmp_path)
    with pytest.raises(notes.NotesError, match="obsidian_vault"):
        notes.run(folder, meta, Config(obsidian_vault=str(tmp_path / "nope")))


def test_english_prompt_and_notes(tmp_path, monkeypatch):
    folder, meta = recording(tmp_path)
    calls = fake_ollama(monkeypatch, [ok()])
    minutes.run(folder, meta, Config(me_name="Alex"), Glossary(terms=["Jira"]))
    system, user = calls[0]["messages"][0]["content"], calls[0]["messages"][1]["content"]
    assert "Write in English" in system and "@me is Alex" in system and "Jira" in system and "{" not in system
    assert user.startswith("Participants:\n@me — Alex") and "@v3 — name unknown" in user and "Transcript:" in user
    vault = tmp_path / "vault"
    vault.mkdir()
    res = notes.run(folder, meta, Config(me_name="Alex", obsidian_vault=str(vault)))
    text = (vault / res["meeting"]).read_text()
    for heading in ("## Decisions", "## Action items", "## Open questions and concerns", "## Discussion"):
        assert heading in text
    assert "unassigned" in text and "no due date" in text and "Full transcript" in text and "## Решения" not in text


def test_old_russian_voice_labels_stay_unnamed():
    doc = transcript_doc()                            # stored "Голос 3"
    assert notes.names_for(doc, Config())["@v3"] == "Голос 3"
    assert "@v3 — name unknown" in minutes.participants(doc, "Alex")


def test_model_text_cannot_act_as_markup():
    assert notes.safe("![x](https://evil.example/?d=1)") == "!\\\\[x](https://evil.example/?d=1)".replace("\\\\", "\\")
    assert "<" not in notes.safe('<img src="https://evil.example"> <!-- ma:end -->')
    assert notes.safe("![[Private note]]").startswith("!\\[")
    doc = transcript_doc()
    doc["utterances"][0]["text"] = "<!-- ma:end --> ![a](http://x)"
    body = notes.transcript_body(notes.paragraphs(doc), notes.names_for(doc, Config()), None)
    assert notes.END not in body and "![a]" not in body
