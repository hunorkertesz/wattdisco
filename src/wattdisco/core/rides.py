"""Recorded rides on disk: list, summary (cached), .fit export, delete.

Real rides live in rides/ and make up the history. A simulated ride (--sim, sim sensors or a sped-up
clock) is not kept: it goes to sim-ride/ next to it, which holds only the latest one, so its summary,
.fit, CSV and comparison still open right after the ride, but it never shows up in the history or
the progress numbers. The folder is emptied when the next simulated ride starts and at app start.
"""

import json
import os
import time
from pathlib import Path

from wattdisco.core.compare import CompareBench
from wattdisco.core.recorder import RIDE_ID, load_ride, ride_path
from wattdisco.core.summary import SUMMARY_VERSION, build_summary
from wattdisco.export.csv_export import ride_csv
from wattdisco.export.fit import build_fit


def _source_row(sources: dict | None) -> dict | None:
    """The main power source against the second one, without the series (for trends across rides)."""
    if not sources:
        return None
    keys = ("a_id", "a_name", "b_id", "b_name", "mean_diff", "mean_diff_pct", "lag_s", "correlation", "seconds")
    return {k: sources.get(k) for k in keys}


SUFFIXES = (".jsonl", ".summary.json", ".fit", ".ride.fit", ".strava.json", ".notes.json")
TRASH_DAYS = 7  # a deleted ride can be brought back for a week
NOTE_CHARS = 2000


