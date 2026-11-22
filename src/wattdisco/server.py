"""FastAPI app: serves the pages in wattdisco/web, pushes live state over a WebSocket, takes commands back.

The browser is only a view. All ride logic lives here and in core/.
"""

import asyncio
import contextlib
import json
import logging
import math
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from wattdisco.awake import KeepAwake, NoKeepAwake
from wattdisco.config import (
    DATA_DIR,
    FTP_RANGE,
    HR_METHODS,
    KnownDevices,
    Settings,
    hr_zone_of,
    hr_zones,
    WEB_DIR,
    save_settings,
    zone_of,
    zones,
)
from wattdisco.core.bus import Bus
from wattdisco.core.clock import Clock
from wattdisco.core.compare import CompareBench
from wattdisco.core.metrics import metric_value
from wattdisco.core import plans, progress
from wattdisco.core.ftp_history import FtpHistory, rebuild
from wattdisco.core.recorder import RECORDED_TOPICS, RIDE_ID, Recorder, ended, load_ride, new_ride_id
from wattdisco.core.rides import RideStore
from wattdisco.core.session import Session
from wattdisco.core.speed import FlatRoad
from wattdisco.core.sources import SensorSource
from wattdisco.core.workout import MODES, Position, WorkoutPlayer
from wattdisco.core.workout_import import WorkoutFileError, import_workout
from wattdisco.core.workouts import (
    CATEGORIES,
    DEFAULT_WORKOUT_ID,
    KINDS,
    PHASE_DEFAULTS,
    TESTS,
    WorkoutStore,
    describe,
    to_workout,
)
from wattdisco.export.strava import SPORT_TYPES, Strava, StravaError
from wattdisco.models import Device, Phase, TrainerCommand, Workout

log = logging.getLogger(__name__)

PUSH_INTERVAL = 0.25  # 4 Hz, in real seconds
PLAY_INTERVAL = 0.25  # workout player tick, in ride seconds
SEND_TIMEOUT = 1.0
ON_TARGET_BAND = 0.05
AUTO_PAUSE_SECONDS = 5  # ride seconds without pedalling before the ride pauses on its own
AUTO_RESUME_SECONDS = 2  # ride seconds of pedalling before it goes on again
PEDAL_WATTS = 20
PEDAL_RPM = 20
AVG_WINDOWS = (30, 60, 300, 600)  # seconds: the nerd-mode averages the rider can pick from


def recent_average(values: deque, seconds: int) -> float | None:
    """Average of the last `seconds` values that are not None (a dropout leaves a gap, not a zero)."""
    tail = [v for v in list(values)[-seconds:] if v is not None]
    return sum(tail) / len(tail) if tail else None


def device_kind(capabilities) -> str:
    caps = set(capabilities)
    if "control" in caps:
        return "Smart trainer"
    if "power" in caps:
        return "Power meter"
    if "heart_rate" in caps:
        return "Heart rate"
    if "temperature" in caps:
        return "Core body temperature"
    return "Sensor"


def default_description(summary: dict) -> str:
    parts = ["Indoor ride with Watt Disco", summary["name"]]
    if summary["nerd"].get("wp"):
        parts.append(f"Weighted power {round(summary['nerd']['wp'])} W")
    if summary["nerd"].get("load"):
        parts.append(f"Training load {round(summary['nerd']['load'])}")
    return " · ".join(parts)


def workout_payload(pos: Position, power: int | None, ftp: int, mode: str, erg: str = "off",
                    adjustments: dict[int, int] | None = None, ease: bool = False) -> dict:
    """Player position plus what the screen needs: target zone, distance to the target,
    and the rider's changes to phases (W by phase index) so the chart can draw them."""
    diff = None if power is None or pos.target is None else power - pos.target
    return {
        **pos.to_dict(),
        "mode": mode,
        "erg": erg,
        "ease": ease,  # ERG eases big target changes in (chosen on the ready screen)
        "target_zone": None if pos.target is None else zone_of(pos.target, ftp),
        "diff": diff,
        "on_target": None if diff is None else abs(diff) <= ON_TARGET_BAND * pos.target,
        "adjustments": {str(i): w for i, w in (adjustments or {}).items()},
    }


