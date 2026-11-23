"""A recording's meta.json: read as a dict (unknown fields are kept), written atomically."""

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

STEPS = ["prepare", "asr", "voices", "transcript", "minutes", "notes", "archive"]


def path(folder: Path) -> Path:
    return folder / "meta.json"


def read(folder: Path) -> dict:
    return json.loads(path(folder).read_text(encoding="utf-8"))


def write(folder: Path, meta: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".meta.", suffix=".tmp", dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path(folder))


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def step(meta: dict, name: str) -> dict:
    return meta.get("pipeline", {}).get(name, {})


def set_step(meta: dict, name: str, status: str, **extra) -> None:
    pipeline = meta.setdefault("pipeline", {})
    entry = {"status": status, "updated_at": now(), **extra}
    pipeline[name] = entry


def recorder_alive(meta: dict) -> bool:
    """The recorder with this pid is still running (recording in progress)."""
    pid = (meta.get("recorder") or {}).get("pid")
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, PermissionError, ValueError):
        return False
    out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout
    return "MeetingRecorder" in out
