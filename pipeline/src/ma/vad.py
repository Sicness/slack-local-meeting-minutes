"""Speech regions (silero-vad). Used as a filter after Whisper, not to cut the audio:
Whisper pads cut pieces with zeros and invents text on those tails.
"""

from bisect import bisect_right
from pathlib import Path

import numpy as np
import soundfile as sf

# Soft thresholds: a short "uh-huh" or "okay" must stay speech.
PARAMS = dict(threshold=0.35, min_speech_duration_ms=150, min_silence_duration_ms=400, speech_pad_ms=300)
MERGE_GAP = 2.0

_model = None


def _load():
    global _model
    if _model is None:
        from silero_vad import load_silero_vad
        _model = load_silero_vad()
    return _model


def regions(wav: Path) -> list[list[float]]:
    import torch
    from silero_vad import get_speech_timestamps

    x, sr = sf.read(str(wav), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if not np.any(x):
        return []
    ts = get_speech_timestamps(torch.from_numpy(x), _load(), sampling_rate=sr, return_seconds=True, **PARAMS)
    out: list[list[float]] = []
    for t in ts:
        if out and t["start"] - out[-1][1] < MERGE_GAP:
            out[-1][1] = float(t["end"])
        else:
            out.append([float(t["start"]), float(t["end"])])
    return out


def overlap(regs: list[list[float]], a: float, b: float) -> float:
    """How many seconds of [a, b] are speech."""
    return sum(max(0.0, min(b, e) - max(a, s)) for s, e in regs)


def inside(regs: list[list[float]], t: float, slack: float = 0.2) -> bool:
    i = bisect_right([r[0] for r in regs], t + slack) - 1
    return i >= 0 and t <= regs[i][1] + slack