class Ride:
    """The live app: sensors, session, connected browsers, and the ride being recorded.

    Sensors and session run all the time. A ride (player, chart history, recorder)
    starts with start_ride() and ends with stop_ride(), which writes the summary and .fit.
    """

    def __init__(
        self,
        source: SensorSource,
        workout: Workout,
        clock: Clock,
        smoothing: str = "3s",
        settings: Settings | None = None,
        workout_factory: Callable[[int], Workout] | None = None,
        data_dir: Path = DATA_DIR,
        mode: str = "erg",
        autostart: bool = True,
        strava: Strava | None = None,
        known: KnownDevices | None = None,
        awake: KeepAwake | None = None,
    ) -> None:
        self.source = source
        self.awake = awake or NoKeepAwake()  # the screen stays on while a ride runs
        self.auto_paused: str | None = None  # "stopped" (no pedalling) or "waiting" (start when you pedal)
        self._still = 0
        self._pedalling = 0
        self.workout = workout
        self.clock = clock
        self.settings = settings or Settings(ftp=workout.ftp)
        self.workout_factory = workout_factory  # rebuilds the workout for a new FTP
        self.data_dir = data_dir
        self.bus = Bus()
        self.session = Session(self.bus, clock, smoothing=smoothing)
        self.player = WorkoutPlayer(workout, mode=mode)
        self.history: list[tuple[int, int | None, int | None]] = []  # (workout t, main power raw, main hr)
        self.clients: set[WebSocket] = set()
        self.store = RideStore(data_dir / "rides")
        self.store.clear_sim()  # a simulated ride is only kept until the next one or the next start
        self.store.purge_deleted()  # rides deleted more than a week ago go for good
        self.autostart = autostart
        self.active = False
        self.ride_id: str | None = None
        self.last_ride_id: str | None = None
        # The finished ride whose summary TRAIN shows until the rider discards it, uploads it or starts
        # the next ride (kept on disk, so a restart does not lose it).
        self._review_path = data_dir / "review.json"
        self.review_id: str | None = self._load_review()
        # A ride the app never stopped (a crash, the power went): offered to continue or save as it is.
        self.interrupted: dict | None = self._find_interrupted()
        self.ride_start = 0.0  # clock time when the ride started
        self.recorder: Recorder | None = None
        self.paused = False
        self.road: FlatRoad | None = None  # estimated flat-road speed for this ride
        self._recent_power: deque[int | None] = deque(maxlen=AVG_WINDOWS[-1])  # main power, last 10 ride minutes
        self._recent_hr: deque[int | None] = deque(maxlen=AVG_WINDOWS[-1])
        self._interval = {"index": None, "seconds": 0, "on_target": 0}  # current phase so far
        self.strava = strava or Strava(None, data_dir)
        self.uploads: dict[str, asyncio.Task] = {}
        self.uploading: dict[str, dict] = {}  # status of uploads in progress (kept in memory)
        self.known = known or KnownDevices(data_dir)
        self.workouts = WorkoutStore(data_dir / "workouts")
        self.workout_id = DEFAULT_WORKOUT_ID  # the library workout behind self.workout
        self.ftp_history = FtpHistory(data_dir)
        self.plans = plans.PlanStore(data_dir)
        self.plan = self.plans.load()  # the rider's training plan, None without one
        self.select_plan_workout()
        self.bench = CompareBench(self.bus, clock, name_of=self._device_name)
        self.compare_clients: set[WebSocket] = set()  # browsers with the sensor comparison open
        self.bus.subscribe("device", self._on_device)
        self.bus.subscribe_all(self._record)

    def _load_review(self) -> str | None:
        try:
            ride_id = json.loads(self._review_path.read_text()).get("ride")
        except (OSError, ValueError, AttributeError):
            return None
        return ride_id if ride_id and self.store.exists(ride_id) else None

    def set_review(self, ride_id: str | None) -> None:
        """The ride TRAIN goes back to (None: TRAIN goes to the ready screen)."""
        self.review_id = ride_id
        try:
            if ride_id is None:
                self._review_path.unlink(missing_ok=True)
            else:
                self._review_path.parent.mkdir(parents=True, exist_ok=True)
                self._review_path.write_text(json.dumps({"ride": ride_id}))
        except OSError:
            log.warning("could not save the ride to review")

    def _save_soon(self, save: Callable[[], None]) -> None:
        """Run a small file write in a thread, so the event loop never waits for the disk."""
        try:
            asyncio.get_running_loop().run_in_executor(None, save)
        except RuntimeError:  # no loop (plain function call in a test): just write
            save()

    def _record(self, topic: str, payload) -> None:
        if self.recorder is not None and topic in RECORDED_TOPICS and (not self.paused or topic == "device"):
            self.recorder.record(topic, payload, self.clock.now() - self.ride_start)

    def use_workout(self, workout_id: str, spec: dict | None = None) -> None:
        """Ride this library workout next, in watts for the current FTP (rescaled if FTP changes)."""
        spec = spec or self.workouts.get(workout_id)
        self.workout_id = workout_id
        self.workout_factory = lambda ftp: to_workout(spec, ftp)
        self.workout = self.workout_factory(self.settings.ftp)
        set_workout = getattr(self.source, "set_workout", None)
        if set_workout:
            set_workout(self.workout)

    def select_plan_workout(self) -> None:
        """Line up the plan's next session (never mid-ride); the rider can still pick another workout."""
        workout_id = plans.next_workout(self.plan)
        if workout_id and not self.active:
            self.use_workout(workout_id)

    def save_plan(self, state: dict | None) -> None:
        """Keep the plan in memory and on disk, and line up its next session."""
        self.plan = state
        if state is None:
            self.plans.clear()
        else:
            self.plans.save(state)
        self.select_plan_workout()

    def start_ride(self, mode: str | None = None, smoothing: str | None = None, power_source: str | None = None,
                   ease: bool = False, wait_for_pedal: bool = False) -> str:
        if self.active:
            raise ValueError("a ride is already running")
        if self.review_id is not None:
            self.set_review(None)  # a new ride: the last one's summary is done with
        if self.interrupted is not None:
            self.save_interrupted()  # not continued: keep it as it is, in the history
        if mode is not None and mode not in MODES:
            raise ValueError(f"mode must be one of {list(MODES)}")
        if smoothing is not None:
            self.session.set_smoothing(smoothing)
        if power_source is not None:
            self.session.set_main_power(power_source)
        connected = [d for d in self.session.devices.values() if d.state == "connected"]
        if connected:
            self.known.remember(connected, save=False)  # reconnect these at the next launch
            self._save_soon(self.known.save)
        started = datetime.now(timezone.utc).replace(microsecond=0)
        ride_id = new_ride_id(started.astimezone())
        while self.store.exists(ride_id):  # two rides in the same second
            started += timedelta(seconds=1)
            ride_id = new_ride_id(started.astimezone())
        now = self.clock.now()
        self.ride_start = now
        self.player = WorkoutPlayer(self.workout, mode=mode or self.player.mode, start=now, ease=ease)
        self.history = []
        self.road = FlatRoad(self.settings.weight_kg)
        self._recent_power = deque(maxlen=AVG_WINDOWS[-1])
        self._recent_hr = deque(maxlen=AVG_WINDOWS[-1])
        self._interval = {"index": None, "seconds": 0, "on_target": 0}
        sim = self.clock.speed != 1 or any(d.id.startswith("sim-") for d in self.session.devices.values())
        self.recorder = Recorder(
            self.store.new_path(ride_id, sim),  # a simulated ride is kept apart, never in the history
            {
                "id": ride_id,
                "started": started.isoformat(),
                "workout": {**asdict(self.workout), "seconds": self.workout.seconds},
                "workout_id": self.workout_id,  # the library workout, so a training plan can tick it off
                "ftp": self.settings.ftp,
                "weight_kg": self.settings.weight_kg,
                "hr_zones": {"method": self.settings.hr_method, "reference": self.settings.hr_reference},
                "speed": self.clock.speed,
                "erg_ease": ease,
                "sim": sim,
            },
        )
        for device in self.session.devices.values():  # names and roles of what is already connected
            self.recorder.record("device", device, 0.0)
        self.ride_id = ride_id
        self.active = True
        self.paused = False
        self.awake.hold()
        # the sensor comparison runs with the ride, the main sensors as reference
        self.bench.start(now, ride_id, {"power": self.session.main_power_id, "hr": self.session.main_hr_id})
        self.auto_paused, self._still, self._pedalling = None, 0, 0
        if wait_for_pedal:
            self.pause(auto="waiting")  # the clock starts with the first pedal strokes
        self._tick()
        return ride_id

    def _find_interrupted(self) -> dict | None:
        """The newest real ride, if it has no end line and started in the last 24 hours."""
        paths = sorted(self.store.dir.glob("*.jsonl"), key=lambda p: p.stem, reverse=True) if self.store.dir.exists() else []
        if not paths:
            return None
        try:
            if ended(paths[0]):
                return None
            data = load_ride(paths[0])
            started = datetime.fromisoformat(data.meta["started"])
            if data.meta.get("sim") or not data.ticks or datetime.now(timezone.utc) - started > timedelta(hours=24):
                return None
            last = data.ticks[-1]
            return {"id": data.id, "name": data.meta["workout"]["name"], "workout_id": data.meta.get("workout_id"),
                    "t": last["t"], "elapsed": last.get("elapsed", last["t"]),
                    "seconds": data.meta["workout"].get("seconds"), "started": data.meta["started"]}
        except (OSError, ValueError, KeyError, TypeError):
            return None  # not a ride we can pick up; it stays in the history as it is

    def save_interrupted(self) -> str | None:
        """Close the interrupted ride as it is (end line, summary, .fit) and show its summary."""
        info, self.interrupted = self.interrupted, None
        if info is None:
            return None
        path = self.store.path(info["id"])
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "end", "t": info["t"]}, separators=(",", ":")) + "\n")
        self.store.finish(info["id"])
        self.last_ride_id = info["id"]
        self.set_review(info["id"])
        return info["id"]

    def continue_ride(self) -> str:
        """Pick the interrupted ride up where it stopped: same file, same workout, the clock where it was.

        It starts paused until the rider pedals; the time in between counts as a pause."""
        if self.active:
            raise ValueError("a ride is already running")
        info = self.interrupted
        if info is None:
            raise ValueError("no interrupted ride")
        data = load_ride(self.store.path(info["id"]))
        spec = data.meta["workout"]
        self.workout = Workout(spec["name"], spec["ftp"], [Phase(**{k: p[k] for k in ("kind", "seconds", "watts", "watts_end", "cadence") if k in p}) for p in spec["phases"]],
                               spec.get("test"))
        self.workout_id = data.meta.get("workout_id")
        set_workout = getattr(self.source, "set_workout", None)
        if set_workout:
            set_workout(self.workout)
        if self.review_id is not None:
            self.set_review(None)
        self.interrupted = None
        now = self.clock.now()
        self.ride_start = now - info["t"]  # the recording's clock goes on from its last second
        mode = next((t["mode"] for t in reversed(data.ticks) if t.get("mode")), self.player.mode)
        self.player = WorkoutPlayer(self.workout, mode=mode, start=now - info["elapsed"], ease=bool(data.meta.get("erg_ease")))
        self.history = [(round(t.get("elapsed", t["t"])), t.get("power"), t.get("hr")) for t in data.ticks]
        self.road = FlatRoad(data.meta.get("weight_kg", self.settings.weight_kg))
        self._recent_power = deque((t.get("power") for t in data.ticks), maxlen=AVG_WINDOWS[-1])
        self._recent_hr = deque((t.get("hr") for t in data.ticks), maxlen=AVG_WINDOWS[-1])
        self._interval = {"index": None, "seconds": 0, "on_target": 0}
        self.recorder = Recorder(self.store.path(info["id"]), None)
        for device in self.session.devices.values():
            self.recorder.record("device", device, info["t"])
        self.ride_id = info["id"]
        self.active = True
        self.paused = False
        self.awake.hold()
        self.bench.start(now, info["id"], {"power": self.session.main_power_id, "hr": self.session.main_hr_id})
        self.auto_paused, self._still, self._pedalling = None, 0, 0
        self.pause(auto="waiting")
        self._tick()
        return info["id"]

    def pause(self, auto: str | None = None) -> None:
        """Stop everything: workout time, recording, the trainer's grip. Sensors stay connected.

        `auto` is set when the app paused on its own ("stopped": no pedalling, "waiting": start when you pedal);
        then pedalling resumes it. A pause the rider chose waits for the rider."""
        if not self.active:
            raise ValueError("no ride is running")
        if self.paused:
            if auto is None:
                self.auto_paused = None  # pressing Pause during an auto-pause: now the rider decides
            return
        now = self.clock.now()
        self.player.pause(now)
        self.paused = self.bench.paused = True
        self.auto_paused, self._pedalling = auto, 0
        if self.recorder is not None:
            self.recorder.write({"type": "pause", "t": round(now - self.ride_start, 3), **({"auto": auto} if auto else {})})
        self._tick()

    def resume(self) -> None:
        if not self.active:
            raise ValueError("no ride is running")
        if not self.paused:
            return
        now = self.clock.now()
        self.player.resume(now)
        self.paused = self.bench.paused = False
        self.auto_paused, self._still = None, 0
        if self.recorder is not None:
            self.recorder.write({"type": "resume", "t": round(now - self.ride_start, 3)})
        self._tick()

    async def stop_ride(self) -> str:
        """End the ride, flush the file, and build its summary and .fit."""
        if not self.active or self.recorder is None:
            raise ValueError("no ride is running")
        self.active = False
        self.paused = False
        self.awake.release()
        self.bench.stop()
        recorder, ride_id = self.recorder, self.ride_id
        recorder.write({"type": "end", "t": round(self.clock.now() - self.ride_start, 3)})
        self.recorder = None
        self.bus.publish("trainer", TrainerCommand("free"))  # let the rider spin down freely
        await asyncio.to_thread(recorder.close)
        await asyncio.to_thread(self.store.finish, ride_id)
        self.last_ride_id = ride_id
        self.ride_id = None
        await asyncio.to_thread(self.set_review, ride_id)  # TRAIN shows its summary until it is dealt with
        await self._apply_test(ride_id)
        await self._count_for_plan(ride_id)
        await self._auto_upload(ride_id)
        return ride_id

    async def _apply_test(self, ride_id: str) -> None:
        """An FTP test sets the FTP when it ends (the summary offers undo). Never from a simulated ride."""
        summary = await asyncio.to_thread(self.store.summary, ride_id)
        est = summary.get("ftp_estimate")
        if summary.get("sim") or not est or est["source"] not in TESTS:
            return
        ftp = min(max(est["ftp"], FTP_RANGE[0]), FTP_RANGE[1])
        if ftp != self.settings.ftp:
            log.info("FTP test %s: FTP %d -> %d W", ride_id, self.settings.ftp, ftp)
            await self.update_settings(replace(self.settings, ftp=ftp), source=est["source"], ride_id=ride_id)

    async def _count_for_plan(self, ride_id: str) -> None:
        """Tick off the plan's next session when this ride was it (not simulated, ridden to the end)."""
        if self.plan is None:
            return
        summary = await asyncio.to_thread(self.store.summary, ride_id)
        state = json.loads(json.dumps(self.plan))
        if plans.record_ride(state, summary):
            await asyncio.to_thread(self.save_plan, state)

    async def _auto_upload(self, ride_id: str) -> None:
        """Upload right after the ride when the rider asked for it. Never for simulated rides."""
        if not (self.settings.strava_auto_upload and self.strava.connected):
            return
        summary = await asyncio.to_thread(self.store.summary, ride_id)
        if summary.get("sim") or not summary["duration"]:
            return
        self.start_upload(ride_id, {
            "name": summary["name"], "description": default_description(summary),
            "sport_type": self.settings.strava_sport_type, "private": self.settings.strava_private,
        })

    def start_upload(self, ride_id: str, options: dict) -> None:
        """Upload in the background; the page polls the status."""
        running = self.uploads.get(ride_id)
        if running and not running.done():
            raise ValueError("this ride is already uploading")
        self.uploading[ride_id] = {"state": "uploading", "message": "Uploading to Strava…"}
        self.uploads[ride_id] = asyncio.create_task(self._upload(ride_id, options))

    async def _upload(self, ride_id: str, options: dict) -> None:
        try:
            fit = await asyncio.to_thread(self.store.fit_path, ride_id, options.get("sport_type", "VirtualRide"))
            status = await asyncio.to_thread(self.strava.upload, fit, ride_id, **options)
        except StravaError as e:
            status = {"state": "error", "message": str(e)}
        except Exception as e:  # network down, Strava unavailable, …
            log.exception("Strava upload failed")
            status = {"state": "error", "message": f"Upload failed: {e.__class__.__name__}"}
        status["at"] = datetime.now(timezone.utc).isoformat()
        await asyncio.to_thread(self.store.set_upload_status, ride_id, status)
        self.uploading.pop(ride_id, None)
        if status.get("state") in ("done", "duplicate") and self.review_id == ride_id:
            await asyncio.to_thread(self.set_review, None)  # uploaded: TRAIN is free for the next ride

    def _on_device(self, device: Device) -> None:
        if device.state != "connected":
            return
        # Roles the rider chose last time for this device.
        roles = self.known.roles(device.id)
        if roles is not None and roles <= device.capabilities and roles != device.roles:
            self.session.set_roles(device.id, roles)
        # A trainer that (re)connects gets the current command, even if it has not changed.
        if "control" in device.roles and self.player.last_command and self.active:
            self.bus.publish("trainer", self.player.last_command)

    # ---------- sensor comparison ----------

    def _device_name(self, device_id: str) -> str:
        device = self.session.devices.get(device_id)
        return device.name if device else device_id

    def bench_candidates(self) -> dict:
        """Connected devices that measure power / heart rate, main sensor first."""
        connected = [d for d in self.session.devices.values() if d.state == "connected"]

        def ordered(capability: str, main: str | None) -> list[dict]:
            devices = [d for d in connected if capability in d.capabilities]
            devices.sort(key=lambda d: d.id != main)
            return [{"id": d.id, "name": d.name} for d in devices]

        return {"power": ordered("power", self.session.main_power_id),
                "hr": ordered("heart_rate", self.session.main_hr_id)}

    def bench_snapshot(self) -> dict:
        return {**self.bench.snapshot(), "candidates": self.bench_candidates()}

    # ---------- devices ----------

    def device_list(self) -> dict:
        """Everything the connect screen needs about devices."""
        snap = self.session.snapshot()
        known = self.known.devices
        found = {d.id for d in snap.devices}
        return {
            "scanning": bool(getattr(self.source, "scanning", False)),
            "auto_connect": self.known.auto_connect,
            "devices": [
                {**asdict(d), "kind": device_kind(d.capabilities), "remembered": d.id in known,
                 "zeroed_at": known.get(d.id, {}).get("zeroed_at"), "zero_offset": known.get(d.id, {}).get("zero_offset")}
                for d in snap.devices
            ],
            "remembered": [
                {"id": i, "name": k.get("name", i), "kind": device_kind(k.get("capabilities", [])),
                 "capabilities": k.get("capabilities", []),
                 "found": i in found,
                 "connected": any(d.id == i and d.state == "connected" for d in snap.devices)}
                for i, k in known.items()
            ],
            "main_power_id": snap.main_power_id,
            "main_hr_id": snap.main_hr_id,
        }

    def set_roles(self, device_id: str, roles: set[str]) -> None:
        self.session.set_roles(device_id, roles)
        self.known.update(device_id, roles=sorted(roles), save=False)
        self._save_soon(self.known.save)

    async def zero_offset(self, device_id: str) -> dict:
        offset = await self.source.zero_offset(device_id)
        at = datetime.now(timezone.utc).isoformat()
        device = self.session.devices.get(device_id)
        if device and device_id not in self.known.devices:  # zeroed before its first ride: remember it now
            self.known.devices[device_id] = {"id": device_id, "name": device.name, "roles": sorted(device.roles),
                                             "capabilities": sorted(device.capabilities)}
        self.known.update(device_id, zeroed_at=at, zero_offset=offset, save=False)
        await asyncio.to_thread(self.known.save)
        return {"offset": offset, "at": at}

    def _count_nerd(self, pos: Position, power: int | None) -> None:
        """Running numbers for nerd mode: recent averages, on-target share of this phase."""
        self._recent_power.append(power)
        if pos.index != self._interval["index"]:
            self._interval = {"index": pos.index, "seconds": 0, "on_target": 0}
        if pos.target and power is not None:
            self._interval["seconds"] += 1
            self._interval["on_target"] += abs(power - pos.target) <= ON_TARGET_BAND * pos.target

    def nerd(self, workout: dict) -> dict:
        interval = self._interval
        same = interval["index"] == workout.get("index") and interval["seconds"] > 0
        target = workout.get("target")
        diff = workout.get("diff")
        return {
            "diff_pct": None if diff is None or not target else 100 * diff / target,
            "on_target_interval_pct": 100 * interval["on_target"] / interval["seconds"] if same else None,
            "avg": {str(n): recent_average(self._recent_power, n) for n in AVG_WINDOWS},
            "hr_avg": {str(n): recent_average(self._recent_hr, n) for n in AVG_WINDOWS},
            "source_diff": self.session.power_difference(),
        }

    def state(self) -> dict:
        snap = self.session.snapshot()
        ftp = self.settings.ftp
        w_per_kg = None if snap.power is None else round(snap.power / self.settings.weight_kg, 2)
        snap.metrics.append(metric_value("w_per_kg", w_per_kg))
        if self.road is not None:
            snap.metrics.append(metric_value("speed", round(self.road.speed * 3.6, 1)))
            snap.metrics.append(metric_value("distance", round(self.road.distance / 1000, 2)))
        return {
            "type": "state",
            **asdict(snap),
            "power_zone": None if snap.power is None else zone_of(snap.power, ftp),
            "hr_zone": None if snap.hr is None else hr_zone_of(snap.hr, self.settings.hr_tops),
            "workout": (workout := workout_payload(self.player.position(snap.t), snap.power, ftp, self.player.mode,
                                                 self.player.erg_state, self.player.adjustments, self.player.ease)),
            "nerd": self.nerd(workout) if self.active else None,
            "point": self.history[-1] if self.history else None,
            "ride": {"active": self.active, "paused": self.paused, "auto_paused": self.auto_paused, "id": self.ride_id,
                     "interrupted": self.interrupted, "last_id": self.last_ride_id,
                     "review_id": self.review_id},
        }

    def hello(self) -> dict:
        return {
            "type": "hello",
            "workout": {**asdict(self.workout), "seconds": self.workout.seconds},
            "history": self.history,
            "speed": self.clock.speed,
            "settings": self.settings.to_dict(),
            "state": self.state(),
        }

    def ftp_entries(self, current: int | None = None) -> list[dict]:
        """The FTP history; the first time, rebuilt from the rides recorded before it existed."""
        if not self.ftp_history.exists():
            rows = self.store.list(exclude=self.ride_id)
            self.ftp_history.save(rebuild(rows, self.settings.ftp if current is None else current))
        return self.ftp_history.load()

    def _log_ftp(self, before: int, ftp: int, source: str, ride_id: str | None) -> None:
        self.ftp_entries(current=before)
        self.ftp_history.add(ftp, before, source, ride_id)

    async def update_settings(self, settings: Settings, source: str = "manual", ride_id: str | None = None) -> None:
        """Save new settings, rescale the built-in workout to the new FTP, refresh every browser.

        An FTP change goes into the FTP history with its source (a test, a ride's best 20 min, or by hand).
        """
        await asyncio.to_thread(save_settings, settings, self.data_dir)
        ftp_changed = settings.ftp != self.settings.ftp
        if ftp_changed:
            await asyncio.to_thread(self._log_ftp, self.settings.ftp, settings.ftp, source, ride_id)
        self.settings = settings
        if ftp_changed and self.workout_factory is not None:
            self.workout = self.workout_factory(settings.ftp)
            self.player.set_workout(self.workout)
            set_workout = getattr(self.source, "set_workout", None)
            if set_workout:
                set_workout(self.workout)
        text = json.dumps(self.hello())
        await asyncio.gather(*(self._send(ws, text) for ws in list(self.clients)))

    def command(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        if cmd == "set_smoothing":
            self.session.set_smoothing(msg["value"])
        elif cmd == "set_main_power":
            self.session.set_main_power(msg["device_id"])
        elif cmd == "set_main_hr":
            self.session.set_main_hr(msg["device_id"])
        elif cmd == "set_main_cadence":
            self.session.set_main_cadence(msg.get("device_id"))
        elif cmd == "set_mode":
            self.player.set_mode(msg["value"])
            self._tick()
        elif cmd == "pause":
            self.pause()
        elif cmd == "resume":
            self.resume()
        elif cmd == "skip_phase":
            self.player.skip_phase(self.clock.now())
            self._tick()
        elif cmd == "adjust_phase":
            self.player.adjust_phase(self.clock.now(), int(msg["steps"]))
            self._tick()
        else:
            raise ValueError(f"unknown command {cmd!r}")

    def _tick(self) -> None:
        """Tell the sim where the rider is, and the trainer what to do if that changed."""
        if not self.active:
            return
        now = self.clock.now()
        snap = self.session.snapshot()
        cmd = self.player.command(now, cadence=snap.cadence, power=snap.power_raw)
        self.bus.publish("workout", self.player.position(now))
        if cmd is not None:
            self.bus.publish("trainer", cmd)

    async def _play(self) -> None:
        while True:
            self._tick()
            await self.clock.sleep(PLAY_INTERVAL)

    async def _record_ticks(self) -> None:
        """Once per ride second: chart history point and a tick line in the recording."""
        last = (None, 0)  # ride start and second of the last tick
        while True:
            if not self.active:
                await self.clock.sleep(PLAY_INTERVAL)
                continue
            start = self.ride_start
            n = math.floor(self.clock.now() - start) + 1
            if last[0] == start:
                n = max(n, last[1] + 1)  # a sleep can wake early (Windows timers): never the same second twice
            last = (start, n)
            await self.clock.sleep(start + n + 0.05 - self.clock.now())
            if self.active and self.ride_start == start:
                self._auto_pause_step()
            if self.active and self.ride_start == start and not self.paused:
                self._write_tick(n)

    def _auto_pause_step(self) -> None:
        """Once per ride second: pause when the rider stops pedalling, go on when they pedal again."""
        snap = self.session.snapshot()
        pedalling = (snap.power_raw or 0) >= PEDAL_WATTS or (snap.cadence or 0) >= PEDAL_RPM
        if self.paused:
            if self.auto_paused is None:
                return
            self._pedalling = self._pedalling + 1 if pedalling else 0
            if self._pedalling >= AUTO_RESUME_SECONDS:
                self.resume()
            return
        done = self.player.position(self.clock.now()).done
        if not self.settings.auto_pause or self.workout.test or done:  # a test ends on its own; after the end, spin down
            self._still = 0
            return
        self._still = 0 if pedalling else self._still + 1
        if self._still >= AUTO_PAUSE_SECONDS:
            self.pause(auto="stopped")

    def _write_tick(self, n: int) -> None:
        snap = self.session.snapshot()
        if self.road is not None:
            self.road.step(snap.power_raw)
        pos = self.player.position(self.clock.now())
        self._count_nerd(pos, snap.power_raw)
        self._recent_hr.append(snap.hr)
        elapsed = round(pos.elapsed)  # on the workout timeline, so skips leave a gap
        if not self.history or elapsed > self.history[-1][0]:
            self.history.append((elapsed, snap.power_raw, snap.hr))
        if self.recorder is not None:
            self.recorder.write({
                "type": "tick",
                "t": n,
                "elapsed": round(pos.elapsed, 2),
                "index": pos.index,
                "kind": pos.kind,
                "target": pos.target,
                "mode": self.player.mode,
                "main_power_id": snap.main_power_id,
                "main_hr_id": snap.main_hr_id,
                "main_cadence_id": snap.main_cadence_id,
                "power": snap.power_raw,
                "hr": snap.hr,
                "cadence": None if snap.cadence is None else round(snap.cadence),
            })

    async def _send(self, ws: WebSocket, text: str) -> None:
        try:
            await asyncio.wait_for(ws.send_text(text), SEND_TIMEOUT)
        except Exception:
            self.clients.discard(ws)

    async def _push(self) -> None:
        while True:
            await asyncio.sleep(PUSH_INTERVAL)
            if self.clients:
                text = json.dumps(self.state())
                await asyncio.gather(*(self._send(ws, text) for ws in list(self.clients)))
            watching = self.compare_clients & self.clients
            if watching:  # comparison data only while that page is open
                text = json.dumps(self.bench_snapshot())
                await asyncio.gather(*(self._send(ws, text) for ws in watching))

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self.source.run(self.bus, self.clock)),
            asyncio.create_task(self._play()),
            asyncio.create_task(self._record_ticks()),
            asyncio.create_task(self._push()),
        ]
        if self.autostart:
            self.start_ride()
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.active:  # app closing mid-ride: keep what was ridden
                with contextlib.suppress(Exception):
                    await self.stop_ride()


