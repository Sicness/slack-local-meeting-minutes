import pytest

from ma import config, doctor, paths
from ma.cli import main


def test_init_creates_config_and_never_overwrites(capsys):
    assert main(["init", "--name", "Алекс \"A\"", "--language", "ru", "--vault", "/tmp/v", "--uv", "/x/uv", "--pipeline", "/x/p"]) == 0
    c = config.load()
    assert (c.me_name, c.language, c.asr_language, c.obsidian_vault) == ('Алекс "A"', "ru", "ru", "/tmp/v")
    before = paths.config().read_text()
    assert main(["init", "--name", "Other"]) == 1
    assert paths.config().read_text() == before


def test_init_rejects_unknown_language():
    with pytest.raises(ValueError):
        doctor.init(None, "xx", None, None, None)


def test_checks_name_the_fix(tmp_path):
    cfg = config.Config(diarizer=str(tmp_path / "none"), obsidian_vault=str(tmp_path / "none"))
    assert doctor._diarizer(cfg)[0] == doctor.FAIL and "build.sh" in doctor._diarizer(cfg)[1]
    assert doctor._vault(cfg)[0] == doctor.FAIL
    assert doctor._tool("definitely-not-a-tool", "install it") == (doctor.FAIL, "not found; install it")


def test_doctor_reports_every_area(capsys, monkeypatch):
    monkeypatch.setattr(doctor, "_ollama", lambda cfg: (doctor.OK, "stub"))
    monkeypatch.setattr(doctor, "_whisper", lambda cfg: (doctor.OK, "stub"))
    names = [r[0] for r in doctor.checks()]
    assert {"System", "Memory", "Data folder", "ffmpeg", "Diarizer", "Ollama", "Whisper", "Obsidian vault"} <= set(names)


def test_find_tool_falls_back_to_name():
    assert config.find_tool("definitely-not-a-tool") == "definitely-not-a-tool"
