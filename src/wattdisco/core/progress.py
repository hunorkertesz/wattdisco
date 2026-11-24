"""Progress across rides, from the ride history lines (core/rides.py): fitness, fatigue and form,
the power curve, and how two power sources compare over weeks. Simulated rides are left out.

Fitness, fatigue and form follow the usual impulse-response model of training load:
- fitness: the daily training load averaged exponentially over 42 days (slow to build, slow to lose)
- fatigue: the same over 7 days (quick to rise, quick to fall)
- form: yesterday's fitness minus yesterday's fatigue, so a hard ride today shows tomorrow.
Every day counts, days without a ride as 0. Each day is a calendar day in local time.
"""

from datetime import date, datetime, timedelta

from wattdisco.core.stats import CURVE_SECONDS

FITNESS_DAYS = 42
FATIGUE_DAYS = 7
# What a form value means for the rider, from fresh to overreaching (upper bound, label).
FORM_BANDS = ((-30, "very tired"), (-10, "building"), (5, "steady"), (25, "fresh"), (float("inf"), "rested"))


def _day(row: dict) -> date:
    return datetime.fromisoformat(row["started"]).astimezone().date()


def real_rides(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r.get("sim") and r.get("duration")]


def form_label(form: float) -> str:
    return next(label for top, label in FORM_BANDS if form < top)


def fitness(rows: list[dict], today: date | None = None) -> dict:
    """Per day from the first ride to today: load, fitness, fatigue, form."""
    today = today or date.today()
    loads: dict[date, float] = {}
    for r in real_rides(rows):
        if r.get("load"):
            loads[_day(r)] = loads.get(_day(r), 0.0) + r["load"]
    if not loads:
        return {"days": [], "now": None}
    day, end = min(loads), max(today, max(loads))
    fit = fat = 0.0
    days = []
    while day <= end:
        form = fit - fat  # from yesterday, before today's ride
        load = loads.get(day, 0.0)
        fit += (load - fit) / FITNESS_DAYS
        fat += (load - fat) / FATIGUE_DAYS
        days.append({"date": day.isoformat(), "load": round(load, 1), "fitness": round(fit, 1),
                     "fatigue": round(fat, 1), "form": round(form, 1)})
        day += timedelta(days=1)
    last = days[-1]
    week_ago = days[-8] if len(days) > 7 else days[0]
    return {"days": days, "now": {**last, "label": form_label(last["form"]),
                                  "fitness_change_7d": round(last["fitness"] - week_ago["fitness"], 1)}}


def power_curve(rows: list[dict], since: date | None = None) -> list[dict]:
    """Best average power for each duration across rides (since a day, or ever), with the ride it came from."""
    best: dict[str, dict] = {}
    for r in real_rides(rows):
        if since and _day(r) < since:
            continue
        for seconds, watts in (r.get("bests") or {}).items():
            if seconds not in best or watts > best[seconds]["watts"]:
                best[seconds] = {"seconds": int(seconds), "watts": watts, "ride": r["id"], "date": r["started"],
                                 "name": r.get("name")}
    return [best[str(s)] for s in CURVE_SECONDS if str(s) in best]


# Critical power from the power curve. The 2-parameter model: the work you can do in t seconds is
# W = CP * t + W', a straight line through (t, best power * t). CP is the power you can hold for a
# long time (close to, a little above, FTP); W' is the work you can do above it before you run out.
CP_SECONDS = (180, 300, 600, 1200)  # 3 to 20 min: short enough to be all-out, long enough to be aerobic
CP_DAYS = 90
CP_ABOVE_FTP = 1.08  # CP usually sits about 5 % above FTP; this much higher suggests a new FTP test
CP_BELOW_FTP = 0.95  # this much lower: the bests were not all-out (FTP rarely drops that fast)
W_PRIME_RANGE = (3000, 50000)  # J; outside this the efforts were not all-out, so the fit means nothing
CP_MAX_MISS = 0.06  # every point within 6 % of the fitted curve


def critical_power(rows: list[dict], ftp: int, today: date | None = None) -> dict:
    """CP and W' from the best 3, 5, 10 and 20 min of the last 90 days, or why there is none."""
    today = today or date.today()
    curve = {p["seconds"]: p for p in power_curve(rows, today - timedelta(days=CP_DAYS))}
    points = [curve[s] for s in CP_SECONDS if s in curve]
    out = {"points": points, "days": CP_DAYS, "ftp": ftp}
    if len(points) < 3:
        return {**out, "status": "few", "message": "needs your best 3, 5, 10 and 20 minutes from the last 90 days"}
    ts = [p["seconds"] for p in points]
    ws = [p["watts"] * p["seconds"] for p in points]
    n = len(ts)
    mt, mw = sum(ts) / n, sum(ws) / n
    cp = sum((t - mt) * (w - mw) for t, w in zip(ts, ws)) / sum((t - mt) ** 2 for t in ts)
    w_prime = mw - cp * mt
    miss = max(abs((cp + w_prime / p["seconds"]) - p["watts"]) / p["watts"] for p in points)
    result = {**out, "cp": round(cp), "w_prime_kj": round(w_prime / 1000, 1), "miss_pct": round(100 * miss, 1),
              "ratio": round(cp / ftp, 3) if ftp else None}
    if not W_PRIME_RANGE[0] <= w_prime <= W_PRIME_RANGE[1] or miss > CP_MAX_MISS:
        return {**result, "status": "not_all_out",
                "message": "your 3–20 min bests don't look like all-out efforts (ERG intervals rarely are)"}
    if cp < ftp * CP_BELOW_FTP:
        return {**result, "status": "below_ftp",
                "message": f"CP is {round(100 * (1 - cp / ftp))} % under your FTP: your 3–20 min bests were probably "
                           "not all-out (ERG holds you at the target), so this says little about your FTP"}
    suggest = cp >= ftp * CP_ABOVE_FTP
    return {**result, "status": "ok", "suggest_test": suggest,
            "message": f"critical power is {round(100 * (cp / ftp - 1))} % above your FTP: time for a ramp test"
            if suggest else None}


