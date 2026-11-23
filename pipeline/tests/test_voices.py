"""Voices: registry, chunks and relabeling, the voices step with a fake ma-diarize, transcript labels."""

import json

import numpy as np
import pytest

from ma import diarize, glossary, meta as M, registry, transcript
from ma.config import Config
from ma.registry import Cluster, Registry, RegistryError

RNG = np.random.default_rng(7)
PEOPLE = dict(zip("abcdef", np.linalg.qr(RNG.standard_normal((256, 6)))[0].T))   # different people are orthogonal


def voice(who: str, noise: float = 0.3, rng=RNG) -> np.ndarray:
    """A person's embedding: noise length ≈ noise (0.3 gives cosine ≈ 0.96 with the clean voice)."""
    return registry.norm(PEOPLE[who] + noise * rng.standard_normal(256) / np.sqrt(256))


def reg_at(tmp_path) -> Registry:
    return Registry(tmp_path / "voices.sqlite")


def match(reg, rid, clusters, threshold=0.65, min_speech=5.0, model="m1"):
    return reg.match_recording(rid, model, clusters, threshold, min_speech)


# --- registry ---

def test_same_people_get_same_numbers(tmp_path):
    with reg_at(tmp_path) as reg:
        a = match(reg, "r1", [Cluster("S1", voice("a"), 60), Cluster("S2", voice("b"), 40)])
        assert {m.decision for m in a.values()} == {"new"}
        b = match(reg, "r2", [Cluster("S1", voice("b"), 30), Cluster("S2", voice("a"), 50), Cluster("S3", voice("c"), 20)])
        assert b["S2"].voice_id == a["S1"].voice_id and b["S1"].voice_id == a["S2"].voice_id
        assert b["S3"].decision == "new" and b["S3"].voice_id not in {a["S1"].voice_id, a["S2"].voice_id}
        assert reg.voice(b["S3"].voice_id)["label"] == f"Voice {b['S3'].voice_id}"


def test_one_to_one_and_split(tmp_path):
    with reg_at(tmp_path) as reg:
        first = match(reg, "r1", [Cluster("S1", voice("a"), 60)])
        vid = first["S1"].voice_id
        # two clusters pull toward one voice: if similar to each other it's a split, otherwise the second is new
        mid = registry.norm(PEOPLE["a"] * 0.6 + PEOPLE["b"] * 0.8)   # ≈ 0.6 with a: above the 0.55 threshold, but not a split
        r = match(reg, "r2", [Cluster("S1", voice("a", 0.1), 60), Cluster("S2", voice("a", 0.2), 10), Cluster("S3", mid, 20)],
                  threshold=0.55)
        assert r["S1"].voice_id == vid and r["S1"].decision == "matched"
        assert r["S2"].voice_id == vid and r["S2"].decision == "split"
        assert r["S3"].voice_id != vid and r["S3"].decision == "new"


def test_short_clusters_not_registered(tmp_path):
    with reg_at(tmp_path) as reg:
        match(reg, "r1", [Cluster("S1", voice("a"), 60)])
        near_b = registry.norm(0.6 * PEOPLE["b"] + 0.8 * PEOPLE["f"])   # ≈ 0.6 with b: below threshold, but the closest
        r = match(reg, "r2", [Cluster("S1", PEOPLE["b"], 40), Cluster("S2", voice("a"), 2),
                              Cluster("S3", near_b, 1), Cluster("S4", voice("e"), 1)])
        assert r["S2"].decision == "short" and r["S2"].voice_id == 1
        assert r["S3"].decision == "short-near" and r["S3"].voice_id == r["S1"].voice_id
        assert r["S4"].decision == "unknown" and r["S4"].voice_id is None
        assert len(reg.list_voices(include_empty=True)) == 2      # only a and b: short clusters create no voices


