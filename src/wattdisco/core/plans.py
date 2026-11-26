"""Training plans: weeks of built-in workouts that suggest the next ride.

A plan is a list of weeks, each a list of built-in workout ids. There is no calendar: the next
session is simply the first one not yet ridden or skipped, so a missed day never puts the rider
"behind". The plan only suggests; targets stay the workouts' own, in % FTP, so an FTP test inside
the plan rescales every session after it.

The training logic (see the plan descriptions):
- FTP grows with time near threshold: sweet spot first (88–94 %, much gain for little fatigue),
  then threshold, with a little VO2 max on top.
- Progressive overload: each build week adds time in zone, about 5–10 % more training load.
- A lighter week every 3–4 weeks, and an easy start to the test week: the body adapts while it
  recovers, and a rested test gives a true number.
- Mostly easy: two key sessions a week (three in the peak week), the rest endurance, and never two
  hard days written back to back.
- Test, train, retest: plans start and end with a ramp test (skip the first if you tested lately).

Progress lives in DATA_DIR/plan.json: the plan, where the rider is (week, session) and a log of
what happened to each session (ridden with a ride id, ridden elsewhere, or skipped).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from wattdisco.core.workouts import BUILTIN, TESTS, base_id, builtin_spec, describe, fit_length, fitted_id

KEY_CATEGORIES = {"sweetspot", "threshold", "vo2", "sprint", "strength", "test"}  # the hard sessions of a week
COUNTS_FROM = 0.8  # share of a workout's time a ride needs to count for its session (tests: a result)
REPEAT_BELOW_ON_TARGET = 85  # % on target over a week's key sessions; below it, suggest repeating it
REPEAT_AFTER_SKIPS = 2  # skipped sessions in a week that suggest repeating it
MAX_ROUNDS = 2  # a week is suggested for repeating at most this many times


# How hard a plan is as written, shown as a colour from easy (green) to very hard (red): the zone
# colours of endurance, tempo, sweet spot and threshold. More days or longer sessions make any plan harder.
LEVELS = {1: "Easy", 2: "Moderate", 3: "Hard", 4: "Very hard"}
LEVEL_ZONE = {1: 2, 2: 3, 3: 4, 4: 5}


# The plan screen shows the plans in four groups, by what a rider wants from them.
GROUPS = {
    "start": {"name": "Getting started", "about": "A base to build on: mostly endurance, for new or returning riders."},
    "ftp": {"name": "Raise your FTP", "about": "Sweet spot and threshold: more power for longer."},
    "top": {"name": "Top-end power", "about": "VO2 max, anaerobic efforts and sprints: a higher ceiling and a harder kick."},
    "climb": {"name": "Climbing and long days", "about": "Force on the pedals, long steady efforts and endurance for big days out."},
}


def _week(focus: str, *sessions: str, light: bool = False) -> dict:
    return {"focus": focus, "light": light, "sessions": list(sessions)}


PLANS = {
    "ftp-builder-6": {
        "group": "ftp",
        "level": 3,
        "level_why": "Threshold work and the most riding time",
        "name": "FTP Builder",
        "for": "Riders with some base who want a higher FTP",
        "about": "Sweet spot, then threshold with a little VO2 max, a lighter week 4 and a ramp test at the end.",
        "weeks": [
            _week("Test and sweet spot", "ftp-ramp-test", "endurance-60-cadence", "sweet-spot-4x8", "endurance-90"),
            _week("Sweet spot", "sweet-spot-4x8", "endurance-60-cadence", "sweet-spot-3x12", "endurance-90"),
            _week("Sweet spot to threshold", "sweet-spot-2x20", "endurance-60-cadence", "threshold-3x10", "endurance-90"),
            _week("Lighter week", "endurance-60-cadence", "sweet-spot-3x4", "endurance-60-tempo-finish", light=True),
            _week("Threshold and VO2 max", "threshold-2x20", "endurance-60-cadence", "vo2-4x4", "endurance-2h"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "base-8": {
        "group": "start",
        "level": 1,
        "level_why": "Mostly endurance, the hardest is tempo and a little sweet spot",
        "name": "Base",
        "for": "New or returning riders",
        "about": "Mostly endurance, then tempo and a first taste of sweet spot. Lighter week 4, test at the end.",
        "weeks": [
            _week("Test and endurance", "ftp-ramp-test", "endurance-45", "endurance-60-cadence"),
            _week("Endurance", "endurance-60-cadence", "endurance-45", "endurance-90"),
            _week("Endurance", "endurance-60-tempo-finish", "endurance-60-cadence", "endurance-90"),
            _week("Lighter week", "easy-spin-30", "endurance-45", "endurance-60-cadence", light=True),
            _week("Tempo", "tempo-3x10", "endurance-60-tempo-finish", "endurance-90"),
            _week("Tempo", "tempo-2x20", "endurance-60-cadence", "endurance-2h"),
            _week("Tempo and sweet spot", "sweet-spot-3x12", "endurance-60-cadence", "tempo-4x12"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "vo2-5": {
        "group": "top",
        "level": 4,
        "level_why": "Two VO2 max sessions a week, the hardest efforts above FTP",
        "name": "VO2 max",
        "for": "Riders with a base who want a higher ceiling",
        "about": "Short, then longer VO2 max intervals, two a week with endurance in between. More power above FTP gives FTP room to grow.",
        "weeks": [
            _week("Test and first VO2", "ftp-ramp-test", "endurance-60-cadence", "vo2-30-30", "endurance-90"),
            _week("Short VO2", "vo2-6x2", "endurance-60-cadence", "vo2-30-30", "endurance-90"),
            _week("Longer VO2", "vo2-5x3", "endurance-60-cadence", "vo2-40-20", "endurance-90"),
            _week("VO2 peak", "vo2-4x4", "endurance-60-cadence", "vo2-3x5", "endurance-2h"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "strength-6": {
        "group": "climb",
        "level": 2,
        "level_why": "Low cadence at sweet spot and threshold, seated and controlled",
        "name": "Strength",
        "for": "Riders who want more force on the pedals for climbs and accelerations",
        "about": "Seated low-cadence work at 55–60 rpm: big gear tempo, low cadence sweet spot and threshold, hill reps. Lighter week 4.",
        "weeks": [
            _week("Test and big gear", "ftp-ramp-test", "endurance-60-cadence", "strength-big-gear-tempo", "endurance-90"),
            _week("Low cadence", "strength-low-cadence-4x6", "endurance-60-cadence", "strength-big-gear-tempo", "endurance-90"),
            _week("More torque", "strength-low-cadence-3x10", "endurance-60-cadence", "strength-hill-reps", "endurance-90"),
            _week("Lighter week", "easy-spin-30", "strength-hill-reps", "endurance-45", light=True),
            _week("Torque at threshold", "strength-threshold-5x4", "endurance-60-cadence", "strength-low-cadence-3x10", "endurance-2h"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "sprint-4": {
        "group": "top",
        "level": 3,
        "level_why": "All-out sprints and anaerobic minutes, short but maximal",
        "name": "Sprint power",
        "for": "Riders who want a harder kick",
        "about": "Short all-out sprints and standing starts in free ride, an anaerobic minute session in week 3, endurance in between.",
        "weeks": [
            _week("Test and first sprints", "ftp-ramp-test", "endurance-45", "sprint-8x10", "endurance-60-cadence"),
            _week("Sprints", "sprint-6x15", "endurance-60-cadence", "sprint-ladder", "endurance-90"),
            _week("Sprints and anaerobic", "anaerobic-6x1", "endurance-60-cadence", "sprint-standing-starts", "endurance-90"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "short-4": {
        "group": "ftp",
        "level": 2,
        "level_why": "Little time, but most of it hard",
        "name": "Short on time",
        "for": "Three rides of 45–60 min a week",
        "about": "Two key sessions and one endurance ride a week: sweet spot, then threshold and VO2 max.",
        "weeks": [
            _week("Test and sweet spot", "ftp-ramp-test", "endurance-45", "sweet-spot-4x8"),
            _week("Sweet spot to threshold", "sweet-spot-4x8", "endurance-45", "threshold-2x15"),
            _week("Threshold and VO2 max", "threshold-2x15", "endurance-45", "vo2-5x3"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "first-steps-4": {
        "group": "start",
        "level": 1,
        "level_why": "Three short endurance rides a week, nothing hard",
        "name": "First steps",
        "for": "Riders new to training indoors",
        "about": "Three easy rides a week of 45–60 min, a few minutes of tempo by week 3, a ramp test at each end.",
        "weeks": [
            _week("Test and easy rides", "ftp-ramp-test", "endurance-45", "endurance-45"),
            _week("Endurance", "endurance-45", "endurance-60-cadence", "endurance-45"),
            _week("Endurance with a tempo finish", "endurance-60-cadence", "endurance-45", "endurance-60-tempo-finish"),
            _week("Easy days, then test", "easy-spin-30", "endurance-45", "ftp-ramp-test", light=True),
        ],
    },
    "comeback-6": {
        "group": "start",
        "level": 2,
        "level_why": "Endurance and tempo, with one sweet spot session near the end",
        "name": "Back after a break",
        "for": "Riders coming back after weeks off the bike",
        "about": "Endurance first, then tempo and a first sweet spot session. Lighter week 4, test at the end.",
        "weeks": [
            _week("Test and endurance", "ftp-ramp-test", "endurance-45", "endurance-60-cadence", "endurance-45"),
            _week("Endurance", "endurance-60-cadence", "endurance-45", "endurance-60-tempo-finish", "endurance-90"),
            _week("First tempo", "tempo-3x10", "endurance-60-cadence", "endurance-60-tempo-finish", "endurance-90"),
            _week("Lighter week", "easy-spin-30", "endurance-45", "endurance-60-cadence", light=True),
            _week("Tempo and sweet spot", "tempo-2x20", "endurance-60-cadence", "sweet-spot-3x12", "endurance-90"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "sweet-spot-8": {
        "group": "ftp",
        "level": 2,
        "level_why": "Two sweet spot sessions a week: hard enough to grow FTP, easy enough to repeat",
        "name": "Sweet spot",
        "for": "Riders who want a higher FTP without the hardest sessions",
        "about": "Sweet spot intervals that grow from 4 × 8 to 3 × 15 min, with over-unders in the second block. Lighter week 4.",
        "weeks": [
            _week("Test and sweet spot", "ftp-ramp-test", "endurance-60-cadence", "sweet-spot-4x8", "endurance-90"),
            _week("Sweet spot", "sweet-spot-4x8", "endurance-60-cadence", "sweet-spot-3x12", "endurance-90"),
            _week("Longer sweet spot", "sweet-spot-3x12", "endurance-60-cadence", "sweet-spot-2x20", "endurance-90"),
            _week("Lighter week", "easy-spin-30", "sweet-spot-3x4", "endurance-60-cadence", light=True),
            _week("Over-unders", "sweet-spot-2x20", "endurance-60-cadence", "sweet-spot-over-unders", "endurance-90"),
            _week("Longer intervals", "sweet-spot-3x15", "endurance-60-cadence", "sweet-spot-over-unders", "endurance-90"),
            _week("Sweet spot peak", "sweet-spot-3x15", "endurance-60-cadence", "sweet-spot-2x20", "endurance-2h"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "threshold-6": {
        "group": "ftp",
        "level": 4,
        "level_why": "Two threshold sessions a week, right at FTP",
        "name": "Threshold",
        "for": "Riders with a solid base who want to hold their FTP longer",
        "about": "Intervals at FTP from 2 × 15 to 2 × 20 min, with over-unders to handle surges. Lighter week 4.",
        "weeks": [
            _week("Test and threshold", "ftp-ramp-test", "endurance-60-cadence", "threshold-2x15", "endurance-90"),
            _week("Threshold", "threshold-3x10", "endurance-60-cadence", "threshold-over-unders", "endurance-90"),
            _week("More time at FTP", "threshold-4x8", "endurance-60-cadence", "threshold-5x6", "endurance-90"),
            _week("Lighter week", "easy-spin-30", "sweet-spot-3x4", "endurance-60-cadence", light=True),
            _week("Threshold peak", "threshold-2x20", "endurance-60-cadence", "threshold-over-unders", "endurance-2h"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "punch-4": {
        "group": "top",
        "level": 3,
        "level_why": "Short efforts well above FTP, two a week",
        "name": "Punch",
        "for": "Riders who want power for attacks, short climbs and group rides",
        "about": "30/30s, over-unders, anaerobic minutes and sprints inside endurance rides, two key sessions a week.",
        "weeks": [
            _week("Test and 30/30s", "ftp-ramp-test", "endurance-45", "vo2-30-30", "endurance-60-cadence"),
            _week("Surges", "threshold-over-unders", "endurance-45", "anaerobic-6x1", "endurance-90"),
            _week("VO2 and sprints", "vo2-40-20", "endurance-60-cadence", "endurance-with-sprints", "endurance-90"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "climbing-6": {
        "group": "climb",
        "level": 3,
        "level_why": "Long sweet spot and threshold efforts, and low cadence work",
        "name": "Climbing",
        "for": "Riders getting ready for long climbs",
        "about": "Long steady efforts like a climb (2 × 20, 3 × 15 min) and seated low-cadence work for torque. Lighter week 4.",
        "weeks": [
            _week("Test and big gear", "ftp-ramp-test", "endurance-60-cadence", "strength-big-gear-tempo", "endurance-90"),
            _week("Long sweet spot", "sweet-spot-2x20", "endurance-60-cadence", "strength-low-cadence-3x10", "endurance-90"),
            _week("Longer efforts", "sweet-spot-3x15", "endurance-60-cadence", "strength-hill-reps", "endurance-2h"),
            _week("Lighter week", "easy-spin-30", "strength-hill-reps", "endurance-45", light=True),
            _week("Threshold climbs", "threshold-2x20", "endurance-60-cadence", "strength-threshold-5x4", "endurance-2h"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
    "long-rides-8": {
        "group": "climb",
        "level": 2,
        "level_why": "Long endurance and tempo, a little sweet spot later on",
        "name": "Long rides",
        "for": "Riders getting ready for a long day out or a multi-day trip",
        "about": "Endurance up to 2 h every week, tempo blocks that grow, then sweet spot. Lighter week 4.",
        "weeks": [
            _week("Test and tempo", "ftp-ramp-test", "endurance-60-cadence", "tempo-3x10", "endurance-90"),
            _week("Endurance", "endurance-60-tempo-finish", "endurance-60-cadence", "tempo-45", "endurance-2h"),
            _week("Tempo", "tempo-2x20", "endurance-60-cadence", "endurance-60-tempo-finish", "endurance-2h"),
            _week("Lighter week", "easy-spin-30", "endurance-45", "endurance-60-cadence", light=True),
            _week("Long tempo", "tempo-4x12", "endurance-60-cadence", "sweet-spot-3x12", "endurance-2h"),
            _week("Tempo and sweet spot", "tempo-4x12", "endurance-60-tempo-finish", "sweet-spot-2x20", "endurance-2h"),
            _week("Longest week", "sweet-spot-3x15", "endurance-60-cadence", "tempo-4x12", "endurance-2h"),
            _week("Easy days, then test", "easy-spin-30", "sweet-spot-3x4", "ftp-ramp-test", light=True),
        ],
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_key(workout_id: str) -> bool:
    return BUILTIN[base_id(workout_id)]["category"] in KEY_CATEGORIES


def is_test(workout_id: str) -> bool:
    return BUILTIN[base_id(workout_id)].get("test") in TESTS


# ---------- tailoring: days per week and session length ----------
DAYS_RANGE = (2, 7)
FILLERS = ("endurance-60-cadence", "endurance-45", "endurance-90")  # extra days are easy endurance
LIGHT_FILLER = "easy-spin-30"
KEEP_LENGTH = {"easy-spin-30"}  # a recovery spin stays short whatever the session length
LIGHTER = {45: 30, 60: 45, 75: 45, 90: 60, 120: 75}  # a lighter week's sessions: the next shorter length
SESSION_MINUTES = tuple(LIGHTER)  # what a rider can pick (30 min leaves a lighter week no room to be lighter)


def check_options(days: int | None, minutes: int | None) -> None:
    if days is not None and not DAYS_RANGE[0] <= days <= DAYS_RANGE[1]:
        raise ValueError(f"days per week must be {DAYS_RANGE[0]} to {DAYS_RANGE[1]}")
    if minutes is not None and minutes not in SESSION_MINUTES:
        raise ValueError(f"session length must be one of {list(SESSION_MINUTES)} minutes")


def tailor_week(week: dict, days: int | None = None, minutes: int | None = None) -> list[str]:
    """A week's session ids for this many days and this session length.

    Fewer days: easy rides go first (from the end), then the last hard sessions; in a lighter week the
    hard sessions go first. An FTP test always stays. More days: easy endurance rides are added (a recovery spin in a lighter week), before the
    test when the week ends with one. A length fits every session but tests and recovery spins to it;
    in a lighter week sessions use the next shorter length and only get shorter, so the week stays light.
    """
    sessions = list(week["sessions"])
    if days is not None:
        # a build week keeps its hard sessions; a lighter week keeps its easy rides
        drop_first = (lambda w: is_key(w) and not is_test(w)) if week["light"] else (lambda w: not is_key(w))
        while len(sessions) > days and any(drop_first(w) for w in sessions):
            sessions.pop(max(i for i, w in enumerate(sessions) if drop_first(w)))
        while len(sessions) > days and any(not is_test(w) for w in sessions):
            sessions.pop(max(i for i, w in enumerate(sessions) if not is_test(w)))
        n = 0
        while len(sessions) < days:
            filler = LIGHT_FILLER if week["light"] else FILLERS[n % len(FILLERS)]
            at = len(sessions) - 1 if sessions and is_test(sessions[-1]) and len(sessions) > 1 else len(sessions)
            sessions.insert(at, filler)
            n += 1
    if minutes is not None:
        length = LIGHTER[minutes] if week["light"] else minutes
        fitted = []
        for w in sessions:
            spec = BUILTIN[w]
            if is_test(w) or w in KEEP_LENGTH:
                fitted.append(w)
                continue
            seconds = sum(p["seconds"] for p in spec["phases"])
            longer = length * 60 > seconds
            keep = abs(seconds - length * 60) < 120 or (week["light"] and longer)  # a lighter week only gets shorter
            fitted.append(w if keep else fitted_id(w, length))
        sessions = fitted
    return sessions


def options_for(state: dict, week: int) -> dict:
    """Days and length that apply to a week: the last choice made at or before it."""
    opts = {"days": None, "minutes": None}
    for o in state.get("options", []):
        if o["from_week"] <= week:
            opts = {"days": o.get("days"), "minutes": o.get("minutes")}
    return opts


def weeks_of(state: dict) -> list[dict]:
    """The plan's weeks as this rider rides them (tailored per week)."""
    out = []
    for w, week in enumerate(PLANS[state["plan"]]["weeks"]):
        o = options_for(state, w)
        out.append({**week, "sessions": tailor_week(week, o["days"], o["minutes"])})
    return out


