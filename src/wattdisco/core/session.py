"""Live ride state: latest value per device, chosen main sensors, smoothed power, dropouts."""

from dataclasses import dataclass, field

from wattdisco.core.bus import Bus
from wattdisco.core.clock import Clock
from wattdisco.core.metrics import CATALOG, MetricValue, metric_value
from wattdisco.core.smoothing import SMOOTHING_WINDOWS, Smoother
from wattdisco.models import Device, HrSample, MetricSample, PowerSample, TrainerStatus

DROPOUT_SECONDS = 5.0


@dataclass
class DeviceState:
    id: str
    name: str
    roles: list[str]
    state: str
    capabilities: list[str] = field(default_factory=list)
    rssi: int | None = None
    watts: int | None = None
    cadence: float | None = None
    bpm: int | None = None
    no_data: bool = False
    battery: int | None = None  # %
    balance: float | None = None  # left pedal share, %, latest sample


@dataclass
class SessionState:
    t: float
    smoothing: str
    main_power_id: str | None
    main_hr_id: str | None
    main_cadence_id: str | None  # None: cadence comes with the main power source
    power: int | None  # main power, smoothed for display
    power_raw: int | None
    cadence: float | None
    hr: int | None
    devices: list[DeviceState] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    trainer: TrainerStatus | None = None  # last answer from the controlled trainer
    metrics: list[MetricValue] = field(default_factory=list)  # extra sensor metrics, one per key


