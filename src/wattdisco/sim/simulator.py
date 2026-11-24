"""Fake trainer, power meter and two heart rate sensors that ride a workout.

The two power sources and the two HR sensors disagree a little on purpose
(offset, lag, noise), so the test bench and main-sensor choice have something to show.
"""

import asyncio
import math
import random
import time
from collections import deque
from collections.abc import Iterable

from wattdisco.core.bus import Bus
from wattdisco.core.clock import Clock
from wattdisco.core.workouts import default_workout
from wattdisco.models import Device, HrSample, MetricSample, PowerSample, TrainerCommand, TrainerStatus, Workout

TRAINER = Device(id="sim-trainer", name="SIM TRAINER", roles={"power", "cadence", "control"}, rssi=-52)  # mains: no battery
POWER_METER = Device(id="sim-pm", name="SIM Power meter", roles={"power", "cadence"}, rssi=-61, battery=64)
CHEST_STRAP = Device(id="sim-hrm", name="SIM HR strap", roles={"heart_rate"}, rssi=-58, battery=88)
ARM_BAND = Device(id="sim-arm", name="SIM HR armband", roles={"heart_rate"}, rssi=-72, battery=14)  # low: shows the warning
CORE = Device(id="sim-core", name="SIM CORE", roles={"temperature"}, rssi=-66, battery=71)
SIM_SCAN_SECONDS = 3.0
SIM_ZERO_SECONDS = 1.5
SIM_SPIN_DOWN_STEP = 0.25  # s between speed updates in a simulated spin-down (about 6 s in all)

REST_HR = 62
MAX_HR = 188
ERG_TAU = 1.5  # seconds for the simulated trainer to settle on a new ERG target
CORE_TAU = 480.0  # core temperature follows effort over minutes
SKIN_TAU = 180.0


class SimRider:
    """Pure model of rider and sensors. `step(t)` returns the samples for second `t`."""

    def __init__(self, workout: Workout | None = None, seed: int = 1) -> None:
        self.workout = workout or default_workout()
        self.rng = random.Random(seed)
        self.hr = 75.0
        self._true_power: deque[float] = deque([0.0], maxlen=2)
        self._hr_history: deque[float] = deque([self.hr] * 4, maxlen=4)
        self._last_t: float | None = None
        self.erg_target: int | None = None  # set: the trainer holds this power
        self.follow: tuple[int, int | None] | None = None  # (target, cadence) shown to the rider
        self.grade: float | None = None  # a climb: the gradient the trainer simulates
        self._erg_power = 0.0
        self.core_temp = 37.1
        self.skin_temp = 33.4

    def _target(self, t: float) -> tuple[float, float]:
        power, cadence = self._planned(t)
        if self.grade is not None:  # on a climb: push a little harder and spin a little slower where it is steep
            g = max(-5.0, min(15.0, self.grade))
            power, cadence = power * (1 + 0.02 * g), max(62.0, cadence - 1.2 * max(0.0, g))
        return power, cadence

    def _planned(self, t: float) -> tuple[float, float]:
        if self.follow is not None:  # the workout player tells the rider what to ride
            target, cadence = self.follow
            return float(target), float(cadence or 88)
        found = self.workout.phase_at(t)
        if found is None:  # ride on gently after the workout
            last = self.workout.phases[-1]
            return float(last.watts_end or last.watts), float(last.cadence or 85)
        _i, phase, s = found
        return float(phase.target_at(s)), float(phase.cadence or 88)

    def step(self, t: float) -> list[PowerSample | HrSample | MetricSample]:
        dt = 1.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        rng = self.rng
        target, target_cadence = self._target(t)

        if self.erg_target is not None:
            # ERG: the trainer settles on its target within a few seconds and holds it tightly.
            self._erg_power += (self.erg_target - self._erg_power) * (1 - math.exp(-dt / ERG_TAU))
            true_power = max(0.0, self._erg_power + rng.gauss(0, 3))
        else:
            # Free ride: the rider chases the target with gears and cadence, less precisely.
            true_power = max(0.0, target * (1 + 0.03 * math.sin(2 * math.pi * t / 47)) + rng.gauss(0, 6))
            self._erg_power = true_power
        self._true_power.append(true_power)
        cadence = max(0.0, target_cadence + 2 * math.sin(t / 23) + rng.gauss(0, 1.5))

        # Heart rate follows power with a first-order lag, rising faster than it falls,
        # plus slow cardiac drift over the ride.
        steady = REST_HR + 95 * true_power / self.workout.ftp + 0.12 * t / 60
        steady = min(steady, MAX_HR)
        tau = 25.0 if steady > self.hr else 40.0
        self.hr += (steady - self.hr) * (1 - math.exp(-dt / tau))
        self._hr_history.append(self.hr)

        # Body temperature: core rises with effort over minutes, skin a bit faster.
        effort = true_power / self.workout.ftp
        self.core_temp += (37.0 + 1.9 * effort**1.3 - self.core_temp) * (1 - math.exp(-dt / CORE_TAU))
        self.skin_temp += (33.2 + 1.2 * effort - self.skin_temp) * (1 - math.exp(-dt / SKIN_TAU))
        heat_strain = 5 * (self.core_temp - 37.0) / 2.5 + 5 * (self.hr - 60) / 120
        heat_strain = min(10.0, max(0.0, heat_strain))

        trainer_watts = round(true_power * 0.99 + rng.gauss(0, 3))
        pm_watts = round(self._true_power[0] * 1.02 + rng.gauss(0, 4))  # reads high, ~1 s late
        # dual-sided: the left leg gives a little less, and a little less again as the ride goes on and in hard efforts
        balance = 49.0 - 0.6 * t / 3600 - 0.8 * max(0.0, effort - 0.9) + rng.gauss(0, 0.8)
        return [
            PowerSample(t=t, device_id=TRAINER.id, watts=max(0, trainer_watts), cadence=round(cadence, 1)),
            PowerSample(t=t, device_id=POWER_METER.id, watts=max(0, pm_watts), cadence=round(cadence + rng.gauss(0, 0.5), 1),
                        balance=round(balance * 2) / 2 if pm_watts > 0 else None),
            HrSample(t=t, device_id=CHEST_STRAP.id, bpm=round(self.hr + rng.gauss(0, 0.7))),
            HrSample(t=t, device_id=ARM_BAND.id, bpm=round(self._hr_history[0] + rng.gauss(0, 1.5))),  # optical, ~3 s late
            MetricSample(t=t, device_id=CORE.id, metric="core_temp", value=round(self.core_temp + rng.gauss(0, 0.01), 2)),
            MetricSample(t=t, device_id=CORE.id, metric="skin_temp", value=round(self.skin_temp + rng.gauss(0, 0.05), 1)),
            MetricSample(t=t, device_id=CORE.id, metric="heat_strain", value=round(heat_strain, 1)),
        ]


