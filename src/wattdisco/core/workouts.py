"""Workout library: built-in workouts plus the rider's own, stored in data/workouts/.

Workouts are stored in % of FTP (`pct` = 0.9 means 90 %), so they follow the rider
when FTP changes. `to_workout` turns one into watts for a given FTP.
"""

import functools
import json
import math
import re
import secrets
from collections import Counter
from pathlib import Path

from wattdisco.config import DEFAULT_FTP
from wattdisco.core import climb, stats
from wattdisco.models import Phase, Workout

KINDS = ("warmup", "endurance", "tempo", "sweetspot", "threshold", "vo2", "sprint", "recovery", "cooldown", "test")
TESTS = {"ramp", "20min"}  # FTP tests: the ride's result sets the FTP
REST_KINDS = {"warmup", "cooldown", "recovery"}
WORKOUT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_PHASES = 100
CATEGORIES = {
    "endurance": "Endurance",
    "tempo": "Tempo",
    "sweetspot": "Sweet spot",
    "threshold": "Threshold",
    "vo2": "VO2 max",
    "sprint": "Sprints",
    "strength": "Strength",
    "test": "FTP tests",
    "climb": "Climbs",
    "ride": "Just ride",
    "custom": "My workouts",
}

# What a new phase of each type starts as in the editor: (seconds, % FTP, ramp end % FTP or None, cadence)
PHASE_DEFAULTS = {
    "warmup": (600, 0.40, 0.70, 85),
    "endurance": (1200, 0.65, None, 88),
    "tempo": (900, 0.82, None, 90),
    "sweetspot": (480, 0.90, None, 90),
    "threshold": (480, 1.00, None, 90),
    "vo2": (180, 1.15, None, 100),
    "sprint": (15, 2.00, None, 110),
    "recovery": (180, 0.50, None, 85),
    "cooldown": (600, 0.60, 0.40, 85),
    "test": (1200, 1.00, None, 90),
}


def _phase(kind: str, seconds: int | None = None, pct: float | None = None, pct_end: float | None = None,
           cadence: int | None = None) -> dict:
    d_seconds, d_pct, d_end, d_cadence = PHASE_DEFAULTS[kind]
    return {
        "kind": kind,
        "seconds": seconds or d_seconds,
        "pct": d_pct if pct is None else pct,
        "pct_end": pct_end if pct is not None else d_end,
        "cadence": cadence or d_cadence,
    }


def _repeat(n: int, *phases: dict) -> list[dict]:
    return [dict(p) for _ in range(n) for p in phases]


WARMUP = _phase("warmup", 600)
COOLDOWN = _phase("cooldown", 600, 0.60, 0.40)


def _w(category: str, name: str, *phases: dict, test: str | None = None, mode: str | None = None) -> dict:
    spec = {"name": name, "category": category, "phases": [dict(p) for p in phases]}
    if test:
        spec["test"] = test
    if mode:
        spec["mode"] = mode  # suggested on the Ready screen
    return spec


RAMP_START, RAMP_STEP, RAMP_STEPS = 0.50, 0.06, 25  # 1 min steps from 50 % FTP, +6 % FTP each


