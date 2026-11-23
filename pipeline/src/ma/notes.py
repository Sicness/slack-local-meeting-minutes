"""notes step: notes in the Obsidian vault, minutes (Meetings/) and transcript (Transcripts/).

Labels (@me, @v3) are replaced here: a named voice becomes [[Name]], an unnamed one plain "Voice N".
Our text sits between <!-- ma:begin --> and <!-- ma:end -->: re-rendering leaves anything the user adds outside alone;
only our frontmatter keys are updated. The file name is chosen once and stored in meta.json → notes.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from . import i18n, log
from .config import Config
from .minutes import LABEL
from .transcript import UNSURE, clock

BEGIN, END = "<!-- ma:begin -->", "<!-- ma:end -->"
OUR_KEYS = ("date", "duration", "participants", "recording_id", "source", "tags", "meeting", "transcript")
BAD_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]\n\r\t]')


class NotesError(RuntimeError):
    pass


def names_for(transcript: dict, cfg: Config) -> dict[str, str]:
    lang = cfg.language
    names = {"@me": f"[[{cfg.me_name}]]", "@room": i18n.t(lang, "room"), "@unknown": i18n.t(lang, "unknown"),
             "@others": i18n.t(lang, "others")}
    for lab, name in transcript.get("speakers", {}).items():
        if lab.startswith("@v"):
            names[lab] = name if i18n.is_unnamed(name) else f"[[{name}]]"
    return names


def sub(text: str, names: dict[str, str]) -> str:
    return LABEL.sub(lambda m: names.get(m.group(0), m.group(0)), text)


def safe(text: str) -> str:
    """Model and speech text must not act as markup: no HTML, no block markers, no images or embeds that load by themselves."""
    return text.replace("<", "&lt;").replace("![", "!\\[")


def started(meta: dict, folder: Path | None = None) -> datetime:
    """Recording start: the recorder's started_at, else from the id (2026-01-15_1151_…), else meta.json mtime."""
    try:
        return datetime.fromisoformat(meta["started_at"])
    except (KeyError, TypeError, ValueError):
        pass
    try:
        return datetime.strptime(str(meta.get("id", ""))[:15], "%Y-%m-%d_%H%M")
    except ValueError:
        pass
    if folder is not None and (folder / "meta.json").exists():
        return datetime.fromtimestamp((folder / "meta.json").stat().st_mtime)
    return datetime.now()


def note_name(meta: dict, title: str, folder: Path | None = None, language: str = "en") -> str:
    t = re.sub(r"\s+", " ", BAD_CHARS.sub(" ", title)).strip(" .") or i18n.t(language, "note_meeting")
    return f"{started(meta, folder):%Y-%m-%d %H%M} — {t[:70].rstrip(' .')}"


def block_id(t: float) -> str:
    s = int(t)
    return f"t{s // 3600:02d}{s // 60 % 60:02d}{s % 60:02d}"


def paragraphs(transcript: dict) -> list[dict]:
    """Consecutive utterances of one speaker form a paragraph (as in transcript.md)."""
    out = []
    for u in transcript["utterances"]:
        text = u["text"] + (" (?)" if u.get("confidence", 1) < UNSURE else "")
        if out and out[-1]["speaker"] == u["speaker"]:
            out[-1]["text"] += " " + text
        else:
            out.append({"speaker": u["speaker"], "start": u["start"], "text": text})
    return out


def _seconds(at: str) -> float | None:
    try:
        h, m, s = (int(x) for x in at.split(":"))
        return h * 3600 + m * 60 + s
    except (ValueError, AttributeError):
        return None


def time_link(at: str, paras: list[dict], transcript_note: str) -> str:
    """Time of the source utterance → link to the transcript paragraph containing it."""
    t = _seconds(at)
    if t is None or not paras:
        return ""
    para = paras[0]
    for p in paras:
        if p["start"] <= t + 1:
            para = p
        else:
            break
    return f" ([[{transcript_note}#^{block_id(para['start'])}|{clock(t)}]])"


def participants(transcript: dict, names: dict[str, str]) -> list[str]:
    seen = ["@me"]
    for u in transcript["utterances"]:
        if u["speaker"].startswith("@v") and u["speaker"] not in seen:
            seen.append(u["speaker"])
    return [names.get(l, l) for l in seen]


