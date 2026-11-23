"""transcript step: words of both tracks → utterances → transcript.json and a temporary transcript.md.

Labels: mic → @me, or @room in room-mode intervals; remote → voices from voices.json (@v7 is registry voice 7,
@unknown is unidentified), @others without voices.json. Merged voices resolve to the final one, names come from
the registry, so after `ma voices name/merge` rebuilding the transcript is enough (rerender).
In room mode the mic hears Slack from the speakers: such words (same word in remote within ECHO) are removed.
Glossary replacements apply here, to utterance text: after editing the glossary, `ma process --from transcript` is enough.
"""

import bisect
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from . import config, i18n, registry
from .glossary import Glossary
from .timeline import TimeMap, mode_at, mode_intervals

LABEL_KEYS = {"@me": "me", "@others": "others", "@room": "room", "@unknown": "unknown"}

def labels_for(language: str) -> dict[str, str]:
    return {k: i18n.t(language, v) for k, v in LABEL_KEYS.items()}


PAUSE = 1.5   # s; a longer pause starts a new utterance
UNSURE = 0.6  # mean word probability below this marks the utterance "(?)"
ECHO = 0.8    # s; an @room word this close to the same remote word is speaker echo


def clock(t: float) -> str:
    s = int(t)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def remote_labels(voices: dict | None, n_words: int, reg: registry.Registry | None) -> tuple[list[str] | None, dict[str, str]]:
    """Remote word labels from voices.json, with merges resolved, plus display names."""
    if voices is None:
        return None, {}
    if voices.get("words") != n_words:
        raise RuntimeError(f"voices.json has {voices.get('words')} words, asr.remote.json has {n_words}: "
                           "run `ma process <id> --from voices`")
    labels = list(voices.get("word_labels") or [])
    names: dict[str, str] = {}
    for i, lab in enumerate(labels):
        if lab and lab.startswith("@v") and reg is not None:
            vid = reg.resolve(int(lab[2:]))
            labels[i] = f"@v{vid}"
            names.setdefault(labels[i], reg.display(vid))
        elif not lab:
            labels[i] = "@unknown"
    return labels, names


def _norm(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower().replace("ё", "е"))


def _same(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    return min(len(a), len(b)) >= 3 and SequenceMatcher(None, a, b).ratio() >= 0.75


def drop_room_echo(mic: list[dict], remote: list[dict]) -> tuple[list[dict], int]:
    """In room mode the mic hears Slack from the speakers: an @room word also present in remote at the same
    time (±ECHO) is the remote speaker's echo, not speech in the room. Times are already seconds from t0."""
    starts = [r["start"] for r in remote]
    kept, dropped = [], 0
    for w in mic:
        if w["speaker"] == "@room":
            n = _norm(w["w"])
            i = bisect.bisect_left(starts, w["start"] - ECHO)
            echo = False
            while i < len(remote) and remote[i]["start"] <= w["start"] + ECHO:
                if _same(n, _norm(remote[i]["w"])):
                    echo = True
                    break
                i += 1
            if echo:
                dropped += 1
                continue
        kept.append(w)
    return kept, dropped


def build(meta: dict, asr: dict[str, dict], glossary: Glossary, voices: dict | None = None,
          reg: registry.Registry | None = None, language: str = "en") -> dict:
    end = max([float(meta.get("duration_seconds") or 0)] + [float(a.get("audio_seconds") or 0) for a in asr.values()])
    intervals = mode_intervals(meta, end)
    n_remote = sum(len(seg.get("words", [])) for seg in asr.get("remote", {}).get("segments", []))
    remote, names = remote_labels(voices, n_remote, reg)
    words: dict[str, list[dict]] = {}
    for track, res in asr.items():
        tmap = TimeMap(meta, track)
        out = words.setdefault(track, [])
        k = -1
        for seg in res.get("segments", []):
            for w in seg.get("words", []):
                k += 1
                start, stop = round(tmap(w["start"]), 3), round(tmap(w["end"]), 3)
                if track == "remote":
                    speaker = remote[k] if remote is not None else "@others"
                else:
                    speaker = "@room" if mode_at(intervals, start) == "room" else "@me"
                out.append({"w": w["w"], "start": start, "end": stop, "p": w["p"], "speaker": speaker})
    echo = 0
    if "mic" in words and "remote" in words:
        words["mic"], echo = drop_room_echo(words["mic"], sorted(words["remote"], key=lambda w: w["start"]))

    utterances = []
    for track, ws in words.items():
        current = None
        for w in ws:
            word = {k: w[k] for k in ("w", "start", "end", "p")}
            if current and current["speaker"] == w["speaker"] and w["start"] - current["end"] <= PAUSE:
                current["words"].append(word)
                current["end"] = w["end"]
            else:
                current = {"start": w["start"], "end": w["end"], "speaker": w["speaker"], "track": track, "words": [word]}
                utterances.append(current)
    for u in utterances:
        u["text"] = glossary.replace(" ".join("".join(w["w"] for w in u["words"]).split()))
        u["confidence"] = round(sum(w["p"] for w in u["words"]) / len(u["words"]), 2)
    utterances.sort(key=lambda u: (u["start"], u["track"]))
    ordered = [{k: u[k] for k in ("start", "end", "speaker", "track", "text", "confidence", "words")} for u in utterances]
    used = {u["speaker"] for u in ordered}
    speakers = {k: v for k, v in {**labels_for(language), **names}.items() if k in used}
    return {"version": 2, "recording_id": meta.get("id"), "speakers": speakers, "utterances": ordered,
            "room_echo_dropped": echo}


def markdown(transcript: dict, meta: dict, language: str = "en") -> str:
    names = transcript.get("speakers") or labels_for(language)
    lines = [f"# {i18n.t(language, 'transcript_title', id=meta.get('id', ''))}", "",
             f"_{i18n.t(language, 'transcript_hint')}_", ""]
    para = None
    for u in transcript["utterances"]:
        text = u["text"] + (" (?)" if u.get("confidence", 1) < UNSURE else "")
        if para and para["speaker"] == u["speaker"]:
            para["text"] += " " + text
            continue
        if para:
            lines += [f"**{clock(para['start'])} {names.get(para['speaker'], para['speaker'])}:** {para['text']}", ""]
        para = {"speaker": u["speaker"], "start": u["start"], "text": text}
    if para:
        lines += [f"**{clock(para['start'])} {names.get(para['speaker'], para['speaker'])}:** {para['text']}", ""]
    return "\n".join(lines)


def run(folder: Path, meta: dict, glossary: Glossary, reg: registry.Registry | None = None) -> dict:
    asr = {t: json.loads((folder / f"asr.{t}.json").read_text(encoding="utf-8")) for t in ("mic", "remote")}
    vpath = folder / "voices.json"
    voices = json.loads(vpath.read_text(encoding="utf-8")) if vpath.exists() else None
    language = config.load().language
    own = reg is None and voices is not None
    if own:
        reg = registry.Registry()
    try:
        tr = build(meta, asr, glossary, voices, reg, language)
    finally:
        if own:
            reg.close()
    (folder / "transcript.json").write_text(json.dumps(tr, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (folder / "transcript.md").write_text(markdown(tr, meta, language), encoding="utf-8")
    counts: dict[str, int] = {}
    for u in tr["utterances"]:
        counts[u["speaker"]] = counts.get(u["speaker"], 0) + 1
    return {"utterances": len(tr["utterances"]), "by_speaker": counts, "room_echo_dropped": tr["room_echo_dropped"]}