def plan_info(plan_id: str, ftp: int, days: int | None = None, minutes: int | None = None,
              weeks: list[dict] | None = None) -> dict:
    """A plan as the plan screen shows it: weeks of sessions with name, minutes and load."""
    plan = PLANS[plan_id]
    if weeks is None:
        weeks = [{**w, "sessions": tailor_week(w, days, minutes)} for w in plan["weeks"]]
    plan_weeks, weeks = weeks, []
    total_seconds = 0
    for n, week in enumerate(plan_weeks):
        sessions, seconds, load = [], 0, 0.0
        for workout_id in week["sessions"]:
            spec = builtin_spec(workout_id)
            d = describe(spec, ftp)
            sessions.append({"workout_id": workout_id, "name": spec["name"], "category": spec["category"],
                             "fitted": spec.get("fitted"),
                             "key": is_key(workout_id), "test": spec.get("test"), "seconds": d["seconds"],
                             "load": round(d["load"]), "text": d["text"], "intervals": d["intervals"],
                             "phases": [{k: p[k] for k in ("kind", "seconds", "pct", "pct_end")} for p in spec["phases"]]})
            seconds += d["seconds"]
            if not spec.get("test"):  # a ramp test's estimate assumes every step; a week's load leaves tests out
                load += d["load"]
        weeks.append({"n": n + 1, "focus": week["focus"], "light": week["light"], "sessions": sessions,
                      "seconds": seconds, "load": round(load)})
        total_seconds += seconds
    return {"id": plan_id, "group": plan["group"], "name": plan["name"], "for": plan["for"], "about": plan["about"],
            "level": plan["level"], "level_name": LEVELS[plan["level"]], "level_zone": LEVEL_ZONE[plan["level"]],
            "level_why": plan["level_why"],
            "weeks": weeks, "seconds": total_seconds, "days": days, "minutes": minutes,
            "per_week": round(sum(len(w["sessions"]) for w in plan_weeks) / len(plan_weeks), 1)}


