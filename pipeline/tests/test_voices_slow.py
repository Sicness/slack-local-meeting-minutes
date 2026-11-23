"""Voice acceptance check on synthetic audio (loads the models): uv run pytest -m slow -s tests/test_voices_slow.py

A meeting is several `say` voices taking turns on the remote track, as in Slack (through opus at 32 kbit/s).
A: 4 people; B: three from A plus a new one. Voices in B must get the same numbers as in A, the new one a new number.

On a short meeting the diarizer merges Milena and Lesya (two synthetic female voices, cosine 0.70) into one;
different real people score at most ~0.5, so A uses Thomas instead of Lesya. Merging similar voices is a known
limit, fixed by hand in the web UI.
"""

import json
import random
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from test_slow import FFMPEG, SR, home, make_recording  # noqa: F401  (home is a fixture)

from ma.process import process

pytestmark = pytest.mark.slow

RU = ["Давайте начнём со статуса по релизу, там вроде всё спокойно.",
      "Я посмотрел логи, падает на миграции базы, буду разбираться.",
      "Кластер обновили вчера вечером, по метрикам нагрузка нормальная.",
      "Нужно договориться, кто берёт задачу с платежами на этой неделе.",
      "Я бы не торопился, сначала прогоним тесты на стейджинге.",
      "Там проблема в том, что сертификат истекает в пятницу.",
      "Клиенты жалуются на медленную загрузку отчётов, особенно утром.",
      "Мы уже пробовали увеличить таймаут, не помогло совсем.",
      "Я напишу документ с вариантами и скину в канал до вечера.",
      "У меня есть вопрос по доступам, мне до сих пор не выдали права."]
SHORT_RU = ["Угу.", "Да.", "Согласен.", "Ага, понял.", "Окей."]
EN = ["Let's start with the release status, everything looks fine so far.",
      "I checked the logs, the migration is failing, I will look into it.",
      "We need to decide who takes the payments task this week.",
      "The problem is that the certificate expires on Friday.",
      "I will write a document with options and post it tonight.",
      "I built a prototype, it seems to work, but it needs a review."]
SHORT_EN = ["Yeah.", "Right.", "Okay.", "Sure, got it."]
SPEAKERS = {"yuri": ("Yuri (Enhanced)", "ru"), "milena": ("Milena", "ru"), "lesya": ("Lesya", "ru"),
            "daniel": ("Daniel", "en"), "xander": ("Xander", "en"), "thomas": ("Thomas", "en")}


def tts(tmp: Path, voice: str, text: str) -> np.ndarray:
    key = abs(hash((voice, text))) % 10 ** 12
    wav = tmp / f"{key}.wav"
    if not wav.exists():
        subprocess.run(["say", "-v", voice, "-o", str(tmp / "s.aiff"), text], check=True)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(tmp / "s.aiff"), "-ar", str(SR), "-ac", "1", str(wav)], check=True)
    return sf.read(str(wav), dtype="float32")[0]


def meeting(tmp: Path, people: list[str], turns: int, seed: int) -> tuple[np.ndarray, list[tuple[float, float, str]]]:
    rnd = random.Random(seed)
    parts, truth, t, prev = [], [], 0.5, None
    for _ in range(turns):
        who = rnd.choice([p for p in people if p != prev])
        voice, lang = SPEAKERS[who]
        short = rnd.random() < 0.25
        pool = (SHORT_RU if short else RU) if lang == "ru" else (SHORT_EN if short else EN)
        x = tts(tmp, voice, " ".join(rnd.sample(pool, 1 if short else rnd.choice([1, 2]))))
        start = t + (rnd.uniform(0.2, 1.0) if prev else 0)
        parts.append((start, x))
        truth.append((start, start + len(x) / SR, who))
        t, prev = start + len(x) / SR, who
    y = np.zeros(int((t + 1) * SR), np.float32)
    for start, x in parts:
        y[int(start * SR):int(start * SR) + len(x)] += x
    raw = tmp / f"m{seed}.wav"
    sf.write(str(raw), y / max(1.0, float(np.abs(y).max())), SR)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(raw), "-c:a", "libopus", "-b:a", "32k", str(tmp / "m.opus")], check=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(tmp / "m.opus"), "-ar", str(SR), "-ac", "1", str(raw)], check=True)
    return sf.read(str(raw), dtype="float32")[0], truth