class Session:
    def __init__(self, bus: Bus, clock: Clock, smoothing: str = "3s") -> None:
        self.clock = clock
        self.set_smoothing(smoothing)
        self.devices: dict[str, Device] = {}
        self.main_power_id: str | None = None
        self.main_hr_id: str | None = None
        self.main_cadence_id: str | None = None
        self._power: dict[str, PowerSample] = {}
        self._hr: dict[str, HrSample] = {}
        self._metrics: dict[tuple[str, str], MetricSample] = {}  # (device, metric) -> latest
        self._smoothers: dict[str, Smoother] = {}
        self._last_data: dict[str, float] = {}
        self._connected_at: dict[str, float] = {}
        self.trainer_status: TrainerStatus | None = None
        bus.subscribe("trainer_status", self._on_trainer_status)
        bus.subscribe("device", self._on_device)
        bus.subscribe("power", self._on_power)
        bus.subscribe("hr", self._on_hr)
        bus.subscribe("metric", self._on_metric)

    def set_smoothing(self, smoothing: str) -> None:
        if smoothing not in SMOOTHING_WINDOWS:
            raise ValueError(f"smoothing must be one of {list(SMOOTHING_WINDOWS)}")
        self.smoothing = smoothing

    def set_main_power(self, device_id: str) -> None:
        if "power" not in self._device(device_id).roles:
            raise ValueError(f"{device_id} is not a power source")
        self.main_power_id = device_id

    def set_main_hr(self, device_id: str) -> None:
        if "heart_rate" not in self._device(device_id).roles:
            raise ValueError(f"{device_id} is not a heart rate sensor")
        self.main_hr_id = device_id

    def set_roles(self, device_id: str, roles: set[str]) -> None:
        """Choose what a device is used for. Its samples are still recorded either way."""
        device = self._device(device_id)
        if not roles <= device.capabilities:
            raise ValueError(f"{device.name} cannot do {sorted(roles - device.capabilities)}")
        device.roles = set(roles)
        if self.main_power_id and "power" not in self.devices[self.main_power_id].roles:
            self.main_power_id = self._first_with("power", self._power)
        if self.main_hr_id and "heart_rate" not in self.devices[self.main_hr_id].roles:
            self.main_hr_id = self._first_with("heart_rate", self._hr)
        if self.main_cadence_id and "cadence" not in self.devices[self.main_cadence_id].roles:
            self.main_cadence_id = None

    def _first_with(self, role: str, samples: dict) -> str | None:
        return next((d for d in samples if role in self.devices[d].roles), None)

    def set_main_cadence(self, device_id: str | None) -> None:
        """Pick a cadence source, or None to take cadence from the main power source."""
        if device_id is not None and "cadence" not in self._device(device_id).roles:
            raise ValueError(f"{device_id} does not measure cadence")
        self.main_cadence_id = device_id

    def _device(self, device_id: str) -> Device:
        if device_id not in self.devices:
            raise KeyError(f"unknown device {device_id}")
        return self.devices[device_id]

    def _on_trainer_status(self, status: TrainerStatus) -> None:
        self.trainer_status = status

    def _on_device(self, device: Device) -> None:
        known = self.devices.get(device.id)
        if device.state == "connected" and (known is None or known.state != "connected"):
            self._connected_at[device.id] = self.clock.now()
        self.devices[device.id] = device

    def _ensure_device(self, device_id: str, role: str) -> Device:
        """Samples from a device nobody announced: add it with that role."""
        if device_id not in self.devices:
            self.devices[device_id] = Device(id=device_id, name=device_id, roles={role}, state="connected")
        device = self.devices[device_id]
        device.capabilities.add(role)
        return device

    def _on_power(self, sample: PowerSample) -> None:
        device = self._ensure_device(sample.device_id, "power")
        self._power[sample.device_id] = sample
        self._smoothers.setdefault(sample.device_id, Smoother()).add(sample.t, sample.watts)
        self._last_data[sample.device_id] = sample.t
        if self.main_power_id is None and "power" in device.roles:
            self.main_power_id = sample.device_id

    def _on_hr(self, sample: HrSample) -> None:
        device = self._ensure_device(sample.device_id, "heart_rate")
        self._hr[sample.device_id] = sample
        self._last_data[sample.device_id] = sample.t
        if self.main_hr_id is None and "heart_rate" in device.roles:
            self.main_hr_id = sample.device_id

    def _on_metric(self, sample: MetricSample) -> None:
        info = CATALOG.get(sample.metric)
        if info is None:
            return  # a metric this version does not know
        self._ensure_device(sample.device_id, info.role or sample.metric)
        self._metrics[(sample.device_id, sample.metric)] = sample
        self._last_data[sample.device_id] = sample.t

    def power_difference(self, window: float = 3.0) -> dict | None:
        """Main power source against the next one with data, smoothed over `window` seconds."""
        now = self.clock.now()
        main = self.main_power_id
        live = [d for d, s in self._power.items() if now - s.t <= DROPOUT_SECONDS]
        if main not in live:
            return None
        other = next((d for d in live if d != main), None)
        if other is None:
            return None
        a = self._smoothers[main].value(window)
        b = self._smoothers[other].value(window)
        if a is None or b is None:
            return None
        return {
            "a_id": main, "a_name": self.devices[main].name,
            "b_id": other, "b_name": self.devices[other].name,
            "diff": a - b, "diff_pct": 100 * (a - b) / b if b else None,
        }

    def _no_data(self, device: Device, now: float) -> bool:
        if device.state != "connected":
            return False
        since = self._last_data.get(device.id, self._connected_at.get(device.id))
        return since is not None and now - since > DROPOUT_SECONDS

    def snapshot(self) -> SessionState:
        now = self.clock.now()
        devices: list[DeviceState] = []
        alerts: list[str] = []
        silent: set[str] = set()
        for device in self.devices.values():
            p = self._power.get(device.id)
            h = self._hr.get(device.id)
            no_data = self._no_data(device, now)
            if no_data:
                silent.add(device.id)
                alerts.append(f"no data from {device.name}")
            elif device.state == "lost":
                silent.add(device.id)
                alerts.append(f"{device.name} lost, reconnecting")
            devices.append(
                DeviceState(
                    id=device.id,
                    name=device.name,
                    roles=sorted(device.roles),
                    state=device.state,
                    capabilities=sorted(device.capabilities),
                    rssi=device.rssi,
                    watts=p.watts if p else None,
                    cadence=p.cadence if p else None,
                    bpm=h.bpm if h else None,
                    no_data=no_data,
                    battery=device.battery,
                    balance=p.balance if p else None,
                )
            )

        power = power_raw = cadence = hr = None
        main_p = self.main_power_id
        if main_p in self._power and main_p not in silent:
            power_raw = self._power[main_p].watts
            smoothed = self._smoothers[main_p].value(SMOOTHING_WINDOWS[self.smoothing])
            power = round(smoothed) if smoothed is not None else None
        cadence_id = self.main_cadence_id or main_p
        if cadence_id in self._power and cadence_id not in silent:
            cadence = self._power[cadence_id].cadence
        if cadence is None:
            cadence = next(
                (s.cadence for d, s in self._power.items() if s.cadence is not None and d not in silent),
                None,
            )
        if self.main_hr_id in self._hr and self.main_hr_id not in silent:
            hr = self._hr[self.main_hr_id].bpm

        # One value per extra metric: the first source that is not silent.
        metrics: dict[str, MetricValue] = {}
        for (device_id, key), sample in self._metrics.items():
            live = device_id not in silent
            if key not in metrics or (metrics[key].value is None and live):
                metrics[key] = metric_value(key, sample.value if live else None, device_id)

        return SessionState(
            t=now,
            smoothing=self.smoothing,
            main_power_id=self.main_power_id,
            main_hr_id=self.main_hr_id,
            main_cadence_id=self.main_cadence_id,
            power=power,
            power_raw=power_raw,
            cadence=cadence,
            hr=hr,
            devices=devices,
            alerts=alerts,
            trainer=self.trainer_status,
            metrics=list(metrics.values()),
        )
