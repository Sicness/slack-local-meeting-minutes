import json

import pytest

from ma import glossary, meta as M, timeline, transcript, vad
from ma.asr import filter_segments, is_loop, needs_rescue
from ma.process import ProcessError, process


# --- glossary ---

def test_glossary_parse():
    g = glossary.parse("# comment\n! консерн\nдашборд   # not in prompt\n!  Jira\nделпой => деплой\n\n")
    assert g.prompt_terms == ["консерн", "Jira"]
    assert g.terms == ["консерн", "дашборд", "Jira"]
    assert g.replacements == [("делпой", "деплой")]


def test_glossary_from_repo_markdown():
    md = (glossary.DATA / "glossary.ru.md").read_text(encoding="utf-8")
    g = glossary.parse(glossary.from_markdown(md))
    assert "челенжить" in g.prompt_terms and "Slack" in g.prompt_terms and "консерн" in g.terms
    assert not any("—" in t for t in g.terms), "explanations after the dash stay out of terms"
    assert ("деплай", "деплой") in g.replacements
    assert not any("конь серн" in w for w, _ in g.replacements), "an example from glossary.md is not a replacement"


def test_prompt_token_limit():
    g = glossary.Glossary(prompt_terms=["аа", "бб", "вв"], head="Созвон команды.")
    prompt, dropped = g.build_prompt(len, limit=len("Созвон команды. аа, бб."))
    assert prompt == "Созвон команды. аа, бб."
    assert dropped == ["вв"]
    assert glossary.Glossary().build_prompt(len)[0] == "Team call."
    assert glossary.Glossary(prompt_terms=["x"], head="").build_prompt(len)[0] == "x."


def test_replacements_whole_words_case_insensitive():
    g = glossary.Glossary(replacements=[("деплай", "деплой"), ("экшн айтем", "экшн-айтем"), ("jira", "Jira")])
    assert g.replace("Деплай на прод, экшн айтем в JIRA") == "деплой на прод, экшн-айтем в Jira"
    assert g.replace("передеплаить") == "передеплаить"


def test_glossary_created_on_first_load(tmp_path, monkeypatch):
    monkeypatch.setenv("MA_HOME", str(tmp_path))
    g = glossary.load(speech_language="ru")
    assert (tmp_path / "glossary.txt").exists()
    assert "челенжить" in g.prompt_terms and g.head == "Созвон команды."


def test_english_glossary_is_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("MA_HOME", str(tmp_path))
    g = glossary.load()
    assert "standup" in g.prompt_terms and "челенжить" not in g.terms and g.head == "Team call."
    assert g.replacements == []


# --- post-Whisper filter ---

def seg(start, end, text, lp=-0.3, words=None):
    words = words if words is not None else [{"word": " " + t, "start": start, "end": end, "probability": 0.9}
                                              for t in text.split()][:1]
    return {"start": start, "end": end, "text": " " + text, "avg_logprob": lp, "words": words}


def test_filter_reasons():
    regs = [[0.0, 10.0], [20.0, 30.0]]
    prompt_words = {"консерн", "челенжить", "асап", "апрувить", "скоуп"}
    segs = [
        seg(1, 3, "Привет, давай начнём"),
        seg(2, 4, "Продолжение следует..."),
        seg(5, 6, "Субтитры создавал DimaTorzok"),
        seg(12, 15, "Спасибо."),                       # silence between speech regions
        seg(21, 24, "えば局 현재 outsider", lp=-6.0),
        seg(25, 27, "что-то тихое", lp=-1.2, words=[{"word": " что", "start": 25, "end": 25.5, "probability": 0.5}]),
        seg(22, 26, "Консерн, челенжить, асап, апрувить, скоуп"),
    ]
    kept, dropped = filter_segments(segs, regs, prompt_words)
    assert [k["text"] for k in kept] == ["Привет, давай начнём", "что-то тихое"]
    reasons = {d["text"]: d["reason"] for d in dropped}
    assert reasons["Продолжение следует..."] == "known hallucination"
    assert reasons["Субтитры создавал DimaTorzok"] == "known hallucination"
    assert reasons["Спасибо."] == "outside speech"
    assert reasons["えば局 현재 outsider"] == "low confidence"
    assert reasons["Консерн, челенжить, асап, апрувить, скоуп"] == "prompt echo"


