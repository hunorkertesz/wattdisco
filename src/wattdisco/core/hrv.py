"""Heart rate variability during a ride: DFA alpha 1 and the aerobic threshold it points to.

DFA (detrended fluctuation analysis) alpha 1 measures how the beat-to-beat (RR) intervals are
correlated over short scales of 4 to 16 beats. At easy intensity the rhythm is complex, alpha 1
is around 1 or higher. As intensity rises it falls towards 0.5 (uncorrelated). The point where
alpha 1 crosses 0.75 lines up with the first ventilatory threshold, the aerobic threshold (Rogers
et al. 2021). It needs a chest strap that sends RR intervals, a clean signal and a ride that goes
from easy to harder (a warm-up into intervals, a ramp).

How it is computed here:
1. RR intervals of the main heart rate strap. Beats outside 300–2000 ms, or more than 20 % off
   the previous good beat, are artefacts and dropped.
2. Two-minute windows, one every 20 s. A window counts when it has at least 100 beats and under
   5 % artefacts. Per window: alpha 1, the average heart rate and the average power.
3. Alpha 1: integrate the RR series (cumulative sum of RR minus its mean), cut it into boxes of
   n = 4 … 16 beats, remove a straight-line trend from each box, and take F(n), the root mean
   square of what is left. Alpha 1 is the slope of log F(n) against log n.
4. Aerobic threshold: a straight line through alpha 1 against heart rate (and against power) over
   the windows; the heart rate and power where that line is at 0.75. Only given when the ride has
   windows on both sides of 0.75 and the line is clear (correlation below -0.5).
"""

import math
from collections import Counter

RR_RANGE = (300, 2000)  # ms
ARTEFACT_JUMP = 0.20
WINDOW_S = 120
STEP_S = 20
MIN_BEATS = 100
MAX_ARTEFACTS = 0.05
BOX_SIZES = range(4, 17)
THRESHOLD = 0.75
MIN_WINDOWS = 6
MAX_CORRELATION = -0.5


def alpha1(rr: list[float]) -> float | None:
    """DFA alpha 1 of a list of RR intervals (ms)."""
    if len(rr) < 2 * max(BOX_SIZES):
        return None
    mean = sum(rr) / len(rr)
    y, acc = [], 0.0
    for x in rr:
        acc += x - mean
        y.append(acc)
    logs = []
    for n in BOX_SIZES:
        # least-squares line through (0..n-1, box) per box; x sums are the same for every box
        sx = n * (n - 1) / 2
        sxx = (n - 1) * n * (2 * n - 1) / 6
        den = n * sxx - sx * sx
        total, count = 0.0, 0
        for start in range(0, len(y) - n + 1, n):
            box = y[start:start + n]
            sy = sum(box)
            sxy = sum(i * v for i, v in enumerate(box))
            b = (n * sxy - sx * sy) / den
            a = (sy - b * sx) / n
            total += sum((v - (a + b * i)) ** 2 for i, v in enumerate(box))
            count += n
        f = math.sqrt(total / count) if count else 0.0
        if f <= 0:
            return None
        logs.append((math.log(n), math.log(f)))
    mx = sum(x for x, _ in logs) / len(logs)
    my = sum(v for _, v in logs) / len(logs)
    return sum((x - mx) * (v - my) for x, v in logs) / sum((x - mx) ** 2 for x, _ in logs)


def beats(samples: list[tuple[float, list[float]]]) -> list[tuple[float, float, bool]]:
    """(time, RR ms, good) for each beat, from (sample time, RR seconds) heart rate samples."""
    out, last_good = [], None
    for t, rr_list in samples:
        for rr_s in rr_list:
            rr = rr_s * 1000
            good = RR_RANGE[0] <= rr <= RR_RANGE[1] and (last_good is None or abs(rr - last_good) <= ARTEFACT_JUMP * last_good)
            if good:
                last_good = rr
            elif RR_RANGE[0] <= rr <= RR_RANGE[1] and last_good is not None and out and not out[-1][2]:
                last_good = rr  # two odd beats in a row: the rhythm changed, follow it
            out.append((t, rr, good))
    return out