BUILTIN = {
    # ---------- endurance ----------
    "endurance-45": _w("endurance", "Endurance 45",
        _phase("warmup", 300, 0.45, 0.65), _phase("endurance", 2100, 0.68), _phase("cooldown", 300, 0.60, 0.40)),
    "endurance-60-cadence": _w("endurance", "Endurance 60 · cadence drills",
        WARMUP, *_repeat(4, _phase("endurance", 510, 0.68, cadence=88), _phase("endurance", 90, 0.68, cadence=105)),
        COOLDOWN),
    "endurance-90": _w("endurance", "Endurance 90", WARMUP, _phase("endurance", 4200, 0.67), COOLDOWN),
    "endurance-2h": _w("endurance", "Endurance 2 h", WARMUP, _phase("endurance", 6000, 0.65), COOLDOWN),
    "easy-spin-30": _w("endurance", "Easy spin 30",
        _phase("warmup", 300, 0.40, 0.55), _phase("endurance", 1200, 0.55, cadence=92),
        _phase("cooldown", 300, 0.55, 0.40)),
    "endurance-60-tempo-finish": _w("endurance", "Endurance 60 · tempo finish",
        WARMUP, _phase("endurance", 1800, 0.68), _phase("tempo", 600, 0.82), COOLDOWN),
    # ---------- tempo ----------
    "tempo-3x10": _w("tempo", "Tempo 3 × 10",
        WARMUP, *_repeat(3, _phase("tempo", 600, 0.82), _phase("recovery", 300, 0.55)), COOLDOWN),
    "tempo-2x20": _w("tempo", "Tempo 2 × 20",
        WARMUP, *_repeat(2, _phase("tempo", 1200, 0.80), _phase("recovery", 300, 0.55)), COOLDOWN),
    "tempo-45": _w("tempo", "Tempo 45 steady", WARMUP, _phase("tempo", 2700, 0.78), COOLDOWN),
    "tempo-4x12": _w("tempo", "Tempo 4 × 12",
        WARMUP, *_repeat(4, _phase("tempo", 720, 0.85), _phase("recovery", 240, 0.55)), COOLDOWN),
    # ---------- sweet spot ----------
    "sweet-spot-3x4": _w("sweetspot", "Sweet spot 3 × 4",
        _phase("warmup", 300),
        *_repeat(3, _phase("sweetspot", 240, 0.90), _phase("recovery", 120, 0.50)),
        _phase("cooldown", 300, 0.60, 0.40)),
    "sweet-spot-4x8": _w("sweetspot", "Sweet spot 4 × 8",
        WARMUP, *_repeat(4, _phase("sweetspot", 480, 0.92), _phase("recovery", 180, 0.50)),
        _phase("cooldown", 420, 0.60, 0.40)),
    "sweet-spot-3x12": _w("sweetspot", "Sweet spot 3 × 12",
        WARMUP, *_repeat(3, _phase("sweetspot", 720, 0.90), _phase("recovery", 300, 0.50)), COOLDOWN),
    "sweet-spot-2x20": _w("sweetspot", "Sweet spot 2 × 20",
        WARMUP, *_repeat(2, _phase("sweetspot", 1200, 0.89), _phase("recovery", 360, 0.50)), COOLDOWN),
    "sweet-spot-3x15": _w("sweetspot", "Sweet spot 3 × 15",
        WARMUP, *_repeat(3, _phase("sweetspot", 900, 0.90), _phase("recovery", 300, 0.50)), COOLDOWN),
    "sweet-spot-over-unders": _w("sweetspot", "Sweet spot over-unders 3 × 12",
        WARMUP,
        *_repeat(3, *_repeat(4, _phase("sweetspot", 120, 0.88), _phase("threshold", 60, 0.97)),
                 _phase("recovery", 300, 0.50)),
        COOLDOWN),
    # ---------- threshold ----------
    "threshold-2x15": _w("threshold", "Threshold 2 × 15",
        WARMUP, _phase("threshold", 900, 1.00), _phase("recovery", 300, 0.50), _phase("threshold", 900, 1.00),
        COOLDOWN),
    "threshold-3x10": _w("threshold", "Threshold 3 × 10",
        WARMUP, *_repeat(3, _phase("threshold", 600, 1.00), _phase("recovery", 300, 0.50)), COOLDOWN),
    "threshold-4x8": _w("threshold", "Threshold 4 × 8",
        WARMUP, *_repeat(4, _phase("threshold", 480, 1.02), _phase("recovery", 240, 0.50)), COOLDOWN),
    "threshold-over-unders": _w("threshold", "Over-unders 3 × 9",
        WARMUP,
        *_repeat(3, *_repeat(3, _phase("threshold", 120, 0.95), _phase("threshold", 60, 1.05)),
                 _phase("recovery", 300, 0.50)),
        COOLDOWN),
    "threshold-2x20": _w("threshold", "Threshold 2 × 20",
        WARMUP, *_repeat(2, _phase("threshold", 1200, 0.98), _phase("recovery", 360, 0.50)), COOLDOWN),
    "threshold-5x6": _w("threshold", "Threshold 5 × 6",
        WARMUP, *_repeat(5, _phase("threshold", 360, 1.03), _phase("recovery", 180, 0.50)), COOLDOWN),
    # ---------- VO2 max ----------
    "vo2-5x3": _w("vo2", "VO2 max 5 × 3",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(5, _phase("vo2", 180, 1.18), _phase("recovery", 180, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40)),
    "vo2-4x4": _w("vo2", "VO2 max 4 × 4",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(4, _phase("vo2", 240, 1.15), _phase("recovery", 240, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40)),
    "vo2-6x2": _w("vo2", "VO2 max 6 × 2",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(6, _phase("vo2", 120, 1.20), _phase("recovery", 120, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40)),
    "vo2-30-30": _w("vo2", "VO2 max 30/30 · 2 × 10",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(2, *_repeat(10, _phase("vo2", 30, 1.25, cadence=100), _phase("recovery", 30, 0.50)),
                 _phase("recovery", 300, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40)),
    "vo2-3x5": _w("vo2", "VO2 max 3 × 5",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(3, _phase("vo2", 300, 1.10), _phase("recovery", 300, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40)),
    "vo2-40-20": _w("vo2", "VO2 max 40/20 · 3 × 8",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(3, *_repeat(8, _phase("vo2", 40, 1.20, cadence=100), _phase("recovery", 20, 0.50)),
                 _phase("recovery", 300, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40)),
    # ---------- sprints ----------
    # Short all-out efforts: ridden in free ride (ERG can't follow a sprint), the target is a guide.
    "sprint-6x15": _w("sprint", "Sprints 6 × 15 s",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(6, _phase("sprint", 15, 2.00, cadence=110), _phase("recovery", 285, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40),
        mode="free"),
    "sprint-8x10": _w("sprint", "Sprints 8 × 10 s",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(8, _phase("sprint", 10, 2.20, cadence=115), _phase("recovery", 170, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40),
        mode="free"),
    "sprint-ladder": _w("sprint", "Sprint ladder 10 · 20 · 30 s",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(2, _phase("sprint", 10, 2.20, cadence=115), _phase("recovery", 110, 0.50),
                 _phase("sprint", 20, 1.90, cadence=110), _phase("recovery", 160, 0.50),
                 _phase("sprint", 30, 1.60, cadence=105), _phase("recovery", 240, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40),
        mode="free"),
    "sprint-standing-starts": _w("sprint", "Standing starts 6 × 12 s",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(6, _phase("sprint", 12, 2.40, cadence=None), _phase("recovery", 288, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40),
        mode="free"),
    "endurance-with-sprints": _w("sprint", "Endurance 60 · sprints",
        WARMUP, *_repeat(4, _phase("endurance", 590, 0.68), _phase("sprint", 10, 2.00, cadence=110)),
        _phase("endurance", 200, 0.68), COOLDOWN,
        mode="free"),
    "anaerobic-6x1": _w("sprint", "Anaerobic 6 × 1 min",
        _phase("warmup", 720, 0.40, 0.75),
        *_repeat(6, _phase("vo2", 60, 1.30, cadence=105), _phase("recovery", 240, 0.50)),
        _phase("cooldown", 540, 0.60, 0.40)),
    # ---------- strength ----------
    # Low cadence, high torque: the work is in the force on the pedals, not the power. Seated, 55–60 rpm,
    # kept above the 50 rpm where ERG lets go of the trainer (core/workout.py LOW_CADENCE).
    "strength-big-gear-tempo": _w("strength", "Big gear tempo 3 × 10 · 60 rpm",
        WARMUP, *_repeat(3, _phase("tempo", 600, 0.80, cadence=60), _phase("recovery", 300, 0.50, cadence=90)),
        COOLDOWN),
    "strength-low-cadence-4x6": _w("strength", "Low cadence sweet spot 4 × 6 · 55 rpm",
        WARMUP, *_repeat(4, _phase("sweetspot", 360, 0.88, cadence=55), _phase("recovery", 240, 0.50, cadence=90)),
        COOLDOWN),
    "strength-low-cadence-3x10": _w("strength", "Low cadence sweet spot 3 × 10 · 55 rpm",
        WARMUP, *_repeat(3, _phase("sweetspot", 600, 0.88, cadence=55), _phase("recovery", 300, 0.50, cadence=90)),
        COOLDOWN),
    "strength-threshold-5x4": _w("strength", "Low cadence threshold 5 × 4 · 60 rpm",
        WARMUP, *_repeat(5, _phase("threshold", 240, 0.95, cadence=60), _phase("recovery", 240, 0.50, cadence=90)),
        COOLDOWN),
    "strength-hill-reps": _w("strength", "Seated hill reps 8 × 1 min · 55 rpm",
        WARMUP, *_repeat(8, _phase("vo2", 60, 1.10, cadence=55), _phase("recovery", 180, 0.50, cadence=90)),
        COOLDOWN),
    # ---------- FTP tests ----------
    # Ramp: 1 min steps until the rider cannot hold one; FTP = 75 % of the best minute.
    # ERG holds each step; cadence under 50 rpm for a few seconds ends the ramp.
    "ftp-ramp-test": _w("test", "FTP ramp test",
        _phase("warmup", 300, 0.40, 0.50),
        *[_phase("test", 60, round(RAMP_START + RAMP_STEP * i, 2), cadence=None) for i in range(RAMP_STEPS)],
        _phase("cooldown", 300, 0.45, 0.35),
        test="ramp", mode="erg"),
    # 20 min: warm-up with openers, then 20 min as hard as you can hold; FTP = 95 % of the average.
    # Ridden in free ride: the target is only a guide for pacing.
    "ftp-20-min-test": _w("test", "FTP 20 min test",
        _phase("warmup", 600, 0.40, 0.70),
        *_repeat(3, _phase("vo2", 60, 1.05, cadence=105), _phase("recovery", 60, 0.50)),
        _phase("recovery", 300, 0.55),
        _phase("test", 1200, 1.00),
        _phase("cooldown", 600, 0.60, 0.40),
        test="20min", mode="free"),
}
DEFAULT_WORKOUT_ID = "sweet-spot-3x4"


# ---------- a workout fitted to a length (training plans: "sessions of 60 min") ----------
# A fitted built-in has the id "<built-in id>--<minutes>m" and is made on the fly, never stored.
FITTED_ID = re.compile(r"^([a-z0-9][a-z0-9-]*?)--(\d{2,3})m$")
FIT_MINUTES = (30, 45, 60, 75, 90, 120)
MIN_WARMUP_S, MIN_STEADY_S = 300, 300


def fitted_id(workout_id: str, minutes: int) -> str:
    return f"{workout_id}--{minutes}m"


def base_id(workout_id: str) -> str:
    m = FITTED_ID.match(workout_id)
    return m.group(1) if m and m.group(1) in BUILTIN else workout_id


def builtin_spec(workout_id: str) -> dict | None:
    """A built-in, or a built-in fitted to a length, by id."""
    if workout_id in BUILTIN:
        return BUILTIN[workout_id]
    m = FITTED_ID.match(workout_id)
    if m and m.group(1) in BUILTIN and int(m.group(2)) in FIT_MINUTES:
        return fit_length(BUILTIN[m.group(1)], int(m.group(2)) * 60)
    return None


def _repeat_run(body: list[dict]) -> tuple[int, int, int] | None:
    """(start, unit length, count) of the longest stretch of a block of phases repeated 2+ times."""
    best = None
    for k in range(1, len(body) // 2 + 1):
        for start in range(0, len(body) - 2 * k + 1):
            unit = body[start:start + k]
            n = 1
            while body[start + n * k:start + (n + 1) * k] == unit:
                n += 1
            if n >= 2 and (best is None or n * k > best[1] * best[2] or (n * k == best[1] * best[2] and k < best[1])):
                best = (start, k, n)
    return best


def fit_length(spec: dict, target: int) -> dict:
    """The workout stretched or shortened to about `target` seconds, keeping what it trains.

    Repeated intervals change in number (at least one, at most half as many again), a steady block
    changes in length, and warm-up and cool-down take up what is left (at least 5 min each).
    FTP tests are never changed. The name says the length.
    """
    if spec.get("test"):
        return dict(spec)
    phases = [dict(p) for p in spec["phases"]]
    head = 0
    while head < len(phases) and phases[head]["kind"] == "warmup":
        head += 1
    tail = len(phases)
    while tail > head and phases[tail - 1]["kind"] == "cooldown":
        tail -= 1
    warm, body, cool = phases[:head], phases[head:tail], phases[tail:]
    total = lambda ps: sum(p["seconds"] for p in ps)  # noqa: E731

    run = _repeat_run(body)
    if run:
        start, k, n = run
        unit = body[start:start + k]
        before, after = body[:start], body[start + n * k:]
        room = target - total(warm) - total(cool) - total(before) - total(after)
        n2 = max(1, min(math.ceil(1.5 * n), round(room / total(unit))))  # never much more hard work than written
        body = before + [dict(p) for _ in range(n2) for p in unit] + after
    else:
        steady = [p for p in body if p["kind"] not in ("recovery",) and p.get("pct_end") is None]
        if steady:
            longest = max(steady, key=lambda p: p["seconds"])
            room = target - total(warm) - total(cool) - (total(body) - longest["seconds"])
            longest["seconds"] = max(MIN_STEADY_S, min(4 * 3600, room))

    # warm-up and cool-down absorb the rest, in proportion, at least 5 min each
    rest = target - total(body)
    ends = warm + cool
    if ends and total(ends):
        scale = max(rest, MIN_WARMUP_S * len(ends)) / total(ends)
        for p in ends:
            p["seconds"] = max(MIN_WARMUP_S if p["kind"] in ("warmup", "cooldown") else 10, round(p["seconds"] * scale / 10) * 10)
    minutes = round(target / 60)
    return {**{k: v for k, v in spec.items() if k != "phases"}, "name": f"{spec['name']} · {minutes} min",
            "phases": warm + body + cool, "fitted": {"from": spec["name"], "minutes": minutes}}


def to_workout(spec: dict, ftp: int) -> Workout:
    def w(pct: float) -> int:
        return round(pct * ftp)

    phases = [
        Phase(p["kind"], p["seconds"], w(p["pct"]), None if p.get("pct_end") is None else w(p["pct_end"]), p.get("cadence"))
        for p in spec["phases"]
    ]
    if spec.get("climb"):  # a climb's one phase lasts as long as the climb takes at a steady pace (an estimate)
        profile = climb.Profile.from_segments(spec["climb"]["segments"])
        phases = [Phase("climb", climb.estimate_seconds(profile, climb.PACE * ftp), w(climb.PACE))]
    return Workout(name=spec["name"], ftp=ftp, phases=phases, test=spec.get("test"), climb=spec.get("climb"),
                   open=bool(spec.get("open")))


def default_workout(ftp: int = DEFAULT_FTP) -> Workout:
    """The built-in sweet spot workout in watts for this FTP."""
    return to_workout(BUILTIN[DEFAULT_WORKOUT_ID], ftp)


sweet_spot_3x4 = default_workout


MAX_CLIMB_SEGMENTS = 5000
MAX_CLIMB_M = 200_000


def climb_spec(name: str, segments: list) -> dict:
    """A climb as the library keeps it: segments of [metres, gradient %] and one estimated "climb" phase."""
    return {"name": name, "category": "climb", "mode": "climb", "climb": {"segments": segments},
            "phases": [{"kind": "climb", "seconds": 0, "pct": climb.PACE, "pct_end": None, "cadence": None}]}


# Climbs: a road whose gradient sets the trainer's resistance (core/climb.py). One "climb" phase whose
# length is only an estimate at 75 % FTP; the ride ends at the top. Built in, so read-only.
for _id, (_name, _segments) in climb.BUILTIN_CLIMBS.items():
    BUILTIN[_id] = climb_spec(_name, _segments)


# "Just ride": no workout, no target, no end (core/workout.OpenRidePlayer). Its one long phase at an easy
# endurance pace only paces the simulated rider; screens and summaries treat it as having no target.
JUST_RIDE_ID = "just-ride"
OPEN_RIDE_SECONDS = 6 * 3600
BUILTIN[JUST_RIDE_ID] = {"name": "Just ride", "category": "ride", "mode": "free", "open": True,
                         "phases": [{"kind": "ride", "seconds": OPEN_RIDE_SECONDS, "pct": 0.65, "pct_end": None, "cadence": None}]}


def _validate_climb(name: str, data: dict) -> dict:
    segments = (data.get("climb") or {}).get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_CLIMB_SEGMENTS:
        raise ValueError(f"a climb needs 1 to {MAX_CLIMB_SEGMENTS} segments")
    clean = []
    for i, seg in enumerate(segments, 1):
        try:
            metres, grade = float(seg[0]), float(seg[1])
        except (TypeError, ValueError, IndexError, KeyError) as e:
            raise ValueError(f"climb segment {i} is incomplete") from e
        if not (1 <= metres <= 10_000 and -25 <= grade <= 30) or not (math.isfinite(metres) and math.isfinite(grade)):
            raise ValueError(f"climb segment {i}: length or gradient out of range")
        clean.append([round(metres, 1), round(grade, 2)])
    if sum(m for m, _ in clean) > MAX_CLIMB_M:
        raise ValueError(f"a climb can be up to {MAX_CLIMB_M // 1000} km long")
    return climb_spec(name, clean)


def validate(data: dict) -> dict:
    """A clean copy of a workout from the editor (or a climb), or ValueError with a plain message."""
    name = str(data.get("name", "")).strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("give the workout a name (up to 80 characters)")
    if data.get("climb") is not None or data.get("category") == "climb":
        return _validate_climb(name, data)
    phases = data.get("phases")
    if not isinstance(phases, list) or not 1 <= len(phases) <= MAX_PHASES:
        raise ValueError(f"a workout needs 1 to {MAX_PHASES} phases")
    category = data.get("category") or "custom"
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {list(CATEGORIES)}")
    clean = []
    for i, p in enumerate(phases, 1):
        try:
            kind = p["kind"]
            seconds = int(p["seconds"])
            pct = float(p["pct"])
            pct_end = None if p.get("pct_end") in (None, "") else float(p["pct_end"])
            cadence = None if p.get("cadence") in (None, "") else int(p["cadence"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"phase {i} is incomplete") from e
        if kind not in KINDS:
            raise ValueError(f"phase {i}: unknown type {kind!r}")
        if not 10 <= seconds <= 4 * 3600:
            raise ValueError(f"phase {i}: duration must be between 0:10 and 4:00:00")
        for value in (pct, pct_end):
            if value is not None and not 0.2 <= value <= 2.5:
                raise ValueError(f"phase {i}: power must be between 20 % and 250 % of FTP")
        if cadence is not None and not 30 <= cadence <= 160:
            raise ValueError(f"phase {i}: cadence must be between 30 and 160 rpm")
        clean.append({"kind": kind, "seconds": seconds, "pct": round(pct, 4),
                      "pct_end": None if pct_end is None else round(pct_end, 4), "cadence": cadence})
    spec = {"name": name, "category": category, "phases": clean}
    if data.get("test") in TESTS:  # a copy of a test is still a test
        spec["test"] = data["test"]
    if data.get("mode") in ("erg", "free"):
        spec["mode"] = data["mode"]
    return spec


def describe(spec: dict, ftp: int) -> dict:
    """Numbers for the Ready screen and the editor: duration, average, intervals, intensity, training load.

    The same workouts are described on every visit to Ready, the editor and the plans, so the answer is kept
    per workout and FTP (a changed workout is a different key)."""
    return dict(_described(json.dumps(spec, sort_keys=True, default=str), ftp))


@functools.lru_cache(maxsize=1024)
def _described(key: str, ftp: int) -> dict:
    return _describe(json.loads(key), ftp)


def _describe(spec: dict, ftp: int) -> dict:
    if spec.get("open"):  # Just ride: nothing planned, so nothing to add up
        return {"seconds": 0, "avg_power": None, "wp": None, "intensity": None, "load": None, "intervals": 0,
                "text": "NO WORKOUT · NO TARGET · RIDE AS YOU LIKE"}
    workout = to_workout(spec, ftp)
    # second by second, phase by phase (the same as target_at(t) for each t, without looking each phase up again)
    targets = [phase.target_at(s) for phase in workout.phases for s in range(phase.seconds)]
    wp = stats.weighted_power(targets)
    work = [p for p in spec["phases"] if p["kind"] not in REST_KINDS]
    return {
        "seconds": workout.seconds,
        "avg_power": stats.mean(targets),
        "wp": wp,
        "intensity": stats.intensity(wp, ftp),
        "load": stats.training_load(workout.seconds, wp, ftp),
        "intervals": len(work),
        "text": _text(spec),
    }


def _minutes(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} S"
    return f"{seconds // 60} MIN" if seconds % 60 == 0 else f"{seconds // 60}:{seconds % 60:02d} MIN"


def _text(spec: dict) -> str:
    """e.g. '3 × 4 MIN @ 90 % FTP · 2 MIN RECOVERY'."""
    if spec.get("climb"):
        return climb.describe(spec["climb"])
    if spec.get("test") == "ramp":
        return "1 MIN STEPS UNTIL YOU CANNOT HOLD ONE · FTP = 75 % OF YOUR BEST MINUTE"
    if spec.get("test") == "20min":
        return "20 MIN AS HARD AS YOU CAN HOLD · FTP = 95 % OF THE AVERAGE"
    work = [(p["seconds"], round(p["pct"] * 100)) for p in spec["phases"] if p["kind"] not in REST_KINDS]
    if not work:
        total = sum(p["seconds"] for p in spec["phases"])
        return f"{_minutes(total)} STEADY"
    (seconds, pct), n = Counter(work).most_common(1)[0]
    parts = [f"{n} × {_minutes(seconds)} @ {pct} % FTP" if n > 1 else f"{_minutes(seconds)} @ {pct} % FTP"]
    if len(work) > n:
        parts[0] += f" + {len(work) - n} MORE"
    rests = [p["seconds"] for p in spec["phases"] if p["kind"] == "recovery"]
    if rests:
        parts.append(f"{_minutes(Counter(rests).most_common(1)[0][0])} RECOVERY")
    return " · ".join(parts)


class WorkoutStore:
    """Built-in workouts (read-only) and the rider's own, one JSON file each."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def _path(self, workout_id: str) -> Path:
        if not WORKOUT_ID.match(workout_id):
            raise ValueError(f"bad workout id {workout_id!r}")
        return self.dir / f"{workout_id}.json"

    def get(self, workout_id: str) -> dict:
        if workout_id in BUILTIN:
            return {"id": workout_id, "builtin": True, **BUILTIN[workout_id]}
        fitted = builtin_spec(workout_id)
        if fitted is not None:  # a built-in fitted to a length (training plans)
            return {"id": workout_id, "builtin": True, **fitted}
        try:
            spec = json.loads(self._path(workout_id).read_text())
        except OSError as e:
            raise KeyError(workout_id) from e
        return {"id": workout_id, "builtin": False, **validate(spec)}

    def list(self) -> list[dict]:
        own = []
        if self.dir.exists():
            for path in sorted(self.dir.glob("*.json")):
                try:
                    own.append(self.get(path.stem))
                except (KeyError, ValueError):
                    continue  # a broken file should not hide the rest
        return [self.get(i) for i in BUILTIN] + sorted(own, key=lambda w: w["name"].lower())

    def save(self, data: dict, workout_id: str | None = None) -> str:
        """Create (no id, or a built-in id: saved as the rider's copy) or update a workout."""
        spec = validate(data)
        if workout_id is None or workout_id in BUILTIN:
            slug = re.sub(r"[^a-z0-9]+", "-", spec["name"].lower()).strip("-")[:40] or "workout"
            workout_id = f"{slug}-{secrets.token_hex(3)}"
        path = self._path(workout_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(spec, indent=2))
        tmp.replace(path)
        return workout_id

    def delete(self, workout_id: str) -> None:
        if workout_id in BUILTIN:
            raise ValueError("built-in workouts cannot be deleted")
        self._path(workout_id).unlink(missing_ok=True)