def scored(folder: Path, truth) -> tuple[float, dict[str, str]]:
    """Voice utterances → person by largest overlap; voice → person by majority. Share of wrong utterances."""
    tr = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    utt = [u for u in tr["utterances"] if u["track"] == "remote"]
    pairs = []
    for u in utt:
        ov = {}
        for a, b, who in truth:
            o = min(u["end"], b) - max(u["start"], a)
            if o > 0:
                ov[who] = ov.get(who, 0) + o
        pairs.append((u["speaker"], max(ov, key=ov.get) if ov else None))
    votes: dict[str, dict[str, int]] = {}
    for lab, who in pairs:
        if who:
            votes.setdefault(lab, {}).setdefault(who, 0)
            votes[lab][who] += 1
    mapping = {lab: max(v, key=v.get) for lab, v in votes.items()}
    wrong = sum(1 for lab, who in pairs if who is None or mapping.get(lab) != who)
    return wrong / len(pairs), {who: lab for lab, who in mapping.items()}


def test_same_people_same_numbers(home, tmp_path):
    silent = lambda x: np.zeros_like(x)
    a_rem, a_truth = meeting(tmp_path, ["yuri", "milena", "thomas", "daniel"], 45, 1)
    b_rem, b_truth = meeting(tmp_path, ["yuri", "milena", "daniel", "xander"], 45, 2)
    fa = make_recording(home / "recordings", "A", silent(a_rem), a_rem)
    process(fa)
    err_a, who_a = scored(fa, a_truth)
    fb = make_recording(home / "recordings", "B", silent(b_rem), b_rem)
    process(fb)
    err_b, who_b = scored(fb, b_truth)
    print(f"\nA: utterance errors {err_a:.0%}, {who_a}\nB: utterance errors {err_b:.0%}, {who_b}")
    assert err_a < 0.15 and err_b < 0.15
    for who in ("yuri", "milena", "daniel"):
        assert who_a[who] == who_b[who], f"{who}: {who_a[who]} on A, {who_b[who]} on B"
    assert who_b["xander"] not in who_a.values()
    assert len(set(who_a.values())) == 4


def test_minutes_with_ollama(home, tmp_path):
    """Minutes from the real Ollama model and notes in a temporary vault: format, labels, links."""
    import re
    import urllib.request
    from ma.config import Config
    try:
        urllib.request.urlopen(Config().ollama_url + "/api/version", timeout=3)
    except OSError:
        pytest.skip("Ollama is not running")
    (home / "config.toml").write_text(f'language = "ru"\nobsidian_vault = "{home / "vault"}"\nmin_meeting_seconds = 60\n')
    rem, truth = meeting(tmp_path, ["yuri", "milena", "daniel"], 30, 5)
    folder = make_recording(home / "recordings", "M", np.zeros_like(rem), rem)
    process(folder)
    mn = json.loads((folder / "minutes.json").read_text(encoding="utf-8"))
    tr = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    labels = set(tr["speakers"])
    m = mn["minutes"]
    assert m["title"] and m["summary"] and m["topics"]
    assert all(a["owner"] in labels | {""} for a in m["action_items"])
    note = json.loads((folder / "meta.json").read_text())["notes"]
    text = (home / "vault" / note["meeting"]).read_text(encoding="utf-8")
    trn = (home / "vault" / note["transcript"]).read_text(encoding="utf-8")
    assert "<!-- ma:begin -->" in text and "@v" not in text and "@me" not in text
    ids = set(re.findall(r"\^(t\d{6})\s*$", trn, re.M))
    assert all(i in ids for i in re.findall(r"#\^(t\d{6})\|", text))
    print(f"\nminutes: {mn['model']}, reasoning {mn['think']}, {mn['seconds']} s")
