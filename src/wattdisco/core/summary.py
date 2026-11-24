"""Everything the ride summary screen shows, computed from a recorded ride."""

from collections import Counter

from wattdisco.config import DEFAULT_WEIGHT_KG, HR_METHODS, hr_zone_tops, hr_zones
from wattdisco.core import hrv, stats
from wattdisco.core.recorder import RideData
from wattdisco.core.speed import virtual_ride

# Bump when the summary gains or changes fields, so cached summaries and .fit files are rebuilt.
SUMMARY_VERSION = 13  # 10: left/right balance; 11: climbs (recorded distance, height); 12: climb power, W/kg, speed; 13: HR drift halves


def build_summary(ride: RideData) -> dict:
    meta = ride.meta
    ftp = meta["ftp"]
    ticks = ride.ticks
    power = [t.get("power") for t in ticks]
    hr = [t.get("hr") for t in ticks]
    target = [t.get("target") for t in ticks]
    cadence = [c for c in (t.get("cadence") for t in ticks) if c]
    seconds = len(ticks)  # moving time: no ticks are written while paused
    span = int(ticks[-1]["t"]) if ticks else 0  # ride time including pauses

    # Estimated flat-road speed and distance (no real speed sensor indoors); a climb recorded its own on the road.
    climbing = bool(meta["workout"].get("climb")) and any(t.get("distance") is not None for t in ticks)
    if climbing:
        speeds, distances, last = [], [], 0.0
        for t in ticks:
            last = t.get("distance") if t.get("distance") is not None else last
            speeds.append(t.get("speed") or 0.0)
            distances.append(last)
    else:
        speeds, distances = virtual_ride(power, meta.get("weight_kg") or DEFAULT_WEIGHT_KG)
    distance = distances[-1] if distances else 0.0

    avg_power = stats.mean(power)
    wp = stats.weighted_power(power)

    intervals = []
    pos = 0  # index of the group's first tick in `ticks`
    for group in stats.split_phases(ticks):
        first = group[0]
        end = pos + len(group)
        before = distances[pos - 1] if pos else 0.0
        lap_distance = distances[end - 1] - before
        lap_speed = {
            "distance": lap_distance,
            "avg_speed": lap_distance / len(group),
            "max_speed": max(speeds[pos:end]),
        }
        pos = end
        kind = first.get("kind") or "free"
        intervals.append(
            {
                "index": first.get("index"),
                "kind": kind,
                "work": kind not in stats.REST_KINDS,
                "start_t": first["t"] - 1,  # a tick at t covers the second before it
                "end_t": group[-1]["t"],
                "start_elapsed": first.get("elapsed"),
                **stats.interval_stats(group),
                **lap_speed,
            }
        )
    work_ticks = [t for t in ticks if (t.get("kind") or "free") not in stats.REST_KINDS]

    return {
        "version": SUMMARY_VERSION,
        "id": ride.id,
        "name": meta["workout"]["name"],
        "started": meta["started"],
        "ftp": ftp,
        "weight_kg": meta.get("weight_kg"),
        "sim": meta.get("sim", False),
        "duration": seconds,
        "elapsed": span,
        "paused": max(0, span - seconds),
        "basic": {
            "avg_power": avg_power,
            "max_power": stats.maximum(power),
            "avg_hr": stats.mean(hr),
            "max_hr": stats.maximum(hr),
            "avg_cadence": stats.mean(cadence),
            "max_cadence": stats.maximum(cadence),
            "work_kj": sum(stats.present(power)) / 1000,
            "distance_km": distance / 1000,
            "avg_speed_kmh": distance / seconds * 3.6 if seconds else None,
            "max_speed_kmh": max(speeds) * 3.6 if speeds else None,
        },
        "nerd": {
            "wp": wp,
            "intensity": stats.intensity(wp, ftp),
            "load": stats.training_load(seconds, wp, ftp),
            "vi": stats.variability_index(wp, avg_power),
            "on_target_pct": stats.on_target_pct(power, target),
            "hr_drift_pct": stats.hr_drift_pct(power, hr),
        },
        "hr_drift": stats.hr_drift(power, hr),
        "zones": stats.time_in_zones(power, ftp),
        "hr_zones": _hr_zones(meta, hr),
        "deviation": stats.deviation(power, target),
        "bests": stats.best_efforts(power),
        "dfa": _dfa(ride, span + 1),
        "climb": _climb(meta, ticks) if climbing else None,
        "balance": _balance(ride, intervals),
        "ftp_estimate": stats.ftp_estimate(power, [t.get("kind") for t in ticks], meta["workout"].get("test"), ftp),
        "intervals": intervals,
        "work_all": stats.interval_stats(work_ticks) if work_ticks else None,
        "sources": _sources(ride, span + 1),
        "workout": meta["workout"],
        "workout_id": meta.get("workout_id"),
        "series": {
            "elapsed": [t.get("elapsed") for t in ticks],
            "power": power,
            "hr": hr,
            "target": target,
            "speed": [round(v, 3) for v in speeds],  # m/s, estimated
            "distance": [round(d, 1) for d in distances],  # m, estimated
            **({"altitude": [t.get("altitude") for t in ticks], "grade": [t.get("grade") for t in ticks]} if climbing else {}),
        },
    }


