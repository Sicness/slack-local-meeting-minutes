"""Remove a person's voice or a whole recording from everything the app stores.

A voiceprint lives in the registry and in each meeting's voices.json, so both are cleaned.
Spoken words stay in transcripts of other people's meetings, attributed to "Unknown".
"""

import fcntl
import json
import re
import shutil
import sys
from pathlib import Path

from . import config, i18n, log, meta as M, notes, paths, registry
from .process import rerender

LABEL = re.compile(r"@v(\d+)\b")


class ForgetError(RuntimeError):
    pass


def confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise ForgetError("refusing to delete without confirmation: pass --yes")
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _ensure_free(folder: Path) -> None:
    """Refuse while the recording is being written or processed."""
    lock = folder / ".ma.lock"
    if lock.exists():
        with open(lock) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
            except BlockingIOError:
                raise ForgetError(f"{folder.name} is being processed, try again later") from None
    meta = M.read(folder)
    if meta.get("status") == "recording" and M.recorder_alive(meta):
        raise ForgetError(f"{folder.name} is still being recorded")


def _relabel(value, ids: set[int]):
    if isinstance(value, str):
        return LABEL.sub(lambda m: "@unknown" if int(m.group(1)) in ids else m.group(0), value)
    if isinstance(value, list):
        return [_relabel(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _relabel(v, ids) for k, v in value.items()}
    return value


def _scrub_voices(folder: Path, ids: set[int]) -> None:
    path = folder / "voices.json"
    if not path.exists():
        return
    v = json.loads(path.read_text(encoding="utf-8"))
    for c in v.get("clusters", []):
        if c.get("voice_id") in ids:
            c.update(voice_id=None, decision="unknown", similarity=None)
            c.pop("centroid", None)
    if v.get("word_labels"):
        v["word_labels"] = [_relabel(w, ids) if w else w for w in v["word_labels"]]
    path.write_text(json.dumps(v, ensure_ascii=False) + "\n", encoding="utf-8")


def _scrub_minutes(folder: Path, ids: set[int]) -> None:
    path = folder / "minutes.json"
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["minutes"] = _relabel(doc["minutes"], ids)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _rename_notes(folder: Path, names: list[str], cfg: config.Config) -> None:
    """Note file names can contain the person's name (it comes from the meeting title)."""
    meta = M.read(folder)
    saved = meta.get("notes") or {}
    unknown = i18n.t(cfg.language, "unknown")
    vault = Path(cfg.obsidian_vault)
    changed = False
    for key, rel in saved.items():
        if not rel:
            continue
        stem = Path(rel).name
        new = stem
        for name in names:
            new = new.replace(name, unknown)
        if new != stem:
            target = Path(rel).with_name(new)
            if (vault / rel).exists() and not (vault / target).exists():
                (vault / rel).rename(vault / target)
            saved[key] = str(target)
            changed = True
    if changed:
        M.write(folder, meta)


def _scrub_logs(terms: list[str]) -> None:
    """Older logs may mention names, titles and ids. Rewritten in place: running processes keep appending."""
    terms = [t for t in terms if len(t) >= 2]
    if not terms or not paths.logs().exists():
        return
    for path in paths.logs().glob("*.log"):
        with open(path, "r+", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            kept = [line for line in lines if not any(t in line for t in terms)]
            if len(kept) != len(lines):
                f.seek(0)
                f.writelines(kept)
                f.truncate()


def delete_voice(voice_id: int) -> dict:
    cfg = config.load()
    with registry.Registry() as reg:
        vid = reg.resolve(voice_id)
        reg.voice(vid)
        family = reg.family(vid)
        names = sorted({n for n in reg.names_of(family) if n}, key=len, reverse=True)
        rids = reg.recordings_of(vid)
        folders = [f for f in (paths.recordings() / r for r in rids) if (f / "meta.json").exists()]
        for folder in folders:
            _ensure_free(folder)
        reg.delete_voice(vid)
        skipped = []
        for folder in folders:
            _scrub_voices(folder, family)
            _scrub_minutes(folder, family)
            _rename_notes(folder, names, cfg)
            if not rerender(folder, reg):
                skipped.append(folder.name)
    _scrub_logs(names)
    log.info(f"voice {vid} deleted from {len(folders)} recordings" + (f", not re-rendered: {skipped}" if skipped else ""))
    return {"voice_id": vid, "recordings": len(folders), "skipped": skipped}


def recording_folder(ref: str) -> Path:
    """Only a real recording folder may be deleted, whatever path was passed."""
    folder = paths.recording(ref).resolve()
    if folder.parent != paths.recordings().resolve() or not (folder / "meta.json").exists():
        raise ForgetError(f"not a recording: {ref}")
    return folder


def delete_recording(ref: str, keep_notes: bool = False) -> dict:
    folder = recording_folder(ref)
    _ensure_free(folder)
    cfg = config.load()
    meta = M.read(folder)
    with registry.Registry() as reg:
        removed = reg.delete_recording(meta.get("id") or folder.name)
    result = {"recording": folder.name, "voices_removed": removed, "notes": {}}
    if not keep_notes:
        vault = Path(cfg.obsidian_vault)
        for rel in (meta.get("notes") or {}).values():
            if rel:
                result["notes"][rel] = notes.remove_note(vault / rel)
    shutil.rmtree(folder)
    stems = [Path(rel).stem for rel in (meta.get("notes") or {}).values() if rel]
    _scrub_logs([folder.name, *stems])
    log.info(f"recording {folder.name} deleted, voices removed: {len(removed)}")
    return result
