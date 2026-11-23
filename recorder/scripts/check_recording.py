# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["numpy>=2", "soundfile>=0.12"]
# ///
"""Checks a recorder recording for correctness.

    uv run recorder/scripts/check_recording.py ~/MeetingAssistant/recordings/<id> [--window START END]

What it checks:
  * meta.json and both tracks exist, with equal lengths matching duration_seconds
  * mic offset against remote: cross-correlation (GCC-PHAT) in 4 s chunks over stretches
    where audio played on speakers (mode / route_changes = room); on headphones the mic can't hear Slack
  * my voice in remote: mic -> remote envelope correlation over headphone stretches
  * long continuous audio in remote (browser music would give tens of seconds in a row)
  * digital silence in remote during a call

Exit code 0 means all good, 1 means a check failed.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

LAG_LIMIT_MS = 100


def load(path: Path) -> tuple[np.ndarray, int]:
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    return x, sr


def envelope_db(x: np.ndarray, sr: int, hop_s: float) -> np.ndarray:
    h = max(1, int(sr * hop_s))
    n = len(x) // h
    r = np.sqrt(np.mean(x[: n * h].astype(np.float64).reshape(n, h) ** 2, axis=1))
    return 20 * np.log10(np.maximum(r, 1e-9))


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return x.astype(np.float64)
    n = int(round(len(x) * dst / src))
    spec = np.fft.rfft(x.astype(np.float64))
    out = np.zeros(n // 2 + 1, complex)
    k = min(len(spec), len(out))
    out[:k] = spec[:k]
    return np.fft.irfft(out, n) * n / len(x)


def chunk_lag(mic: np.ndarray, rem: np.ndarray, sr: int, max_s=0.4) -> tuple[float, float]:
    """Mic offset against remote (ms, + means the mic is later) and confidence (peak / median)."""
    n = min(len(mic), len(rem))
    w = np.hanning(n)
    f = np.fft.rfftfreq(n, 1 / sr)
    keep = (f >= 300) & (f <= min(3800, sr / 2 - 200))
    m = np.fft.rfft(mic[:n] * w) * keep
    r = np.fft.rfft(rem[:n] * w) * keep
    g = m * np.conj(r)
    g /= np.maximum(np.abs(g), 1e-12)
    cc = np.fft.irfft(g, n)
    lim = int(max_s * sr)
    cc = np.concatenate([cc[-lim:], cc[: lim + 1]])
    i = int(np.argmax(cc))
    return (i - lim) / sr * 1000, float(cc[i] / (np.median(np.abs(cc)) + 1e-12))


def mode_intervals(meta: dict, end: float) -> list[tuple[float, float, str]]:
    """Recording stretches with their mode: [(start, end, headphones|room)]."""
    points = [(0.0, meta["mode"])] + [(c["at_seconds"], c["mode"]) for c in meta["route_changes"]]
    return [(t, points[i + 1][0] if i + 1 < len(points) else end, mode) for i, (t, mode) in enumerate(points)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--window", nargs=2, type=float, metavar=("START", "END"), help="seconds from the start of the recording")
    args = ap.parse_args()
    folder = args.folder.expanduser()
    failed = []

    meta = json.loads((folder / "meta.json").read_text())
    dur = meta["duration_seconds"]
    print(f"Recording {meta['id']}: status {meta['status']}, {dur:.1f} s, mode {meta['mode']} ({meta['mode_reason']})")
    if meta["status"] != "recorded":
        print("  WARNING: status is not recorded, so the recorder didn't finish (crashed or still recording)")
    for ch in meta["route_changes"]:
        print(f"  output change at {ch['at_seconds']:.1f} s -> {ch['mode']} ({ch['reason']})")
    for p in meta["call_periods"]:
        end = p.get("end_seconds")
        print(f"  Slack held the mic: {p['start_seconds']:.1f}-{end:.1f} s" if end else f"  Slack held the mic from {p['start_seconds']:.1f} s")
    for w in meta["warnings"]:
        print(f"  warning at {w['at_seconds']:.1f} s: {w['text']}")

    tracks = {}
    for name in ("mic", "remote"):
        t = meta["tracks"][name]
        path = folder / t["file"]
        if not path.exists():
            failed.append(f"missing file {t['file']}")
            continue
        x, sr = load(path)
        tracks[name] = (x, sr)
        print(f"\n{name}: {t['file']}, {sr} Hz, {len(x) / sr:.3f} s, {len(t['segments'])} segments, "
              f"{t['discontinuities']} discontinuities, {t['overflow_frames']} overflow frames")
        for s in t["segments"]:
            info = s["info"]
            what = info.get("device") or info.get("target", "")
            print(f"  segment {s['index']}: from {s['seconds_from_t0']:.2f} s, {s['sample_rate']:.0f} Hz, "
                  f"{info.get('reason', '')} - {what}")
        if t["errors"]:
            failed.append(f"{name}: write errors {t['errors']}")
        if t["overflow_frames"]:
            failed.append(f"{name}: lost {t['overflow_frames']} frames (overflow)")
        if abs(len(x) / sr - dur) > 0.1:
            failed.append(f"{name}: length {len(x) / sr:.3f} s doesn't match duration_seconds {dur:.3f}")

    if len(tracks) < 2:
        print("\nFAILED: " + "; ".join(failed))
        return 1
    mic, mic_sr = tracks["mic"]
    rem, rem_sr = tracks["remote"]
    diff = abs(len(mic) / mic_sr - len(rem) / rem_sr)
    print(f"\nTrack lengths differ by {diff * 1000:.1f} ms")
    if diff > 0.05:
        failed.append(f"track lengths differ by {diff * 1000:.0f} ms")

    a, b = args.window if args.window else (0.0, min(len(mic) / mic_sr, len(rem) / rem_sr))
    mic_w = mic[int(a * mic_sr) : int(b * mic_sr)]
    rem_w = rem[int(a * rem_sr) : int(b * rem_sr)]

    intervals = [(max(s0, a), min(s1, b), m) for s0, s1, m in mode_intervals(meta, dur) if min(s1, b) > max(s0, a)]
    # The offset depends on the mic, so stretches are also split at mic segments.
    cuts = sorted(seg["seconds_from_t0"] for seg in meta["tracks"]["mic"]["segments"])
    split = []
    for s0, s1, m in intervals:
        edges = [s0] + [c for c in cuts if s0 < c < s1] + [s1]
        split += [(e0, e1, m) for e0, e1 in zip(edges, edges[1:])]
    intervals = split

    # Offset: in room stretches the mic hears Slack from the speakers.
    for s0, s1, mode in intervals:
        if mode != "room":
            continue
        lags, t = [], s0 + 1
        while t + 4 <= s1 - 1:
            r = rem[int(t * rem_sr) : int((t + 4) * rem_sr)]
            if np.sqrt(np.mean(r.astype(np.float64) ** 2)) > 3e-3:
                lag, strength = chunk_lag(mic[int(t * mic_sr) : int((t + 4) * mic_sr)].astype(np.float64),
                                          resample(r, rem_sr, mic_sr), mic_sr)
                if strength > 8:
                    lags.append(lag)
            t += 2
        if lags:
            med = float(np.median(lags))
            print(f"Mic offset against remote at {s0:.0f}-{s1:.0f} s (speakers): median {med:+.1f} ms "
                  f"over {len(lags)} chunks, spread {min(lags):+.0f}..{max(lags):+.0f} (+ means the mic is later)")
            if abs(med) > LAG_LIMIT_MS:
                failed.append(f"offset {med:+.0f} ms at {s0:.0f}-{s1:.0f} s exceeds +/-{LAG_LIMIT_MS} ms")
        else:
            print(f"Offset at {s0:.0f}-{s1:.0f} s (speakers): can't measure, Slack was silent or the mic didn't hear it")
    if not any(m == "room" for *_, m in intervals):
        print("Offset: can't measure, on headphones throughout so the mic can't hear Slack")

    # My voice in remote: headphones only (on speakers the mic hears Slack anyway).
    for s0, s1, mode in intervals:
        if mode != "headphones" or s1 - s0 < 30:
            continue
        for p in meta["call_periods"]:
            c0, c1 = max(s0, p["start_seconds"]), min(s1, p.get("end_seconds") or dur)
            if c1 - c0 < 30:
                continue
            em = envelope_db(mic[int(c0 * mic_sr) : int(c1 * mic_sr)], mic_sr, 0.01)
            er = envelope_db(rem[int(c0 * rem_sr) : int(c1 * rem_sr)], rem_sr, 0.01)
            n = min(len(em), len(er))
            em, er = em[:n], er[:n]
            both = np.mean((em > -40) & (er > -45)) * 100
            x = (em - em.mean()) / (em.std() + 1e-9)
            y = (er - er.mean()) / (er.std() + 1e-9)
            corr = max(float(np.mean(x[: n - L] * y[L:])) for L in range(0, min(101, n - 1)))
            print(f"Mic voice in remote at {c0:.0f}-{c1:.0f} s (headphones): envelope correlation {corr:+.2f} "
                  f"(>= 0.3 is suspicious), simultaneous speech {both:.1f} %")
            if corr >= 0.3:
                failed.append(f"remote seems to contain the mic at {c0:.0f}-{c1:.0f} s (correlation {corr:.2f})")

    em = envelope_db(mic_w, mic_sr, 0.01)
    er = envelope_db(rem_w, rem_sr, 0.01)
    n = min(len(em), len(er))
    er = er[:n]

    # Long stretches of audio in remote.
    active = er > -45
    smooth = np.convolve(active.astype(float), np.ones(50) / 50, mode="same") > 0.3
    runs, start = [], None
    for i, v in enumerate(np.append(smooth, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append(((a + start * 0.01), (i - start) * 0.01))
            start = None
    runs.sort(key=lambda r: -r[1])
    longest = runs[0][1] if runs else 0.0
    print("Longest audio stretches in remote: "
          + (", ".join(f"{s:.0f} s ({l:.1f} s)" for s, l in runs[:5]) if runs else "none"))
    if longest > 20:
        print(f"  WARNING: {longest:.0f} s of continuous audio, check whether it is music from another app")

    # Digital silence during a call.
    for p in meta["call_periods"]:
        s0, s1 = p["start_seconds"], p.get("end_seconds") or dur
        seg = rem[int(s0 * rem_sr) : int(s1 * rem_sr)]
        if len(seg) > rem_sr * 5:
            z = float(np.mean(seg == 0)) * 100
            print(f"Digital silence in remote during call {s0:.0f}-{s1:.0f} s: {z:.1f} %")
            if z > 50:
                failed.append(f"remote is {z:.0f} % digital silence during the call, the tap isn't capturing Slack")

    if failed:
        print("\nFAILED: " + "; ".join(failed))
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
