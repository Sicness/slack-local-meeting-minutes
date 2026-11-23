"""Recording time: headphones/room intervals and mapping file time to seconds from t0 using anchors."""

from bisect import bisect_right


def mode_intervals(meta: dict, end: float) -> list[tuple[float, float, str]]:
    """[(start, end, headphones|room)] from 0 to end."""
    points = [(0.0, meta.get("mode", "headphones"))]
    points += [(float(c["at_seconds"]), c["mode"]) for c in meta.get("route_changes", [])]
    out = []
    for i, (t, mode) in enumerate(points):
        stop = points[i + 1][0] if i + 1 < len(points) else end
        if stop > t:
            out.append((t, stop, mode))
    return out


def mode_at(intervals: list[tuple[float, float, str]], t: float) -> str:
    for start, stop, mode in intervals:
        if start <= t < stop:
            return mode
    return intervals[-1][2] if intervals else "headphones"


class TimeMap:
    """Track file seconds → seconds from t0.

    The recorder places each segment by host time, so this is the identity at a segment start.
    Inside a segment the device clock may drift: anchors (file frame, host time) reveal it,
    with linear interpolation between them. Without anchors, the identity.
    """

    MAX_SHIFT = 2.0   # larger means broken anchors: do not trust them

    def __init__(self, meta: dict, track: str):
        t0 = int(meta.get("t0_host_time", 0))
        ticks = float(meta.get("host_ticks_per_second") or 24_000_000)
        tr = (meta.get("tracks") or {}).get(track) or {}
        rate = float(tr.get("sample_rate") or 0)
        self.points: list[tuple[float, float]] = []   # (file seconds, seconds from t0)
        if not rate:
            return
        for seg in tr.get("segments", []):
            for a in seg.get("anchors", []):
                file_s = a["frame"] / rate
                true_s = (int(a["host_time"]) - t0) / ticks
                if abs(true_s - file_s) <= self.MAX_SHIFT:
                    self.points.append((file_s, true_s))
        self.points.sort()
        self._x = [p[0] for p in self.points]

    def __call__(self, t: float) -> float:
        if len(self.points) < 2:
            return t
        i = bisect_right(self._x, t) - 1
        i = max(0, min(i, len(self.points) - 2))
        (x0, y0), (x1, y1) = self.points[i], self.points[i + 1]
        if t < x0 and i == 0:
            return t + (y0 - x0)
        if x1 == x0:
            return t + (y0 - x0)
        return y0 + (t - x0) * (y1 - y0) / (x1 - x0)