# ---------- progress ----------


def new_state(plan_id: str, ftp: int, days: int | None = None, minutes: int | None = None) -> dict:
    if plan_id not in PLANS:
        raise KeyError(plan_id)
    check_options(days, minutes)
    return {"plan": plan_id, "started": _now(), "ftp_start": ftp, "week": 0, "session": 0, "rounds": {}, "log": [],
            "options": [{"from_week": 0, "days": days, "minutes": minutes}]}


def set_options(state: dict, days: int | None, minutes: int | None) -> None:
    """New days per week and session length, from the current week on (weeks already ridden stay)."""
    check_options(days, minutes)
    if current(state) is None:
        raise ValueError("the plan is finished")
    week = state["week"]
    state["options"] = [o for o in state.get("options", []) if o["from_week"] < week]
    state["options"].append({"from_week": week, "days": days, "minutes": minutes})
    sessions = weeks_of(state)[week]["sessions"]
    if state["session"] >= len(sessions):  # this week is now shorter than what's done of it
        state["week"], state["session"] = week + 1, 0


def current(state: dict) -> tuple[int, int] | None:
    """(week, session) of the next session, or None when the plan is finished."""
    if state["week"] >= len(PLANS[state["plan"]]["weeks"]):
        return None
    return state["week"], state["session"]


