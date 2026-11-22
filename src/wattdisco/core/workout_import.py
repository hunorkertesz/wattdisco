"""Read workouts riders already have: Zwift .zwo, and .erg / .mrc (the TrainerRoad, Golden Cheetah and
PerfPRO text format). The result is a normal workout spec in % FTP (see core/workouts.py), saved as one
of the rider's own workouts.

- .zwo: XML. Power is a fraction of FTP. Warmup, Cooldown and Ramp go from PowerLow to PowerHigh,
  SteadyState holds Power, IntervalsT repeats an on and an off part. FreeRide and MaxEffort have no
  target, so they get a fixed one (see FREE_RIDE_PCT, MAX_EFFORT_PCT) and a note.
- .erg / .mrc: a [COURSE DATA] list of (minutes, watts) or (minutes, % FTP) points, joined by straight
  lines. Two points at the same minute make a step. An .erg converts with the FTP in its header, else
  with the rider's FTP.
"""

import math
import re
import xml.etree.ElementTree as ET
from pathlib import PurePath

from wattdisco.config import ZONE_TOPS
from wattdisco.core.workouts import MAX_PHASES, validate

MAX_BYTES = 2_000_000
MIN_SECONDS = 10  # shorter parts are folded into their neighbour (the editor's shortest phase)
FREE_RIDE_PCT = 0.60
MAX_EFFORT_PCT = 1.60
SPRINT_FROM = 1.50  # above this a work part is a sprint (neuromuscular), else VO2 max
EXTENSIONS = (".zwo", ".erg", ".mrc")


class WorkoutFileError(ValueError):
    """The file can't be read as a workout; the message says why, in plain words."""


def import_workout(filename: str, text: str, ftp: int) -> tuple[dict, list[str]]:
    """A validated workout spec from the file's text, and notes about anything that was changed."""
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise WorkoutFileError("that file is too big for a workout")
    ext = PurePath(filename).suffix.lower()
    if ext == ".zwo":
        name, parts, notes = _zwo(text)
    elif ext in (".erg", ".mrc"):
        name, parts, notes = _erg(text, ext, ftp)
    else:
        raise WorkoutFileError("Watt Disco reads .zwo, .erg and .mrc workout files")
    if not parts:
        raise WorkoutFileError("no workout steps found in that file")
    phases = _phases(parts, notes)
    if len(phases) > MAX_PHASES:
        raise WorkoutFileError(f"that workout has {len(phases)} steps; Watt Disco takes up to {MAX_PHASES}")
    name = (name or PurePath(filename).stem or "Imported workout").strip()[:80]
    try:
        spec = validate({"name": name, "category": "custom", "phases": phases})
    except ValueError as e:
        raise WorkoutFileError(str(e)) from e
    return spec, notes


# ---------- parts: (seconds, start % FTP, end % FTP or None, cadence or None, hint) ----------
# hint is "warmup", "cooldown", "rest" (the off part of an interval) or None; the kind follows the power.


def _zwo(text: str):
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise WorkoutFileError("that .zwo file is not valid XML") from e
    workout = root.find("workout")
    if workout is None:
        raise WorkoutFileError("that .zwo file has no <workout> section")
    name = (root.findtext("name") or "").strip()
    parts, notes = [], []
    skipped = set()

    def num(e, *keys, default=None):
        for key in keys:
            value = e.get(key)
            if value is None:
                for k, v in e.attrib.items():  # attribute names differ in case between tools
                    if k.lower() == key.lower():
                        value = v
                        break
            if value not in (None, ""):
                try:
                    return float(value)
                except ValueError:
                    pass
        return default

    for e in workout:
        tag = e.tag.lower()
        seconds = num(e, "Duration", default=0)
        cadence = num(e, "Cadence")
        if tag in ("warmup", "cooldown", "ramp"):
            low, high = num(e, "PowerLow", default=0.5), num(e, "PowerHigh", default=0.5)
            if tag == "cooldown" and low < high:
                low, high = high, low  # a cool-down goes down, whichever way the file writes it
            parts.append((seconds, low, high, cadence, tag if tag != "ramp" else None))
        elif tag == "steadystate":
            power = num(e, "Power")
            if power is None:
                power = (num(e, "PowerLow", default=0.5) + num(e, "PowerHigh", default=0.5)) / 2
            parts.append((seconds, power, None, cadence, None))
        elif tag == "intervalst":
            repeat = int(num(e, "Repeat", default=1))
            on = (num(e, "OnDuration", default=0), num(e, "OnPower", "PowerOnHigh", default=1.0), cadence)
            off = (num(e, "OffDuration", default=0), num(e, "OffPower", "PowerOffLow", default=0.5),
                   num(e, "CadenceResting"))
            for _ in range(max(1, min(repeat, MAX_PHASES))):
                parts.append((on[0], on[1], None, on[2], None))
                parts.append((off[0], off[1], None, off[2], "rest"))
        elif tag in ("freeride", "free"):
            parts.append((seconds, FREE_RIDE_PCT, None, cadence, None))
            notes.append(f"free ride parts became {round(FREE_RIDE_PCT * 100)} % FTP")
        elif tag == "maxeffort":
            parts.append((seconds, MAX_EFFORT_PCT, None, cadence, None))
            notes.append(f"max efforts became {round(MAX_EFFORT_PCT * 100)} % FTP")
        else:
            skipped.add(e.tag)
    if skipped:
        notes.append("skipped " + ", ".join(sorted(skipped)))
    return name, parts, list(dict.fromkeys(notes))