class WebFiles(StaticFiles):
    """Static files from wattdisco/web. Design references are never served, even if one lands there."""

    async def get_response(self, path: str, scope):
        if path.replace("\\", "/").split("/")[0] == "design":
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class MainSensors(BaseModel):
    power: str | None = None
    hr: str | None = None


class Smoothing(BaseModel):
    value: str


class SettingsIn(BaseModel):
    ftp: int | None = None  # left out: unchanged
    weight_kg: float | None = None
    auto_pause: bool | None = None
    sound: bool | None = None
    units: str | None = None
    setup_done: bool | None = None
    favourites: list[str] | None = None
    hr_method: str | None = None
    max_hr: int | None = None
    lthr: int | None = None
    ftp_source: str | None = None  # where a new FTP came from: "ramp", "20min", "best20"; else by hand
    ride_id: str | None = None  # the ride it came from


class NotesIn(BaseModel):
    rpe: int | None = None  # how hard it felt, 1 (very easy) to 10 (all out)
    note: str = ""


class StravaOptions(BaseModel):
    auto_upload: bool | None = None
    sport_type: str | None = None
    private: bool | None = None


class RideStartIn(BaseModel):
    workout_id: str | None = None
    mode: str | None = None
    smoothing: str | None = None
    power_source: str | None = None
    ease: bool = False  # ERG eases big target changes in instead of a step
    wait_for_pedal: bool = False  # the workout clock starts with the first pedal strokes