def next_workout(state: dict | None) -> str | None:
    pos = current(state) if state else None
    return None if pos is None else weeks_of(state)[pos[0]]["sessions"][pos[1]]


def _round(state: dict, week: int) -> int:
    return state["rounds"].get(str(week), 0)


def _advance(state: dict, entry: dict) -> None:
    week, session = state["week"], state["session"]
    state["log"].append({"week": week, "session": session, "round": _round(state, week), "at": _now(), **entry})
    if session + 1 < len(weeks_of(state)[week]["sessions"]):
        state["session"] += 1
    else:
        state["week"], state["session"] = week + 1, 0


def counts(state: dict, summary: dict) -> str | None:
    """Why a finished ride does not count for the next session, or None when it does."""
    workout_id = next_workout(state)
    # the session as fitted to the rider's length, or the workout as written
    if workout_id is None or summary.get("workout_id") not in (workout_id, base_id(workout_id)):
        return "other"  # a different workout: recorded, not counted
    if summary.get("sim"):
        return "sim"
    if is_test(workout_id):
        est = summary.get("ftp_estimate") or {}
        return None if est.get("source") in TESTS else "short"
    planned = (summary.get("workout") or {}).get("seconds") or 0
    return None if summary.get("duration", 0) >= COUNTS_FROM * planned else "short"


