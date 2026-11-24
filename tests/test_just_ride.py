import time

from fastapi.testclient import TestClient

from wattdisco.core.workout import OpenRidePlayer
from wattdisco.core.workouts import BUILTIN, JUST_RIDE_ID, OPEN_RIDE_SECONDS, default_workout, to_workout
from wattdisco.core.clock import Clock
from wattdisco.models import TrainerCommand
from wattdisco.server import Ride, create_app
from wattdisco.sim.simulator import SimSource


def test_open_ride_counts_up_without_a_target():
    workout = to_workout(BUILTIN[JUST_RIDE_ID], 240)
    assert workout.open and workout.seconds == OPEN_RIDE_SECONDS
    player = OpenRidePlayer(workout, start=0.0)
    pos = player.position(600.0)
    assert pos.target is None and pos.total == 600 and not pos.done and pos.next is None and pos.interval is None
    assert player.command(600.0, cadence=90, power=200) == TrainerCommand("free")  # a flat road, never ERG
    assert player.command(601.0) is None
    assert player.adjust_phase(600.0, 1) == 0
    player.pause(700.0)
    player.resume(760.0)
    assert player.position(800.0).elapsed == 740.0  # pauses don't count
    assert player.position(OPEN_RIDE_SECONDS + 100.0).done  # it ends on its own only after hours


def test_just_ride_records_and_summarises_without_a_target(tmp_path):
    workout = default_workout()
    source = SimSource(workout)
    ride = Ride(source, workout, Clock(speed=60), data_dir=tmp_path, autostart=False)
    with TestClient(create_app(ride)) as c:
        time.sleep(0.3)
        for d in ("sim-trainer", "sim-hrm"):
            c.post(f"/api/devices/{d}/connect")
        time.sleep(0.2)
        listed = [w for w in c.get("/api/workouts").json()["workouts"] if w["id"] == JUST_RIDE_ID]
        assert listed and listed[0]["open"] and listed[0]["category"] == "ride"
        assert c.post("/api/ride/start", json={"workout_id": JUST_RIDE_ID, "mode": "free"}).status_code == 200
        time.sleep(2.0)
        state = c.get("/api/state").json()
        assert state["workout"]["target"] is None and state["workout"]["kind"] == "ride"
        assert state["workout"]["total"] >= 60 and not state["workout"]["done"]
        assert {cmd.mode for cmd in source.trainer_log} == {"free"}
        ride_id = c.post("/api/ride/stop").json()["id"]
        summary = c.get(f"/api/rides/{ride_id}/summary").json()
        description = c.get(f"/api/rides/{ride_id}/description").json()
    assert summary["workout"]["open"] and summary["nerd"]["on_target_pct"] is None
    assert summary["basic"]["avg_power"] > 0 and summary["basic"]["distance_km"] > 0
    assert description["name"] == "Watt Disco: Just ride" and "On target" not in description["description"]
