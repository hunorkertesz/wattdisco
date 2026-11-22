"""Ride statistics. Pure functions on 1 Hz series, where None means no value that second.

Power load numbers (Watt Disco's own names):
  weighted power   fourth-power mean of the 30 s rolling average power
  intensity        weighted power / FTP
  training load    hours × intensity² × 100 (1 h at FTP = 100)
  variability      weighted power / average power
  on target   |power − target| ≤ 5 % of target
  HR drift    power:HR ratio of the first half vs the second half, in %
"""

import math
from collections.abc import Iterable, Sequence

from wattdisco.config import HR_ZONE_NAMES, ZONE_NAMES, hr_zone_of, zone_of

ON_TARGET_BAND = 0.05
WEIGHTED_WINDOW = 30
MIN_DRIFT_HALF = 120  # seconds of data needed in each half for HR drift
MIN_OVERLAP = 30  # seconds two power sources must overlap to be compared
REST_KINDS = {"warmup", "cooldown", "recovery", "free"}


def present(xs: Iterable) -> list:
    return [x for x in xs if x is not None]


def mean(xs: Iterable) -> float | None:
    v = present(xs)
    return sum(v) / len(v) if v else None


def maximum(xs: Iterable):
    v = present(xs)
    return max(v) if v else None


def weighted_power(power: Sequence[int | None]) -> float | None:
    p = present(power)
    if len(p) < WEIGHTED_WINDOW:
        return mean(p)
    window = sum(p[:WEIGHTED_WINDOW])
    fourth = [(window / WEIGHTED_WINDOW) ** 4]
    for i in range(WEIGHTED_WINDOW, len(p)):
        window += p[i] - p[i - WEIGHTED_WINDOW]
        fourth.append((window / WEIGHTED_WINDOW) ** 4)
    return (sum(fourth) / len(fourth)) ** 0.25


def intensity(wp: float | None, ftp: int) -> float | None:
    return None if wp is None else wp / ftp


def training_load(seconds: int, wp: float | None, ftp: int) -> float | None:
    if wp is None or not seconds:
        return None
    return seconds / 3600 * (wp / ftp) ** 2 * 100


def variability_index(wp: float | None, avg: float | None) -> float | None:
    return None if wp is None or not avg else wp / avg


def on_target_pct(power: Sequence, target: Sequence, band: float = ON_TARGET_BAND) -> float | None:
    pairs = [(p, t) for p, t in zip(power, target) if p is not None and t]
    if not pairs:
        return None
    return 100 * sum(abs(p - t) <= band * t for p, t in pairs) / len(pairs)


def hr_drift_pct(power: Sequence, hr: Sequence) -> float | None:
    """Positive when heart rate rose relative to power in the second half."""
    pairs = [(p, h) for p, h in zip(power, hr) if p and h]
    half = len(pairs) // 2
    if half < MIN_DRIFT_HALF:
        return None
    first, second = pairs[:half], pairs[half:]
    r1 = mean(p for p, _ in first) / mean(h for _, h in first)
    r2 = mean(p for p, _ in second) / mean(h for _, h in second)
    return (r1 - r2) / r1 * 100


def time_in_zones(power: Sequence, ftp: int) -> list[dict]:
    seconds = [0] * len(ZONE_NAMES)
    for p in present(power):
        seconds[zone_of(p, ftp) - 1] += 1
    return [{"n": i + 1, "name": name, "seconds": s} for i, (name, s) in enumerate(zip(ZONE_NAMES, seconds))]


def time_in_hr_zones(hr: Sequence, tops: list[int]) -> list[dict]:
    seconds = [0] * len(HR_ZONE_NAMES)
    for h in present(hr):
        seconds[hr_zone_of(h, tops) - 1] += 1
    return [{"n": i + 1, "name": name, "seconds": s} for i, (name, s) in enumerate(zip(HR_ZONE_NAMES, seconds))]


