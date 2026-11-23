"""`ma cleanup`: delete audio of recordings older than audio_retention_days. JSON, transcripts and notes stay.

Only touches fully processed recordings (archive step done): unprocessed audio is never deleted.
"""

from datetime import datetime, timedelta
from pathlib import Path

from . import log, meta as M, notes, paths

AUDIO = ("mic.16k.wav", "remote.16k.wav", "audio.m4a", "mic.caf", "remote.caf")


def candidates(days: float, now: datetime | None = None) -> list[tuple[Path, list[Path]]]:
    root = paths.recordings()
    if not root.exists():
        return []
    now = now or datetime.now().astimezone()
    out = []
    for folder in sorted(p for p in root.iterdir() if (p / "meta.json").exists()):
        meta = M.read(folder)
        if M.step(meta, "archive").get("status") != "done":
            continue
        when = notes.started(meta, folder)
        if when.tzinfo is None:
            when = when.astimezone()
        if now - when < timedelta(days=days):
            continue
        files = [folder / f for f in AUDIO if (folder / f).exists()]
        if files:
            out.append((folder, files))
    return out


def overdue(days: float, now: datetime | None = None) -> list[str]:
    """Recordings past retention that keep audio because processing never finished."""
    root = paths.recordings()
    if not root.exists():
        return []
    now = now or datetime.now().astimezone()
    out = []
    for folder in sorted(p for p in root.iterdir() if (p / "meta.json").exists()):
        meta = M.read(folder)
        if M.step(meta, "archive").get("status") == "done" or meta.get("status") == "recording":
            continue
        when = notes.started(meta, folder)
        when = when.astimezone() if when.tzinfo is None else when
        if now - when >= timedelta(days=days) and any((folder / f).exists() for f in AUDIO):
            out.append(folder.name)
    return out


def run(days: float, dry_run: bool = False) -> int:
    total = 0
    for folder, files in candidates(days):
        size = sum(f.stat().st_size for f in files)
        total += size
        if dry_run:
            log.info(f"cleanup: would delete {folder.name}: {', '.join(f.name for f in files)} ({size / 1e6:.0f} MB)")
            continue
        for f in files:
            f.unlink()
        meta = M.read(folder)
        meta["audio_deleted_at"] = M.now()
        M.write(folder, meta)
        log.info(f"cleanup: {folder.name}: audio deleted ({size / 1e6:.0f} MB)")
    log.info(f"cleanup: {'would free' if dry_run else 'freed'} {total / 1e6:.0f} MB (audio older than {days:g} days)")
    return total
