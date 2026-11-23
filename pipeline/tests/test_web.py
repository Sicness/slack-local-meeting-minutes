"""Web UI on a synthetic recording: pages, audio, protection, voice name in all notes, moving, merging."""

import json

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from ma import i18n, meta as M, notes, registry, transcript, web
from ma.config import Config
from ma.glossary import Glossary
from ma.registry import Cluster


def signed_in(c):
    c.cookies.set(web.cookie_name(8765), web.ui_token())
    return c


def make(home, rid, voices_people, started):
    """A recording after all steps: two remote speakers, one utterance each; notes in the vault."""
    folder = home / "recordings" / rid
    folder.mkdir(parents=True)
    sf.write(str(folder / "remote.16k.wav"), np.zeros(16000 * 30, np.float32), 16000)
    words = [{"w": f" w{i}", "start": 1 + i, "end": 1.5 + i, "p": 0.9} for i in range(6)]
    asr = {"remote": {"audio_seconds": 30, "segments": [{"start": 1, "end": 7, "text": "", "words": words}]},
           "mic": {"audio_seconds": 30, "segments": []}}
    for t, d in asr.items():
        (folder / f"asr.{t}.json").write_text(json.dumps(d))
    with registry.Registry() as reg:
        res = reg.match_recording(rid, "m", [Cluster(f"S{i + 1}", e, 20, [[1 + 3 * i, 3 + 3 * i]])
                                             for i, e in enumerate(voices_people)], 0.65, 5)
    clusters = [{"cluster": c, "voice_id": m.voice_id, "speech_seconds": 20, "decision": m.decision, "similarity": m.similarity,
                 "clips": [[1, 3]], "centroid": list(voices_people[int(c[1:]) - 1])} for c, m in res.items()]
    labels = [f"@v{res['S1'].voice_id}"] * 3 + [f"@v{res['S2'].voice_id}"] * 3
    (folder / "voices.json").write_text(json.dumps({"words": 6, "word_labels": labels, "clusters": clusters,
                                                    "chunks": [{"cluster": "S1", "words": 3}, {"cluster": "S2", "words": 3}]}))
    meta = {"id": rid, "status": "recorded", "started_at": started, "duration_seconds": 30, "tracks": {},
            "pipeline": {s: {"status": "done"} for s in M.STEPS}}
    with registry.Registry() as reg:
        transcript.run(folder, meta, Glossary(), reg)
        notes.run(folder, meta, Config(obsidian_vault=str(home / "vault")))
    meta["pipeline"]["notes"]["result"] = {"vault": str(home / "vault")}
    M.write(folder, meta)
    return folder, res


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "vault").mkdir(parents=True)
    (home / "config.toml").write_text(f'obsidian_vault = "{home / "vault"}"\n')
    monkeypatch.setenv("MA_HOME", str(home))
    return home


@pytest.fixture
def client(home):
    return signed_in(TestClient(web.create_app({"testserver"})))


def people():
    rng = np.random.default_rng(0)
    return [registry.norm(v) for v in np.linalg.qr(rng.standard_normal((256, 3)))[0].T]


def test_pages_and_clip(home, client):
    a, b, c = people()
    make(home, "2026-01-15_1000_aaaa", [a, b], "2026-01-15T10:00:00+00:00")
    r = client.get("/")
    assert r.status_code == 200 and "2026-01-15_1000_aaaa" in r.text and "done" in r.text
    r = client.get("/r/2026-01-15_1000_aaaa")
    assert r.status_code == 200 and "Voice 1" in r.text and "Voice 2" in r.text and "/clip?start=1" in r.text
    assert client.get("/voices").status_code == 200
    wav = client.get("/r/2026-01-15_1000_aaaa/clip", params={"start": 1, "end": 3})
    assert wav.status_code == 200 and wav.headers["content-type"] == "audio/wav" and wav.content[:4] == b"RIFF"
    assert client.get("/r/nope").status_code == 404
    assert client.get("/r/..%2F..").status_code == 404


