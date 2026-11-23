"""Voice registry across meetings: ~/MeetingAssistant/voices.sqlite.

A voice is "Voice N" with a permanent number (N = id); the user assigns the name. Each appearance in a meeting is
a sample (the meeting cluster's embedding); the centroid is the mean of samples, weighted by speech (at most 120 s per meeting).
Embeddings of different models are not comparable, so the model is stored in the registry and never mixed.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import config, i18n, paths

SCHEMA_VERSION = 1
MAX_WEIGHT = 120.0   # s of one meeting's speech counted in a voice centroid
SPLIT_SIM = 0.75     # two clusters of a meeting closer than this are one person split by the diarizer
NEAR_CLUSTER = 0.5   # a short cluster joins a large cluster of the meeting at or above this

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    name TEXT,
    centroid BLOB,
    created_at TEXT NOT NULL,
    merged_into INTEGER REFERENCES voices(id)
);
CREATE TABLE IF NOT EXISTS voice_samples (
    voice_id INTEGER NOT NULL REFERENCES voices(id),
    recording_id TEXT NOT NULL,
    cluster TEXT NOT NULL,
    embedding BLOB NOT NULL,
    speech_seconds REAL NOT NULL,
    clips TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (recording_id, cluster)
);
CREATE TABLE IF NOT EXISTS recording_voices (
    recording_id TEXT NOT NULL,
    cluster TEXT NOT NULL,
    voice_id INTEGER REFERENCES voices(id),
    similarity REAL,
    decision TEXT NOT NULL,
    speech_seconds REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (recording_id, cluster)
);
CREATE INDEX IF NOT EXISTS voice_samples_voice ON voice_samples(voice_id);
CREATE INDEX IF NOT EXISTS recording_voices_voice ON recording_voices(voice_id);
"""


class RegistryError(RuntimeError):
    pass


@dataclass
class Cluster:
    """A meeting cluster: embedding (any length, normalized here), speech, playable clips."""
    cluster: str
    embedding: np.ndarray
    speech_seconds: float
    clips: list = field(default_factory=list)


@dataclass
class Match:
    voice_id: int | None
    similarity: float | None
    decision: str   # matched | new | split | short | short-near | unknown


