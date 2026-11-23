"""Settings from ~/MeetingAssistant/config.toml. No file means defaults."""

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import log, paths

LANGUAGES = ("en", "ru")
DEFAULT_ME = {"en": "Me", "ru": "Я"}


def find_tool(name: str) -> str:
    """On PATH or in Homebrew: apps started from Finder have a minimal PATH."""
    return shutil.which(name) or shutil.which(name, path="/opt/homebrew/bin:/usr/local/bin") or name


@dataclass(frozen=True)
class Config:
    asr_model: str = "mlx-community/whisper-large-v3-turbo"
    # a second model rechecks weak spots; "" turns it off
    second_model: str = "mlx-community/whisper-large-v3-mlx"
    # language of the UI, notes and minutes
    language: str = "en"
    # spoken language for Whisper; without [asr] language, same as language
    asr_language: str = "en"
    ffmpeg: str = field(default_factory=lambda: find_tool("ffmpeg"))
    # background | low | normal, see priority.py
    priority: str = "background"
    # [voices]: diarizer (diarizer/), registry match threshold, minimum speech for a new voice
    diarizer: str = str(paths.REPO / "diarizer" / "build" / "ma-diarize")
    match_threshold: float = 0.65
    min_speech_seconds: float = 5.0
    # minutes: local model via Ollama; meetings shorter than min_meeting_seconds get none
    minutes_model: str = "qwen3.5:9b"
    ollama_url: str = "http://127.0.0.1:11434"
    minutes_think: bool = True
    minutes_max_tokens: int = 16384   # reply cap including reasoning: the model sometimes loops
    min_meeting_seconds: float = 120.0
    # Obsidian
    obsidian_vault: str = str(Path.home() / "Documents" / "Obsidian")
    meetings_folder: str = "Meetings"
    transcripts_folder: str = "Transcripts"
    me_name: str = "Me"
    audio_retention_days: float = 7.0
    # web UI: 127.0.0.1 only, the port is configurable
    ui_port: int = 8765

    @property
    def ffprobe(self) -> str:
        return str(Path(self.ffmpeg).with_name("ffprobe"))


def load(path: Path | None = None) -> Config:
    path = path or paths.config()
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    asr = data.get("asr", {})
    voices = data.get("voices", {})
    minutes = data.get("minutes", {})
    base = Config()
    language = str(data.get("language", base.language))
    if language not in LANGUAGES:
        log.warn(f"config.toml: language = {language!r} is not supported, using {base.language}")
        language = base.language
    return Config(
        asr_model=str(asr.get("model", base.asr_model)),
        second_model=str(asr.get("second_model", base.second_model)),
        language=language,
        asr_language=str(asr.get("language", language)),
        ffmpeg=str(data.get("ffmpeg", base.ffmpeg)),
        priority=str(data.get("pipeline", {}).get("priority", base.priority)),
        diarizer=str(Path(voices.get("diarizer", base.diarizer)).expanduser()),
        match_threshold=float(voices.get("match_threshold", base.match_threshold)),
        min_speech_seconds=float(voices.get("min_speech_seconds", base.min_speech_seconds)),
        minutes_model=str(minutes.get("model", base.minutes_model)),
        ollama_url=str(minutes.get("ollama_url", base.ollama_url)).rstrip("/"),
        minutes_think=bool(minutes.get("think", base.minutes_think)),
        minutes_max_tokens=int(minutes.get("max_tokens", base.minutes_max_tokens)),
        min_meeting_seconds=float(data.get("min_meeting_seconds", base.min_meeting_seconds)),
        obsidian_vault=str(Path(data.get("obsidian_vault", base.obsidian_vault)).expanduser()),
        meetings_folder=str(data.get("meetings_folder", base.meetings_folder)),
        transcripts_folder=str(data.get("transcripts_folder", base.transcripts_folder)),
        me_name=str(data.get("me_name") or DEFAULT_ME[language]),
        ui_port=int(data.get("ui", {}).get("port", base.ui_port)),
        audio_retention_days=float(data.get("audio_retention_days", base.audio_retention_days)),
    )