def test_reprocessing_keeps_numbers(tmp_path):
    with reg_at(tmp_path) as reg:
        r1 = match(reg, "r1", [Cluster("S1", voice("a"), 60), Cluster("S2", voice("b"), 60)])
        for _ in range(3):
            again = match(reg, "r1", [Cluster("S1", voice("a"), 60), Cluster("S2", voice("b"), 60)])
            assert {k: m.voice_id for k, m in again.items()} == {k: m.voice_id for k, m in r1.items()}
        assert len(reg.list_voices(include_empty=True)) == 2
        reg.forget_recording("r1")
        assert reg.list_voices() == []                            # voices with no meetings and no name are hidden
        assert match(reg, "r1", [Cluster("S1", voice("a"), 60)])["S1"].voice_id == r1["S1"].voice_id


def test_model_mismatch(tmp_path):
    with reg_at(tmp_path) as reg:
        match(reg, "r1", [Cluster("S1", voice("a"), 60)], model="m1")
        with pytest.raises(RegistryError, match="not comparable"):
            match(reg, "r2", [Cluster("S1", voice("a"), 60)], model="m2")


def test_names_and_merge(tmp_path):
    with reg_at(tmp_path) as reg:
        a = match(reg, "r1", [Cluster("S1", voice("a"), 60)])["S1"].voice_id
        # same person in another setting is not recognized: the user merges by hand
        b = match(reg, "r2", [Cluster("S1", voice("c"), 60)])["S1"].voice_id
        reg.set_name(b, "Борис")
        assert reg.display(b) == "Борис" and reg.display(a) == f"Voice {a}"
        assert reg.merge(a, b) == a
        assert reg.resolve(b) == a and reg.display(b) == "Борис"     # the name carries over if the final voice had none
        assert reg.recordings_of(b) == ["r1", "r2"]
        assert [v["id"] for v in reg.list_voices()] == [a]
        with pytest.raises(RegistryError):
            reg.merge(a, b)
        # the next meeting recognizes both by the shared centroid
        r3 = match(reg, "r3", [Cluster("S1", voice("a"), 30)], threshold=0.5)
        assert r3["S1"].voice_id == a


# --- chunks and relabeling ---

def words(*spans):
    return [{"w": f" w{i}", "start": a, "end": b, "p": 0.9} for i, (a, b) in enumerate(spans)]


def test_chunks_follow_segments_and_pauses():
    ws = words((0.0, 0.4), (0.5, 0.9), (1.0, 1.3), (5.0, 5.2), (5.3, 5.6), (20.0, 20.0))
    segs = [{"start": 0.0, "end": 1.0, "speaker": "S1"}, {"start": 1.0, "end": 6.0, "speaker": "S2"}]
    cs = diarize.make_chunks(ws, segs)
    assert [(c.first, c.last, c.seg_label) for c in cs] == [(0, 1, "S1"), (2, 2, "S2"), (3, 4, "S2"), (5, 5, None)]
    assert diarize.span(cs[-1], 30.0) == [19.7, 20.3]            # a zero-length word is extended
    assert diarize.span(cs[0], 30.0) == [0.0, 0.9]
    edge = diarize.Chunk(29.9, 30.0, 0, 0, None)
    assert diarize.span(edge, 30.0) == [29.4, 30.0]


def test_relabel_fixes_short_turns():
    rng = np.random.default_rng(1)
    cs = [diarize.Chunk(0, 5, 0, 0, "S1"), diarize.Chunk(5.2, 5.5, 1, 1, "S1"),   # b's "uh-huh" given to S1
          diarize.Chunk(6, 10, 2, 2, "S2"), diarize.Chunk(10.5, 15, 3, 3, "S1"), diarize.Chunk(16, 16.4, 4, 4, None)]
    for c, who in zip(cs, "abbaa"):
        c.embedding = voice(who, 0.5, rng)
    diarize.relabel(cs)
    assert [c.label for c in cs] == ["S1", "S2", "S2", "S1", "S1"]
    assert cs[1].label != cs[1].seg_label and cs[1].margin > diarize.KEEP_MARGIN
    merged = diarize.merge_adjacent(cs)
    assert [(c.first, c.last, c.label) for c in merged] == [(0, 0, "S1"), (1, 2, "S2"), (3, 4, "S1")]
    clusters = {c.cluster: c for c in diarize.meeting_clusters(cs)}
    assert clusters["S1"].speech_seconds == pytest.approx(5 + 4.5 + 0.4)
    assert len(clusters["S1"].clips) == 3