def deviation(power: Sequence, target: Sequence, band: float = ON_TARGET_BAND) -> dict | None:
    """How far power was from the target, over the seconds that had a target."""
    pairs = [(p, t) for p, t in zip(power, target) if p is not None and t]
    if not pairs:
        return None
    diffs = [p - t for p, t in pairs]
    pcts = [100 * (p - t) / t for p, t in pairs]
    m = mean(diffs)
    above = sum(p > t * (1 + band) for p, t in pairs)
    below = sum(p < t * (1 - band) for p, t in pairs)
    # longest run of seconds outside the band
    longest = run = 0
    for p, t in pairs:
        run = run + 1 if abs(p - t) > band * t else 0
        longest = max(longest, run)
    n = len(pairs)
    return {
        "seconds": n,
        "mean_w": m,
        "mean_pct": mean(pcts),
        "sd_w": math.sqrt(sum((d - m) ** 2 for d in diffs) / n),
        "mean_abs_w": mean(abs(d) for d in diffs),
        "above_pct": 100 * above / n,
        "below_pct": 100 * below / n,
        "within_pct": 100 * (n - above - below) / n,
        "max_over_w": max(diffs),
        "max_under_w": min(diffs),
        "longest_off_s": longest,
    }


def best_average(power: Sequence, seconds: int) -> float | None:
    """Best average power over any `seconds` in a row (a missing second counts as 0 W)."""
    values = [p or 0 for p in power]
    if seconds <= 0 or len(values) < seconds:
        return None
    window = best = sum(values[:seconds])
    for i in range(seconds, len(values)):
        window += values[i] - values[i - seconds]
        best = max(best, window)
    return best / seconds


# Durations of the power curve, in seconds. 5 s, 1 min, 5 min and 20 min are the headline ones.
CURVE_SECONDS = (1, 5, 15, 30, 60, 120, 180, 300, 600, 1200, 1800, 3600)
KEY_EFFORTS = (5, 60, 300, 1200)


def best_efforts(power: Sequence) -> dict[str, float]:
    """Best average power for each curve duration the ride is long enough for: {"5": 612.4, ...}."""
    out = {}
    for seconds in CURVE_SECONDS:
        best = best_average(power, seconds)
        if best is not None and best > 0:
            out[str(seconds)] = round(best, 1)
    return out


# FTP from a ride: (seconds of the best effort, share of it that is FTP)
FTP_RULES = {
    "ramp": (60, 0.75),  # ramp test: 75 % of the best minute
    "20min": (1200, 0.95),  # 20 min test, and any ride: 95 % of the best 20 minutes
}
RAMP_MIN_TEST_S = 240  # a ramp test needs this many seconds of steps to count
TEST_20_MIN_S = 1140  # a 20 min test stopped before this is not a test result


def ftp_estimate(power: Sequence, kinds: Sequence, test: str | None, ftp: int) -> dict | None:
    """New FTP from an FTP test, or from the best 20 minutes of any ride when that beats `ftp`.

    `kinds` is the phase kind of each second. Returns None when the ride says nothing new.
    """
    tested = sum(k == "test" for k in kinds)
    if test == "20min" and tested < TEST_20_MIN_S:
        test = None  # stopped early: only the best 20 minutes, and only if they beat the FTP
    if test == "ramp" and tested < RAMP_MIN_TEST_S:
        return None
    seconds, share = FTP_RULES[test or "20min"]
    if test == "ramp":
        power = [p if k == "test" else None for p, k in zip(power, kinds)]
    best = best_average(power, seconds)
    if best is None:
        return None
    new = round(best * share)
    if not test and new <= ftp:
        return None
    return {"source": test or "best20", "seconds": seconds, "best_w": best, "share": share,
            "ftp": new, "ftp_before": ftp, "change": new - ftp}


def per_second(samples: Sequence[tuple[float, float]], seconds: int) -> list[float | None]:
    """Average raw samples into whole seconds 0 … seconds-1."""
    sums = [0.0] * seconds
    counts = [0] * seconds
    for t, v in samples:
        i = math.floor(t)
        if 0 <= i < seconds and v is not None:
            sums[i] += v
            counts[i] += 1
    return [s / c if c else None for s, c in zip(sums, counts)]


