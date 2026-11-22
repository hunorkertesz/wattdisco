"""Sensor comparison: every power source and every heart rate sensor side by side.

Runs with the ride: it starts when the ride starts, skips paused time and stops when
the ride ends. It listens to the bus directly, so any device that measures power or
heart rate is compared, even one whose role is switched off (up to MAX_SOURCES per
kind). Samples are averaged into whole seconds of ride time. A finished ride is
rebuilt from its recording with `from_ride`, so its comparison and CSV stay available.

Each source is compared with the reference source (A, the main sensor by default):
difference A − B, mean difference, lag and correlation.
"""

import csv
import io
import math
from collections.abc import Callable
from dataclasses import dataclass, field

from wattdisco.core import stats
from wattdisco.core.bus import Bus
from wattdisco.core.clock import Clock
from wattdisco.core.recorder import RideData
from wattdisco.models import HrSample, PowerSample

KINDS = ("power", "hr")
LABELS = "ABC"
MAX_SOURCES = len(LABELS)  # per kind; three power sources is already rare
CHART_SECONDS = 120  # live charts show the last two minutes; a finished ride shows all of it
MAX_SECONDS = 4 * 3600  # stats over at most the last four hours
STALE_S = 5  # a "now" value older than this is shown as no signal


@dataclass
class Channel:
    """One device: per-second averages and its latest raw value."""

    device_id: str
    sums: dict[int, float] = field(default_factory=dict)
    counts: dict[int, int] = field(default_factory=dict)
    latest: float | None = None
    latest_t: float | None = None

    def add(self, second: int, value: float, t: float) -> None:
        self.sums[second] = self.sums.get(second, 0.0) + value
        self.counts[second] = self.counts.get(second, 0) + 1
        self.latest, self.latest_t = value, t

    def series(self, first: int, last: int) -> list[float | None]:
        return [self.sums[s] / self.counts[s] if s in self.counts else None for s in range(first, last)]


