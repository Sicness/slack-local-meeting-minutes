"""`ma doctor` and `ma init`: what is missing and how to fix it."""

import json
import platform
import shutil
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from . import asr, cleanup, config, paths

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "✓", WARN: "!", FAIL: "✗"}
MIN_MACOS = 15
MIN_MEMORY_GB = 16
CERTIFICATE = "Meeting Assistant Dev"


def _system() -> tuple[str, str]:
    version = platform.mac_ver()[0]
    if platform.machine() != "arm64":
        return FAIL, f"needs Apple Silicon, this is {platform.machine()}"
    if not version or int(version.split(".")[0]) < MIN_MACOS:
        return FAIL, f"needs macOS {MIN_MACOS} or later, this is {version or 'not macOS'}"
    return OK, f"macOS {version}, Apple Silicon"


def _memory() -> tuple[str, str]:
    try:
        gb = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)) / 1024 ** 3
    except (OSError, ValueError, subprocess.CalledProcessError):
        return WARN, "could not read the memory size"
    if gb < MIN_MEMORY_GB:
        return WARN, f"{gb:.0f} GB: the default minutes model may not fit; set a smaller [minutes] model"
    return OK, f"{gb:.0f} GB"


def _data() -> tuple[str, str]:
    root = paths.home()
    if not root.exists():
        return WARN, f"{root} will be created on first run"
    if stat.S_IMODE(root.stat().st_mode) & 0o077:
        return FAIL, f"{root} is readable by other users; any `ma` command fixes it"
    return OK, f"{root} (private)"


def _config() -> tuple[str, str]:
    if not paths.config().exists():
        return WARN, "no config.toml, using defaults; run `ma init`"
    cfg = config.load()
    return OK, f"language {cfg.language}, speech {cfg.asr_language}, your name: {cfg.me_name}"


def _tool(path: str, install: str) -> tuple[str, str]:
    found = shutil.which(path)
    return (OK, found) if found else (FAIL, f"not found; {install}")


def _diarizer(cfg: config.Config) -> tuple[str, str]:
    if Path(cfg.diarizer).exists():
        return OK, cfg.diarizer
    return FAIL, "not built; run diarizer/scripts/build.sh"


def _ollama(cfg: config.Config) -> tuple[str, str]:
    try:
        with urllib.request.urlopen(f"{cfg.ollama_url}/api/tags", timeout=3) as r:
            models = {m["name"] for m in json.load(r).get("models", [])}
    except (urllib.error.URLError, OSError, ValueError):
        return WARN, f"not running at {cfg.ollama_url}; transcripts work, minutes wait until you start Ollama"
    if cfg.minutes_model not in models:
        return FAIL, f"model missing; run `ollama pull {cfg.minutes_model}`"
    return OK, cfg.minutes_model


def _whisper(cfg: config.Config) -> tuple[str, str]:
    from huggingface_hub import snapshot_download
    missing = []
    for repo in filter(None, (cfg.asr_model, cfg.second_model)):
        try:
            snapshot_download(repo, revision=asr.REVISIONS.get(repo), local_files_only=True)
        except Exception:
            missing.append(repo)
    if missing:
        return WARN, f"downloaded on first use (1.6–3 GB each): {', '.join(missing)}"
    return OK, "models are downloaded"


def _vault(cfg: config.Config) -> tuple[str, str]:
    if Path(cfg.obsidian_vault).is_dir():
        return OK, cfg.obsidian_vault
    return FAIL, f"no folder {cfg.obsidian_vault}; create it or set obsidian_vault in config.toml"


def _retention(cfg: config.Config) -> tuple[str, str]:
    stale = cleanup.overdue(cfg.audio_retention_days)
    if stale:
        return WARN, (f"unfinished recordings keep their audio past {cfg.audio_retention_days:g} days: {', '.join(stale)}; "
                      "run `ma process <id>` or `ma delete <id>`")
    return OK, f"audio is deleted after {cfg.audio_retention_days:g} days"


def _recorder() -> tuple[str, str]:
    for app in (paths.REPO / "recorder" / "build" / "MeetingRecorder.app", Path("/Applications/MeetingRecorder.app")):
        if app.exists():
            return OK, str(app)
    return WARN, "not built; run recorder/scripts/build-app.sh"


def _certificate() -> tuple[str, str]:
    try:
        out = subprocess.run(["security", "find-identity", "-p", "codesigning"], capture_output=True, text=True).stdout
    except OSError:
        return WARN, "could not check"
    if f'"{CERTIFICATE}"' in out:
        return OK, CERTIFICATE
    return WARN, "no signing certificate: macOS forgets permissions after each rebuild (see the README)"


def checks() -> list[tuple[str, str, str]]:
    cfg = config.load()
    rows = [("System", _system), ("Memory", _memory), ("Data folder", _data), ("Config", _config),
            ("ffmpeg", lambda: _tool(cfg.ffmpeg, "run `brew install ffmpeg`")),
            ("ffprobe", lambda: _tool(cfg.ffprobe, "run `brew install ffmpeg`")),
            ("Diarizer", lambda: _diarizer(cfg)), ("Ollama", lambda: _ollama(cfg)), ("Whisper", lambda: _whisper(cfg)),
            ("Obsidian vault", lambda: _vault(cfg)), ("Audio retention", lambda: _retention(cfg)),
            ("Recorder app", _recorder), ("Certificate", _certificate)]
    return [(name, *fn()) for name, fn in rows]


def run() -> int:
    results = checks()
    for name, status, detail in results:
        print(f"{MARK[status]} {name:15} {detail}")
    failed = sum(status == FAIL for _, status, _ in results)
    print("\nReady." if not failed else f"\n{failed} problem(s) to fix.")
    return 1 if failed else 0


def init(name: str | None, language: str, vault: str | None, uv: str | None, pipeline: str | None) -> Path:
    """Create config.toml; an existing file is never overwritten."""
    path = paths.config()
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    if language not in config.LANGUAGES:
        raise ValueError(f"language must be one of: {', '.join(config.LANGUAGES)}")
    q = lambda v: json.dumps(v, ensure_ascii=False)
    lines = ["# Meeting Assistant settings. A missing key uses its default.", f"language = {q(language)}"]
    if name:
        lines.append(f"me_name = {q(name)}")
    if vault:
        lines.append(f"obsidian_vault = {q(vault)}")
    if uv or pipeline:
        lines += ["", "[recorder]"]
        lines += [f"uv = {q(uv)}"] if uv else []
        lines += [f"pipeline = {q(pipeline)}"] if pipeline else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
