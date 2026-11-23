"""voices step: remote track → meeting voices → registry voices. The result is voices.json.

1. ma-diarize (FluidAudio, CoreML on CPU and Neural Engine) splits the track into voice segments S1, S2…
2. Whisper words → chunks: consecutive words of one voice segment with pauses ≤ 1.5 s
3. An embedding per chunk and relabeling to the nearest meeting voice. The diarizer often gives short
   replies ("uh-huh", "yes") to a neighbor; the chunk's own embedding identifies them
4. Meeting clusters → registry (registry.py)

Times are remote file time, as for words in asr.remote.json; the transcript step converts to seconds from t0.
"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import log, registry
from .config import Config

PAUSE = 1.5        # s; same utterance boundary as in transcript
NEAREST = 1.0      # a word overlapping no segment takes the nearest one within this distance
MIN_SPAN = 0.6     # shorter chunks are extended around their middle for the embedding
LONG = 1.5         # chunks at least this long form the meeting voice centroid
KEEP_MARGIN = 0.05 # embedding barely separates two voices: keep the diarizer's label
CLIPS = 3


@dataclass
class Chunk:
    start: float
    end: float
    first: int            # index of the first word (in the track's flat word list)
    last: int             # index of the last word, inclusive
    seg_label: str | None # label from diarizer segments
    label: str | None = None
    margin: float | None = None
    embedding: np.ndarray | None = field(default=None, repr=False)

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.0)


def words_of(asr: dict) -> list[dict]:
    return [w for seg in asr.get("segments", []) for w in seg.get("words", [])]


def segment_of(start: float, end: float, segments: list[dict]) -> int | None:
    """Index of the diarizer segment with the largest overlap; with none, the nearest within NEAREST."""
    best, best_ov = None, 0.0
    for i, s in enumerate(segments):
        ov = min(end, s["end"]) - max(start, s["start"])
        if ov > best_ov:
            best, best_ov = i, ov
    if best is not None:
        return best
    near = min(((min(abs(s["start"] - end), abs(s["end"] - start)), i) for i, s in enumerate(segments)), default=None)
    return near[1] if near and near[0] <= NEAREST else None


def make_chunks(words: list[dict], segments: list[dict]) -> list[Chunk]:
    """A chunk is words of one diarizer segment with pauses ≤ PAUSE. Cut at segment boundaries even with the
    same label: the diarizer finds a short reply as its own segment but often gives it a neighbor's label."""
    chunks: list[Chunk] = []
    prev_seg = None
    for i, w in enumerate(words):
        seg = segment_of(w["start"], w["end"], segments)
        lab = segments[seg]["speaker"] if seg is not None else None
        cur = chunks[-1] if chunks else None
        if cur and seg == prev_seg and w["start"] - cur.end <= PAUSE:
            cur.end, cur.last = max(cur.end, w["end"]), i
        else:
            chunks.append(Chunk(w["start"], w["end"], i, i, lab))
        prev_seg = seg
    return chunks


def span(c: Chunk, audio_seconds: float) -> list[float]:
    a, b = c.start, c.end
    if b - a < MIN_SPAN:
        mid = (a + b) / 2
        a, b = mid - MIN_SPAN / 2, mid + MIN_SPAN / 2
    a, b = max(0.0, a), min(audio_seconds, b)
    if b - a < MIN_SPAN:   # at the file edge
        a, b = max(0.0, min(a, audio_seconds - MIN_SPAN)), min(audio_seconds, max(b, MIN_SPAN))
    return [round(a, 3), round(b, 3)]


def centroids(chunks: list[Chunk], attr: str) -> dict[str, np.ndarray]:
    """Meeting voice centroid: long chunks weighted by length; all chunks if none are long."""
    groups: dict[str, list[Chunk]] = {}
    for c in chunks:
        lab = getattr(c, attr)
        if lab is not None and c.embedding is not None:
            groups.setdefault(lab, []).append(c)
    out = {}
    for lab, cs in groups.items():
        use = [c for c in cs if c.duration >= LONG] or cs
        out[lab] = registry.norm(sum(max(c.duration, 0.1) * registry.norm(c.embedding) for c in use))
    return out