def test_filter_drops_words_outside_speech():
    regs = [[0.0, 2.0]]
    words = [{"word": " да", "start": 0.5, "end": 0.8, "probability": 0.9},
             {"word": " спасибо", "start": 6.0, "end": 6.5, "probability": 0.4}]
    kept, _ = filter_segments([seg(0.4, 2.2, "да спасибо", words=words)], regs, set())
    assert [w["w"] for w in kept[0]["words"]] == [" да"]


def test_vad_helpers():
    regs = [[1.0, 2.0], [5.0, 6.0]]
    assert vad.overlap(regs, 0, 10) == pytest.approx(2.0)
    assert vad.inside(regs, 1.5) and not vad.inside(regs, 3.5)


# --- time ---

def test_mode_intervals():
    m = {"mode": "headphones", "route_changes": [{"at_seconds": 10, "mode": "room"}, {"at_seconds": 20, "mode": "headphones"}]}
    iv = timeline.mode_intervals(m, 30)
    assert iv == [(0.0, 10.0, "headphones"), (10.0, 20.0, "room"), (20.0, 30, "headphones")]
    assert timeline.mode_at(iv, 15) == "room" and timeline.mode_at(iv, 25) == "headphones"


def anchors_meta(ppm: float):
    ticks, t0, rate = 24_000_000, 1_000_000_000, 16_000
    anchors = [{"frame": int(k * 10 * rate), "sample_time": 0,
                "host_time": t0 + int(k * 10 * (1 + ppm * 1e-6) * ticks), "rate_scalar": 1} for k in range(7)]
    return {"t0_host_time": t0, "host_ticks_per_second": ticks,
            "tracks": {"mic": {"sample_rate": rate, "segments": [{"anchors": anchors}]}}}


def test_timemap_identity_and_drift():
    assert timeline.TimeMap(anchors_meta(0), "mic")(35.0) == pytest.approx(35.0)
    # mic clock runs 1000 ppm fast: 60 s of file = 60.06 s of real time
    assert timeline.TimeMap(anchors_meta(1000), "mic")(60.0) == pytest.approx(60.06, abs=1e-3)
    assert timeline.TimeMap({"tracks": {}}, "mic")(5.0) == 5.0


# --- transcript ---

def asr_doc(*segments):
    return {"audio_seconds": 30, "segments": [
        {"start": a, "end": b, "text": t,
         "words": [{"w": " " + w, "start": a + i * 0.3, "end": a + i * 0.3 + 0.25, "p": 0.9} for i, w in enumerate(t.split())]}
        for a, b, t in segments]}


def test_transcript_build_and_markdown():
    meta = {"id": "r1", "mode": "headphones", "duration_seconds": 30,
            "route_changes": [{"at_seconds": 20, "mode": "room"}], "tracks": {}}
    asr = {"mic": asr_doc((1, 2, "привет как дела"), (2.5, 3, "деплай готов"), (21, 22, "это в комнате")),
           "remote": asr_doc((5, 6, "всё хорошо"), (12, 13, "отлично"))}
    g = glossary.Glossary(replacements=[("деплай", "деплой")])
    tr = transcript.build(meta, asr, g, language="ru")
    texts = [(u["speaker"], u["text"]) for u in tr["utterances"]]
    assert texts == [("@me", "привет как дела деплой готов"), ("@others", "всё хорошо"),
                     ("@others", "отлично"), ("@room", "это в комнате")]
    md = transcript.markdown(tr, meta, "ru")
    assert "**00:00:01 Я:** привет как дела деплой готов" in md
    assert "**00:00:05 Остальные:** всё хорошо отлично" in md     # consecutive ones are joined
    assert "**00:00:21 Комната:** это в комнате" in md