class CompareBench:
    def __init__(self, bus: Bus | None, clock: Clock | None, name_of: Callable[[str], str] = str) -> None:
        self.clock = clock
        self.name_of = name_of
        self.running = False
        self.paused = False
        self.started: float | None = None
        self.stopped: float | None = None
        self.ride_id: str | None = None
        self.channels: dict[str, list[Channel]] = {kind: [] for kind in KINDS}
        self.reference: dict[str, str | None] = {kind: None for kind in KINDS}
        if bus is not None:
            bus.subscribe("power", self._on_power)
            bus.subscribe("hr", self._on_hr)

    # ---------- control (follows the ride) ----------

    def start(self, started: float, ride_id: str | None = None, reference: dict[str, str | None] | None = None) -> None:
        """A new ride began at clock time `started`; `reference` is the main sensor per kind."""
        self.started, self.stopped = started, None
        self.ride_id = ride_id
        self.running, self.paused = True, False
        self.channels = {kind: [] for kind in KINDS}
        self.reference = {kind: (reference or {}).get(kind) for kind in KINDS}

    def stop(self) -> None:
        if self.running:
            self.running = self.paused = False
            self.stopped = self.clock.now()

    def set_reference(self, kind: str, device_id: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {list(KINDS)}")
        if device_id not in (c.device_id for c in self.channels[kind]):
            raise ValueError("that device has sent no data")
        self.reference[kind] = device_id

    @classmethod
    def from_ride(cls, ride: RideData, reference: dict[str, str | None] | None = None) -> "CompareBench":
        """A finished ride's comparison, rebuilt from every sensor's recorded samples."""
        bench = cls(None, None, name_of=ride.device_name)
        bench.started = 0.0
        last_tick = ride.ticks[-1]["t"] if ride.ticks else 0.0
        bench.stopped = ride.ended_t if ride.ended_t is not None else last_tick
        bench.ride_id = ride.id
        mains = {"power": "main_power_id", "hr": "main_hr_id"}
        for kind, samples in (("power", ride.power), ("hr", ride.hr)):
            for device_id, values in samples.items():
                for t, value in values:
                    bench._add(kind, device_id, value, t)
            main = next((tick.get(mains[kind]) for tick in ride.ticks if tick.get(mains[kind])), None)
            bench.reference[kind] = (reference or {}).get(kind) or main
        return bench

    # ---------- samples ----------

    def _add(self, kind: str, device_id: str, value: float, t: float) -> None:
        if self.started is None or t < self.started or self.paused:
            return
        channels = self.channels[kind]
        channel = next((c for c in channels if c.device_id == device_id), None)
        if channel is None:
            if len(channels) >= MAX_SOURCES:
                return
            channel = Channel(device_id)
            channels.append(channel)
        channel.add(math.floor(t - self.started), value, t)

    def _on_power(self, sample: PowerSample) -> None:
        if self.running:
            self._add("power", sample.device_id, sample.watts, sample.t)

    def _on_hr(self, sample: HrSample) -> None:
        if self.running:
            self._add("hr", sample.device_id, sample.bpm, sample.t)

    # ---------- results ----------

    @property
    def elapsed(self) -> float:
        if self.started is None:
            return 0.0
        end = self.clock.now() if self.running else self.stopped
        return max(0.0, end - self.started)

    def _seconds(self) -> tuple[int, int]:
        """Whole seconds that are complete: [first, last)."""
        last = math.floor(self.elapsed)
        return max(0, last - MAX_SECONDS), last

    def ordered(self, kind: str) -> list[Channel]:
        """Reference source first (A), then the others in the order they appeared."""
        ref = self.reference[kind]
        return sorted(self.channels[kind], key=lambda c: c.device_id != ref)

    def _now(self, channel: Channel) -> float | None:
        if not self.running or channel.latest_t is None or self.clock.now() - channel.latest_t >= STALE_S:
            return None
        return channel.latest

    def _kind(self, kind: str) -> dict | None:
        channels = self.ordered(kind)
        if not channels:
            return None
        first, last = self._seconds()
        series = [c.series(first, last) for c in channels]
        nows = [self._now(c) for c in channels]
        sources = [{
            "id": c.device_id,
            "label": LABELS[i],
            "name": self.name_of(c.device_id),
            "now": nows[i],
            "mean": stats.mean(s),
            "max": stats.maximum(s),
        } for i, (c, s) in enumerate(zip(channels, series))]
        diffs = []
        for i in range(1, len(channels)):
            result = stats.source_difference(series[0], series[i]) or {}
            diff = None if nows[0] is None or nows[i] is None else nows[0] - nows[i]
            diffs.append({
                "id": channels[i].device_id,
                "label": f"A − {LABELS[i]}",
                "diff_now": diff,
                "diff_pct_now": None if diff is None or not nows[i] else 100 * diff / nows[i],
                **{k: result.get(k) for k in ("mean_diff", "mean_diff_pct", "lag_s", "correlation", "seconds")},
            })
        chart_from = max(0, len(series[0]) - CHART_SECONDS) if self.running else 0
        return {
            "sources": sources,
            "diffs": diffs,
            "series": {"t": list(range(first + chart_from, last)), "values": [s[chart_from:] for s in series]},
        }

    def snapshot(self) -> dict:
        return {
            "type": "compare",
            "running": self.running,
            "paused": self.paused,
            "started": self.started is not None,
            "ride_id": self.ride_id,
            "elapsed": self.elapsed,
            **{kind: self._kind(kind) for kind in KINDS},
        }

    def to_csv(self) -> str:
        """One row per second of the ride: every source and its difference to A."""
        last = math.floor(self.elapsed)
        columns: list[tuple[str, list]] = []
        units = {"power": "W", "hr": "bpm"}
        for kind in KINDS:
            channels = self.ordered(kind)
            series = [c.series(0, last) for c in channels]
            unit = units[kind]
            for i, (c, s) in enumerate(zip(channels, series)):
                columns.append((f"{kind} {LABELS[i]} {self.name_of(c.device_id)} ({unit})", s))
            for i in range(1, len(channels)):
                columns.append((f"{kind} A-{LABELS[i]} ({unit})",
                                [None if x is None or y is None else x - y for x, y in zip(series[0], series[i])]))
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["second", *(name for name, _ in columns)])
        for i in range(last):
            writer.writerow([i, *("" if col[i] is None else round(col[i], 2) for _, col in columns)])
        return out.getvalue()