def _slope_per_week(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of (day, value) in value per week; None with fewer than 3 rides or one day."""
    if len(points) < 3:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx == 0:
        return None
    return 7 * sum((x - mx) * (y - my) for x, y in points) / sxx


def sensor_trend(rows: list[dict]) -> list[dict]:
    """For each pair of power sources ridden together: their difference ride by ride, newest last.

    The pair keeps one direction (the first-seen main source is A), so a ride where the other one
    was main is flipped: the difference always reads "A minus B".
    """
    pairs: dict[frozenset, dict] = {}
    for r in sorted(real_rides(rows), key=lambda r: r["started"]):
        s = r.get("sources")
        if not s or s.get("mean_diff") is None or not s.get("a_id") or not s.get("b_id"):
            continue
        key = frozenset((s["a_id"], s["b_id"]))
        pair = pairs.setdefault(key, {"a_id": s["a_id"], "a_name": s["a_name"], "b_id": s["b_id"],
                                      "b_name": s["b_name"], "rides": []})
        flip = -1 if s["a_id"] != pair["a_id"] else 1
        pct = s.get("mean_diff_pct")
        if flip == -1 and pct is not None:  # A-B as a share of A instead of B: B/A - 1 = -(A-B)/B / (1+(A-B)/B)
            pct = 100 * (-(pct / 100) / (1 + pct / 100))
        pair["rides"].append({"ride": r["id"], "date": r["started"], "name": r.get("name"),
                              "diff_w": round(flip * s["mean_diff"], 1),
                              "diff_pct": None if pct is None else round(pct, 2),
                              "lag_s": None if s.get("lag_s") is None else round(flip * s["lag_s"], 2),
                              "correlation": s.get("correlation"), "seconds": s.get("seconds")})
    out = []
    for pair in pairs.values():
        rides = pair["rides"]
        pcts = [x["diff_pct"] for x in rides if x["diff_pct"] is not None]
        first = datetime.fromisoformat(rides[0]["date"])
        points = [((datetime.fromisoformat(x["date"]) - first).total_seconds() / 86400, x["diff_pct"])
                  for x in rides if x["diff_pct"] is not None]
        lags = [x["lag_s"] for x in rides if x["lag_s"] is not None]
        out.append({**pair, "count": len(rides),
                    "mean_pct": round(sum(pcts) / len(pcts), 2) if pcts else None,
                    "latest_pct": pcts[-1] if pcts else None,
                    "spread_pct": round(max(pcts) - min(pcts), 2) if pcts else None,
                    "trend_pct_per_week": None if (t := _slope_per_week(points)) is None else round(t, 2),
                    "mean_lag_s": round(sum(lags) / len(lags), 2) if lags else None})
    return sorted(out, key=lambda p: -p["count"])


def best_climbs(rows: list[dict]) -> list[dict]:
    """Your best time up each climb you have ridden: one line per climb, the one ridden most recently first.

    Only real rides that reached the top set a time; every ride up it counts as an attempt."""
    climbs: dict[str, dict] = {}
    for row in real_rides(rows):
        if not row.get("climb") or not row.get("workout_id"):
            continue
        c = climbs.setdefault(row["workout_id"], {"workout_id": row["workout_id"], "name": row.get("name"),
                                                  "attempts": 0, "tops": 0, "last": row["started"], "best": None})
        c["attempts"] += 1
        if row["started"] > c["last"]:
            c["last"], c["name"] = row["started"], row.get("name")
        t = row.get("climb_time") or {}
        if not (t.get("top") and t.get("seconds")):
            continue
        c["tops"] += 1
        if c["best"] is None or t["seconds"] < c["best"]["seconds"]:
            c["best"] = {"id": row["id"], "started": row["started"], "seconds": t["seconds"], "vam": t.get("vam"),
                         "w_per_kg": t.get("w_per_kg"), "avg_power": t.get("avg_power"), "difficulty": t.get("difficulty")}
    return sorted(climbs.values(), key=lambda c: c["last"], reverse=True)
