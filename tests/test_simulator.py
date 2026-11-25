import asyncio

import pytest

from wattdisco.core.bus import Bus
from wattdisco.core.clock import ManualClock
from wattdisco.core.session import Session
from wattdisco.models import HrSample, Phase, PowerSample, Workout
from wattdisco.core.workouts import default_workout
from wattdisco.sim.simulator import SimRider, SimSource


def test_workout_targets():
    w = Workout("t", 250, [Phase("warmup", 100, 100, watts_end=200), Phase("sweetspot", 60, 225)])
    assert w.seconds == 160
    assert w.target_at(0) == 100
    assert w.target_at(50) == 150
    assert w.target_at(100) == 225
    assert w.phase_at(159)[0] == 1
    assert w.target_at(160) is None


def test_rider_is_deterministic():
    a, b = SimRider(seed=7), SimRider(seed=7)
    assert [a.step(t) for t in range(30)] == [b.step(t) for t in range(30)]


def test_rider_follows_workout_and_hr_responds():
    workout = default_workout(250)
    rider = SimRider(workout)
    samples = {t: rider.step(t) for t in range(workout.seconds)}

    def mean_watts(device_id, t0, t1):
        vals = [s.watts for t in range(t0, t1) for s in samples[t] if isinstance(s, PowerSample) and s.device_id == device_id]
        return sum(vals) / len(vals)

    def hr_at(t):
        return next(s.bpm for s in samples[t] if isinstance(s, HrSample) and s.device_id == "sim-hrm")

    # first sweet spot interval: 300..540 s at 225 W
    assert mean_watts("sim-trainer", 330, 540) == pytest.approx(225, rel=0.05)
    # power meter reads about 3 % above the trainer
    assert mean_watts("sim-pm", 330, 540) / mean_watts("sim-trainer", 330, 540) == pytest.approx(1.03, abs=0.02)
    # HR rises through the interval and drops in recovery
    assert hr_at(539) > hr_at(301) + 15
    assert hr_at(659) < hr_at(539) - 10


def test_sim_source_feeds_session():
    async def go():
        bus, clock = Bus(), ManualClock()
        session = Session(bus, clock)
        source = SimSource()
        task = asyncio.create_task(source.run(bus, clock))
        while clock.now() < 20:
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return session.snapshot()

    s = asyncio.run(go())
    assert len(s.devices) == 5
    assert all(d.state == "connected" and not d.no_data for d in s.devices)
    assert s.main_power_id == "sim-trainer"
    assert 80 < s.power < 130
    assert s.hr is not None and s.cadence is not None
    assert s.alerts == []


def test_erg_holds_power_tighter_than_free_ride():
    import statistics

    def spread(erg):
        rider = SimRider(default_workout(250))
        rider.follow = (200, 90)
        rider.erg_target = 200 if erg else None
        watts = [s.watts for t in range(120) for s in rider.step(t) if s.device_id == "sim-trainer"][20:]
        return statistics.mean(watts), statistics.pstdev(watts)

    erg_mean, erg_sd = spread(True)
    free_mean, free_sd = spread(False)
    assert erg_mean == pytest.approx(198, abs=4)  # trainer reads 1 % low
    assert erg_sd < free_sd


def test_sim_obeys_trainer_commands():
    from wattdisco.models import TrainerCommand

    async def go():
        bus, clock = Bus(), ManualClock()
        statuses = []
        bus.subscribe("trainer_status", statuses.append)
        source = SimSource()
        task = asyncio.create_task(source.run(bus, clock))
        await asyncio.sleep(0)
        bus.publish("trainer", TrainerCommand("erg", 180))
        erg = source.rider.erg_target
        bus.publish("trainer", TrainerCommand("free"))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return erg, source.rider.erg_target, statuses

    erg, free, statuses = asyncio.run(go())
    assert (erg, free) == (180, None)
    assert [s.mode for s in statuses] == ["erg", "free"]


def test_sim_skips_ahead_when_it_falls_behind_the_clock():
    """A busy computer (or a fast sim) delays the sensors: they send what they measure now, never old seconds,
    which the session would take for a dropout (and the ride would pause on its own)."""

    async def go():
        bus, clock = Bus(), ManualClock()
        session = Session(bus, clock)
        stamps = []
        bus.subscribe("power", lambda s: stamps.append(s.t) if s.device_id == "sim-trainer" else None)
        source = SimSource()
        task = asyncio.create_task(source.run(bus, clock))
        while clock.now() < 10:
            await asyncio.sleep(0)
        clock.advance(30)  # the sim was held up for 30 ride seconds
        while clock.now() < 45:
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return stamps, session.snapshot()

    stamps, s = asyncio.run(go())
    assert stamps[-1] >= 44 and not any(11 < t < 40 for t in stamps)  # the missed seconds are dropped
    assert s.power_raw is not None and s.alerts == []


def test_sim_rider_stops_only_when_the_rider_pauses():
    """An app pause (no pedalling seen, or waiting for the first strokes) ends when the rider pedals, so the
    simulated rider rides on through it; a pause the rider chose stops the pedals."""
    from dataclasses import replace

    from wattdisco.core.workout import WorkoutPlayer

    async def go():
        bus, clock = Bus(), ManualClock()
        source = SimSource()
        task = asyncio.create_task(source.run(bus, clock))
        await asyncio.sleep(0)
        pos = replace(WorkoutPlayer(default_workout(250)).position(100.0), paused=True)
        follows = {}
        for who in ("stopped", "waiting", "rider"):
            bus.publish("pause", who)
            bus.publish("workout", pos)
            follows[who] = source.rider.follow
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return pos, follows

    pos, follows = asyncio.run(go())
    assert follows["stopped"] == follows["waiting"] == (pos.target, pos.cadence)
    assert follows["rider"] == (0, 1)
