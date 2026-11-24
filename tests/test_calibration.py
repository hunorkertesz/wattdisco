import json
import time

from fastapi.testclient import TestClient

from wattdisco.core.clock import Clock
from wattdisco.core.workouts import default_workout
from wattdisco.server import Ride, create_app
from wattdisco.sim import simulator
from wattdisco.sim.simulator import SimSource


def test_spin_down_calibration_through_the_app(tmp_path, monkeypatch):
    monkeypatch.setattr(simulator, "SIM_SPIN_DOWN_STEP", 0.005)
    workout = default_workout()
    source = SimSource(workout)
    ride = Ride(source, workout, Clock(speed=40), data_dir=tmp_path, autostart=False)
    with TestClient(create_app(ride)) as c:
        time.sleep(0.3)
        assert c.post("/api/devices/sim-hrm/calibrate").status_code == 409  # not a trainer
        assert c.get("/api/calibration").json() == {"stage": None}
        c.post("/api/devices/sim-trainer/connect")
        time.sleep(0.2)
        r = c.post("/api/devices/sim-trainer/calibrate")
        assert r.status_code == 200 and r.json()["name"] == "SIM TRAINER"
        speeds = []
        for _ in range(200):
            state = c.get("/api/calibration").json()
            if state["speed"] is not None:
                speeds.append(state["speed"])
            if state["stage"] in ("success", "error"):
                break
            time.sleep(0.01)
        assert state["stage"] == "success" and state["low"] == 30.0 and state["high"] == 35.0
        assert speeds and state["speed"] is not None  # live speed while it ran (how many polls catch it depends on timers)
        device = next(d for d in c.get("/api/devices").json()["devices"] if d["id"] == "sim-trainer")
        assert device["calibrated_at"]
        time.sleep(0.6)  # the remembered devices are saved shortly after
        assert json.loads((tmp_path / "devices.json").read_text())

        c.post("/api/ride/start", json={"mode": "erg"})
        refused = c.post("/api/devices/sim-trainer/calibrate")
        assert refused.status_code == 409 and "end the ride" in refused.json()["detail"]
        c.post("/api/ride/stop")