def record_ride(state: dict, summary: dict) -> bool:
    """Tick off the next session with this ride if it counts. True when it did."""
    if counts(state, summary) is not None:
        return False
    _advance(state, {"ride": summary["id"], "on_target": (summary.get("nerd") or {}).get("on_target_pct")})
    return True


def mark_done(state: dict) -> None:
    """The session was ridden somewhere else (outdoors, another app)."""
    if current(state) is None:
        raise ValueError("the plan is finished")
    _advance(state, {"elsewhere": True})


def skip(state: dict) -> None:
    if current(state) is None:
        raise ValueError("the plan is finished")
    _advance(state, {"skipped": True})


def undo(state: dict) -> None:
    """Take back the last ticked-off or skipped session (the ride itself stays recorded)."""
    if not state["log"]:
        raise ValueError("nothing to undo")
    last = state["log"].pop()
    state["week"], state["session"] = last["week"], last["session"]


def repeat_week(state: dict) -> None:
    """Ride the week before the current one again (or the current one, from its start)."""
    pos = current(state)
    if pos is not None and pos[1] > 0:
        week = pos[0]
    elif state["week"] > 0:
        week = state["week"] - 1
    else:
        raise ValueError("nothing to repeat yet")
    state["rounds"][str(week)] = _round(state, week) + 1
    state["week"], state["session"] = week, 0