def relabel(chunks: list[Chunk]) -> None:
    """Each chunk → nearest meeting voice by embedding; two passes, recomputing centroids."""
    for c in chunks:
        c.label = c.seg_label
    if not any(c.label for c in chunks):
        for c in chunks:
            c.label = "S0"   # diarizer found no speech but there are words: one voice
    for _ in range(2):
        cents = centroids(chunks, "label")
        if not cents:
            return
        new = []
        for c in chunks:
            if c.embedding is None:
                new.append((c.label, None))
                continue
            sims = sorted(((float(registry.norm(c.embedding) @ v), lab) for lab, v in cents.items()), reverse=True)
            margin = sims[0][0] - sims[1][0] if len(sims) > 1 else 1.0
            lab = sims[0][1]
            if margin < KEEP_MARGIN and c.seg_label in cents:
                lab = c.seg_label
            new.append((lab, round(margin, 4)))
        for c, (lab, margin) in zip(chunks, new):
            c.label, c.margin = lab, margin


def merge_adjacent(chunks: list[Chunk]) -> list[Chunk]:
    out: list[Chunk] = []
    for c in chunks:
        if out and out[-1].label == c.label and c.start - out[-1].end <= PAUSE:
            p = out[-1]
            p.end, p.last = max(p.end, c.end), c.last
            p.margin = min(x for x in (p.margin, c.margin) if x is not None) if (p.margin is not None or c.margin is not None) else None
        else:
            out.append(Chunk(c.start, c.end, c.first, c.last, c.seg_label, c.label, c.margin))
    return out


def meeting_clusters(chunks: list[Chunk]) -> list[registry.Cluster]:
    cents = centroids(chunks, "label")
    out = []
    for lab, v in cents.items():
        cs = [c for c in chunks if c.label == lab]
        clips = sorted(cs, key=lambda c: -c.duration)[:CLIPS]
        out.append(registry.Cluster(lab, v, round(sum(c.duration for c in cs), 2),
                                    [[round(c.start, 2), round(c.end, 2)] for c in sorted(clips, key=lambda c: c.start)]))
    return out


def _call(cfg: Config, *args: str) -> None:
    exe = Path(cfg.diarizer)
    if not exe.exists():
        raise RuntimeError(f"missing {exe}: run diarizer/scripts/build.sh (or set [voices] diarizer in config.toml)")
    p = subprocess.run([str(exe), *args], capture_output=True, text=True)
    if p.returncode != 0:
        tail = [l for l in p.stderr.splitlines() if l.strip()][-3:]
        raise RuntimeError(f"ma-diarize {args[0]}: exit code {p.returncode}: {' | '.join(tail)}")


def label_of(match: registry.Match | None) -> str:
    return f"@v{match.voice_id}" if match and match.voice_id is not None else "@unknown"


def set_cluster_voice(folder: Path, cluster: str, voice_id: int | None) -> None:
    """After "this is a different voice": the cluster's voice in voices.json and word labels (chunks are contiguous)."""
    path = folder / "voices.json"
    v = json.loads(path.read_text(encoding="utf-8"))
    for c in v["clusters"]:
        if c["cluster"] == cluster:
            c["voice_id"], c["decision"], c["similarity"] = voice_id, "manual", None
    by_cluster = {c["cluster"]: c["voice_id"] for c in v["clusters"]}
    labels, i = [], 0
    for ch in v["chunks"]:
        vid = by_cluster.get(ch["cluster"])
        labels += [f"@v{vid}" if vid is not None else "@unknown"] * ch["words"]
        i += ch["words"]
    if i != v["words"]:
        raise RuntimeError(f"voices.json: chunks cover {i} words, but there are {v['words']}; run ma process --from voices")
    v["word_labels"] = labels
    path.write_text(json.dumps(v, ensure_ascii=False) + "\n", encoding="utf-8")