def _climb(meta: dict, ticks: list[dict]) -> dict:
    """A climb's numbers: how far and how high, whether the top was reached and how fast."""
    from wattdisco.core.climb import Profile  # here: the summary needs it only for climbs

    profile = Profile.from_segments(meta["workout"]["climb"]["segments"])
    top = next((i for i, t in enumerate(ticks) if (t.get("distance") or 0) >= profile.length - 0.5), None)
    seconds = top + 1 if top is not None else len(ticks)  # moving seconds to the top (or all that was ridden)
    climbed = profile.ascent_at(max((t.get("distance") or 0) for t in ticks))
    up = ticks[:seconds]  # the way up (all that was ridden, if the top was not reached)
    power = stats.mean([t.get("power") for t in up])
    weight = meta.get("weight_kg")
    ridden = max((t.get("distance") or 0) for t in up) if up else 0.0
    return {
        "length": round(profile.length, 1),
        "ascent": round(profile.total_ascent, 1),
        "climbed": round(climbed, 1),
        "avg_grade": round(profile.avg_grade, 1),
        "top": top is not None,
        "seconds": seconds,
        "vam": round(climbed / seconds * 3600) if seconds else None,  # metres climbed per hour
        "difficulty": meta.get("climb_difficulty", 1.0),
        "distance": round(ridden, 1),
        "avg_power": None if power is None else round(power),
        "w_per_kg": None if power is None or not weight else round(power / weight, 2),
        "avg_speed": round(ridden / seconds, 2) if seconds else None,  # m/s
    }


def _left_share(samples: list[tuple[float, float, int]]) -> float | None:
    """The left pedal's share of the work, %: each sample weighted by its power."""
    work = sum(w for _, _, w in samples if w > 0)
    return round(sum(b * w for _, b, w in samples if w > 0) / work, 1) if work else None


def _balance(ride: RideData, intervals: list[dict]) -> dict | None:
    """Left/right balance from a dual-sided power meter: the ride, its halves and each work interval."""
    if not ride.balance:
        return None
    mains = Counter(t.get("main_power_id") for t in ride.ticks if t.get("main_power_id"))
    device = next((d for d, _ in mains.most_common() if d in ride.balance), None) or max(ride.balance, key=lambda d: len(ride.balance[d]))
    samples = ride.balance[device]
    left = _left_share(samples)
    if left is None:
        return None
    mid = (samples[0][0] + samples[-1][0]) / 2
    per_interval = []
    for iv in intervals:
        if not iv["work"]:
            continue
        part = [s for s in samples if iv["start_t"] <= s[0] < iv["end_t"]]
        share = _left_share(part)
        if share is not None:
            per_interval.append({"index": iv["index"], "kind": iv["kind"], "left": share, "right": round(100 - share, 1)})
    first, second = _left_share([s for s in samples if s[0] < mid]), _left_share([s for s in samples if s[0] >= mid])
    return {"device_id": device, "device": ride.device_name(device), "left": left, "right": round(100 - left, 1),
            "first_half_left": first, "second_half_left": second,
            "drift": None if first is None or second is None else round(second - first, 1),
            "intervals": per_interval}


def _dfa(ride: RideData, seconds: int) -> dict | None:
    """DFA alpha 1 windows and the aerobic threshold from the main strap's RR intervals (core/hrv.py)."""
    power = [None] * seconds  # by ride time, like the RR samples (pauses leave gaps)
    for t in ride.ticks:
        i = int(t["t"]) - 1
        if 0 <= i < seconds:
            power[i] = t.get("power")
    return hrv.dfa_ride(ride, power)


def _hr_zones(meta: dict, hr: list) -> dict:
    """Time in heart rate zones, with the zones as they were set when the ride started."""
    z = meta.get("hr_zones") or {"method": "max", "reference": HR_METHODS["max"]["default"]}
    tops = hr_zone_tops(z["method"], z["reference"])
    return {
        "method": z["method"],
        "reference": z["reference"],
        "zones": [{**zone, **t} for zone, t in zip(hr_zones(z["method"], z["reference"]), stats.time_in_hr_zones(hr, tops))],
    }


def _sources(ride: RideData, seconds: int) -> dict | None:
    """Main power source against the next one, if the ride had two."""
    mains = Counter(t.get("main_power_id") for t in ride.ticks if t.get("main_power_id"))
    if not mains or len(ride.power) < 2:
        return None
    a = mains.most_common(1)[0][0]
    b = next((d for d in ride.power if d != a), None)
    if a not in ride.power or b is None:
        return None
    diff = stats.source_difference(stats.per_second(ride.power[a], seconds), stats.per_second(ride.power[b], seconds))
    if diff is None:
        return None
    return {"a_id": a, "a_name": ride.device_name(a), "b_id": b, "b_name": ride.device_name(b), **diff}