def test_guard(home):
    c = signed_in(TestClient(web.create_app({"testserver"})))
    assert TestClient(web.create_app({"127.0.0.1:8765"})).get("/").status_code == 403    # foreign Host
    assert c.post("/api/voices/merge", json={}).status_code == 403                         # no X-MA
    assert c.post("/api/voices/merge", json={}, headers={"X-MA": "1", "Origin": "https://evil.example"}).status_code == 403


def test_name_appears_in_all_notes(home, client):
    a, b, c = people()
    f1, r1 = make(home, "2026-01-15_1000_aaaa", [a, b], "2026-01-15T10:00:00+00:00")
    f2, r2 = make(home, "2026-01-16_1000_bbbb", [a, c], "2026-01-16T10:00:00+00:00")
    vid = r1["S1"].voice_id
    assert r2["S1"].voice_id == vid                    # same person in the second meeting
    h = {"X-MA": "1"}
    d = client.post(f"/api/voices/{vid}/name", json={"name": "Алиса"}, headers=h).json()
    assert d["rerendered"] == 2
    for f in (f1, f2):
        note = home / "vault" / M.read(f)["notes"]["transcript"]
        assert "[[Алиса]]" in note.read_text()
    assert "Алиса" in client.get("/voices").text

    # "this is a different voice": S2 in the second meeting is really person b from the first
    d = client.post("/api/r/2026-01-16_1000_bbbb/reassign", json={"cluster": "S2", "voice_id": r1["S2"].voice_id}, headers=h).json()
    assert d["voice_id"] == r1["S2"].voice_id
    v = json.loads((f2 / "voices.json").read_text())
    assert v["word_labels"][3:] == [f"@v{r1['S2'].voice_id}"] * 3

    # merging and re-rendering one recording
    other = make(home, "2026-01-17_1000_cccc", [c, people()[2] * -1], "2026-01-17T10:00:00+00:00")[1]
    d = client.post("/api/voices/merge", json={"into": vid, "other": other["S1"].voice_id}, headers=h).json()
    assert d["voice_id"] == vid
    assert client.post("/api/r/2026-01-17_1000_cccc/rerender", headers=h).json() == {"rerendered": True}
    assert "[[Алиса]]" in (home / "vault" / M.read(home / "recordings" / "2026-01-17_1000_cccc")["notes"]["transcript"]).read_text()


def test_russian_interface(home, monkeypatch):
    (home / "config.toml").write_text(f'language = "ru"\nobsidian_vault = "{home / "vault"}"\n', encoding="utf-8")
    a, b, _ = people()
    make(home, "2026-01-15_1000_aaaa", [a, b], "2026-01-15T10:00:00+00:00")
    c = signed_in(TestClient(web.create_app({"testserver"})))
    page = c.get("/").text
    assert '<html lang="ru">' in page and "Записи" in page and "готово" in page
    assert "Голос 1" in c.get("/r/2026-01-15_1000_aaaa").text


def test_translations_have_the_same_keys():
    assert set(i18n.CATALOG["en"]) == set(i18n.CATALOG["ru"])


def test_session_token(home):
    c = TestClient(web.create_app({"testserver"}), follow_redirects=False)
    assert c.get("/").status_code == 401
    assert c.get("/voices", params={"token": "wrong"}).status_code == 401
    r = c.get("/voices", params={"token": web.ui_token(), "x": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/voices?x=1"
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert c.get("/voices").status_code == 200
    assert (home / "ui.token").stat().st_mode & 0o077 == 0


def test_identity_proves_the_token_without_revealing_it(home):
    import hmac
    c = TestClient(web.create_app({"testserver"}))
    nonce = "n" * 32
    r = c.get("/ma-identity", params={"nonce": nonce})
    assert r.status_code == 200 and r.text == hmac.new(web.ui_token().encode(), nonce.encode(), "sha256").hexdigest()
    assert web.ui_token() not in r.text
    assert c.get("/ma-identity", params={"nonce": "short"}).status_code == 400
    assert TestClient(web.create_app({"127.0.0.1:8765"})).get("/ma-identity", params={"nonce": nonce}).status_code == 403
