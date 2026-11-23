"""Transcription acceptance check on synthetic audio (loads the model): uv run pytest -m slow -s

Recordings are built like the recorder's: mic.caf, remote.caf (Float32) and meta.json. Speech comes from `say`.
"""

import re
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ma import meta as M
from ma.config import Config
from ma.process import process

pytestmark = pytest.mark.slow
SR = 16_000
FFMPEG = Config().ffmpeg

MIC_LINES = [
    "Привет! Давай засинкаемся по релизу. У меня есть консерн по скоупу этой фичи.",
    "Окей, тогда апрувим аппроч: сначала хотфикс на проде, потом деплой на стейджинг.",
    "Задачу заведу в Jira, а дашборд соберу в Grafana.",
]
REMOTE_LINES = [
    "Согласен. Я бы челенжил дедлайн: бэклог у нас большой, а пул-реквест ещё на ревью.",
    "Договорились. Экшн-айтем на мне: фоллоу-ап по метрикам до пятницы, асап.",
    "И ещё нужен ретро после спринта, там обсудим техдолг и рефакторинг.",
]
TERMS = ["засинк", "консерн", "скоуп", "фич", "апрув", "аппроч", "хотфикс", "прод", "деплой", "стейджинг", "jira",
         "дашборд", "grafana", "челенж", "дедлайн", "бэклог", "пул-реквест", "ревью", "экшн-айтем", "фоллоу-ап",
         "метрик", "асап", "ретро", "спринт", "техдолг", "рефакторинг"]


def say(text: str, voice: str, tmp: Path) -> np.ndarray:
    aiff, wav = tmp / "say.aiff", tmp / "say.wav"
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(aiff), "-ar", str(SR), "-ac", "1", str(wav)], check=True)
    return sf.read(str(wav), dtype="float32")[0]


def dialog(tmp: Path) -> tuple[np.ndarray, np.ndarray]:
    """Utterances take turns: mic is silent while remote speaks and vice versa, with pauses between."""
    mic, rem = [], []
    gap = np.zeros(int(1.2 * SR), np.float32)
    for m_line, r_line in zip(MIC_LINES, REMOTE_LINES):
        m = say(m_line, "Milena", tmp)
        r = say(r_line, "Yuri", tmp)
        mic += [m, gap, np.zeros_like(r), gap]
        rem += [np.zeros_like(m), gap, r, gap]
    return np.concatenate(mic), np.concatenate(rem)


def make_recording(root: Path, rid: str, mic: np.ndarray, rem: np.ndarray) -> Path:
    folder = root / rid
    folder.mkdir(parents=True)
    n = max(len(mic), len(rem))
    mic, rem = np.pad(mic, (0, n - len(mic))), np.pad(rem, (0, n - len(rem)))
    for name, x in (("mic", mic), ("remote", rem)):
        sf.write(str(folder / f"{name}.caf"), x, SR, format="CAF", subtype="FLOAT")
    track = lambda f: {"file": f, "sample_rate": SR, "frames": n, "seconds": n / SR, "segments": []}
    M.write(folder, {"version": 1, "id": rid, "status": "recorded", "trigger": "manual", "duration_seconds": n / SR,
                     "t0_host_time": 0, "host_ticks_per_second": 24_000_000, "mode": "headphones",
                     "route_changes": [], "call_periods": [], "warnings": [],
                     "tracks": {"mic": track("mic.caf"), "remote": track("remote.caf")}})
    return folder


@pytest.fixture
def home(tmp_path, monkeypatch):
    if not Path(Config().diarizer).exists():
        pytest.skip("ma-diarize missing: run diarizer/scripts/build.sh")
    monkeypatch.setenv("MA_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    (home / "vault").mkdir(parents=True)
    # a separate Obsidian vault (not the real one!) and no minutes: another test covers them
    (home / "config.toml").write_text(f'language = "ru"\nobsidian_vault = "{home / "vault"}"\nmin_meeting_seconds = 1000000\n')
    return home


def text_of(folder: Path) -> str:
    return (folder / "transcript.md").read_text(encoding="utf-8").lower()


def test_glossary_terms(home, tmp_path):
    mic, rem = dialog(tmp_path)
    folder = make_recording(home / "recordings", "terms", mic, rem)
    process(folder)
    text = text_of(folder)
    print("\n" + (folder / "transcript.md").read_text(encoding="utf-8"))
    missing = [t for t in TERMS if t not in text]
    assert not missing, f"terms not spelled as in the glossary: {missing}"
    assert "**00:00:00 я:**" in text and "голос " in text
    assert not (folder / "mic.caf").exists() and (folder / "audio.m4a").exists()


def test_no_text_on_silence(home):
    rng = np.random.default_rng(1)
    n = 5 * 60 * SR
    # mic: pink room noise at −60 dBFS; remote: 2 min of exact zeros, then −99 dBFS noise, like Slack in pauses
    white = rng.standard_normal(n)
    pink = np.fft.irfft(np.fft.rfft(white) / np.sqrt(np.arange(1, n // 2 + 2)), n)
    pink = (pink / np.sqrt(np.mean(pink ** 2)) * 10 ** (-60 / 20)).astype(np.float32)
    rem = np.zeros(n, np.float32)
    rem[2 * 60 * SR:] = (rng.standard_normal(n - 2 * 60 * SR) * 10 ** (-99 / 20)).astype(np.float32)
    folder = make_recording(home / "recordings", "silence", pink, rem)
    process(folder)
    import json
    tr = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    assert tr["utterances"] == [], f"invented text on silence: {[u['text'] for u in tr['utterances']]}"


def test_hour_under_five_minutes(home, tmp_path):
    mic, rem = dialog(tmp_path)
    reps = int(np.ceil(3600 * SR / len(mic)))
    folder = make_recording(home / "recordings", "hour", np.tile(mic, reps)[: 3600 * SR], np.tile(rem, reps)[: 3600 * SR])
    t = time.time()
    process(folder)
    elapsed = time.time() - t
    steps = {s: M.read(folder)["pipeline"][s]["seconds"] for s in M.STEPS}
    print(f"\none hour of recording: {elapsed:.0f} s ({steps})")
    assert elapsed < 300
    words = len(re.findall(r"\w+", text_of(folder)))
    assert words > 5000, f"too few words for an hour: {words}"