class RolesIn(BaseModel):
    roles: list[str]


class AutoConnectIn(BaseModel):
    enabled: bool


class ReferenceIn(BaseModel):
    kind: str
    id: str


class WorkoutIn(BaseModel):
    name: str
    category: str | None = None
    phases: list[dict]
    test: str | None = None  # FTP test type, kept when a test is copied
    mode: str | None = None  # suggested mode


class WorkoutFileIn(BaseModel):
    filename: str
    text: str  # the file's contents; workout files are small text or XML


class PlanIn(BaseModel):
    plan: str
    days: int | None = None  # days per week; None: as the plan is written
    minutes: int | None = None  # session length; None: as written


class PlanOptionsIn(BaseModel):
    days: int | None = None
    minutes: int | None = None


class UploadIn(BaseModel):
    name: str
    description: str = ""
    sport_type: str = "VirtualRide"
    private: bool = True
    allow_sim: bool = False  # simulated rides need an explicit yes


def create_app(ride: Ride) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(ride.run())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="Watt Disco", lifespan=lifespan)
    app.state.ride = ride

    @app.get("/", include_in_schema=False)
    async def index():
        # Start-up flow: launch -> connect -> (sensors) -> ready -> workout. Mid-ride, straight back in.
        return RedirectResponse("/workout.html" if ride.active else "/launch.html")

    # pages renamed: Settings is now Zones, the test bench is now Compare
    def moved(new: str):
        return lambda: RedirectResponse(new)

    for old, new in (("/settings.html", "/zones.html"), ("/testbench.html", "/compare.html")):
        app.add_api_route(old, moved(new), include_in_schema=False)

    @app.get("/train", include_in_schema=False)
    async def train():
        # the running ride, else the finished ride not yet discarded or uploaded, else the ready screen
        if ride.active:
            return RedirectResponse("/workout.html")
        if ride.review_id and ride.store.exists(ride.review_id):
            return RedirectResponse(f"/summary.html?ride={ride.review_id}")
        return RedirectResponse("/ready.html")

    @app.post("/api/ride/review/done")
    async def review_done():
        """The rider is done with the last ride's summary: TRAIN goes to the ready screen again."""
        await asyncio.to_thread(ride.set_review, None)
        return {"review_id": None}

    @app.get("/api/state")
    async def get_state():
        return ride.state()

    @app.get("/api/workout")
    async def get_workout():
        return ride.hello()["workout"]

    @app.post("/api/main-sensors")
    async def set_main_sensors(body: MainSensors):
        try:
            if body.power:
                ride.session.set_main_power(body.power)
            if body.hr:
                ride.session.set_main_hr(body.hr)
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"main_power_id": ride.session.main_power_id, "main_hr_id": ride.session.main_hr_id}

    @app.post("/api/smoothing")
    async def set_smoothing(body: Smoothing):
        try:
            ride.session.set_smoothing(body.value)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"smoothing": ride.session.smoothing}

    @app.post("/api/ride/start")
    async def ride_start(body: RideStartIn | None = None):
        body = body or RideStartIn()
        try:
            if body.workout_id:
                ride.use_workout(body.workout_id, await asyncio.to_thread(ride.workouts.get, body.workout_id))
            ride_id = ride.start_ride(mode=body.mode, smoothing=body.smoothing, power_source=body.power_source,
                                      ease=body.ease, wait_for_pedal=body.wait_for_pedal)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"unknown workout or device {e}") from e
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        text = json.dumps(ride.hello())
        await asyncio.gather(*(ride._send(ws, text) for ws in list(ride.clients)))
        return {"id": ride_id}

    @app.post("/api/ride/continue")
    async def ride_continue():
        """Continue the ride the app never stopped (after a crash), where it was."""
        try:
            ride_id = ride.continue_ride()
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        text = json.dumps(ride.hello())
        await asyncio.gather(*(ride._send(ws, text) for ws in list(ride.clients)))
        return {"id": ride_id}

    @app.post("/api/ride/interrupted/save")
    async def ride_interrupted_save():
        """Keep the interrupted ride as it is: it gets its end, summary and .fit, and TRAIN shows it."""
        ride_id = await asyncio.to_thread(ride.save_interrupted)
        if ride_id is None:
            raise HTTPException(status_code=404, detail="no interrupted ride")
        return {"id": ride_id}

    @app.post("/api/ride/pause")
    async def ride_pause():
        try:
            ride.pause()
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return ride.state()["ride"]

    @app.post("/api/ride/resume")
    async def ride_resume():
        try:
            ride.resume()
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return ride.state()["ride"]

    @app.post("/api/ride/stop")
    async def ride_stop():
        try:
            return {"id": await ride.stop_ride()}
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    # ---------- devices ----------

    @app.get("/api/devices")
    async def get_devices():
        return ride.device_list()

    @app.post("/api/scan")
    async def scan():
        ride.source.scan()
        return {"scanning": True}

    def device_call(fn, *args):
        try:
            fn(*args)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"unknown device {e}") from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return ride.device_list()

    @app.post("/api/devices/{device_id}/connect")
    async def connect_device(device_id: str):
        return device_call(ride.source.connect, device_id)

    @app.post("/api/devices/{device_id}/disconnect")
    async def disconnect_device(device_id: str):
        return device_call(ride.source.disconnect, device_id)

    @app.post("/api/devices/{device_id}/role")
    async def device_roles(device_id: str, body: RolesIn):
        return device_call(ride.set_roles, device_id, set(body.roles))

    @app.post("/api/devices/{device_id}/zero-offset")
    async def zero_offset(device_id: str):
        try:
            return await ride.zero_offset(device_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"unknown device {e}") from e
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.put("/api/devices/auto-connect")
    async def auto_connect(body: AutoConnectIn):
        ride.known.auto_connect = body.enabled
        await asyncio.to_thread(ride.known.save)
        return ride.device_list()

    # ---------- sensor comparison ----------
    # It runs with the ride on its own; a finished ride's comparison is rebuilt from its file.

    def compare_csv_response(text: str, ride_id: str | None) -> Response:
        stamp = ride_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        return Response(text, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="wattdisco-compare-{stamp}.csv"'})

    @app.get("/api/compare")
    async def get_compare():
        return ride.bench_snapshot()

    @app.post("/api/compare/reference")
    async def compare_reference(body: ReferenceIn):
        try:
            ride.bench.set_reference(body.kind, body.id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return ride.bench_snapshot()

    @app.get("/api/compare/export.csv")
    async def compare_csv():
        return compare_csv_response(ride.bench.to_csv(), ride.bench.ride_id)

    # ---------- workouts ----------

    def workout_info(spec: dict) -> dict:
        return {**spec, **describe(spec, ride.settings.ftp), "ftp": ride.settings.ftp}

    @app.get("/api/workouts")
    async def list_workouts(include: str | None = None):
        specs = await asyncio.to_thread(ride.workouts.list)
        # a plan session fitted to a length is made on the fly: list it when it is picked or asked for
        listed = {s["id"] for s in specs}
        for extra in dict.fromkeys(x for x in (ride.workout_id, include) if x and x not in listed):
            try:
                specs.append(await asyncio.to_thread(ride.workouts.get, extra))
            except (KeyError, ValueError):
                pass
        defaults = {k: dict(zip(("seconds", "pct", "pct_end", "cadence"), PHASE_DEFAULTS[k])) for k in KINDS}
        return {"selected": ride.workout_id, "ftp": ride.settings.ftp, "phase_defaults": defaults,
                "categories": [{"id": k, "label": v} for k, v in CATEGORIES.items()],
                "workouts": [workout_info(s) for s in specs]}

    @app.get("/api/workouts/{workout_id}")
    async def get_workout_spec(workout_id: str):
        try:
            return workout_info(await asyncio.to_thread(ride.workouts.get, workout_id))
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=404, detail="no such workout") from e

    @app.post("/api/workouts")
    async def create_workout(body: WorkoutIn):
        try:
            workout_id = await asyncio.to_thread(ride.workouts.save, body.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return workout_info(await asyncio.to_thread(ride.workouts.get, workout_id))

    @app.post("/api/workouts/import")
    async def import_workout_file(body: WorkoutFileIn):
        """A .zwo, .erg or .mrc file, saved as one of the rider's own workouts."""
        def run() -> tuple[dict, list[str]]:
            spec, notes = import_workout(body.filename, body.text, ride.settings.ftp)
            return ride.workouts.get(ride.workouts.save(spec)), notes

        try:
            spec, notes = await asyncio.to_thread(run)
        except WorkoutFileError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"workout": workout_info(spec), "notes": notes}

    @app.put("/api/workouts/{workout_id}")
    async def save_workout(workout_id: str, body: WorkoutIn):
        try:
            new_id = await asyncio.to_thread(ride.workouts.save, body.model_dump(), workout_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        spec = await asyncio.to_thread(ride.workouts.get, new_id)
        if new_id == ride.workout_id and not ride.active:
            ride.use_workout(new_id, spec)
        return workout_info(spec)

    @app.delete("/api/workouts/{workout_id}")
    async def delete_workout(workout_id: str):
        try:
            await asyncio.to_thread(ride.workouts.delete, workout_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if ride.workout_id == workout_id and not ride.active:
            ride.use_workout(DEFAULT_WORKOUT_ID)
        return {"deleted": workout_id}

    @app.post("/api/workouts/{workout_id}/select")
    async def select_workout(workout_id: str):
        if ride.active:
            raise HTTPException(status_code=409, detail="a ride is running")
        try:
            ride.use_workout(workout_id, await asyncio.to_thread(ride.workouts.get, workout_id))
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=404, detail="no such workout") from e
        text = json.dumps(ride.hello())
        await asyncio.gather(*(ride._send(ws, text) for ws in list(ride.clients)))
        return {"selected": workout_id}

    # ---------- progress across rides ----------

    @app.get("/api/progress")
    async def get_progress():
        """Fitness, fatigue and form, the power curve, FTP history and the power sources over weeks."""
        def build() -> dict:
            rows = ride.store.list(exclude=ride.ride_id)
            today = date.today()
            return {
                "fitness": progress.fitness(rows, today),
                "curve": {"all": progress.power_curve(rows),
                          "90": progress.power_curve(rows, today - timedelta(days=90)),
                          "28": progress.power_curve(rows, today - timedelta(days=28))},
                "ftp": ride.ftp_entries(),
                "sensors": progress.sensor_trend(rows),
                "cp": progress.critical_power(rows, ride.settings.ftp, today),
                "weight_kg": ride.settings.weight_kg,
                "ftp_now": ride.settings.ftp,
            }

        return await asyncio.to_thread(build)

    @app.get("/api/home")
    async def home():
        """The TRAIN home band: this week (or the plan's week), today's form and the last ride."""
        def build() -> dict:
            rows = ride.store.list(exclude=ride.ride_id)
            today = date.today()
            recent = [r for r in rows if datetime.fromisoformat(r["started"]).astimezone().date() > today - timedelta(days=7)]
            plan = None
            if ride.plan:
                v = plans.view(ride.plan, ride.settings.ftp)
                if v["next"]:
                    week = v["weeks"][v["next"]["week"] - 1]["sessions"]
                    plan = {"name": v["name"], "week": v["next"]["week"], "weeks": v["next"]["weeks"],
                            "week_done": sum(x["status"] in ("done", "elsewhere") for x in week), "week_sessions": len(week)}
                else:
                    plan = {"name": v["name"], "finished": True}
            last = rows[0] if rows else None
            return {
                "week": {"rides": len(recent), "seconds": sum(r.get("duration") or 0 for r in recent),
                         "load": round(sum(r.get("load") or 0 for r in recent))},
                "form": progress.fitness(rows, today)["now"],
                "last": last and {k: last.get(k) for k in ("id", "name", "started", "duration", "load", "avg_power")},
                "plan": plan,
            }

        return await asyncio.to_thread(build)

    @app.get("/api/critical-power")
    async def get_critical_power():
        """CP and W' from the best 3–20 min of the last 90 days, next to the FTP."""
        def build() -> dict:
            return progress.critical_power(ride.store.list(exclude=ride.ride_id), ride.settings.ftp)

        return await asyncio.to_thread(build)

    @app.get("/api/ftp-history")
    async def get_ftp_history():
        return await asyncio.to_thread(ride.ftp_entries)

    # ---------- training plan ----------

    def plan_view(days: int | None = None, minutes: int | None = None) -> dict:
        # shortest plan first: by weeks, then by riding time; previews tailored to days and length
        infos = sorted((plans.plan_info(p, ride.settings.ftp, days, minutes) for p in plans.PLANS),
                       key=lambda i: (len(i["weeks"]), i["seconds"]))
        return {"plans": infos,
                "active": plans.view(ride.plan, ride.settings.ftp) if ride.plan else None}

    async def change_plan(change: Callable[[dict], None]) -> dict:
        if ride.plan is None:
            raise HTTPException(status_code=409, detail="no training plan is running")
        state = json.loads(json.dumps(ride.plan))  # a copy: a refused change leaves the plan as it was
        try:
            change(state)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        await asyncio.to_thread(ride.save_plan, state)
        return plan_view()

    def options_or_400(days: int | None, minutes: int | None) -> None:
        try:
            plans.check_options(days, minutes)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/plans")
    async def get_plans(days: int | None = None, minutes: int | None = None):
        options_or_400(days, minutes)
        return await asyncio.to_thread(plan_view, days, minutes)

    @app.post("/api/plan")
    async def start_plan(body: PlanIn):
        options_or_400(body.days, body.minutes)
        try:
            state = plans.new_state(body.plan, ride.settings.ftp, body.days, body.minutes)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="no such plan") from e
        await asyncio.to_thread(ride.save_plan, state)
        return plan_view()

    @app.delete("/api/plan")
    async def end_plan():
        await asyncio.to_thread(ride.save_plan, None)
        return plan_view()

    @app.put("/api/plan/options")
    async def plan_options(body: PlanOptionsIn):
        """New days per week and session length, from the current week on."""
        options_or_400(body.days, body.minutes)
        return await change_plan(lambda s: plans.set_options(s, body.days, body.minutes))

    @app.post("/api/plan/skip")
    async def plan_skip():
        return await change_plan(plans.skip)

    @app.post("/api/plan/done")
    async def plan_done():
        """The next session was ridden somewhere else."""
        return await change_plan(plans.mark_done)

    @app.post("/api/plan/undo")
    async def plan_undo():
        return await change_plan(plans.undo)

    @app.post("/api/plan/repeat")
    async def plan_repeat():
        return await change_plan(plans.repeat_week)

    @app.get("/api/plan/next")
    async def plan_next():
        """The plan's next session for the ready screen, or null without a plan."""
        return plans.view(ride.plan, ride.settings.ftp)["next"] if ride.plan else None

    @app.get("/api/rides/{ride_id}/plan")
    async def ride_plan(ride_id: str):
        """What this ride did for the plan: the session it ticked off, or why it did not count."""
        if ride.plan is None:
            return None
        summary = await asyncio.to_thread(ride.store.summary, known_ride(ride_id))
        entry = plans.ride_entry(ride.plan, ride_id)
        view = plans.view(ride.plan, ride.settings.ftp)
        # why it did not count only makes sense for the ride that just ended, against the session still next
        reason = plans.counts(ride.plan, summary) if not entry and ride_id == ride.last_ride_id else None
        return {"counted": entry, "reason": reason, "next": view["next"], "finished": view["finished"],
                "name": view["name"]}

    def known_ride(ride_id: str) -> str:
        if not ride.store.exists(ride_id):
            raise HTTPException(status_code=404, detail="no such ride")
        if ride_id == ride.ride_id:
            raise HTTPException(status_code=409, detail="ride is still running")
        return ride_id

    @app.get("/api/rides")
    async def list_rides(include_sim: bool = False):
        # Finished rides only: the one being recorded has no summary yet. The history is real rides;
        # include_sim=true adds the latest simulated ride, for pages that open "the last ride".
        rows = await asyncio.to_thread(ride.store.list, exclude=ride.ride_id, include_sim=include_sim)
        for row in rows:  # an upload in progress is only known in memory
            if row["id"] in ride.uploading:
                row["strava"] = {**ride.uploading[row["id"]], "url": None}
        return rows

    @app.get("/api/rides/{ride_id}/summary")
    async def ride_summary(ride_id: str):
        return await asyncio.to_thread(ride.store.summary, known_ride(ride_id))

    async def ride_compare(ride_id: str, power: str | None, hr: str | None):
        return await asyncio.to_thread(ride.store.compare, known_ride(ride_id), {"power": power, "hr": hr})

    @app.get("/api/rides/{ride_id}/compare")
    async def finished_compare(ride_id: str, power: str | None = None, hr: str | None = None):
        """A finished ride's sensor comparison; `power` / `hr` pick the reference source."""
        return (await ride_compare(ride_id, power, hr)).snapshot()

    @app.get("/api/rides/{ride_id}/compare.csv")
    async def finished_compare_csv(ride_id: str, power: str | None = None, hr: str | None = None):
        return compare_csv_response((await ride_compare(ride_id, power, hr)).to_csv(), ride_id)

    @app.get("/api/rides/{ride_id}/export.csv")
    async def ride_csv(ride_id: str):
        text = await asyncio.to_thread(ride.store.csv, known_ride(ride_id))
        return Response(text, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="wattdisco-{ride_id}.csv"'})

    @app.get("/api/rides/{ride_id}/export.fit")
    async def ride_fit(ride_id: str):
        try:
            path = await asyncio.to_thread(ride.store.fit_path, known_ride(ride_id))
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return FileResponse(path, media_type="application/vnd.ant.fit", filename=f"wattdisco-{ride_id}.fit")

    # ---------- Strava ----------

    def strava_status() -> dict:
        s = ride.settings
        return {**ride.strava.status(), "auto_upload": s.strava_auto_upload,
                "sport_type": s.strava_sport_type, "private": s.strava_private}

    @app.get("/api/strava/status")
    async def get_strava_status():
        return await asyncio.to_thread(strava_status)

    @app.get("/api/strava/connect")
    async def strava_connect(request: Request, return_to: str = "/upload.html"):
        try:
            url = ride.strava.authorize_url(str(request.url_for("strava_callback")), return_to)
        except StravaError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return RedirectResponse(url)

    @app.get("/api/strava/callback", name="strava_callback")
    async def strava_callback(state: str = "", code: str = "", scope: str = "", error: str = ""):
        def back(page: str, **params) -> RedirectResponse:
            sep = "&" if "?" in page else "?"
            return RedirectResponse(f"{page}{sep}{urlencode(params)}")

        if error:  # the rider pressed "Cancel" on Strava
            return back(ride.strava._states.pop(state, "/upload.html"), strava="denied")
        try:
            return_to = await asyncio.to_thread(ride.strava.finish_sign_in, code, state, scope)
        except StravaError as e:
            return back("/upload.html", strava="error", message=str(e))
        except Exception:
            log.exception("Strava sign-in failed")
            return back("/upload.html", strava="error", message="Strava sign-in failed, please try again")
        return back(return_to, strava="connected")

    @app.post("/api/strava/disconnect")
    async def strava_disconnect():
        await asyncio.to_thread(ride.strava.disconnect)
        return await asyncio.to_thread(strava_status)

    @app.put("/api/strava/options")
    async def strava_options(body: StravaOptions):
        s = ride.settings
        if body.sport_type is not None and body.sport_type not in SPORT_TYPES:
            raise HTTPException(status_code=400, detail=f"activity type must be one of {list(SPORT_TYPES)}")
        new = replace(
            s,
            strava_auto_upload=s.strava_auto_upload if body.auto_upload is None else body.auto_upload,
            strava_sport_type=body.sport_type or s.strava_sport_type,
            strava_private=s.strava_private if body.private is None else body.private,
        )
        await asyncio.to_thread(save_settings, new, ride.data_dir)
        ride.settings = new
        return await asyncio.to_thread(strava_status)

    @app.get("/api/rides/{ride_id}/upload")
    async def ride_upload_status(ride_id: str):
        known_ride(ride_id)
        if ride_id in ride.uploading:
            return ride.uploading[ride_id]
        return await asyncio.to_thread(ride.store.upload_status, ride_id)

    @app.post("/api/rides/{ride_id}/upload")
    async def ride_upload(ride_id: str, body: UploadIn):
        known_ride(ride_id)
        if not ride.strava.connected:
            raise HTTPException(status_code=409, detail="connect Strava first")
        if body.sport_type not in SPORT_TYPES:
            raise HTTPException(status_code=400, detail=f"activity type must be one of {list(SPORT_TYPES)}")
        summary = await asyncio.to_thread(ride.store.summary, ride_id)
        if summary.get("sim") and not body.allow_sim:
            raise HTTPException(status_code=409, detail="this is a simulated ride")
        if not summary["duration"]:
            raise HTTPException(status_code=409, detail="ride too short to upload")
        try:
            ride.start_upload(ride_id, body.model_dump(exclude={"allow_sim"}))
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        # remember the choices for next time and for auto-upload
        ride.settings = replace(ride.settings, strava_sport_type=body.sport_type, strava_private=body.private)
        await asyncio.to_thread(save_settings, ride.settings, ride.data_dir)
        return {"state": "uploading"}

    @app.get("/api/rides/{ride_id}/description")
    async def ride_description(ride_id: str):
        summary = await asyncio.to_thread(ride.store.summary, known_ride(ride_id))
        return {"name": summary["name"], "description": default_description(summary)}

    @app.delete("/api/rides/{ride_id}")
    async def ride_delete(ride_id: str):
        await asyncio.to_thread(ride.store.delete, known_ride(ride_id))
        if ride.plan is not None:
            state = json.loads(json.dumps(ride.plan))
            if plans.forget_ride(state, ride_id):
                await asyncio.to_thread(ride.save_plan, state)
        if ride.last_ride_id == ride_id:
            ride.last_ride_id = None
        if ride.review_id == ride_id:
            await asyncio.to_thread(ride.set_review, None)
        return {"deleted": ride_id}

    @app.post("/api/rides/{ride_id}/restore")
    async def ride_restore(ride_id: str):
        """Undo a delete: the ride comes back from deleted/ (kept there for a week)."""
        if not RIDE_ID.match(ride_id) or not await asyncio.to_thread(ride.store.restore, ride_id):
            raise HTTPException(status_code=404, detail="nothing to restore")
        return {"restored": ride_id}

    @app.get("/api/rides/{ride_id}/notes")
    async def ride_notes(ride_id: str):
        return await asyncio.to_thread(ride.store.notes, known_ride(ride_id))

    @app.put("/api/rides/{ride_id}/notes")
    async def ride_set_notes(ride_id: str, body: NotesIn):
        try:
            return await asyncio.to_thread(ride.store.set_notes, known_ride(ride_id), body.rpe, body.note)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/settings")
    async def get_settings():
        return ride.settings.to_dict()

    @app.get("/api/zones")
    async def get_zones(ftp: int):
        """Zones for any FTP, so the settings page can preview before saving."""
        if not FTP_RANGE[0] <= ftp <= FTP_RANGE[1]:
            raise HTTPException(status_code=400, detail=f"FTP must be between {FTP_RANGE[0]} and {FTP_RANGE[1]} W")
        return zones(ftp)

    @app.get("/api/hr-zones")
    async def get_hr_zones(method: str, reference: int):
        """HR zones for a method and reference heart rate, so the zones page can preview."""
        if method not in HR_METHODS:
            raise HTTPException(status_code=400, detail=f"method must be one of {list(HR_METHODS)}")
        lo, hi = HR_METHODS[method]["range"]
        if not lo <= reference <= hi:
            raise HTTPException(status_code=400, detail=f"{HR_METHODS[method]['label']} must be between {lo} and {hi} bpm")
        return hr_zones(method, reference)

    @app.put("/api/settings")
    async def put_settings(body: SettingsIn):
        fields = ("ftp", "weight_kg", "hr_method", "max_hr", "lthr", "auto_pause", "sound", "units", "setup_done", "favourites")
        change = {k: getattr(body, k) for k in fields if getattr(body, k) is not None}
        try:  # keep the Strava preferences, change only what was sent
            settings = replace(ride.settings, **change)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        source = body.ftp_source if body.ftp_source in ("ramp", "20min", "best20") else "manual"
        ride_id = body.ride_id if source != "manual" and body.ride_id and ride.store.exists(body.ride_id) else None
        await ride.update_settings(settings, source=source, ride_id=ride_id)
        return settings.to_dict()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text(json.dumps(ride.hello()))
        ride.clients.add(websocket)
        try:
            while True:
                msg = await websocket.receive_json()
                if msg.get("cmd") == "watch_compare":  # per-browser, so handled here
                    (ride.compare_clients.add if msg.get("value") else ride.compare_clients.discard)(websocket)
                    continue
                try:
                    ride.command(msg)
                except (KeyError, ValueError) as e:
                    await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except WebSocketDisconnect:
            pass
        finally:
            ride.clients.discard(websocket)
            ride.compare_clients.discard(websocket)

    app.mount("/", WebFiles(directory=WEB_DIR, html=True), name="web")
    return app
