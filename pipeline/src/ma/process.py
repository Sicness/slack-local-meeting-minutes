"""`ma process`: steps in order, statuses in meta.json. The next run retries a failed step
and skips finished ones."""

import fcntl
import time
from collections.abc import Callable
from pathlib import Path

from . import asr, config, diarize, glossary, log, meta as M, minutes, notes, prepare, transcript


class ProcessError(RuntimeError):
    pass


def _steps(folder: Path, cfg, gloss) -> dict[str, Callable[[dict], dict]]:
    return {
        "prepare": lambda m: prepare.run(folder, m, cfg),
        "asr": lambda m: asr.run(folder, m, cfg, gloss),
        "voices": lambda m: diarize.run(folder, m, cfg),
        "transcript": lambda m: transcript.run(folder, m, gloss),
        "minutes": lambda m: minutes.run(folder, m, cfg, gloss),
        "notes": lambda m: notes.run(folder, m, cfg),
        "archive": lambda m: prepare.archive(folder, m, cfg),
    }


def check_ready(meta: dict) -> None:
    status = meta.get("status")
    if status == "recorded":
        return
    if status == "recording":
        if M.recorder_alive(meta):
            raise ProcessError("recording is still in progress")
        meta["interrupted"] = True   # recorder crashed: take the length from the files
        log.warn("recorder ended abnormally, processing what was recorded")
        return
    raise ProcessError(f"unknown recording status: {status!r}")


def process(folder: Path, force: bool = False, from_step: str | None = None,
            steps: dict[str, Callable[[dict], dict]] | None = None) -> dict:
    if not M.path(folder).exists():
        raise ProcessError(f"missing {M.path(folder)}")
    if from_step and from_step not in M.STEPS:
        raise ProcessError(f"no step {from_step!r}, available: {', '.join(M.STEPS)}")
    with open(folder / ".ma.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProcessError("another ma is already processing this recording") from None
        meta = M.read(folder)
        check_ready(meta)
        if steps is None:
            steps = _steps(folder, config.load(), glossary.load())
        first = M.STEPS.index(from_step) if from_step else 0
        for i, name in enumerate(M.STEPS):
            done = M.step(meta, name).get("status") == "done"
            if i < first:
                if not done:
                    raise ProcessError(f"step {name} is not done yet, start from it")
                continue
            if done and not force and from_step is None:
                log.info(f"{folder.name}: {name}: already done")
                continue
            M.set_step(meta, name, "running")
            M.write(folder, meta)
            t = time.time()
            try:
                result = steps[name](meta)
            except Exception as e:
                M.set_step(meta, name, "failed", error=f"{type(e).__name__}: {e}", seconds=round(time.time() - t, 2))
                M.write(folder, meta)
                log.error(f"{folder.name}: {name} failed: {e}")
                raise
            M.set_step(meta, name, "done", seconds=round(time.time() - t, 2), result=result)
            M.write(folder, meta)
            log.info(f"{folder.name}: {name}: done in {time.time() - t:.1f} s")
        return meta


def rerender(folder: Path, reg=None) -> bool:
    """Rebuild the transcript and Obsidian notes (voice names and merges) without models or other steps.
    False: the recording is not ready or another ma is processing it."""
    meta = M.read(folder)
    if M.step(meta, "transcript").get("status") != "done":
        return False
    with open(folder / ".ma.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        meta = M.read(folder)
        transcript.run(folder, meta, glossary.load(), reg)
        if M.step(meta, "notes").get("status") == "done":
            notes.run(folder, meta, config.load())
            M.write(folder, meta)
        return True