class RideStore:
    def __init__(self, rides_dir: Path) -> None:
        self.dir = rides_dir
        self.sim_dir = rides_dir.parent / "sim-ride"
        self._rows: dict[str, tuple[tuple, dict]] = {}  # ride id -> (file times, history line)

    def exists(self, ride_id: str) -> bool:
        return RIDE_ID.match(ride_id) is not None and self.path(ride_id).exists()

    def path(self, ride_id: str, suffix: str = ".jsonl") -> Path:
        """A ride's file: in rides/, or in sim-ride/ for the latest simulated ride."""
        sim = ride_path(self.sim_dir, ride_id)
        return ride_path(self.sim_dir if sim.exists() else self.dir, ride_id, suffix)

    def new_path(self, ride_id: str, sim: bool) -> Path:
        """Where a new ride is recorded. A simulated one replaces the last simulated one."""
        if not sim:
            return ride_path(self.dir, ride_id)
        self.clear_sim()
        return ride_path(self.sim_dir, ride_id)

    def clear_sim(self) -> None:
        if self.sim_dir.exists():
            for f in self.sim_dir.iterdir():
                if f.is_file():
                    f.unlink(missing_ok=True)

    def list(self, exclude: str | None = None, include_sim: bool = False) -> list[dict]:
        """The history, newest first: real rides only. `include_sim` adds simulated ones (the latest in
        sim-ride/, and any recorded in rides/ before they were kept apart), for "open the last ride"."""
        paths = list(self.dir.glob("*.jsonl")) + (list(self.sim_dir.glob("*.jsonl")) if include_sim and self.sim_dir.exists() else [])
        rides = []
        for path in sorted(paths, key=lambda p: p.stem, reverse=True):
            if not RIDE_ID.match(path.stem) or path.stem == exclude:
                continue
            try:
                row = self._cached_row(path.stem)
            except (OSError, ValueError, KeyError):
                continue
            if include_sim or not row.get("sim"):
                rides.append(row)
        return rides

    def _file_times(self, ride_id: str) -> tuple:
        paths = (self.path(ride_id, s) for s in (".jsonl", ".summary.json", ".strava.json", ".notes.json"))
        return tuple(p.stat().st_mtime_ns if p.exists() else None for p in paths)

    def _cached_row(self, ride_id: str) -> dict:
        """Parsing every full summary takes about 20 ms a ride, so the lines are kept until a file changes."""
        hit = self._rows.get(ride_id)
        if hit and hit[0] == self._file_times(ride_id):
            return hit[1]
        row = self._row(self.summary(ride_id))
        self._rows[ride_id] = (self._file_times(ride_id), row)
        return row

    def _row(self, summary: dict) -> dict:
        """One line of the ride history: the headline numbers, not the series."""
        basic, nerd = summary.get("basic") or {}, summary.get("nerd") or {}
        upload = self.upload_status(summary["id"])
        return {
            **{k: summary.get(k) for k in ("id", "name", "started", "duration", "ftp", "sim")},
            "test": (summary.get("workout") or {}).get("test"),
            "climb": bool((summary.get("workout") or {}).get("climb")),
            **{k: basic.get(k) for k in ("avg_power", "avg_hr", "distance_km", "work_kj", "max_hr", "avg_cadence",
                                         "avg_speed_kmh")},
            **{k: nerd.get(k) for k in ("wp", "load", "intensity", "vi", "on_target_pct", "hr_drift_pct")},
            # for comparing rides: the workout, the aerobic threshold and the left share
            "workout_id": summary.get("workout_id"),
            "aet_hr": ((summary.get("dfa") or {}).get("threshold") or {}).get("hr"),
            "balance_left": (summary.get("balance") or {}).get("left"),
            "bests": summary.get("bests") or {},
            "sources": _source_row(summary.get("sources")),
            "ftp_estimate": summary.get("ftp_estimate"),
            "strava": {"state": upload["state"], "url": upload.get("url"), "message": upload.get("message")}
            if upload["state"] != "none" else None,
            **{k: v for k, v in self.notes(summary["id"]).items() if k in ("rpe", "note")},
        }

    def summary(self, ride_id: str) -> dict:
        cache = self.path(ride_id, ".summary.json")
        source = self.path(ride_id)
        if cache.exists() and cache.stat().st_mtime >= source.stat().st_mtime:
            cached = json.loads(cache.read_text())
            if cached.get("version") == SUMMARY_VERSION:
                return cached
        summary = build_summary(load_ride(source))
        cache.write_text(json.dumps(summary))
        return summary

    def fit_path(self, ride_id: str, sport_type: str = "VirtualRide") -> Path:
        """The .fit file, written on first use and rebuilt whenever its summary is newer.

        Virtual Ride is the normal file; a plain Ride (indoor cycling) gets its own copy for upload.
        """
        path = self.path(ride_id, ".fit" if sport_type == "VirtualRide" else ".ride.fit")
        source = self.path(ride_id)
        summary = self.summary(ride_id)
        cache = self.path(ride_id, ".summary.json")
        if not path.exists() or path.stat().st_mtime < max(source.stat().st_mtime, cache.stat().st_mtime):
            ride = load_ride(source)
            if not ride.ticks:
                raise ValueError("ride too short to export")
            path.write_bytes(build_fit(ride, summary, sport_type))
        return path

    def csv(self, ride_id: str) -> str:
        """The whole ride, one row per second, every sensor in its own columns."""
        return ride_csv(load_ride(self.path(ride_id)), self.summary(ride_id))

    def compare(self, ride_id: str, reference: dict[str, str | None] | None = None) -> CompareBench:
        """The ride's sensor comparison, rebuilt from every sensor's recorded samples."""
        return CompareBench.from_ride(load_ride(self.path(ride_id)), reference)

    def finish(self, ride_id: str) -> None:
        """Called once after a ride stops: build the summary and the .fit right away."""
        self.summary(ride_id)
        try:
            self.fit_path(ride_id)
        except ValueError:
            pass

    def upload_status(self, ride_id: str) -> dict:
        path = self.path(ride_id, ".strava.json")
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {"state": "none"}

    def set_upload_status(self, ride_id: str, status: dict) -> None:
        self.path(ride_id, ".strava.json").write_text(json.dumps(status))

    def notes(self, ride_id: str) -> dict:
        """How the ride felt (RPE 1–10) and the rider's note."""
        try:
            return json.loads(self.path(ride_id, ".notes.json").read_text())
        except (OSError, ValueError):
            return {}

    def set_notes(self, ride_id: str, rpe: int | None, note: str) -> dict:
        data = {"rpe": rpe, "note": note.strip()[:NOTE_CHARS]}
        if rpe is not None and not 1 <= rpe <= 10:
            raise ValueError("feel must be 1 to 10")
        self.path(ride_id, ".notes.json").write_text(json.dumps(data))
        return data

    @property
    def trash(self) -> Path:
        return self.dir.parent / "deleted"

    def delete(self, ride_id: str) -> None:
        """Move the ride's files to deleted/ (undo with restore); purge_deleted() empties it later."""
        self.trash.mkdir(parents=True, exist_ok=True)
        folder = self.path(ride_id).parent  # rides/ or sim-ride/, decided before the ride file moves
        for suffix in SUFFIXES:
            path = ride_path(folder, ride_id, suffix)
            if path.exists():
                path.replace(self.trash / path.name)
                os.utime(self.trash / path.name)  # the time it was deleted, for purging
        self._rows.pop(ride_id, None)

    def restore(self, ride_id: str) -> bool:
        """Bring a deleted ride back into rides/."""
        moved = False
        self.dir.mkdir(parents=True, exist_ok=True)
        for suffix in SUFFIXES:
            src = ride_path(self.trash, ride_id, suffix)
            if src.exists():
                src.replace(ride_path(self.dir, ride_id, suffix))
                moved = True
        return moved

    def purge_deleted(self, days: int = TRASH_DAYS) -> None:
        """Delete for good what was deleted more than `days` ago."""
        if not self.trash.exists():
            return
        cutoff = time.time() - days * 86400
        for f in self.trash.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
