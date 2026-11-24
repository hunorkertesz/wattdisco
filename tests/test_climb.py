import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from fitread import read_fit

from wattdisco.ble import ftms
from wattdisco.core.clock import Clock
from wattdisco.core.climb import (
    BUILTIN_CLIMBS,
    GRADE_STEP,
    ClimbPlayer,
    Profile,
    estimate_seconds,
    trainer_grade,
)
from wattdisco.core.recorder import load_ride
from wattdisco.core.speed import DRIVETRAIN, Road, resistance, steady_speed
from wattdisco.core.summary import build_summary
from wattdisco.core.workouts import BUILTIN, default_workout, to_workout
from wattdisco.export.fit import build_fit
from wattdisco.models import TrainerCommand
from wattdisco.server import Ride, create_app
from wattdisco.sim.simulator import SimSource


def climb_workout(climb_id="climb-steady-6k", ftp=240):
    return to_workout(BUILTIN[climb_id], ftp)


def test_profile_heights_and_gentle_changes():
    p = Profile.from_segments([[1000, 5], [1000, 10]])
    assert p.length == 2000
    assert p.total_ascent == pytest.approx(150, abs=1)  # 50 m + 100 m: blending moves height, never adds it
    assert p.grade_at(200) == pytest.approx(5) and p.grade_at(1800) == pytest.approx(10)
    steps = [abs(b - a) for a, b in zip(p.grades, p.grades[1:])]
    assert max(steps) < 1.5  # 5 % to 10 % spread over 60 m, not one jump
    assert p.grade_at(2500) == 0  # flat after the top
    down = Profile.from_segments([[500, 4], [500, -6]])
    assert down.ascent[-1] == pytest.approx(20, abs=1.5)  # a descent never counts as climbing
    with pytest.raises(ValueError):
        Profile.from_segments([])


def test_builtin_climbs_add_up():
    steady = Profile.from_segments(BUILTIN_CLIMBS["climb-steady-6k"][1])
    assert steady.length == pytest.approx(6000) and steady.avg_grade == pytest.approx(7, abs=0.05)
    for _name, segments in BUILTIN_CLIMBS.values():
        assert 1000 <= Profile.from_segments(segments).length <= 12000


def test_trainer_grade_follows_difficulty_and_limits():
    assert trainer_grade(8) == 8.0
    assert trainer_grade(8, 0.5) == 4.0
    assert trainer_grade(30) == 20.0  # most trainers can't simulate more at climbing speed
    assert trainer_grade(-6) == -3.0  # a trainer can't push: descents at half
    assert trainer_grade(-20) == -5.0


def test_slope_physics_matches_hand_numbers():
    v = steady_speed(250, 75, grade=7)
    assert 3.6 < v < 3.9  # about 13.5 km/h
    assert resistance(v, 83, 7) * v == pytest.approx(250 * DRIVETRAIN, rel=0.01)
    assert steady_speed(250, 75) == steady_speed(250, 75, grade=0)  # flat rides are unchanged
    descent = Road(75, grade_at=lambda _d: -6)
    for _ in range(60):
        descent.step(0)  # coasting downhill speeds up
    assert descent.speed > 10
    climb = Road(75, grade_at=lambda _d: 12)
    for _ in range(30):
        climb.step(0)  # no power on a steep road: it stops, it never rolls back
    assert climb.speed == 0


def test_estimate_is_slower_on_steeper_roads():
    flatish = Profile.from_segments([[2000, 2]])
    steep = Profile.from_segments([[2000, 10]])
    assert estimate_seconds(steep, 200) > 2 * estimate_seconds(flatish, 200)
    assert to_workout(BUILTIN["climb-steady-6k"], 300).seconds < to_workout(BUILTIN["climb-steady-6k"], 200).seconds


def test_climb_player_sends_the_gradient_ahead_and_only_real_changes():
    player = ClimbPlayer(climb_workout(), start=0.0)
    first = player.command(0.0)
    assert first.mode == "sim" and first.grade == pytest.approx(player.profile.grade_at(0), abs=0.1)
    assert player.command(0.5) is None  # nothing changed
    pos = player.position(0.5)
    assert pos.target is None and not pos.done and pos.kind == "climb"

    player.road.distance = 2950  # just before the segment border (7 % -> 6.5 %... and on)
    sent = player.last_command
    nxt = None
    for now in (1.0, 1.5, 2.0, 2.5):
        player.road.distance += 30
        nxt = player.command(now) or nxt
    assert nxt is None or abs(nxt.grade - sent.grade) >= GRADE_STEP

    player.pause(3.0)
    assert player.command(3.0) == TrainerCommand("free")  # paused: a flat road
    player.resume(4.0)
    assert player.command(4.0).mode == "sim"
    player.road.distance = player.profile.length + 1
    assert player.position(5.0).done
    assert player.command(5.0) == TrainerCommand("free")  # at the top: flat again
    assert player.climb_state()["top"] is True


