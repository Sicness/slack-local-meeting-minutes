"""Steps prepare (CAF → *.16k.wav for the models) and archive (audio.m4a for playback, then CAFs are deleted).

CAFs are deleted only after checking the length of everything made from them.
"""

import subprocess
from pathlib import Path

import soundfile as sf

from . import log
from .config import Config

TRACKS = ("mic", "remote")
TOLERANCE = 0.02   # s, wav vs CAF
M4A_TOLERANCE = 0.1


def duration(path: Path) -> float:
    return sf.info(str(path)).duration


def ffprobe_duration(cfg: Config, path: Path) -> float:
    out = subprocess.run([cfg.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def ffmpeg(cfg: Config, *args: str) -> None:
    subprocess.run([cfg.ffmpeg, "-nostdin", "-v", "error", "-y", *args], check=True)


def run(folder: Path, meta: dict, cfg: Config) -> dict:
    """CAF → 16 kHz wav for the models. CAFs stay until the archive step."""
    cafs = {t: folder / meta["tracks"][t]["file"] for t in TRACKS}
    wavs = {t: folder / f"{t}.16k.wav" for t in TRACKS}
    if not all(c.exists() for c in cafs.values()):
        if all(w.exists() for w in wavs.values()):
            log.info("prepare: CAFs already deleted, wavs in place")
            return {"wav_seconds": {t: round(duration(wavs[t]), 3) for t in TRACKS}}
        raise RuntimeError(f"missing files {[c.name for c in cafs.values() if not c.exists()]}")
    out = {}
    for t in TRACKS:
        tmp = wavs[t].with_suffix(".tmp.wav")
        ffmpeg(cfg, "-i", str(cafs[t]), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp))
        tmp.replace(wavs[t])
        caf_s, wav_s = duration(cafs[t]), duration(wavs[t])
        if abs(wav_s - caf_s) > TOLERANCE:
            raise RuntimeError(f"{wavs[t].name}: {wav_s:.3f} s, but CAF is {caf_s:.3f} s")
        out[t] = round(wav_s, 3)
    log.info(f"prepare: wav {out['mic']:.1f} / {out['remote']:.1f} s")
    return {"wav_seconds": out}


def archive(folder: Path, meta: dict, cfg: Config) -> dict:
    """audio.m4a (mic left, remote right) for playback; CAFs are deleted after a length check.
    A separate step after transcription: text does not need it and AAC encoding is slow."""
    cafs = {t: folder / meta["tracks"][t]["file"] for t in TRACKS}
    m4a = folder / "audio.m4a"
    if not all(c.exists() for c in cafs.values()):
        if m4a.exists():
            return {"m4a_seconds": round(ffprobe_duration(cfg, m4a), 3)}
        raise RuntimeError("no CAFs to build audio.m4a from")
    tmp = folder / "audio.tmp.m4a"
    ffmpeg(cfg, "-i", str(cafs["mic"]), "-i", str(cafs["remote"]), "-filter_complex",
           "[0:a]aresample=48000[a];[1:a]aresample=48000[b];[a][b]join=inputs=2:channel_layout=stereo[out]",
           "-map", "[out]", "-c:a", "aac", "-b:a", "96k", str(tmp))
    tmp.replace(m4a)
    m4a_seconds = ffprobe_duration(cfg, m4a)
    longest = max(duration(c) for c in cafs.values())
    if abs(m4a_seconds - longest) > M4A_TOLERANCE:
        raise RuntimeError(f"audio.m4a: {m4a_seconds:.3f} s, but tracks are {longest:.3f} s; keeping CAFs")
    wavs = [folder / f"{t}.16k.wav" for t in TRACKS]
    if not all(w.exists() for w in wavs):
        raise RuntimeError("wav files missing; keeping CAFs")
    for c in cafs.values():
        c.unlink()
    log.info(f"archive: m4a {m4a_seconds:.1f} s, CAFs deleted")
    return {"m4a_seconds": round(m4a_seconds, 3)}