def test_relabel_keeps_diarizer_label_when_unsure():
    cs = [diarize.Chunk(0, 5, 0, 0, "S1"), diarize.Chunk(6, 10, 1, 1, "S2"), diarize.Chunk(11, 11.3, 2, 2, "S2")]
    cs[0].embedding, cs[1].embedding = PEOPLE["a"], PEOPLE["b"]
    cs[2].embedding = registry.norm(PEOPLE["a"] + PEOPLE["b"])   # halfway
    diarize.relabel(cs)
    assert cs[2].label == "S2"


# --- voices step and transcript ---

def remote_asr(turns):
    segs = []
    for a, who, n in turns:
        segs.append({"start": a, "end": a + n * 0.4, "text": "",
                     "words": [{"w": f" {who}{i}", "start": a + i * 0.4, "end": a + i * 0.4 + 0.3, "p": 0.9} for i in range(n)]})
    return {"audio_seconds": 120, "segments": segs}


TURNS = [(1.0, "a", 15), (8.0, "b", 2), (9.5, "c", 12), (16.0, "a", 1), (18.0, "b", 20), (40.0, "c", 14)]


def fake_diarizer(monkeypatch, who_at):
    """Fake ma-diarize: segments follow TURNS, but a short reply goes to a neighbor; embedding follows the speaker."""
    rng = np.random.default_rng(3)

    def call(cfg, cmd, *args):
        if cmd == "diarize":
            segs = []
            for a, who, n in TURNS:
                lab = {"a": "S1", "b": "S2", "c": "S3"}[who]
                if n <= 2:
                    lab = segs[-1]["speaker"]
                segs.append({"start": a, "end": a + n * 0.4, "speaker": lab})
            json.dump({"model": "fake@1", "audio_seconds": 120, "segments": segs, "speakers": {}, "seconds": 0.1},
                      open(args[1], "w"))
        else:
            spans = json.load(open(args[1]))
            json.dump({"model": "fake@1", "embeddings": [voice(who_at((a + b) / 2), 0.5, rng).tolist() for a, b in spans],
                       "seconds": 0.1}, open(args[2], "w"))
    monkeypatch.setattr(diarize, "_call", call)


def who_at(t):
    for a, who, n in reversed(TURNS):
        if t >= a - 0.5:
            return who
    return "a"


def test_voices_step_and_transcript(tmp_path, monkeypatch):
    folder = tmp_path / "rec"
    folder.mkdir()
    meta = {"id": "rec", "mode": "headphones", "duration_seconds": 120, "tracks": {}}
    asr = {"remote": remote_asr(TURNS), "mic": {"audio_seconds": 120, "segments": [
        {"start": 30, "end": 31, "text": "", "words": [{"w": " я", "start": 30, "end": 30.5, "p": 0.9}]}]}}
    for t, d in asr.items():
        (folder / f"asr.{t}.json").write_text(json.dumps(d))
    fake_diarizer(monkeypatch, who_at)
    cfg = Config(min_speech_seconds=3.0)
    with Registry(tmp_path / "voices.sqlite") as reg:
        res = diarize.run(folder, meta, cfg, reg)
        assert res["clusters"] == 3 and res["relabeled"] >= 2
        v = json.loads((folder / "voices.json").read_text())
        assert v["words"] == 64 and len(v["word_labels"]) == 64 and None not in v["word_labels"]
        ids = {c["cluster"]: c["voice_id"] for c in v["clusters"]}

        reg.set_name(ids["S2"], "Борис")
        tr = transcript.build(meta, asr, glossary.Glossary(), v, reg)
        seq = [(u["speaker"], len(u["words"])) for u in tr["utterances"] if u["track"] == "remote"]
        a, b, c = (f"@v{ids[k]}" for k in ("S1", "S2", "S3"))
        assert seq == [(a, 15), (b, 2), (c, 12), (a, 1), (b, 20), (c, 14)]   # short replies go to their own voices
        assert tr["speakers"][b] == "Борис" and tr["speakers"][a] == f"Voice {ids['S1']}"
        md = transcript.markdown(tr, meta)
        assert "Борис:**" in md and "Me:**" in md

        # reprocessing keeps the same numbers
        diarize.run(folder, meta, cfg, reg)
        v2 = json.loads((folder / "voices.json").read_text())
        assert {c["cluster"]: c["voice_id"] for c in v2["clusters"]} == ids

        # merge: both voices are one in the transcript
        reg.merge(ids["S1"], ids["S3"])
        tr = transcript.build(meta, asr, glossary.Glossary(), v2, reg)
        assert {u["speaker"] for u in tr["utterances"] if u["track"] == "remote"} == {a, b}


