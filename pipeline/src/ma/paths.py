"""Where data lives. Everything is outside the repository; MA_HOME overrides the root (for tests)."""

import os
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def home() -> Path:
    return Path(os.environ.get("MA_HOME") or Path.home() / "MeetingAssistant")


def secure_home() -> None:
    """Recordings, voiceprints and logs are readable by the owner only (700/600)."""
    os.umask(0o077)
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    if not stat.S_IMODE(root.stat().st_mode) & 0o077:
        return
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder) / name
            if not path.is_symlink():
                path.chmod(0o700 if path.is_dir() else 0o600)
    root.chmod(0o700)   # last, so an interrupted pass runs again


def recordings() -> Path:
    return home() / "recordings"


def logs() -> Path:
    return home() / "logs"


def config() -> Path:
    return home() / "config.toml"


def glossary() -> Path:
    return home() / "glossary.txt"


def recording(ref: str) -> Path:
    """Recording folder by id or path."""
    p = Path(ref).expanduser()
    if p.is_dir():
        return p.resolve()
    return recordings() / ref
