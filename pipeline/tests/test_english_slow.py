"""The default setup end to end: an English call through every step with the real models."""

import json
import shutil
import subprocess
import urllib.request

import numpy as np
import pytest

from test_slow import SR, home, make_recording  # noqa: F401  (home is a fixture)
from test_voices_slow import tts

from ma.config import Config
from ma.process import process

pytestmark = pytest.mark.slow

ME, SAM, PRIYA = "Daniel", "Samantha", "Fred"
SCRIPT = [
    (ME, "Thanks for joining. Let's go through the billing launch and then the on-call schedule."),
    (SAM, "Status from me. The invoice export is finished, and the load test on staging reached two hundred and forty requests per second."),
    (PRIYA, "I am worried about the retry logic in the payment webhook. If we receive a duplicate event, we might charge a customer twice."),
    (ME, "Good catch. Priya, can you add an idempotency key to the webhook by Thursday?"),
    (PRIYA, "Yes, I will take that, and I will have it done by Thursday."),
    (SAM, "Should we launch to all customers on Monday?"),
    (ME, "No. Let's do a ten percent rollout on Tuesday, and go to a full rollout after one week of metrics."),
    (SAM, "Okay, agreed. Ten percent on Tuesday."),
    (PRIYA, "Agreed."),
    (SAM, "I will write the rollback runbook after this meeting."),
    (PRIYA, "Who is on call during the rollout? We never settled that."),
    (ME, "I don't know yet. Let's come back to it. Thanks, everyone."),
]


def call(tmp):
    gap = np.zeros(int(0.9 * SR), np.float32)
    mic, remote = [], []
    for voice, text in SCRIPT:
        x = tts(tmp, voice, text)
        mic += [x if voice == ME else np.zeros_like(x), gap]
        remote += [np.zeros_like(x) if voice == ME else x, gap]
    return np.concatenate(mic), np.concatenate(remote)


def test_english_call_end_to_end(home, tmp_path):
    voices = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    if not all(v in voices for v in (ME, SAM, PRIYA)) or not shutil.which("say"):
        pytest.skip("English system voices are missing")
    try:
        urllib.request.urlopen(Config().ollama_url + "/api/version", timeout=3)
    except OSError:
        pytest.skip("Ollama is not running")
    (home / "config.toml").write_text(f'me_name = "Alex"\nobsidian_vault = "{home / "vault"}"\nmin_meeting_seconds = 30\n')
    (home / "vault").mkdir(exist_ok=True)
    mic, remote = call(tmp_path)
    folder = make_recording(home / "recordings", "EN", mic, remote)

    process(folder)

    tr = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    said = " ".join(u["text"] for u in tr["utterances"]).lower()
    for phrase in ("billing", "webhook", "thursday", "tuesday", "runbook", "on call"):
        assert phrase in said or phrase.replace(" ", "-") in said, phrase
    assert not any("Ѐ" <= ch <= "ӿ" for ch in said)
    assert sum(k.startswith("@v") for k in tr["speakers"]) == 2 and tr["speakers"]["@me"] == "Me"

    doc = json.loads((folder / "minutes.json").read_text(encoding="utf-8"))
    m = doc["minutes"]
    assert doc["language"] == "en" and m["decisions"] and m["open_questions"]
    assert any("thursday" in a["due"].lower() and a["owner"].startswith("@v") for a in m["action_items"])

    saved = json.loads((folder / "meta.json").read_text())["notes"]
    note = (home / "vault" / saved["meeting"]).read_text(encoding="utf-8")
    for heading in ("## Decisions", "## Action items", "## Open questions and concerns", "## Discussion"):
        assert heading in note
    assert "[[Alex]]" in note and "@v" not in note and "@me" not in note
    assert not any("Ѐ" <= ch <= "ӿ" for ch in note)
    print("\n" + note)
