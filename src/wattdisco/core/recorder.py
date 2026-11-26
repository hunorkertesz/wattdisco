"""Ride recording: every sample from every sensor, plus one "tick" per second.

One JSON object per line in data/rides/<id>.jsonl:
  {"type": "meta", ...}                       first line: start time, workout, FTP, weight
  {"type": "device" | "power" | "hr" | "metric" | "trainer", "t": ..., ...}   raw, all sensors
  {"type": "tick", "t": ..., "elapsed": ..., "target": ..., "power": ..., ...}  1 Hz, main values
  {"type": "end", "t": ...}

`t` is seconds since the ride started. Writing happens in a thread fed by a queue,
so the bus and WebSocket loops never wait for the disk. Recorded values stay raw.
"""

import json
import queue
import re
import threading
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path

RIDE_ID = re.compile(r"^\d{8}-\d{6}$")
RECORDED_TOPICS = ("device", "power", "hr", "metric", "trainer")


def new_ride_id(started: datetime) -> str:
    return started.strftime("%Y%m%d-%H%M%S")


def ride_path(rides_dir: Path, ride_id: str, suffix: str = ".jsonl") -> Path:
    if not RIDE_ID.match(ride_id):
        raise ValueError(f"bad ride id {ride_id!r}")
    return rides_dir / f"{ride_id}{suffix}"


class Recorder:
    """Appends JSON lines from any thread without blocking it."""

    def __init__(self, path: Path, meta: dict | None) -> None:
        """`meta` starts a new ride file; None appends to one that is already there (a ride continued)."""
        self.path = path
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._write_loop, name=f"recorder-{path.stem}", daemon=True)
        self._thread.start()
        if meta is not None:
            self.write({"type": "meta", **meta})

    def write(self, record: dict) -> None:
        if not self._closed:
            self._queue.put(record)

    def record(self, topic: str, payload, t: float) -> None:
        """A bus message with its time on the ride clock."""
        data = asdict(payload) if is_dataclass(payload) else dict(payload)
        data.pop("t", None)
        for key, value in data.items():
            if isinstance(value, (set, frozenset)):
                data[key] = sorted(value)
        self.write({"type": topic, "t": round(t, 3), **data})

    def close(self) -> None:
        """Flush everything and stop the writer. Blocks; call it from a thread."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join()

    def _write_loop(self) -> None:
        cut = _ends_mid_line(self.path)
        with self.path.open("a", encoding="utf-8") as f:
            if cut:  # a ride continued after a crash: end its half-written line, so the next one stays whole
                f.write("\n")
            while True:
                record = self._queue.get()
                if record is None:
                    break
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
                if self._queue.empty():
                    f.flush()


@dataclass
class RideData:
    """A recorded ride read back from its .jsonl file."""

    meta: dict
    ticks: list[dict] = field(default_factory=list)
    power: dict[str, list[tuple[float, int]]] = field(default_factory=dict)  # device -> (t, watts)
    hr: dict[str, list[tuple[float, int]]] = field(default_factory=dict)
    cadence: dict[str, list[tuple[float, float]]] = field(default_factory=dict)  # device -> (t, rpm), from power
    rr: dict[str, list[tuple[float, list[float]]]] = field(default_factory=dict)  # device -> (t, RR intervals in s)
    balance: dict[str, list[tuple[float, float, int]]] = field(default_factory=dict)  # device -> (t, left %, watts)
    metrics: dict[str, list[tuple[float, float]]] = field(default_factory=dict)  # metric key -> (t, value)
    devices: dict[str, dict] = field(default_factory=dict)
    pauses: list[tuple[float, float | None]] = field(default_factory=list)  # (paused at, resumed at)
    ended_t: float | None = None

    @property
    def id(self) -> str:
        return self.meta["id"]

    def device_name(self, device_id: str) -> str:
        return self.devices.get(device_id, {}).get("name", device_id)


def _ends_mid_line(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            if f.tell() == 0:
                return False
            f.seek(-1, 2)
            return f.read(1) != b"\n"
    except OSError:  # no file yet: a new ride
        return False


def ended(path: Path) -> bool:
    """True when the ride file has its end line (a ride the app stopped); reads only the file's tail."""
    with path.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 4096))
        tail = f.read().decode("utf-8", "replace")
    return '"type":"end"' in tail


def load_ride(path: Path) -> RideData:
    ride: RideData | None = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # a half-written last line after a crash
            kind = rec.get("type")
            if kind == "meta":
                ride = RideData(meta=rec)
            elif ride is None:
                continue
            elif kind == "tick":
                ride.ticks.append(rec)
            elif kind == "power":
                ride.power.setdefault(rec["device_id"], []).append((rec["t"], rec["watts"]))
                if rec.get("cadence") is not None:
                    ride.cadence.setdefault(rec["device_id"], []).append((rec["t"], rec["cadence"]))
                if rec.get("balance") is not None:
                    ride.balance.setdefault(rec["device_id"], []).append((rec["t"], rec["balance"], rec["watts"]))
            elif kind == "hr":
                ride.hr.setdefault(rec["device_id"], []).append((rec["t"], rec["bpm"]))
                if rec.get("rr"):
                    ride.rr.setdefault(rec["device_id"], []).append((rec["t"], rec["rr"]))
            elif kind == "metric":
                ride.metrics.setdefault(rec["metric"], []).append((rec["t"], rec["value"]))
            elif kind == "device":
                ride.devices[rec["id"]] = rec
            elif kind == "pause":
                ride.pauses.append((rec["t"], None))
            elif kind == "resume" and ride.pauses and ride.pauses[-1][1] is None:
                ride.pauses[-1] = (ride.pauses[-1][0], rec["t"])
            elif kind == "end":
                ride.ended_t = rec["t"]
    if ride is None:
        raise ValueError(f"{path.name} has no meta line")
    return ride