def _fit(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    """Slope, intercept and correlation of y on x."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx, my - sxy / sxx * mx, sxy / math.sqrt(sxx * syy)


def dfa_ride(ride, power_by_second: list[int | None]) -> dict | None:
    """Alpha 1 windows and the aerobic threshold estimate for a recorded ride, or None without RR data."""
    mains = Counter(t.get("main_hr_id") for t in ride.ticks if t.get("main_hr_id"))
    device = mains.most_common(1)[0][0] if mains else next(iter(ride.rr), None)
    samples = ride.rr.get(device) if device else None
    if not samples:
        return None
    all_beats = beats(samples)
    if len(all_beats) < MIN_BEATS:
        return None
    windows = []
    end = all_beats[-1][0]
    start, i0 = all_beats[0][0], 0
    while start + WINDOW_S <= end + 1e-9:
        while i0 < len(all_beats) and all_beats[i0][0] < start:
            i0 += 1
        i1 = i0
        while i1 < len(all_beats) and all_beats[i1][0] < start + WINDOW_S:
            i1 += 1
        win = all_beats[i0:i1]
        good = [rr for _, rr, ok in win if ok]
        artefacts = 1 - len(good) / len(win) if win else 1
        if len(good) >= MIN_BEATS and artefacts < MAX_ARTEFACTS:
            a = alpha1(good)
            if a is not None:
                watts = [p for p in power_by_second[int(start):int(start + WINDOW_S)] if p is not None]
                windows.append({"t": round(start + WINDOW_S / 2), "alpha1": round(a, 3),
                                "hr": round(60000 / (sum(good) / len(good)), 1),
                                "power": round(sum(watts) / len(watts)) if watts else None,
                                "artefacts_pct": round(100 * artefacts, 1)})
        start += STEP_S
    bad = sum(1 for *_, ok in all_beats if not ok)
    out = {"device_id": device, "device": ride.device_name(device), "beats": len(all_beats),
           "artefacts_pct": round(100 * bad / len(all_beats), 1), "windows": windows,
           "mean_alpha1": round(sum(w["alpha1"] for w in windows) / len(windows), 3) if windows else None,
           "threshold": None, "reason": None}
    out["reason"] = _threshold(windows, out)
    return out


def _threshold(windows: list[dict], out: dict) -> str | None:
    """Fill out["threshold"] when the ride shows it; else say why not."""
    if len(windows) < MIN_WINDOWS:
        return "too few clean two-minute windows (a chest strap with RR data and steady contact helps)"
    alphas = [w["alpha1"] for w in windows]
    if min(alphas) > THRESHOLD:
        return "alpha 1 stayed above 0.75: the whole ride was below your aerobic threshold"
    if max(alphas) < THRESHOLD:
        return "alpha 1 stayed below 0.75: the whole ride was above your aerobic threshold"
    hr_fit = _fit([w["hr"] for w in windows], alphas)
    if not hr_fit or hr_fit[0] >= 0 or hr_fit[2] > MAX_CORRELATION:
        return "alpha 1 did not fall clearly as heart rate rose (a ride from easy to hard shows it best)"
    slope, intercept, r = hr_fit
    hr = (THRESHOLD - intercept) / slope
    hrs = [w["hr"] for w in windows]
    if not min(hrs) <= hr <= max(hrs):
        return "the 0.75 crossing lies outside the heart rates of this ride"
    with_power = [w for w in windows if w["power"]]
    watts = None
    p_fit = _fit([w["power"] for w in with_power], [w["alpha1"] for w in with_power]) if len(with_power) >= MIN_WINDOWS else None
    if p_fit and p_fit[0] < 0 and p_fit[2] <= MAX_CORRELATION:
        watts = (THRESHOLD - p_fit[1]) / p_fit[0]
        ps = [w["power"] for w in with_power]
        watts = round(watts) if min(ps) <= watts <= max(ps) else None
    out["threshold"] = {"hr": round(hr), "power": watts, "correlation": round(r, 2), "windows": len(windows)}
    return None
