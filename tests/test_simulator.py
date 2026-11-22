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