TOPIC = {PowerSample: "power", HrSample: "hr", MetricSample: "metric"}


class SimSource:
    """SensorSource that publishes the SimRider once per (ride clock) second.

    Behaves like the Bluetooth manager: devices are found, then connected one by one.
    `connect` lists the ids to connect at start (default: all of them).
    """

    def __init__(self, workout: Workout | None = None, seed: int = 1, connect: Iterable[str] | None = None) -> None:
        self.workout = workout or default_workout()
        self.rider = SimRider(self.workout, seed)
        self.devices = [
            Device(id=d.id, name=d.name, roles=set(d.roles), rssi=d.rssi, battery=d.battery)
            for d in (TRAINER, POWER_METER, CHEST_STRAP, ARM_BAND, CORE)
        ]
        self._initial = {d.id for d in self.devices} if connect is None else set(connect)
        self._scan_until = 0.0
        self._bus: Bus | None = None
        self._rng = random.Random(seed + 7)
        self.trainer_log: list[TrainerCommand] = []  # what the app asked the trainer to do (tests look at it)

    def _device(self, device_id: str) -> Device:
        for d in self.devices:
            if d.id == device_id:
                return d
        raise KeyError(f"unknown device {device_id}")

    def _set_state(self, device: Device, state: str) -> None:
        device.state = state
        if self._bus is not None:
            self._bus.publish("device", device)

    @property
    def scanning(self) -> bool:
        return time.monotonic() < self._scan_until

    def scan(self) -> None:
        self._scan_until = time.monotonic() + SIM_SCAN_SECONDS

    def connect(self, device_id: str) -> None:
        self._set_state(self._device(device_id), "connected")

    def disconnect(self, device_id: str) -> None:
        self._set_state(self._device(device_id), "disconnected")

    async def zero_offset(self, device_id: str) -> int:
        device = self._device(device_id)
        if device.state != "connected" or "power" not in device.capabilities or "control" in device.capabilities:
            raise ValueError(f"{device.name} cannot be zeroed here")
        await asyncio.sleep(SIM_ZERO_SECONDS)
        return self._rng.randint(-12, 12)

    async def calibrate(self, device_id: str, progress) -> None:
        """A spin-down as a real trainer runs it: ride up past the band, stop pedalling, the wheel runs down."""
        device = self._device(device_id)
        if device.state != "connected" or "control" not in device.capabilities:
            raise ValueError(f"{device.name} is not a connected trainer")
        low, high = 30.0, 35.0
        progress({"stage": "speed_up", "low": low, "high": high})
        speed = 24.0
        while speed < high + 1:
            speed += 1.2
            progress({"speed": round(speed, 1)})
            await asyncio.sleep(SIM_SPIN_DOWN_STEP)
        progress({"stage": "stop"})
        while speed > 8:
            speed -= 3.5
            progress({"speed": round(max(0.0, speed), 1)})
            await asyncio.sleep(SIM_SPIN_DOWN_STEP)
        progress({"stage": "success"})

    def set_workout(self, workout: Workout) -> None:
        """Follow a new workout from now on (e.g. rescaled after an FTP change)."""
        self.workout = workout
        self.rider.workout = workout

    async def run(self, bus: Bus, clock: Clock) -> None:
        self._bus = bus

        def on_trainer(cmd: TrainerCommand) -> None:
            trainer = self._device(TRAINER.id)
            if trainer.state != "connected" or "control" not in trainer.roles:
                return  # nobody listening, like a real trainer that is off or not used for control
            self.rider.erg_target = cmd.watts if cmd.mode == "erg" else None
            self.rider.grade = cmd.grade if cmd.mode == "sim" else None
            self.trainer_log.append(cmd)
            bus.publish("trainer_status", TrainerStatus(TRAINER.id, True, cmd.mode, cmd.watts, "ok", cmd.grade))

        def on_workout(pos) -> None:
            if pos.paused:
                self.rider.follow = (0, 1)  # the rider stops pedalling
            else:
                self.rider.follow = None if pos.done or pos.target is None else (pos.target, pos.cadence)

        unsubscribe = [bus.subscribe("trainer", on_trainer), bus.subscribe("workout", on_workout)]
        try:
            self.scan()
            for device in self.devices:
                self._set_state(device, "connected" if device.id in self._initial else "found")
            tick = 0
            while True:
                await clock.sleep(tick - clock.now())
                connected = {d.id for d in self.devices if d.state == "connected"}
                for sample in self.rider.step(float(tick)):
                    if sample.device_id in connected:
                        bus.publish(TOPIC[type(sample)], sample)
                tick += 1
        finally:
            for off in unsubscribe:
                off()