# --- steps ---

def make_recording(tmp_path, status="recorded", **extra):
    folder = tmp_path / "rec"
    folder.mkdir()
    M.write(folder, {"version": 1, "id": "rec", "status": status, "custom": {"keep": True}, **extra})
    return folder


def test_steps_resume_after_failure(tmp_path):
    folder = make_recording(tmp_path)
    calls = []
    fail = {"asr": True}

    def step(name):
        def run(meta):
            calls.append(name)
            if fail.get(name):
                raise RuntimeError("boom")
            return {"ok": name}
        return run

    steps = {n: step(n) for n in M.STEPS}
    with pytest.raises(RuntimeError):
        process(folder, steps=steps)
    m = M.read(folder)
    assert m["pipeline"]["prepare"]["status"] == "done"
    assert m["pipeline"]["asr"]["status"] == "failed" and "boom" in m["pipeline"]["asr"]["error"]
    assert m["custom"] == {"keep": True}

    fail.clear()
    calls.clear()
    process(folder, steps=steps)
    assert calls == ["asr", "voices", "transcript", "minutes", "notes", "archive"]     # prepare is not repeated
    calls.clear()
    process(folder, steps=steps, from_step="transcript")
    assert calls == ["transcript", "minutes", "notes", "archive"]
    calls.clear()
    process(folder, steps=steps, force=True)
    assert calls == M.STEPS


def test_refuses_while_recording(tmp_path):
    import os
    folder = make_recording(tmp_path, status="recording", recorder={"pid": os.getpid()})
    # pid is alive but not MeetingRecorder: the recorder crashed, recording interrupted, process it
    process(folder, steps={n: (lambda m: {}) for n in M.STEPS})
    assert M.read(folder)["interrupted"] is True


def test_unknown_step(tmp_path):
    with pytest.raises(ProcessError):
        process(make_recording(tmp_path), from_step="nope", steps={})


def test_meta_write_is_valid_json(tmp_path):
    folder = make_recording(tmp_path)
    m = M.read(folder)
    m["текст"] = "ё"
    M.write(folder, m)
    assert json.loads((folder / "meta.json").read_text(encoding="utf-8"))["текст"] == "ё"
    assert not list(folder.glob(".meta.*"))


def test_rescue_selection():
    regs = [[0.0, 30.0]]
    loop = {"reason": "outside speech", "text": "мёж " * 20, "avg_logprob": -0.1, "start": 5, "end": 25}
    junk = {"reason": "low confidence", "text": "Sabрусаном что-то", "avg_logprob": -3.0, "start": 1, "end": 6}
    tiny = {"reason": "low confidence", "text": "ок", "avg_logprob": -3.0, "start": 40, "end": 41}
    hall = {"reason": "known hallucination", "text": "Продолжение следует...", "avg_logprob": -0.4, "start": 2, "end": 9}
    assert is_loop(loop["text"]) and not is_loop("давай засинкаемся по релизу и деплою на прод сегодня")
    assert needs_rescue(loop, regs) and needs_rescue(junk, regs)
    assert not needs_rescue(tiny, regs) and not needs_rescue(hall, regs)


