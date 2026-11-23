"""Log: stderr and ~/MeetingAssistant/logs/pipeline.log."""

import sys
from datetime import datetime

from . import paths


def _write(level: str, text: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{level}] {text}"
    print(line, file=sys.stderr, flush=True)
    try:
        paths.logs().mkdir(parents=True, exist_ok=True)
        with open(paths.logs() / "pipeline.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def info(text: str) -> None:
    _write("info", text)


def warn(text: str) -> None:
    _write("warn", text)


def error(text: str) -> None:
    _write("error", text)