def _entries(state: dict, week: int) -> dict[int, dict]:
    """This round's log entry per session of a week."""
    r = _round(state, week)
    return {e["session"]: e for e in state["log"] if e["week"] == week and e.get("round", 0) == r}


def suggestion(state: dict) -> dict | None:
    """Suggest repeating the last week when it went badly: sessions skipped, or key sessions off target."""
    pos = current(state)
    if state["week"] == 0 or (pos is not None and pos[1] > 0):
        return None  # only at the start of a week, about the week just finished
    week = state["week"] - 1
    plan_week = weeks_of(state)[week]
    if plan_week["light"] or _round(state, week) >= MAX_ROUNDS:
        return None
    entries = _entries(state, week).values()
    skipped = sum(1 for e in entries if e.get("skipped"))
    ids = plan_week["sessions"]
    on_target = [e["on_target"] for e in entries if e.get("on_target") is not None and e["session"] < len(ids)
                 and is_key(ids[e["session"]]) and not is_test(ids[e["session"]])]
    avg = sum(on_target) / len(on_target) if on_target else None
    if skipped >= REPEAT_AFTER_SKIPS:
        reason = f"{skipped} sessions of week {week + 1} were skipped"
    elif avg is not None and avg < REPEAT_BELOW_ON_TARGET:
        reason = f"the key sessions of week {week + 1} were {round(avg)} % on target"
    else:
        return None
    return {"week": week + 1, "reason": reason}