def norm(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def _vec(b: bytes | None) -> np.ndarray | None:
    return None if b is None else np.frombuffer(b, dtype=np.float32).astype(np.float64)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def db_path() -> Path:
    return paths.home() / "voices.sqlite"


class Registry:
    def __init__(self, path: Path | None = None, language: str | None = None):
        self.language = language or config.load().language
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA secure_delete = ON")   # voiceprints are biometric data
        self.db.executescript(SCHEMA)
        version = self._meta("schema_version")
        if version is None:
            self._set_meta("schema_version", str(SCHEMA_VERSION))
            self.db.commit()
        elif int(version) != SCHEMA_VERSION:
            raise RegistryError(f"{self.path}: schema {version}, but the code knows {SCHEMA_VERSION}")

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- internals ---

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, value))

    def check_model(self, model: str) -> None:
        known = self._meta("embedding_model")
        if known is None:
            self._set_meta("embedding_model", model)
        elif known != model:
            raise RegistryError(
                f"voice registry was built with embeddings from '{known}', the current model is '{model}': "
                f"they are not comparable. Restore the previous model, or start a new registry "
                f"(rename {self.path.name}) and reprocess recordings with --from voices")

    def resolve(self, voice_id: int) -> int:
        """The voice after all merges."""
        seen = set()
        while True:
            row = self.db.execute("SELECT merged_into FROM voices WHERE id = ?", (voice_id,)).fetchone()
            if row is None:
                raise RegistryError(f"no voice {voice_id}")
            if row["merged_into"] is None or voice_id in seen:
                return voice_id
            seen.add(voice_id)
            voice_id = row["merged_into"]

    def voice(self, voice_id: int) -> dict:
        row = self.db.execute("SELECT * FROM voices WHERE id = ?", (voice_id,)).fetchone()
        if row is None:
            raise RegistryError(f"no voice {voice_id}")
        return {**dict(row), "label": i18n.voice_label(self.language, row["id"])}

    def display(self, voice_id: int) -> str:
        v = self.voice(self.resolve(voice_id))
        return v["name"] or v["label"]

    def _recompute(self, voice_id: int) -> None:
        rows = self.db.execute("SELECT embedding, speech_seconds FROM voice_samples WHERE voice_id = ?", (voice_id,)).fetchall()
        if not rows:
            return   # with no samples the last centroid stays, so reprocessing finds the voice again
        acc = sum(min(r["speech_seconds"], MAX_WEIGHT) * norm(_vec(r["embedding"])) for r in rows)
        self.db.execute("UPDATE voices SET centroid = ? WHERE id = ?", (_blob(norm(acc)), voice_id))

    def _live(self) -> dict[int, np.ndarray]:
        rows = self.db.execute("SELECT id, centroid FROM voices WHERE merged_into IS NULL AND centroid IS NOT NULL").fetchall()
        return {r["id"]: norm(_vec(r["centroid"])) for r in rows}

    def _new_voice(self, centroid: np.ndarray) -> int:
        cur = self.db.execute("INSERT INTO voices(label, centroid, created_at) VALUES('', ?, ?)", (_blob(norm(centroid)), _now()))
        vid = cur.lastrowid
        self.db.execute("UPDATE voices SET label = ? WHERE id = ?", (f"Voice {vid}", vid))
        return vid

    # --- meeting ---

    def _forget(self, recording_id: str) -> None:
        touched = {r["voice_id"] for r in self.db.execute(
            "SELECT voice_id FROM voice_samples WHERE recording_id = ?", (recording_id,))}
        self.db.execute("DELETE FROM voice_samples WHERE recording_id = ?", (recording_id,))
        self.db.execute("DELETE FROM recording_voices WHERE recording_id = ?", (recording_id,))
        for vid in touched:
            self._recompute(vid)

    def forget_recording(self, recording_id: str) -> None:
        """Remove a recording's samples and voices (for example, if it has no remote speakers)."""
        with self.db:
            self._forget(recording_id)

    def match_recording(self, recording_id: str, model: str, clusters: list[Cluster],
                        threshold: float, min_speech: float) -> dict[str, Match]:
        """Meeting clusters → registry voices. Calling again for a recording replaces its earlier samples."""
        with self.db:
            self.check_model(model)
            self._forget(recording_id)

            emb = {c.cluster: norm(c.embedding) for c in clusters}
            big = sorted((c for c in clusters if c.speech_seconds >= min_speech), key=lambda c: -c.speech_seconds)
            small = [c for c in clusters if c.speech_seconds < min_speech]
            live = self._live()
            result: dict[str, Match] = {}
            owner: dict[int, str] = {}   # voice → first meeting cluster that took it

            # one to one, greedy by descending similarity
            pairs = sorted(((float(emb[c.cluster] @ v), c.cluster, vid) for c in big for vid, v in live.items()),
                           reverse=True)
            for sim, cl, vid in pairs:
                if sim < threshold:
                    break
                if cl in result:
                    continue
                if vid in owner:
                    if float(emb[cl] @ emb[owner[vid]]) >= SPLIT_SIM:
                        result[cl] = Match(vid, round(sim, 4), "split")
                    continue
                owner[vid] = cl
                result[cl] = Match(vid, round(sim, 4), "matched")

            # unmatched become new voices; a split new person becomes one new voice
            new: dict[int, str] = {}
            for c in big:
                if c.cluster in result:
                    continue
                twin = next((vid for vid, cl in new.items() if float(emb[c.cluster] @ emb[cl]) >= SPLIT_SIM), None)
                if twin is not None:
                    result[c.cluster] = Match(twin, round(float(emb[c.cluster] @ emb[new[twin]]), 4), "split")
                else:
                    vid = self._new_voice(emb[c.cluster])
                    new[vid] = c.cluster
                    result[c.cluster] = Match(vid, None, "new")

            for c in big:
                m = result[c.cluster]
                self.db.execute("INSERT INTO voice_samples(voice_id, recording_id, cluster, embedding, speech_seconds, clips) "
                                "VALUES(?, ?, ?, ?, ?, ?)",
                                (m.voice_id, recording_id, c.cluster, _blob(emb[c.cluster]), c.speech_seconds,
                                 json.dumps(c.clips)))
            for vid in {result[c.cluster].voice_id for c in big}:
                self._recompute(vid)

            # short clusters are not stored in the registry
            live = self._live()
            for c in small:
                sims = sorted(((float(emb[c.cluster] @ v), vid) for vid, v in live.items()), reverse=True)
                if sims and sims[0][0] >= threshold:
                    result[c.cluster] = Match(sims[0][1], round(sims[0][0], 4), "short")
                    continue
                near = sorted(((float(emb[c.cluster] @ emb[b.cluster]), b.cluster) for b in big), reverse=True)
                if near and near[0][0] >= NEAR_CLUSTER:
                    result[c.cluster] = Match(result[near[0][1]].voice_id, round(near[0][0], 4), "short-near")
                else:
                    result[c.cluster] = Match(None, round(sims[0][0], 4) if sims else None, "unknown")

            speech = {c.cluster: c.speech_seconds for c in clusters}
            for cl, m in result.items():
                self.db.execute("INSERT INTO recording_voices(recording_id, cluster, voice_id, similarity, decision, speech_seconds) "
                                "VALUES(?, ?, ?, ?, ?, ?)", (recording_id, cl, m.voice_id, m.similarity, m.decision, speech[cl]))
        return result

    # --- manual operations ---

    def set_name(self, voice_id: int, name: str | None) -> int:
        vid = self.resolve(voice_id)
        with self.db:
            self.db.execute("UPDATE voices SET name = ? WHERE id = ?", ((name or "").strip() or None, vid))
        return vid

    def merge(self, into: int, other: int) -> int:
        """Voice other is the same person as into. Samples and meetings move to into."""
        a, b = self.resolve(into), self.resolve(other)
        if a == b:
            raise RegistryError(f"voices {into} and {other} are already one voice ({a})")
        with self.db:
            va, vb = self.voice(a), self.voice(b)
            if not va["name"] and vb["name"]:
                self.db.execute("UPDATE voices SET name = ? WHERE id = ?", (vb["name"], a))
            self.db.execute("UPDATE voice_samples SET voice_id = ? WHERE voice_id = ?", (a, b))
            self.db.execute("UPDATE recording_voices SET voice_id = ? WHERE voice_id = ?", (a, b))
            self.db.execute("UPDATE voices SET merged_into = ? WHERE id = ?", (a, b))
            if not self.db.execute("SELECT 1 FROM voice_samples WHERE voice_id = ?", (a,)).fetchone() and vb["centroid"]:
                self.db.execute("UPDATE voices SET centroid = ? WHERE id = ?", (vb["centroid"], a))
            self._recompute(a)
        return a

    def reassign(self, recording_id: str, cluster: str, voice_id: int | None,
                 embedding: np.ndarray | None = None) -> int:
        """User says it's a different voice: meeting cluster → voice_id or a new voice (None). Returns the final voice.
        A voice left with no meetings and no name merges into the final one: it was the same person."""
        with self.db:
            row = self.db.execute("SELECT voice_id FROM recording_voices WHERE recording_id = ? AND cluster = ?",
                                  (recording_id, cluster)).fetchone()
            if row is None:
                raise RegistryError(f"meeting {recording_id} has no voice {cluster}")
            old = row["voice_id"]
            sample = self.db.execute("SELECT embedding FROM voice_samples WHERE recording_id = ? AND cluster = ?",
                                     (recording_id, cluster)).fetchone()
            if voice_id is None:
                emb = _vec(sample["embedding"]) if sample else embedding
                if emb is None:
                    raise RegistryError("no embedding to create a new voice from")
                vid = self._new_voice(emb)
            else:
                vid = self.resolve(voice_id)
            if sample:
                self.db.execute("UPDATE voice_samples SET voice_id = ? WHERE recording_id = ? AND cluster = ?",
                                (vid, recording_id, cluster))
            self.db.execute("UPDATE recording_voices SET voice_id = ?, decision = 'manual', similarity = NULL "
                            "WHERE recording_id = ? AND cluster = ?", (vid, recording_id, cluster))
            self._recompute(vid)
            if old is not None and old != vid:
                self._recompute(old)
                left = self.db.execute("SELECT 1 FROM recording_voices WHERE voice_id = ? LIMIT 1", (old,)).fetchone()
                if not left and not self.voice(old)["name"]:
                    self.db.execute("UPDATE voices SET merged_into = ? WHERE id = ?", (vid, old))
        return vid

    def family(self, voice_id: int) -> set[int]:
        """A voice and every voice merged into it."""
        root = self.resolve(voice_id)
        return {r["id"] for r in self.db.execute("SELECT id FROM voices").fetchall() if self.resolve(r["id"]) == root}

    def names_of(self, ids: set[int]) -> list[str | None]:
        marks = ",".join("?" * len(ids))
        return [r["name"] for r in self.db.execute(f"SELECT name FROM voices WHERE id IN ({marks})", sorted(ids)).fetchall()]

    def delete_voice(self, voice_id: int) -> set[int]:
        """Erase a voiceprint, its samples and its name. Ids are never reused."""
        ids = self.family(voice_id)
        marks, args = ",".join("?" * len(ids)), sorted(ids)
        with self.db:
            self.db.execute(f"DELETE FROM voice_samples WHERE voice_id IN ({marks})", args)
            self.db.execute("UPDATE recording_voices SET voice_id = NULL, similarity = NULL, decision = 'unknown' "
                            f"WHERE voice_id IN ({marks})", args)
            self.db.execute(f"UPDATE voices SET merged_into = NULL WHERE id IN ({marks})", args)
            self.db.execute(f"DELETE FROM voices WHERE id IN ({marks})", args)
        self.db.execute("VACUUM")   # older versions of a row can survive in free space of a page
        return ids

    def delete_recording(self, recording_id: str) -> list[int]:
        """Remove a recording's samples; voices known only from it are erased. Returns the erased voices."""
        rows = self.db.execute("SELECT voice_id FROM voice_samples WHERE recording_id = ? UNION "
                               "SELECT voice_id FROM recording_voices WHERE recording_id = ? AND voice_id IS NOT NULL",
                               (recording_id, recording_id)).fetchall()
        touched = {self.resolve(r["voice_id"]) for r in rows}
        with self.db:
            self._forget(recording_id)
        removed = []
        for vid in sorted(touched):
            ids = self.family(vid)
            marks, args = ",".join("?" * len(ids)), sorted(ids)
            used = self.db.execute(f"SELECT 1 FROM voice_samples WHERE voice_id IN ({marks}) UNION ALL "
                                   f"SELECT 1 FROM recording_voices WHERE voice_id IN ({marks}) LIMIT 1", args + args).fetchone()
            if not used:
                self.delete_voice(vid)
                removed.append(vid)
        self.db.execute("VACUUM")
        return removed

    def recording_clusters(self, recording_id: str) -> dict[str, dict]:
        rows = self.db.execute("SELECT cluster, voice_id, similarity, decision, speech_seconds FROM recording_voices "
                               "WHERE recording_id = ?", (recording_id,)).fetchall()
        return {r["cluster"]: dict(r) for r in rows}

    def recordings_of(self, voice_id: int) -> list[str]:
        vid = self.resolve(voice_id)
        rows = self.db.execute("SELECT DISTINCT recording_id FROM recording_voices WHERE voice_id = ? ORDER BY recording_id",
                               (vid,)).fetchall()
        return [r["recording_id"] for r in rows]

    def list_voices(self, include_empty: bool = False) -> list[dict]:
        rows = self.db.execute("""
            SELECT v.id, v.label, v.name, v.created_at,
                   COUNT(DISTINCT rv.recording_id) AS meetings,
                   COALESCE(SUM(rv.speech_seconds), 0) AS speech_seconds,
                   MAX(rv.recording_id) AS last_recording
            FROM voices v LEFT JOIN recording_voices rv ON rv.voice_id = v.id
            WHERE v.merged_into IS NULL
            GROUP BY v.id ORDER BY v.id""").fetchall()
        out = [{**dict(r), "label": i18n.voice_label(self.language, r["id"])} for r in rows]
        return out if include_empty else [v for v in out if v["meetings"] or v["name"]]

    def samples(self, voice_id: int) -> list[dict]:
        vid = self.resolve(voice_id)
        rows = self.db.execute("SELECT recording_id, cluster, speech_seconds, clips FROM voice_samples WHERE voice_id = ? "
                               "ORDER BY recording_id", (vid,)).fetchall()
        return [{**dict(r), "clips": json.loads(r["clips"])} for r in rows]