def run(folder: Path, meta: dict, cfg: Config, reg: registry.Registry | None = None) -> dict:
    asr = json.loads((folder / "asr.remote.json").read_text(encoding="utf-8"))
    words = words_of(asr)
    out_path = folder / "voices.json"
    rid = meta.get("id") or folder.name
    own = reg is None
    reg = reg or registry.Registry()
    try:
        if not words:
            # nobody but the recording owner spoke: drop this recording's earlier samples, if any
            reg.forget_recording(rid)
            out_path.write_text(json.dumps({"version": 1, "words": 0, "clusters": [], "chunks": [], "segments": []},
                                           ensure_ascii=False) + "\n", encoding="utf-8")
            log.info("voices: no words on the remote track")
            return {"voices": 0, "words": 0}

        wav = folder / "remote.16k.wav"
        raw, emb_in, emb_out = folder / ".diarize.json", folder / ".spans.json", folder / ".spans.emb.json"
        try:
            _call(cfg, "diarize", str(wav), str(raw))
            d = json.loads(raw.read_text())
            segments = d["segments"]
            chunks = make_chunks(words, segments)
            emb_in.write_text(json.dumps([span(c, d["audio_seconds"]) for c in chunks]))
            _call(cfg, "embed", str(wav), str(emb_in), str(emb_out))
            e = json.loads(emb_out.read_text())
        finally:
            for p in (raw, emb_in, emb_out):
                p.unlink(missing_ok=True)
        if len(e["embeddings"]) != len(chunks):
            raise RuntimeError(f"ma-diarize embed returned {len(e['embeddings'])} embeddings for {len(chunks)} chunks")
        for c, v in zip(chunks, e["embeddings"]):
            c.embedding = np.asarray(v, dtype=np.float64)

        relabel(chunks)
        changed = sum(c.label != c.seg_label for c in chunks)
        clusters = meeting_clusters(chunks)
        matches = reg.match_recording(rid, d["model"], clusters, cfg.match_threshold, cfg.min_speech_seconds)
        final = merge_adjacent(chunks)

        word_labels = [None] * len(words)
        for c in final:
            for i in range(c.first, c.last + 1):
                word_labels[i] = label_of(matches.get(c.label))
        res = {
            "version": 1,
            "model": d["model"],
            "words": len(words),
            "word_labels": word_labels,
            "clusters": [{"cluster": c.cluster, "speech_seconds": c.speech_seconds,
                          "voice_id": matches[c.cluster].voice_id, "similarity": matches[c.cluster].similarity,
                          "decision": matches[c.cluster].decision, "clips": c.clips,
                          "centroid": [round(float(x), 5) for x in c.embedding]} for c in clusters],
            "chunks": [{"start": round(c.start, 3), "end": round(c.end, 3), "cluster": c.label, "margin": c.margin,
                        "words": c.last - c.first + 1} for c in final],
            "segments": segments,
            "relabeled_chunks": changed,
            "seconds": {"diarize": round(d["seconds"], 2), "embed": round(e["seconds"], 2)},
        }
        out_path.write_text(json.dumps(res, ensure_ascii=False) + "\n", encoding="utf-8")
    finally:
        if own:
            reg.close()

    decisions: dict[str, int] = {}
    for m in matches.values():
        decisions[m.decision] = decisions.get(m.decision, 0) + 1
    log.info(f"voices: {len(clusters)} meeting voices ({', '.join(f'{k} {v}' for k, v in sorted(decisions.items()))}), "
             f"{len(chunks)} chunks, {changed} relabeled by embedding, "
             f"diarization {d['seconds']:.1f} s, embeddings {e['seconds']:.1f} s")
    return {"voices": len({m.voice_id for m in matches.values()}), "clusters": len(clusters), "decisions": decisions,
            "chunks": len(chunks), "relabeled": changed}