def duration_text(seconds: float, language: str = "en") -> str:
    m = round(seconds / 60)
    if m >= 60:
        return i18n.t(language, "duration_h", h=m // 60, m=m % 60)
    return i18n.t(language, "duration_m", m=m)


def meeting_body(minutes: dict, names: dict[str, str], paras: list[dict], transcript_note: str, language: str = "en") -> str:
    m = minutes["minutes"]
    s = lambda t: sub(safe(t), names)
    tr = lambda key, **kw: i18n.t(language, key, **kw)
    L = [f"# {s(m['title'])}", "", f"**{tr('summary')}** {s(m['summary'])}", "", f"## {tr('decisions')}"]
    L += [f"- {s(d['text'])}" + (f" ({', '.join(s(w) for w in d['who'])})" if d["who"] else "")
          + time_link(d.get("at", ""), paras, transcript_note) for d in m["decisions"]] or ["- —"]
    L += ["", f"## {tr('action_items')}"]
    L += [f"- [ ] {s(a['owner']) if a['owner'] else tr('unassigned')} — {s(a['task'])} — {safe(a['due']) or tr('no_due')}"
          + time_link(a.get("at", ""), paras, transcript_note) for a in m["action_items"]] or ["- —"]
    L += ["", f"## {tr('open_questions')}"] + ([f"- {s(q)}" for q in m["open_questions"]] or ["- —"])
    L += ["", f"## {tr('topics')}"]
    L += [f"{i}. **{s(t['title']).rstrip('.')}.** " + " ".join(s(p) for p in t["points"]) for i, t in enumerate(m["topics"], 1)]
    L += ["", f"[[{transcript_note}|{tr('full_transcript')}]] · _{tr('footer', model=minutes['model'])}_"]
    return "\n".join(L)


def transcript_body(paras: list[dict], names: dict[str, str], meeting_note: str | None, language: str = "en") -> str:
    L = [f"{i18n.t(language, 'minutes_link')}: [[{meeting_note}]]", ""] if meeting_note else []
    for p in paras:
        L += [f"**{clock(p['start'])} {names.get(p['speaker'], p['speaker'])}:** {sub(safe(p['text']), names)} ^{block_id(p['start'])}", ""]
    return "\n".join(L).rstrip()


def _frontmatter(text: str) -> tuple[list[str], str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[4:end].splitlines(), text[end + 5:]
    return [], text


def write_note(path: Path, front: dict, body: str) -> bool:
    """Create the note or update our block and frontmatter keys. False: block was removed, leave it alone."""
    ours = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()]
    block = f"{BEGIN}\n{body}\n{END}"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\n" + "\n".join(ours) + "\n---\n\n" + block + "\n", encoding="utf-8")
        return True
    old = path.read_text(encoding="utf-8")
    lines, rest = _frontmatter(old)
    if BEGIN not in rest or END not in rest:
        log.warn(f"notes: a note has no {BEGIN} … {END} block, leaving it alone")
        return False
    keep = [l for l in lines if l.split(":", 1)[0].strip() not in front]
    a, b = rest.index(BEGIN), rest.index(END) + len(END)
    new = "---\n" + "\n".join(ours + keep) + "\n---\n" + rest[:a] + block + rest[b:]
    if new != old:
        path.write_text(new, encoding="utf-8")
    return True


def remove_note(path: Path) -> str:
    """Delete our note. Text the user wrote outside our block stays. → deleted, stripped, kept or missing."""
    if not path.exists():
        return "missing"
    lines, rest = _frontmatter(path.read_text(encoding="utf-8"))
    if BEGIN not in rest or END not in rest:
        return "kept"
    a, b = rest.index(BEGIN), rest.index(END) + len(END)
    own_text = (rest[:a] + rest[b:]).strip()
    own_keys = [l for l in lines if l.split(":", 1)[0].strip() not in OUR_KEYS]
    if not own_text and not own_keys:
        path.unlink()
        return "deleted"
    head = "---\n" + "\n".join(own_keys) + "\n---\n\n" if own_keys else ""
    path.write_text(head + own_text + "\n", encoding="utf-8")
    return "stripped"


def run(folder: Path, meta: dict, cfg: Config) -> dict:
    vault = Path(cfg.obsidian_vault)
    if not vault.is_dir():
        raise NotesError(f"no Obsidian vault at {vault}: create the folder or set obsidian_vault in config.toml")
    transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    mpath = folder / "minutes.json"
    minutes = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    lang = cfg.language
    names = names_for(transcript, cfg)
    paras = paragraphs(transcript)

    saved = meta.get("notes") or {}
    title = minutes["minutes"]["title"] if minutes else i18n.t(lang, "note_transcript")
    base = note_name(meta, sub(title, {k: v.strip("[]") for k, v in names.items()}), folder, lang)
    t_rel = saved.get("transcript") or f"{cfg.transcripts_folder}/{base}.md"
    m_rel = (saved.get("meeting") or f"{cfg.meetings_folder}/{base}.md") if minutes else None
    t_link, m_link = t_rel.removesuffix(".md"), m_rel.removesuffix(".md") if m_rel else None

    when = started(meta, folder)
    common = {"date": f"{when:%Y-%m-%d %H:%M}", "duration": duration_text(float(meta.get("duration_seconds") or 0), lang),
              "participants": participants(transcript, names), "recording_id": meta.get("id", folder.name),
              "source": meta.get("source", "slack")}
    written = {}
    if minutes:
        ok = write_note(vault / m_rel, {**common, "tags": ["meeting"], "transcript": f"[[{t_link}]]"},
                        meeting_body(minutes, names, paras, t_link, lang))
        written["meeting"] = m_rel if ok else None
    ok = write_note(vault / t_rel, {**common, "tags": ["meeting-transcript"], **({"meeting": f"[[{m_link}]]"} if m_link else {})},
                    transcript_body(paras, names, m_link, lang))
    written["transcript"] = t_rel if ok else None
    meta["notes"] = {"meeting": m_rel, "transcript": t_rel}
    log.info(f"notes: {sum(1 for v in (m_rel, t_rel) if v)} written")
    return {"vault": str(vault), **meta["notes"]}