def test_climb_player_difficulty_and_rules():
    w = climb_workout("climb-short-steep-2k")
    half = ClimbPlayer(w, difficulty=0.5)
    half.road.distance = 900
    full = ClimbPlayer(w, difficulty=1.0)
    full.road.distance = 900
    assert half.command(0.0).grade == pytest.approx(full.command(0.0).grade / 2, abs=0.1)
    with pytest.raises(ValueError):
        ClimbPlayer(w, difficulty=0.3)
    with pytest.raises(ValueError):
        ClimbPlayer(default_workout())  # not a climb
    with pytest.raises(ValueError):
        half.set_mode("erg")  # a climb has no ERG
    assert half.adjust_phase(0.0, 1) == 0 and half.erg_state == "off"


class FeatureTrainer:
    """A connected FTMS trainer that tells whether it can simulate a road."""

    def __init__(self, simulation: bool):
        targets = (1 << 3) | ((1 << ftms.TARGET_SIMULATION_BIT) if simulation else 0)  # power + maybe simulation
        self.features = (0).to_bytes(4, "little") + targets.to_bytes(4, "little")
        self.written: list[bytes] = []
        self.callback = None

    async def read_gatt_char(self, uuid):
        if uuid == ftms.FITNESS_MACHINE_FEATURE:
            return self.features
        return bytes.fromhex("0000" "e803" "0100")

    async def start_notify(self, _uuid, callback):
        self.callback = callback

    async def write_gatt_char(self, _uuid, data, response=True):
        self.written.append(bytes(data))
        asyncio.get_running_loop().call_soon(self.callback, None, bytearray([0x80, data[0], 0x01]))


def test_ftms_sends_the_gradient_or_says_it_cannot():
    async def run(simulation):
        trainer, results = FeatureTrainer(simulation), []
        controller = ftms.FtmsController(trainer, lambda ok, cmd, msg: results.append((ok, msg)))
        await controller.setup()
        ok = await controller.apply(TrainerCommand("sim", grade=7.5))
        return ok, trainer.written, results

    ok, written, _ = asyncio.run(run(True))
    assert ok and written[-1] == ftms.set_simulation(grade_pct=7.5)
    assert written[-1][3:5] == (750).to_bytes(2, "little", signed=True)
    ok, written, results = asyncio.run(run(False))
    assert not ok and results[-1] == (False, ftms.NO_SIMULATION)
    assert all(w[0] != ftms.OP_SET_SIMULATION for w in written)  # nothing sent to a trainer that can't
    assert ftms.supports_simulation(FeatureTrainer(True).features)


def test_climb_ride_end_to_end_with_the_simulator(tmp_path):
    workout = default_workout()
    source = SimSource(workout)
    ride = Ride(source, workout, Clock(speed=40), data_dir=tmp_path, autostart=False)
    with TestClient(create_app(ride)) as c:
        time.sleep(0.3)
        r = c.post("/api/ride/start", json={"workout_id": "climb-short-steep-2k", "mode": "climb", "difficulty": 0.5})
        assert r.status_code == 200
        assert c.get("/workout.html").status_code == 200 and c.get("/climb.html").status_code == 200
        time.sleep(2.0)
        state = c.get("/api/state").json()
        assert state["climb"]["distance"] > 50 and state["workout"]["target"] is None
        sims = [cmd for cmd in source.trainer_log if cmd.mode == "sim"]
        assert sims and max(cmd.grade for cmd in sims) <= 6.0  # 12 % at most, at half difficulty
        ride_id = c.post("/api/ride/stop").json()["id"]
        assert source.trainer_log[-1] == TrainerCommand("free")  # the end: a flat road to spin down on
        summary = c.get(f"/api/rides/{ride_id}/summary").json()
        assert c.post("/api/ride/start", json={"workout_id": "sweet-spot-3x4", "mode": "climb"}).status_code == 409

    climb = summary["climb"]
    assert climb["length"] == 2000 and climb["climbed"] > 0 and climb["difficulty"] == 0.5 and not climb["top"]
    assert summary["series"]["altitude"][-1] > 0
    data = load_ride(ride.store.path(ride_id, ".jsonl"))
    assert data.meta["climb_difficulty"] == 0.5 and data.meta["workout"]["climb"]
    ticks = data.ticks
    assert all(t["distance"] >= 0 for t in ticks) and ticks[-1]["distance"] >= ticks[0]["distance"]
    records = read_fit(build_fit(data, build_summary(data)))["record"]
    assert records[-1].altitude == pytest.approx(ticks[-1]["altitude"], abs=0.5)
    assert records[-1].distance == pytest.approx(ticks[-1]["distance"], abs=1)