def _erg(text: str, ext: str, ftp: int):
    header, data, section = {}, [], None
    columns = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        upper = line.upper()
        if upper.startswith("[") and upper.endswith("]"):
            section = upper.strip("[]").strip()
            continue
        if section == "COURSE HEADER":
            if "=" in line:
                key, value = line.split("=", 1)
                header[key.strip().upper()] = value.strip()
            elif "MINUTES" in upper or "SECONDS" in upper:
                columns = upper
        elif section == "COURSE DATA":
            fields = re.split(r"[\s,]+", line)
            try:
                data.append((float(fields[0]), float(fields[1])))
            except (IndexError, ValueError):
                continue
    if not data:
        raise WorkoutFileError(f"that {ext} file has no [COURSE DATA] points")
    percent = "PERCENT" in columns if columns else ext == ".mrc"
    per_second = "SECONDS" in columns
    if percent:
        scale = 0.01
    else:
        try:
            file_ftp = float(header.get("FTP", ""))
        except ValueError:
            file_ftp = 0
        scale = 1 / (file_ftp if file_ftp > 0 else ftp)
    parts = []
    for (t0, v0), (t1, v1) in zip(data, data[1:]):
        seconds = (t1 - t0) * (1 if per_second else 60)
        if seconds <= 0:
            continue  # two points at one time: a step
        start, end = v0 * scale, v1 * scale
        parts.append((seconds, start, None if math.isclose(start, end, abs_tol=0.005) else end, None, None))
    name = header.get("DESCRIPTION") or header.get("FILE NAME") or ""
    name = PurePath(name).stem if name.lower().endswith(EXTENSIONS) else name
    return name, parts, []


# ---------- parts to phases ----------


def _zone_kind(pct: float) -> str:
    kinds = ("recovery", "endurance", "tempo", "sweetspot", "threshold")
    for kind, top in zip(kinds, ZONE_TOPS):
        if pct <= top + 1e-9:
            return kind
    return "sprint" if pct > SPRINT_FROM else "vo2"


def _phases(parts: list, notes: list[str]) -> list[dict]:
    clamped = False
    phases = []
    for seconds, start, end, cadence, hint in parts:
        seconds = round(seconds)
        if seconds <= 0:
            continue
        values = [start] + ([end] if end is not None else [])
        if any(not 0.2 <= v <= 2.5 for v in values):
            clamped = True
        start = min(max(start, 0.2), 2.5)
        end = None if end is None else min(max(end, 0.2), 2.5)
        if hint in ("warmup", "cooldown"):
            kind = hint
        else:
            kind = _zone_kind(max(start, end or 0))
            if hint == "rest" and kind == "endurance":
                kind = "recovery"  # the easy part of an interval; a hard "off" part (over-unders) keeps its zone
        cadence = round(cadence) if cadence and 30 <= cadence <= 160 else None
        phase = {"kind": kind, "seconds": seconds, "pct": round(start, 4),
                 "pct_end": None if end is None else round(end, 4), "cadence": cadence}
        prev = phases[-1] if phases else None
        if prev and prev["pct_end"] is None and phase["pct_end"] is None and prev["kind"] == kind \
                and prev["pct"] == phase["pct"] and prev["cadence"] == cadence:
            prev["seconds"] += seconds  # one steady block, however the file split it
        else:
            phases.append(phase)
    if clamped:
        notes.append("power below 20 % or above 250 % FTP was limited to that range")
    _fold_short(phases, notes)
    if phases and len(phases) > 1:  # an easy start and finish read as warm-up and cool-down
        first, last = phases[0], phases[-1]
        easy = ("recovery", "endurance")
        if first["kind"] in easy and (first["pct_end"] is None or first["pct_end"] >= first["pct"]):
            first["kind"] = "warmup"
        if last["kind"] in easy and (last["pct_end"] is None or last["pct_end"] <= last["pct"]):
            last["kind"] = "cooldown"
    return phases


def _fold_short(phases: list[dict], notes: list[str]) -> None:
    """Parts under 10 s join the phase before them (or after, at the start)."""
    i, folded = 0, False
    while i < len(phases) and len(phases) > 1:
        if phases[i]["seconds"] < MIN_SECONDS:
            target = phases[i - 1] if i > 0 else phases[i + 1]
            target["seconds"] += phases[i]["seconds"]
            del phases[i]
            folded = True
            continue
        i += 1
    if phases and phases[0]["seconds"] < MIN_SECONDS:
        phases[0]["seconds"] = MIN_SECONDS
        folded = True
    if folded:
        notes.append("steps shorter than 10 s were joined to the one before")
