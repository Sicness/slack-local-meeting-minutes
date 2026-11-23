"""Deleting a voice or a recording removes the voiceprint everywhere."""

import json

import pytest
from fastapi.testclient import TestClient

from ma import forget, notes, registry, web
from test_web import client, home, make, people  # noqa: F401


def blob_of(vector):
    return registry._blob(registry.norm(vector))


def test_delete_voice_erases_print_everywhere(home):
    a, b, c = people()
    f1, r1 = make(home, "2026-01-15_1000_aaaa", [a, b], "2026-01-15T10:00:00+00:00")
    f2, r2 = make(home, "2026-01-16_1000_bbbb", [a, c], "2026-01-16T10:00:00+00:00")
    vid, other = r1["S1"].voice_id, r1["S2"].voice_id
    with registry.Registry() as reg:
        reg.set_name(vid, "Secret Person")
    (f1 / "minutes.json").write_text(json.dumps({"model": "m", "minutes": {
        "title": f"Plan with @v{vid}", "summary": f"@v{vid} and @v{other}", "decisions": [],
        "action_items": [{"owner": f"@v{vid}", "task": "x", "due": "", "at": ""}], "open_questions": [], "topics": []}}))
    with registry.Registry() as reg:
        from ma.process import rerender
        assert rerender(f1, reg)

    res = forget.delete_voice(vid)
    assert res["recordings"] == 2 and res["skipped"] == []

    raw = (home / "voices.sqlite").read_bytes()
    assert blob_of(a) not in raw and b"Secret Person" not in raw
    with registry.Registry() as reg:
        with pytest.raises(registry.RegistryError):
            reg.voice(vid)
        assert reg.voice(other)["id"] == other                     # other voices are intact
    for folder in (f1, f2):
        v = json.loads((folder / "voices.json").read_text())
        gone = next(x for x in v["clusters"] if x["cluster"] == "S1")
        assert gone["voice_id"] is None and "centroid" not in gone
        assert f"@v{vid}" not in v["word_labels"] and "@unknown" in v["word_labels"]
        assert f"@v{vid}" not in (folder / "transcript.json").read_text()
    m = json.loads((f1 / "minutes.json").read_text())["minutes"]
    assert m["action_items"][0]["owner"] == "@unknown" and f"@v{other}" in m["summary"]
    for note in (home / "vault").rglob("*.md"):
        assert "Secret Person" not in note.read_text() and "Secret Person" not in note.name
        assert f"@v{vid}" not in note.read_text()


def test_deleted_voice_includes_merged_prints(home):
    a, b, c = people()
    f1, r1 = make(home, "2026-01-15_1000_aaaa", [a, b], "2026-01-15T10:00:00+00:00")
    f2, r2 = make(home, "2026-01-16_1000_bbbb", [c, b], "2026-01-16T10:00:00+00:00")
    with registry.Registry() as reg:
        into = reg.merge(r1["S1"].voice_id, r2["S1"].voice_id)
    forget.delete_voice(into)
    raw = (home / "voices.sqlite").read_bytes()
    assert blob_of(a) not in raw and blob_of(c) not in raw


def test_delete_recording_keeps_shared_voices_and_user_text(home):
    a, b, c = people()
    f1, r1 = make(home, "2026-01-15_1000_aaaa", [a, b], "2026-01-15T10:00:00+00:00")
    f2, r2 = make(home, "2026-01-16_1000_bbbb", [a, c], "2026-01-16T10:00:00+00:00")
    shared, only_here = r1["S1"].voice_id, r1["S2"].voice_id
    note = home / "vault" / json.loads((f1 / "meta.json").read_text())["notes"]["transcript"]
    note.write_text(note.read_text() + "\nMy own text\n")

    res = forget.delete_recording(f1.name)

    assert not f1.exists() and f2.exists() and res["voices_removed"] == [only_here]
    assert "My own text" in note.read_text() and notes.BEGIN not in note.read_text()
    with registry.Registry() as reg:
        assert reg.recordings_of(shared) == [f2.name]
        with pytest.raises(registry.RegistryError):
            reg.voice(only_here)


def test_delete_refuses_paths_outside_recordings(home, tmp_path):
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "meta.json").write_text("{}")
    with pytest.raises(forget.ForgetError):
        forget.delete_recording(str(outside))
    assert outside.exists()


def test_web_delete(home, client):
    a, b, _ = people()
    f, r = make(home, "2026-01-15_1000_aaaa", [a, b], "2026-01-15T10:00:00+00:00")
    h = {"X-MA": "1"}
    assert client.post(f"/api/voices/{r['S1'].voice_id}/delete").status_code == 403    # no X-MA
    assert client.post(f"/api/voices/{r['S1'].voice_id}/delete", headers=h).status_code == 200
    assert client.post(f"/api/r/{f.name}/delete", headers=h).status_code == 200
    assert not f.exists() and client.get(f"/r/{f.name}").status_code == 404


def test_delete_removes_mentions_from_logs(home):
    a, b, c = people()
    f1, r1 = make(home, "2026-01-15_1000_aaaa", [a, b], "2026-01-15T10:00:00+00:00")
    vid = r1["S1"].voice_id
    with registry.Registry() as reg:
        reg.set_name(vid, "Secret Person")
    logs = home / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "pipeline.log").write_text(f"keep me\nname Secret Person\n{f1.name}: done\n")
    forget.delete_voice(vid)
    assert "Secret Person" not in (logs / "pipeline.log").read_text()
    forget.delete_recording(f1.name)
    text = (logs / "pipeline.log").read_text()
    assert "keep me" in text and f"{f1.name}: done" not in text