def pearson(xs: Sequence, ys: Sequence) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    mx = mean(x for x, _ in pairs)
    my = mean(y for _, y in pairs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else None


def detrend(xs: Sequence) -> list[float | None]:
    """Remove the straight-line trend, so a steady climb (warm-up HR) does not hide the lag."""
    points = [(i, x) for i, x in enumerate(xs) if x is not None]
    if len(points) < 3:
        return list(xs)
    mi = mean(i for i, _ in points)
    mx = mean(x for _, x in points)
    sii = sum((i - mi) ** 2 for i, _ in points)
    slope = sum((i - mi) * (x - mx) for i, x in points) / sii if sii else 0.0
    return [None if x is None else x - (mx + slope * (i - mi)) for i, x in enumerate(xs)]


def source_difference(a: Sequence, b: Sequence, max_lag: int = 5) -> dict | None:
    """Compare two 1 Hz series of the same quantity (power or heart rate).

    lag_s > 0 means b reports later than a. It is found on detrended series and
    refined below one second by fitting a parabola through the correlation peak.
    The reported correlation is on the raw values at that lag.
    """
    both = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(both) < MIN_OVERLAP:
        return None
    mean_diff = mean(x - y for x, y in both)
    mean_b = mean(y for _, y in both)

    def corr_at(x: Sequence, y: Sequence, k: int) -> float | None:
        if k >= 0:
            return pearson(x[: len(x) - k], y[k:])
        return pearson(x[-k:], y[: len(y) + k])

    da, db = detrend(a), detrend(b)
    corr = {k: corr_at(da, db, k) for k in range(-max_lag, max_lag + 1)}
    best = max((k for k in corr if corr[k] is not None), key=lambda k: corr[k], default=None)
    lag = None
    if best is not None:
        lag = float(best)
        c0, cm, cp = corr[best], corr.get(best - 1), corr.get(best + 1)
        if cm is not None and cp is not None and (cm - 2 * c0 + cp) != 0:
            lag += (cm - cp) / (2 * (cm - 2 * c0 + cp))
    return {
        "mean_diff": mean_diff,
        "mean_diff_pct": None if not mean_b else 100 * mean_diff / mean_b,
        "lag_s": lag,
        "correlation": None if best is None else corr_at(a, b, best),
        "seconds": len(both),
    }


def interval_stats(ticks: Sequence[dict]) -> dict:
    """Stats for a stretch of 1 Hz ticks (one workout phase, or all work phases)."""
    power = [t.get("power") for t in ticks]
    target = [t.get("target") for t in ticks]
    cadence = [c for c in (t.get("cadence") for t in ticks) if c]  # pedalling only
    hr = [t.get("hr") for t in ticks]
    avg = mean(power)
    tgt = mean(target)
    diff = None if avg is None or tgt is None else avg - tgt
    return {
        "seconds": len(ticks),
        "target": None if tgt is None else round(tgt),
        "avg_power": avg,
        "max_power": maximum(power),
        "wp": weighted_power(power) if len(present(power)) >= WEIGHTED_WINDOW else None,
        "diff": diff,
        "diff_pct": None if diff is None or not tgt else 100 * diff / tgt,
        "on_target_pct": on_target_pct(power, target),
        "avg_hr": mean(hr),
        "max_hr": maximum(hr),
        "avg_cadence": mean(cadence),
        "max_cadence": maximum(cadence),
    }


def split_phases(ticks: Sequence[dict]) -> list[list[dict]]:
    """Consecutive ticks with the same workout phase index."""
    groups: list[list[dict]] = []
    for tick in ticks:
        if groups and groups[-1][-1].get("index") == tick.get("index"):
            groups[-1].append(tick)
        else:
            groups.append([tick])
    return groups