def test_second_opinion_picks_more_confident(monkeypatch):
    from ma import asr
    from ma.config import Config

    def s(a, b, text, lp, p=0.9):
        return {"start": a, "end": b, "text": text, "avg_logprob": lp, "speech_share": 1.0,
                "words": [{"w": " " + text, "start": a, "end": b, "p": p}]}

    kept = [s(0, 2, "хорошо", -0.2), s(3, 4, "Соня снег", -0.8, 0.4), s(4.5, 5, "на", -0.9, 0.3),
            s(10, 11, "горе", -0.6, 0.5)]
    answers = {3: [s(3, 5, "снег падает", -0.3)], 10: [s(10, 11, "горячо", -0.75)]}

    def fake_decode(x, sr, a, b, model, cfg, prompt, temperature=None):
        return [{"start": c["start"], "end": c["end"], "text": c["text"], "avg_logprob": c["avg_logprob"],
                 "words": [{"word": w["w"], "start": w["start"], "end": w["end"], "probability": w["p"]} for w in c["words"]]}
                for c in answers[round(a + asr.SECOND_PAD)]]

    monkeypatch.setattr(asr, "decode_span", fake_decode)
    out, replaced = asr.second_opinion(None, 16000, kept, "m2", Config(), "", [[0, 20]], set())
    assert [o["text"] for o in out] == ["хорошо", "снег падает", "горе"]
    assert replaced == 1 and out[1]["replaced"] == "Соня снег на"


def test_priority_background_in_subprocess():
    import subprocess, sys
    code = ("from ma import priority; import os; m = priority.apply('background'); "
            "print(m, os.getpriority(os.PRIO_PROCESS, 0))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert out[0] == "background" and int(out[1]) >= 10


def test_room_echo_dropped():
    meta = {"id": "r1", "mode": "headphones", "duration_seconds": 30,
            "route_changes": [{"at_seconds": 10, "mode": "room"}], "tracks": {}}
    asr = {"mic": asr_doc((2, 3, "привет как дела"), (12, 13, "ну давайте релиз"), (20, 21, "а я вот думаю")),
           "remote": asr_doc((12.1, 13, "Ну, давайте релиз"), (25, 26, "ок"))}
    tr = transcript.build(meta, asr, glossary.Glossary())
    texts = [(u["speaker"], u["text"]) for u in tr["utterances"]]
    # remote echo from the speakers (12 s) is removed; own speech in the room (20 s) and headphones (2 s) is not
    assert texts == [("@me", "привет как дела"), ("@others", "Ну, давайте релиз"), ("@room", "а я вот думаю"), ("@others", "ок")]
    assert tr["room_echo_dropped"] == 3


def test_cleanup_only_processed_and_old(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from ma import cleanup
    monkeypatch.setenv("MA_HOME", str(tmp_path))
    now = datetime.now().astimezone()
    def rec(rid, days_ago, archived):
        f = tmp_path / "recordings" / rid
        f.mkdir(parents=True)
        for name in ("mic.16k.wav", "remote.16k.wav", "audio.m4a"):
            (f / name).write_bytes(b"x" * 10)
        (f / "transcript.json").write_text("{}")
        M.write(f, {"id": rid, "status": "recorded", "started_at": (now - timedelta(days=days_ago)).isoformat(),
                    "pipeline": {"archive": {"status": "done" if archived else "failed"}}})
        return f
    old, new, unprocessed = rec("old", 10, True), rec("new", 2, True), rec("stuck", 30, False)
    assert cleanup.run(7, dry_run=True) == 30 and (old / "audio.m4a").exists()
    cleanup.run(7)
    assert not any((old / n).exists() for n in cleanup.AUDIO) and (old / "transcript.json").exists()
    assert "audio_deleted_at" in M.read(old)
    assert (new / "audio.m4a").exists() and (unprocessed / "audio.m4a").exists()


def test_overdue_audio_is_reported_not_deleted(tmp_path, monkeypatch):
    from ma import cleanup, meta as M
    monkeypatch.setenv("MA_HOME", str(tmp_path))
    folder = tmp_path / "recordings" / "2026-01-01_1000_aaaa"
    folder.mkdir(parents=True)
    (folder / "remote.caf").write_bytes(b"x")
    M.write(folder, {"id": folder.name, "status": "recorded", "started_at": "2026-01-01T10:00:00+00:00"})
    assert cleanup.overdue(7) == [folder.name] and cleanup.candidates(7) == []
    assert (folder / "remote.caf").exists()
