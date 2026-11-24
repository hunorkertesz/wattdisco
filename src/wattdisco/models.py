"""Plain data types shared by the whole app. Nothing here knows about Bluetooth."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PowerSample:
    t: float  # seconds since ride start, monotonic clock on arrival
    device_id: str
    watts: int
    cadence: float | None
    balance: float | None = None  # left pedal's share of the power, % (dual-sided meters)


@dataclass(frozen=True)
class HrSample:
    t: float
    device_id: str
    bpm: int
    rr: tuple[float, ...] = ()  # RR intervals in seconds, if the strap sends them


@dataclass
class Device:
    id: str
    name: str
    roles: set[str] = field(default_factory=set)  # in use: power, cadence, control, heart_rate, temperature
    state: str = "found"  # found, connecting, connected, lost, disconnected
    capabilities: set[str] = field(default_factory=set)  # what it can do; roles are a subset
    rssi: int | None = None
    battery: int | None = None  # %, when the device reports it (Bluetooth battery service)

    def __post_init__(self) -> None:
        if not self.capabilities:
            self.capabilities = set(self.roles)


@dataclass
class Phase:
    kind: str  # warmup, endurance, sweetspot, threshold, vo2, recovery, cooldown
    seconds: int
    watts: int
    watts_end: int | None = None  # set for ramps
    cadence: int | None = None

    def target_at(self, s: float) -> int:
        if self.watts_end is None:
            return self.watts
        return round(self.watts + (self.watts_end - self.watts) * s / self.seconds)


@dataclass
class Workout:
    name: str
    ftp: int
    phases: list[Phase]
    test: str | None = None  # "ramp" or "20min" for an FTP test
    climb: dict | None = None  # a climb: {"segments": [[metres, gradient %], ...]} (core/climb.py)
    open: bool = False  # "Just ride": no target, no end; the one phase only paces the simulated rider

    @property
    def seconds(self) -> int:
        return sum(p.seconds for p in self.phases)

    def phase_at(self, t: float) -> tuple[int, Phase, float] | None:
        """(index, phase, seconds into phase) at ride time `t`, or None after the end."""
        start = 0
        for i, phase in enumerate(self.phases):
            if t < start + phase.seconds:
                return i, phase, max(0.0, t - start)
            start += phase.seconds
        return None

    def target_at(self, t: float) -> int | None:
        found = self.phase_at(t)
        return found[1].target_at(found[2]) if found else None


@dataclass(frozen=True)
class TrainerCommand:
    mode: str  # "erg": hold `watts`; "free": flat-road resistance; "sim": a road of `grade` %; the rider sets the power
    watts: int | None = None
    grade: float | None = None  # % for "sim" (negative downhill)


@dataclass(frozen=True)
class TrainerStatus:
    device_id: str
    ok: bool
    mode: str | None = None
    watts: int | None = None
    message: str = ""
    grade: float | None = None


@dataclass(frozen=True)
class MetricSample:
    """Any extra sensor value beyond power and heart rate, e.g. core body temperature."""

    t: float
    device_id: str
    metric: str  # key in core/metrics.py CATALOG
    value: float