def view(state: dict, ftp: int) -> dict:
    """The plan with each session's status ("done", "elsewhere", "skipped", "next", "todo") for the screen."""
    info = plan_info(state["plan"], ftp, weeks=weeks_of(state))
    pos = current(state)
    o = options_for(state, pos[0] if pos else len(info["weeks"]) - 1)
    info["days"], info["minutes"] = o["days"], o["minutes"]
    done_sessions = total = 0
    for w, week in enumerate(info["weeks"]):
        entries = _entries(state, w)
        week["round"] = _round(state, w)
        for s, session in enumerate(week["sessions"]):
            total += 1
            entry = entries.get(s) if (pos is None or (w, s) < pos) else None
            if (w, s) == pos:
                session["status"] = "next"
            elif entry is None:
                session["status"] = "todo"
            else:
                session["status"] = "skipped" if entry.get("skipped") else "elsewhere" if entry.get("elsewhere") else "done"
                session["ride"] = entry.get("ride")
                done_sessions += session["status"] != "skipped"
    nxt = None
    if pos is not None:
        week = info["weeks"][pos[0]]
        nxt = {"week": pos[0] + 1, "weeks": len(info["weeks"]), "session": pos[1] + 1,
               "sessions": len(week["sessions"]), "focus": week["focus"], **week["sessions"][pos[1]]}
    return {**info, "started": state["started"], "ftp_start": state["ftp_start"], "ftp": ftp,
            "done": done_sessions, "total": total, "finished": pos is None, "next": nxt,
            "suggest_repeat": suggestion(state), "can_undo": bool(state["log"]),
            "rides": [e["ride"] for e in state["log"] if e.get("ride")]}


def forget_ride(state: dict, ride_id: str) -> bool:
    """A deleted ride: its session stays done, without a link to the ride. True when the plan changed."""
    changed = False
    for e in state["log"]:
        if e.get("ride") == ride_id:
            del e["ride"]
            changed = True
    return changed


def ride_entry(state: dict, ride_id: str) -> dict | None:
    """Which session a ride ticked off, if any: {"week", "session", "sessions"} (1-based)."""
    for e in state["log"]:
        if e.get("ride") == ride_id:
            weeks = weeks_of(state)
            return {"week": e["week"] + 1, "weeks": len(weeks), "session": e["session"] + 1,
                    "sessions": len(weeks[e["week"]]["sessions"])}
    return None


class PlanStore:
    """The rider's active plan in DATA_DIR/plan.json (none: no file)."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "plan.json"

    def load(self) -> dict | None:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        return state if isinstance(state, dict) and state.get("plan") in PLANS else None

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
