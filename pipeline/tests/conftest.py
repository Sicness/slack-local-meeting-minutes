import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Tests never read or write the real ~/MeetingAssistant."""
    monkeypatch.setenv("MA_HOME", str(tmp_path / "ma-home"))