def test_transcript_rejects_stale_voices():
    asr = {"remote": remote_asr([(1.0, "a", 3)]), "mic": {"segments": []}}
    with pytest.raises(RuntimeError, match="--from voices"):
        transcript.build({"id": "r", "tracks": {}}, asr, glossary.Glossary(), {"words": 5, "word_labels": []}, None)


def test_voices_step_without_remote_words(tmp_path, monkeypatch):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "asr.remote.json").write_text(json.dumps({"audio_seconds": 10, "segments": []}))
    monkeypatch.setattr(diarize, "_call", lambda *a: pytest.fail("ma-diarize is not needed"))
    with Registry(tmp_path / "voices.sqlite") as reg:
        assert diarize.run(folder, {"id": "rec"}, Config(), reg)["voices"] == 0
    assert json.loads((folder / "voices.json").read_text())["words"] == 0


def test_missing_diarizer_message(tmp_path):
    with pytest.raises(RuntimeError, match="build.sh"):
        diarize._call(Config(diarizer=str(tmp_path / "nope")), "diarize")


def test_reassign_cluster(tmp_path):
    with reg_at(tmp_path) as reg:
        r1 = match(reg, "r1", [Cluster("S1", voice("a"), 60), Cluster("S2", voice("b"), 60)])
        a, b = r1["S1"].voice_id, r1["S2"].voice_id
        # on r2 person b is misidentified as a: the user says "this is a different voice" → b
        r2 = match(reg, "r2", [Cluster("S1", voice("a"), 40)])
        assert r2["S1"].voice_id == a
        assert reg.reassign("r2", "S1", b) == b
        assert reg.recording_clusters("r2")["S1"]["decision"] == "manual"
        assert reg.recordings_of(b) == ["r1", "r2"] and reg.recordings_of(a) == ["r1"]
        # new voice from the cluster; the old one with no meetings or name merges into it
        r3 = match(reg, "r3", [Cluster("S1", voice("c"), 40)])
        c = r3["S1"].voice_id
        new = reg.reassign("r3", "S1", None)
        assert new not in (a, b, c) and reg.resolve(c) == new
        with pytest.raises(RegistryError):
            reg.reassign("r3", "S9", None)


def test_set_cluster_voice_rebuilds_word_labels(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "voices.json").write_text(json.dumps({
        "words": 5, "word_labels": ["@v1", "@v1", "@v2", "@v1", "@unknown"],
        "clusters": [{"cluster": "S1", "voice_id": 1}, {"cluster": "S2", "voice_id": 2}, {"cluster": "S3", "voice_id": None}],
        "chunks": [{"cluster": "S1", "words": 2}, {"cluster": "S2", "words": 1}, {"cluster": "S1", "words": 1},
                   {"cluster": "S3", "words": 1}]}))
    diarize.set_cluster_voice(folder, "S1", 7)
    v = json.loads((folder / "voices.json").read_text())
    assert v["word_labels"] == ["@v7", "@v7", "@v2", "@v7", "@unknown"]
    assert v["clusters"][0] == {"cluster": "S1", "voice_id": 7, "decision": "manual", "similarity": None}
