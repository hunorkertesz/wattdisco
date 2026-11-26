import pytest
from fastapi.testclient import TestClient

from wattdisco.config import load_settings
from wattdisco.core import plans
from wattdisco.core.clock import Clock
from wattdisco.core.workouts import default_workout
from wattdisco.models import Phase, Workout
from wattdisco.core.workout import WorkoutPlayer
from wattdisco.server import Ride, create_app, workout_payload
from wattdisco.sim.simulator import SimSource

WORKOUT = Workout(
    "test",
    250,
    [
        Phase("warmup", 60, 100, watts_end=160),
        Phase("sweetspot", 120, 225),
        Phase("recovery", 60, 125),
        Phase("threshold", 60, 250),
        Phase("cooldown", 60, 120),
    ],
)


def test_workout_payload_mid_phase():
    pos = WorkoutPlayer(WORKOUT).position(90)
    w = workout_payload(pos, power=230, ftp=250, mode="erg")
    assert (w["kind"], w["target"], w["target_zone"], w["mode"]) == ("sweetspot", 225, 4, "erg")
    assert w["seconds_left"] == 90
    assert w["next"]["kind"] == "recovery"
    assert w["interval"] == {"n": 1, "of": 2}
    assert (w["diff"], w["on_target"]) == (5, True)


def test_workout_payload_off_target_no_power_done():
    player = WorkoutPlayer(WORKOUT)
    assert workout_payload(player.position(30), 100, 250, "free")["on_target"] is False
    assert workout_payload(player.position(10), None, 250, "erg")["diff"] is None
    done = workout_payload(player.position(400), 100, 250, "erg")
    assert done["done"] and done["target"] is None and done["target_zone"] is None


@pytest.fixture
def client(tmp_path):
    ride = Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), data_dir=tmp_path)
    with TestClient(create_app(ride)) as c:
        yield c


def test_renamed_pages_redirect(client):
    for old, new in (("/settings.html", "/zones.html"), ("/testbench.html", "/compare.html")):
        r = client.get(old, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == new


def test_fonts_kept_pages_checked_again(client):
    font = client.get("/fonts/chakra-petch-700-latin.woff2")
    assert font.status_code == 200 and font.headers["cache-control"].startswith("max-age=")
    assert client.get("/workout.html").headers["cache-control"] == "no-cache"
    assert client.get("/theme.css").headers["cache-control"] == "no-cache"


def test_pages_served_design_hidden(client):
    assert client.get("/workout.html").status_code == 200
    assert client.get("/theme.css").status_code == 200
    assert client.get("/design/Main.dc.html").status_code == 404
    for page in ("launch", "connect", "sensors", "ready", "editor", "workout", "summary", "upload", "zones", "compare", "history",
                 "plan", "progress"):
        assert client.get(f"/{page}.html").status_code == 200, page


def test_websocket_hello_state_and_command(client):
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["workout"]["seconds"] == 360
        assert len(hello["workout"]["phases"]) == 5

        state = ws.receive_json()
        assert state["type"] == "state"
        assert len(state["devices"]) == 5
        assert state["workout"]["kind"] in {"warmup", "sweetspot"}

        ws.send_json({"cmd": "set_smoothing", "value": "10s"})
        for _ in range(10):
            state = ws.receive_json()
            if state.get("smoothing") == "10s":
                break
        assert state["smoothing"] == "10s"

        ws.send_json({"cmd": "set_smoothing", "value": "7s"})
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "error":
                break
        assert msg["type"] == "error"


def test_rest_main_sensors(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.receive_json()  # sim devices are connected by now
    r = client.post("/api/main-sensors", json={"power": "sim-pm", "hr": "sim-arm"})
    assert r.status_code == 200
    assert r.json() == {"main_power_id": "sim-pm", "main_hr_id": "sim-arm"}
    assert client.post("/api/main-sensors", json={"power": "sim-hrm"}).status_code == 400
    assert client.post("/api/smoothing", json={"value": "raw"}).json() == {"smoothing": "raw"}
    state = client.get("/api/state").json()
    assert state["main_power_id"] == "sim-pm"
    assert state["smoothing"] == "raw"


def test_settings_update_rescales_workout(tmp_path):
    workout = default_workout(240)
    source = SimSource(workout)
    ride = Ride(source, workout, Clock(speed=20), workout_factory=default_workout, data_dir=tmp_path)
    with TestClient(create_app(ride)) as client:
        s = client.get("/api/settings").json()
        assert (s["ftp"], s["weight_kg"], s["w_per_kg"]) == (240, 75.0, 3.2)
        assert len(s["zones"]) == 6

        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["settings"]["ftp"] == 240
            r = client.put("/api/settings", json={"ftp": 300, "weight_kg": 70})
            assert r.status_code == 200
            assert r.json()["zones"][1]["to_w"] == 225
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "hello":
                    break
            assert msg["type"] == "hello"
            assert msg["settings"]["ftp"] == 300
            assert msg["workout"]["phases"][1]["watts"] == 270

        assert source.rider.workout.phases[1].watts == 270
        assert load_settings(tmp_path).ftp == 300
        assert client.put("/api/settings", json={"ftp": 10, "weight_kg": 70}).status_code == 400


def test_state_has_power_zone(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        state = ws.receive_json()
    assert state["power_zone"] in range(1, 7)


def test_zones_preview(client):
    z = client.get("/api/zones", params={"ftp": 300}).json()
    assert z[1]["to_w"] == 225
    assert client.get("/api/zones", params={"ftp": 5}).status_code == 400
    assert client.get("/zones.html").status_code == 200


def receive_until(ws, check, tries=40):
    for _ in range(tries):
        msg = ws.receive_json()
        if msg["type"] == "state" and check(msg):
            return msg
    raise AssertionError("condition not reached")


def test_erg_mode_switch_and_trainer_status(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        state = receive_until(ws, lambda m: m["trainer"] is not None)
        assert state["workout"]["mode"] == "erg"
        assert state["trainer"]["mode"] == "erg" and state["trainer"]["ok"]

        ws.send_json({"cmd": "set_mode", "value": "free"})
        state = receive_until(ws, lambda m: m["trainer"]["mode"] == "free")
        assert state["workout"]["mode"] == "free"

        ws.send_json({"cmd": "set_mode", "value": "slope"})
        for _ in range(10):
            if ws.receive_json()["type"] == "error":
                break
        else:
            raise AssertionError("no error for a bad mode")


def test_skip_phase(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        receive_until(ws, lambda m: m["workout"]["kind"] == "warmup")
        ws.send_json({"cmd": "skip_phase"})
        state = receive_until(ws, lambda m: m["workout"]["kind"] == "sweetspot")
        assert state["workout"]["elapsed"] >= 60


def test_adjust_phase(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        before = receive_until(ws, lambda m: m["workout"]["target"] is not None)["workout"]
        ws.send_json({"cmd": "adjust_phase", "steps": 2})
        state = receive_until(ws, lambda m: m["workout"]["adjust"] == 10)
        assert state["workout"]["adjustments"] == {str(state["workout"]["index"]): 10}
        assert state["workout"]["target"] >= before["target"] + 5  # warm-up ramps up too


def test_state_has_extra_metrics_and_cadence_choice(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        state = receive_until(ws, lambda m: any(x["key"] == "core_temp" for x in m["metrics"]))
        keys = {m["key"] for m in state["metrics"]}
        assert {"core_temp", "skin_temp", "heat_strain", "w_per_kg"} <= keys
        wkg = next(m for m in state["metrics"] if m["key"] == "w_per_kg")
        assert wkg["value"] == pytest.approx(state["power"] / 75, abs=0.01)

        ws.send_json({"cmd": "set_main_cadence", "device_id": "sim-pm"})
        state = receive_until(ws, lambda m: m["main_cadence_id"] == "sim-pm")
        assert state["cadence"] is not None


def test_ride_stop_summary_fit_delete_and_restart(client, tmp_path):
    import time

    from fitread import read_fit

    # ride well past the 30 s two power sources need to overlap before they are compared
    deadline = time.monotonic() + 10
    while (state := client.get("/api/state").json())["workout"]["elapsed"] < 45:
        assert time.monotonic() < deadline, "sim ride did not reach 45 s"
        time.sleep(0.1)
    assert state["ride"]["active"] and state["ride"]["id"]
    running = state["ride"]["id"]
    assert client.get(f"/api/rides/{running}/summary").status_code == 409  # still riding

    ride_id = client.post("/api/ride/stop").json()["id"]
    assert ride_id == running
    assert client.post("/api/ride/stop").status_code == 409
    assert client.get("/api/state").json()["ride"] == {"active": False, "paused": False, "auto_paused": None, "id": None, "interrupted": None, "last_id": ride_id,
                                                       "review_id": ride_id}

    summary = client.get(f"/api/rides/{ride_id}/summary").json()
    assert summary["duration"] >= 10
    assert summary["basic"]["avg_power"] > 0
    assert summary["sources"]["b_name"]  # the sim has two power sources
    assert client.get("/api/rides").json() == []  # simulated: not in the history
    [line] = client.get("/api/rides?include_sim=true").json()
    assert line["id"] == ride_id and line["sim"] is True and line["test"] is None and line["strava"] is None
    assert line["avg_power"] == summary["basic"]["avg_power"] and line["load"] == summary["nerd"]["load"]
    assert line["duration"] == summary["duration"] and line["ftp"] == summary["ftp"]
    assert "series" not in line  # the history gets headline numbers only
    # the history line follows the ride's files: a Strava upload shows up without a restart
    from wattdisco.core.rides import RideStore

    RideStore(tmp_path / "rides").set_upload_status(ride_id, {"state": "done", "url": "https://www.strava.com/activities/1"})
    assert client.get("/api/rides?include_sim=true").json()[0]["strava"] == {"state": "done", "url": "https://www.strava.com/activities/1", "message": None}

    fit = client.get(f"/api/rides/{ride_id}/export.fit")
    assert fit.status_code == 200
    assert "attachment" in fit.headers["content-disposition"]
    assert len(read_fit(fit.content)["record"]) == summary["duration"]

    assert client.get("/api/rides/nope/summary").status_code == 404
    assert client.delete(f"/api/rides/{ride_id}").status_code == 200
    assert client.get("/api/rides").json() == []

    new_id = client.post("/api/ride/start").json()["id"]
    assert new_id and client.get("/api/state").json()["ride"]["active"]
    assert client.post("/api/ride/start").status_code == 409


def test_start_routes(client):
    # mid-ride the app opens straight on the workout
    assert client.get("/", follow_redirects=False).headers["location"] == "/workout.html"
    assert client.get("/train", follow_redirects=False).headers["location"] == "/workout.html"
    ride_id = client.post("/api/ride/stop").json()["id"]
    assert client.get("/", follow_redirects=False).headers["location"] == "/launch.html"
    # after the ride TRAIN goes back to its summary until the rider moves on
    assert client.get("/train", follow_redirects=False).headers["location"] == f"/summary.html?ride={ride_id}"
    client.post("/api/ride/review/done")
    assert client.get("/train", follow_redirects=False).headers["location"] == "/ready.html"


# ---------- Strava ----------

def strava_client(tmp_path):
    from test_strava import KEYS, FakeStravaApi

    from wattdisco.export.strava import Strava

    api = FakeStravaApi()
    strava = Strava(KEYS, tmp_path, client_factory=api.factory, clock=lambda: api.now, sleep=lambda s: None,
                    revoke=api.revoke)
    ride = Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), data_dir=tmp_path, strava=strava)
    return TestClient(create_app(ride)), api


def test_strava_not_set_up(client):
    assert client.get("/api/strava/status").json()["configured"] is False
    assert client.get("/api/strava/connect", follow_redirects=False).status_code == 409
    assert client.get("/upload.html").status_code == 200


def test_strava_connect_button_is_the_official_image(client):
    page = client.get("/upload.html").text
    assert 'id="connect"' in page and "/img/btn_strava_connect_with_orange.png" in page
    for name in ("btn_strava_connect_with_orange.png", "btn_strava_connect_with_orange_x2.png"):
        r = client.get(f"/img/{name}")
        assert r.status_code == 200 and r.content.startswith(b"\x89PNG")


def test_strava_sign_in_and_options(tmp_path):
    from urllib.parse import parse_qs, urlparse

    c, api = strava_client(tmp_path)
    with c:
        r = c.get("/api/strava/connect", params={"return_to": "/upload.html?ride=x"}, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"].startswith("https://www.strava.com/oauth/authorize")
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        redirect = next(kw for name, kw in api.calls if name == "authorize")["redirect_uri"]
        assert redirect.endswith("/api/strava/callback")

        forged = c.get("/api/strava/callback", params={"state": "nope", "code": "c", "scope": "activity:write"},
                       follow_redirects=False)
        assert "strava=error" in forged.headers["location"]

        ok = c.get("/api/strava/callback", params={"state": state, "code": "c", "scope": "read,activity:write"},
                   follow_redirects=False)
        assert ok.headers["location"] == "/upload.html?ride=x&strava=connected"
        status = c.get("/api/strava/status").json()
        assert status["connected"] and status["athlete"] == "Test Rider"
        assert "access_token" not in str(status)  # tokens never reach the browser

        assert c.put("/api/strava/options", json={"auto_upload": True}).json()["auto_upload"] is True
        c.put("/api/settings", json={"ftp": 250, "weight_kg": 70})  # rider settings keep the Strava choice
        assert c.get("/api/strava/status").json()["auto_upload"] is True
        assert c.put("/api/strava/options", json={"sport_type": "Run"}).status_code == 400

        assert c.post("/api/strava/disconnect").json()["connected"] is False


def test_upload_needs_consent_for_simulated_rides(tmp_path):
    import time

    from urllib.parse import parse_qs, urlparse

    c, api = strava_client(tmp_path)
    with c:
        r = c.get("/api/strava/connect", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        c.get("/api/strava/callback", params={"state": state, "code": "c", "scope": "activity:write"})
        c.put("/api/strava/options", json={"auto_upload": True})
        time.sleep(1.0)
        ride_id = c.post("/api/ride/stop").json()["id"]
        time.sleep(0.2)
        assert not any(call[0] == "upload" for call in api.calls)  # simulated: never uploaded on its own

        body = {"name": "Test", "description": "d", "sport_type": "VirtualRide", "private": True}
        refused = c.post(f"/api/rides/{ride_id}/upload", json=body)
        assert refused.status_code == 409 and "simulated" in refused.json()["detail"]

        assert c.post(f"/api/rides/{ride_id}/upload", json={**body, "allow_sim": True}).json() == {"state": "uploading"}
        for _ in range(50):
            status = c.get(f"/api/rides/{ride_id}/upload").json()
            if status["state"] != "uploading":
                break
            time.sleep(0.05)
        assert status["state"] == "done" and status["url"].endswith("/activities/99")
        upload = next(call for call in api.calls if call[0] == "upload")
        assert upload[2] > 200  # the real .fit bytes were sent
        assert upload[3]["name"] == "Test"


# ---------- devices, workouts, ride start (step 7) ----------

def idle_client(tmp_path, connect=()):
    ride = Ride(SimSource(WORKOUT, connect=connect), WORKOUT, Clock(speed=20), data_dir=tmp_path, autostart=False)
    return TestClient(create_app(ride)), ride


def test_devices_connect_roles_zero_and_remember(tmp_path):
    import time

    c, ride = idle_client(tmp_path)
    with c:
        time.sleep(0.1)
        d = c.get("/api/devices").json()
        assert {x["state"] for x in d["devices"]} == {"found"}
        assert d["remembered"] == [] and d["auto_connect"] is True
        kinds = {x["id"]: x["kind"] for x in d["devices"]}
        assert kinds["sim-trainer"] == "Smart trainer" and kinds["sim-pm"] == "Power meter"

        c.post("/api/devices/sim-pm/connect")
        c.post("/api/devices/sim-hrm/connect")
        c.post("/api/devices/sim-trainer/connect")
        d = c.get("/api/devices").json()
        assert {x["id"] for x in d["devices"] if x["state"] == "connected"} == {"sim-pm", "sim-hrm", "sim-trainer"}

        # use the trainer only for control: power comes from the power meter
        assert c.post("/api/devices/sim-trainer/role", json={"roles": ["control"]}).status_code == 200
        assert c.post("/api/devices/sim-trainer/role", json={"roles": ["heart_rate"]}).status_code == 400
        time.sleep(0.2)
        assert c.get("/api/state").json()["main_power_id"] == "sim-pm"

        z = c.post("/api/devices/sim-pm/zero-offset")
        assert z.status_code == 200 and isinstance(z.json()["offset"], int)
        assert c.post("/api/devices/sim-trainer/zero-offset").status_code == 409  # trainers are not zeroed here

        c.post("/api/devices/sim-hrm/disconnect")
        assert next(x for x in c.get("/api/devices").json()["devices"] if x["id"] == "sim-hrm")["state"] == "disconnected"
        assert c.post("/api/devices/nope/connect").status_code == 404

        assert c.post("/api/scan").json() == {"scanning": True}
        assert c.get("/api/devices").json()["scanning"] is True

        # starting a ride remembers the connected devices and their roles for next time
        c.post("/api/ride/start", json={})
        c.post("/api/ride/stop")
        assert c.put("/api/devices/auto-connect", json={"enabled": False}).json()["auto_connect"] is False

    from wattdisco.config import KnownDevices

    known = KnownDevices(tmp_path)
    assert set(known.ids) == {"sim-pm", "sim-trainer"}
    assert known.roles("sim-trainer") == {"control"}
    assert known.devices["sim-pm"]["zeroed_at"]
    assert known.auto_connect is False


def test_remembered_roles_come_back_on_connect(tmp_path):
    import time

    from wattdisco.config import KnownDevices
    from wattdisco.models import Device

    KnownDevices(tmp_path).remember([Device("sim-trainer", "SIM TRAINER", {"control"}, "connected",
                                            capabilities={"power", "cadence", "control"})])
    c, ride = idle_client(tmp_path, connect=["sim-trainer"])
    with c:
        time.sleep(0.2)
        trainer = next(x for x in c.get("/api/devices").json()["devices"] if x["id"] == "sim-trainer")
        assert trainer["roles"] == ["control"] and trainer["remembered"]


def test_workout_library_and_ride_start_options(tmp_path):
    c, ride = idle_client(tmp_path, connect=None)
    with c:
        lib = c.get("/api/workouts").json()
        assert lib["selected"] == "sweet-spot-3x4"
        first = next(w for w in lib["workouts"] if w["id"] == "sweet-spot-3x4")
        assert first["builtin"] and first["seconds"] == 1680 and first["text"].startswith("3 × 4 MIN")
        assert first["category"] == "sweetspot" and len(lib["categories"]) == 11  # with Climbs and Just ride

        mine = c.post("/api/workouts", json={"name": "Short test", "phases": [
            {"kind": "warmup", "seconds": 60, "pct": 0.4, "pct_end": 0.6, "cadence": 85},
            {"kind": "vo2", "seconds": 120, "pct": 1.2, "cadence": 100}]}).json()
        assert mine["intervals"] == 1 and mine["seconds"] == 180
        bad = c.post("/api/workouts", json={"name": "x", "phases": [{"kind": "vo2", "seconds": 1, "pct": 1}]})
        assert bad.status_code == 400

        r = c.post("/api/ride/start", json={"workout_id": mine["id"], "mode": "free", "smoothing": "10s",
                                            "power_source": "sim-pm", "ease": True})
        assert r.status_code == 200
        state = c.get("/api/state").json()
        assert state["workout"]["mode"] == "free" and state["smoothing"] == "10s"
        assert state["workout"]["ease"] is True
        assert state["main_power_id"] == "sim-pm"
        assert c.get("/api/workout").json()["name"] == "Short test"
        assert c.post(f"/api/workouts/{mine['id']}/select").status_code == 409  # not mid-ride
        c.post("/api/ride/stop")

        renamed = c.put(f"/api/workouts/{mine['id']}", json={"name": "Short test 2", "phases": mine["phases"]}).json()
        assert renamed["id"] == mine["id"] and renamed["name"] == "Short test 2"
        copy = c.put("/api/workouts/vo2-5x3", json={"name": "VO2 copy", "phases": [{"kind": "vo2", "seconds": 60, "pct": 1.2}]}).json()
        assert copy["id"] != "vo2-5x3" and not copy["builtin"]
        assert c.delete("/api/workouts/vo2-5x3").status_code == 400
        assert c.delete(f"/api/workouts/{copy['id']}").status_code == 200
        assert c.post("/api/ride/start", json={"workout_id": "missing"}).status_code == 404
        assert c.post("/api/ride/start", json={"mode": "slope"}).status_code == 409


# ---------- sensor comparison ----------

def wait_for(client, seconds):
    import time

    deadline = time.monotonic() + 10
    while client.get("/api/state").json()["workout"]["elapsed"] < seconds:
        assert time.monotonic() < deadline, f"sim ride did not reach {seconds} s"
        time.sleep(0.1)


def test_comparison_runs_with_the_ride(client):
    wait_for(client, 45)  # past the 30 s two sources need to overlap
    snap = client.get("/api/compare").json()
    assert snap["running"] and snap["ride_id"] == client.get("/api/state").json()["ride"]["id"]
    power = snap["power"]
    assert [x["id"] for x in power["sources"]] == ["sim-trainer", "sim-pm"]  # main power is A
    assert power["sources"][0]["mean"] > 0 and power["diffs"][0]["mean_diff_pct"] is not None
    assert snap["hr"]["sources"][1]["id"] == "sim-arm"
    assert snap["hr"]["diffs"][0]["lag_s"] is not None and snap["hr"]["diffs"][0]["correlation"] > 0.8

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"cmd": "watch_compare", "value": True})
        for _ in range(40):
            msg = ws.receive_json()
            if msg["type"] == "compare":
                break
        assert msg["type"] == "compare" and msg["power"]["series"]["t"]

    swapped = client.post("/api/compare/reference", json={"kind": "power", "id": "sim-pm"}).json()
    assert swapped["power"]["sources"][0]["id"] == "sim-pm"
    assert client.post("/api/compare/reference", json={"kind": "power", "id": "nope"}).status_code == 400
    r = client.get("/api/compare/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("second,power A SIM Power meter (W),power B SIM TRAINER (W)")

    # the workout ends: the comparison stops with it and stays available from the recording
    ride_id = client.post("/api/ride/stop").json()["id"]
    assert client.get("/api/compare").json()["running"] is False
    done = client.get(f"/api/rides/{ride_id}/compare").json()
    assert not done["running"] and done["ride_id"] == ride_id
    assert [x["id"] for x in done["power"]["sources"]] == ["sim-trainer", "sim-pm"]
    assert len(done["power"]["series"]["t"]) >= 45
    flipped = client.get(f"/api/rides/{ride_id}/compare", params={"power": "sim-pm"}).json()
    assert flipped["power"]["sources"][0]["id"] == "sim-pm"
    csv_r = client.get(f"/api/rides/{ride_id}/compare.csv")
    assert f'wattdisco-compare-{ride_id}.csv' in csv_r.headers["content-disposition"]
    assert csv_r.text.splitlines()[0].startswith("second,power A SIM TRAINER (W),power B SIM Power meter (W)")
    assert client.get("/api/rides/20000101-000000/compare").status_code == 404


def test_pause_stops_the_comparison_clock_samples(client):
    wait_for(client, 5)
    client.post("/api/ride/pause")
    assert client.get("/api/compare").json()["paused"] is True
    client.post("/api/ride/resume")
    assert client.get("/api/compare").json()["paused"] is False


def test_nerd_numbers_in_state(client):
    import time

    time.sleep(2.0)  # ~40 ride seconds at speed 20
    nerd = client.get("/api/state").json()["nerd"]
    assert set(nerd["avg"]) == set(nerd["hr_avg"]) == {"30", "60", "300", "600"}
    assert nerd["avg"]["30"] > 0 and nerd["hr_avg"]["30"] > 0
    assert 0 <= nerd["on_target_interval_pct"] <= 100
    assert nerd["source_diff"]["a_id"] == "sim-trainer" and nerd["source_diff"]["b_id"] == "sim-pm"
    client.post("/api/ride/stop")
    assert client.get("/api/state").json()["nerd"] is None


def test_pause_stops_everything_and_resume_continues(client):
    import time

    time.sleep(0.6)
    r = client.post("/api/ride/pause").json()
    assert r["paused"] is True
    before = client.get("/api/state").json()
    time.sleep(0.6)  # ~12 ride seconds paused
    during = client.get("/api/state").json()
    assert during["workout"]["elapsed"] == before["workout"]["elapsed"]  # workout clock frozen
    assert during["workout"]["paused"] is True
    assert during["trainer"]["mode"] == "free"  # trainer let go
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
    paused_history = len(hello["history"])
    time.sleep(0.3)
    with client.websocket_connect("/ws") as ws:
        assert len(ws.receive_json()["history"]) == paused_history  # nothing recorded while paused

    assert client.post("/api/ride/resume").json()["paused"] is False
    time.sleep(0.5)
    after = client.get("/api/state").json()
    assert after["workout"]["elapsed"] > during["workout"]["elapsed"]
    assert after["trainer"]["mode"] == "erg"
    ride_id = client.post("/api/ride/stop").json()["id"]
    summary = client.get(f"/api/rides/{ride_id}/summary").json()
    assert summary["paused"] >= 8
    assert client.post("/api/ride/pause").status_code == 409  # no ride


def test_no_second_is_recorded_twice_when_sleeps_wake_early(tmp_path):
    import asyncio
    import time

    from wattdisco.core.recorder import load_ride

    class EarlyClock(Clock):  # like Windows, where timers can fire up to 16 ms early
        async def sleep(self, seconds):
            await asyncio.sleep(max(0.001, seconds / self.speed - 0.016))

    ride = Ride(SimSource(WORKOUT), WORKOUT, EarlyClock(speed=20), data_dir=tmp_path)
    with TestClient(create_app(ride)) as c:
        time.sleep(0.8)
        ride_id = c.post("/api/ride/stop").json()["id"]
    seconds = [tick["t"] for tick in load_ride(ride.store.path(ride_id, ".jsonl")).ticks]
    assert len(seconds) > 5 and len(seconds) == len(set(seconds))


def test_running_ride_is_not_listed(client):
    import time

    time.sleep(0.3)
    running = client.get("/api/state").json()["ride"]["id"]
    assert running and running not in [r["id"] for r in client.get("/api/rides?include_sim=true").json()]
    finished = client.post("/api/ride/stop").json()["id"]
    assert [r["id"] for r in client.get("/api/rides?include_sim=true").json()] == [finished]


def test_hr_zone_settings_api_and_summary(client):
    s = client.put("/api/settings", json={"ftp": 250, "weight_kg": 75, "hr_method": "lthr", "lthr": 168}).json()
    assert s["hr_method"] == "lthr" and s["hr_zones"][4]["from_bpm"] == round(0.99 * 168) + 1
    assert client.get("/api/hr-zones", params={"method": "max", "reference": 200}).json()[0]["to_bpm"] == 120
    assert client.get("/api/hr-zones", params={"method": "max", "reference": 50}).status_code == 400
    assert client.put("/api/settings", json={"ftp": 250, "weight_kg": 75, "max_hr": 999}).status_code == 400
    assert client.get("/api/state").json()["hr_zone"] in (None, 1, 2, 3, 4, 5)
    client.post("/api/ride/stop")
    client.post("/api/ride/start")
    import time
    time.sleep(1.0)
    ride_id = client.post("/api/ride/stop").json()["id"]
    summary = client.get(f"/api/rides/{ride_id}/summary").json()
    assert summary["hr_zones"]["method"] == "lthr" and summary["hr_zones"]["reference"] == 168
    assert len(summary["hr_zones"]["zones"]) == 5
    assert summary["deviation"]["seconds"] > 0


@pytest.mark.parametrize(("summary", "ftp"), [
    ({"sim": False, "ftp_estimate": {"source": "ramp", "ftp": 262}}, 262),  # a test sets the FTP
    ({"sim": False, "ftp_estimate": {"source": "20min", "ftp": 231}}, 231),  # also when lower
    ({"sim": True, "ftp_estimate": {"source": "ramp", "ftp": 262}}, 250),  # never from a simulated ride
    ({"sim": False, "ftp_estimate": {"source": "best20", "ftp": 262}}, 250),  # any other ride only suggests
    ({"sim": False, "ftp_estimate": None}, 250),
])
def test_ftp_test_sets_ftp_at_the_end(tmp_path, summary, ftp):
    import asyncio

    from wattdisco.config import Settings

    ride = Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), settings=Settings(ftp=250), data_dir=tmp_path)
    ride.store.summary = lambda ride_id: summary
    asyncio.run(ride._apply_test("r1"))
    assert ride.settings.ftp == ftp
    if ftp != 250:
        assert load_settings(tmp_path).ftp == ftp  # saved


def test_import_workout_file(client):
    zwo = ('<workout_file><name>Imported 3 x 5</name><workout>'
           '<Warmup Duration="600" PowerLow="0.4" PowerHigh="0.75"/>'
           '<IntervalsT Repeat="3" OnDuration="300" OffDuration="180" OnPower="1.05" OffPower="0.5"/>'
           '<Cooldown Duration="300" PowerLow="0.6" PowerHigh="0.4"/></workout></workout_file>')
    r = client.post("/api/workouts/import", json={"filename": "three.zwo", "text": zwo})
    assert r.status_code == 200, r.text
    imported = r.json()["workout"]
    assert imported["name"] == "Imported 3 x 5" and not imported["builtin"] and imported["category"] == "custom"
    assert imported["seconds"] == 600 + 3 * 480 + 300
    assert imported["id"] in [w["id"] for w in client.get("/api/workouts").json()["workouts"]]
    assert client.get(f"/api/workouts/{imported['id']}").json()["phases"] == imported["phases"]

    bad = client.post("/api/workouts/import", json={"filename": "plan.pdf", "text": "x"})
    assert bad.status_code == 400 and "reads .zwo" in bad.json()["detail"]


def test_training_plan_api_and_ride_ticks_off(tmp_path):
    c, ride = idle_client(tmp_path)
    with c:
        assert c.get("/api/plans").json()["active"] is None
        assert c.get("/api/plan/next").json() is None
        assert c.post("/api/plan/skip").status_code == 409
        assert c.post("/api/plan", json={"plan": "nope"}).status_code == 404

        r = c.post("/api/plan", json={"plan": "short-4"}).json()
        weeks = [len(p["weeks"]) for p in r["plans"]]
        assert weeks == sorted(weeks) and len(r["plans"]) == len(plans.PLANS)  # shortest first
        assert [g["id"] for g in r["groups"]] == list(plans.GROUPS) and all(p["group"] in plans.GROUPS for p in r["plans"])
        assert r["active"]["next"]["workout_id"] == "ftp-ramp-test"
        assert c.get("/api/workouts").json()["selected"] == "ftp-ramp-test"  # the next session is lined up
        assert (tmp_path / "plan.json").exists()

        r = c.post("/api/plan/skip").json()
        assert r["active"]["next"]["workout_id"] == "endurance-45"
        assert c.get("/api/workouts").json()["selected"] == "endurance-45"

        # a real (not simulated) ride of the whole session ticks it off
        ride_id = c.post("/api/ride/start", json={"workout_id": "endurance-45"}).json()["id"]
        real = ride.store.summary

        def summary(rid):  # the real summary, made to look like a real ride of the whole session
            s = real(rid)  # complete, so the .fit built when the ride stops (once a tick is recorded) works
            if rid == ride_id:
                s = {**s, "sim": False, "duration": 2700, "workout": {**s["workout"], "seconds": 2700},
                     "nerd": {**s["nerd"], "on_target_pct": 97.0}, "ftp_estimate": None}
            return s

        ride.store.summary = summary
        assert c.post("/api/ride/stop").json()["id"] == ride_id
        r = c.get("/api/plans").json()["active"]
        assert r["next"]["workout_id"] == "sweet-spot-4x8" and r["rides"] == [ride_id]
        assert c.get(f"/api/rides/{ride_id}/plan").json()["counted"] == {"week": 1, "weeks": 4, "session": 2,
                                                                       "sessions": 3}
        # the recorded ride knows its workout
        assert real(ride_id)["workout_id"] == "endurance-45"
        counted = ride_id

        # a simulated ride of the next session does not count, and says why
        ride_id = c.post("/api/ride/start").json()["id"]
        ride.store.summary = real
        c.post("/api/ride/stop")
        assert c.get(f"/api/rides/{ride_id}/plan").json()["reason"] == "sim"
        assert c.get("/api/plan/next").json()["workout_id"] == "sweet-spot-4x8"

        # only the latest simulated ride is kept: the earlier one is gone (its session stays done)
        assert c.get(f"/api/rides/{counted}/summary").status_code == 404
        assert c.get(f"/api/rides/{ride_id}/summary").status_code == 200
        # deleting a counted ride keeps its session done
        ride.plan["log"][-1]["ride"] = ride_id  # pretend the kept ride was the counted one
        assert c.delete(f"/api/rides/{ride_id}").status_code == 200
        r = c.get("/api/plans").json()["active"]
        assert r["rides"] == [] and r["weeks"][0]["sessions"][1]["status"] == "done"

        assert c.post("/api/plan/undo").json()["active"]["next"]["workout_id"] == "endurance-45"
        assert c.delete("/api/plan").json()["active"] is None
        assert not (tmp_path / "plan.json").exists()


def test_plan_lines_up_its_next_session_at_start(tmp_path):
    from wattdisco.core.plans import PlanStore, new_state

    PlanStore(tmp_path).save(new_state("base-8", 250))
    ride = Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), data_dir=tmp_path)
    assert ride.workout_id == "ftp-ramp-test" and ride.workout.test == "ramp"


def test_ftp_changes_go_into_the_history_and_progress(tmp_path):
    c, ride = idle_client(tmp_path)
    with c:
        assert c.get("/api/ftp-history").json()[0]["source"] == "start"
        s = c.get("/api/settings").json()
        c.put("/api/settings", json={"ftp": s["ftp"] + 10, "weight_kg": s["weight_kg"]})
        c.put("/api/settings", json={"ftp": s["ftp"] + 20, "weight_kg": s["weight_kg"], "ftp_source": "best20",
                                     "ride_id": "20261101-120000"})  # no such ride: kept as the source, not the link
        h = c.get("/api/ftp-history").json()
        assert [(e["ftp"], e["source"]) for e in h[-2:]] == [(s["ftp"] + 10, "manual"), (s["ftp"] + 20, "best20")]
        assert h[-1]["ride"] is None
        p = c.get("/api/progress").json()
        assert set(p) >= {"fitness", "curve", "ftp", "sensors"} and set(p["curve"]) == {"all", "90", "28"}


def test_full_ride_csv(client):
    import csv
    import io
    import time

    time.sleep(1.0)
    ride_id = client.post("/api/ride/stop").json()["id"]
    r = client.get(f"/api/rides/{ride_id}/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert f"wattdisco-{ride_id}.csv" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    head = rows[0]
    assert head[:6] == ["time (s)", "workout time (s)", "phase", "target (W)", "mode", "power (W)"]
    assert any(h.endswith("power (W)") and h != "power (W)" for h in head)  # each sensor on its own
    assert any(h.endswith("heart rate (bpm)") and h != "heart rate (bpm)" for h in head)
    assert len(rows) > 2 and all(len(x) == len(head) for x in rows)
    assert "bests" in client.get(f"/api/rides/{ride_id}/summary").json()


def test_simulated_rides_are_kept_apart_and_only_the_latest(tmp_path):
    c, ride = idle_client(tmp_path)
    with c:
        first = c.post("/api/ride/start").json()["id"]
        c.post("/api/ride/stop")
        assert (tmp_path / "sim-ride" / f"{first}.jsonl").exists()
        assert not list((tmp_path / "rides").glob("*.jsonl")) if (tmp_path / "rides").exists() else True
        import time
        time.sleep(1.1)  # a new ride id
        second = c.post("/api/ride/start").json()["id"]
        c.post("/api/ride/stop")
        assert not (tmp_path / "sim-ride" / f"{first}.jsonl").exists()
        assert c.get("/api/rides").json() == []
        assert [r["id"] for r in c.get("/api/rides?include_sim=true").json()] == [second]
        assert c.get(f"/api/rides/{second}/summary").status_code == 200
    # the next start clears it
    Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), data_dir=tmp_path, autostart=False)
    assert not list((tmp_path / "sim-ride").glob("*"))


def test_plan_days_and_session_length(tmp_path):
    c, ride = idle_client(tmp_path)
    with c:
        preview = c.get("/api/plans?days=3&minutes=45").json()["plans"]
        assert all(len(w["sessions"]) == 3 for p in preview for w in p["weeks"])
        assert c.get("/api/plans?days=9").status_code == 400 and c.get("/api/plans?minutes=50").status_code == 400
        r = c.post("/api/plan", json={"plan": "short-4", "days": 3, "minutes": 60}).json()["active"]
        assert (r["days"], r["minutes"]) == (3, 60)
        c.post("/api/plan/skip")
        nxt = c.get("/api/plan/next").json()["workout_id"]
        assert nxt == "endurance-45--60m"
        assert ride.workout_id == nxt and ride.workout.seconds == 3600  # the fitted session is lined up
        w = c.get(f"/api/workouts/{nxt}").json()
        assert w["name"] == "Endurance 45 · 60 min" and w["seconds"] == 3600 and w["builtin"]
        assert nxt in [x["id"] for x in c.get("/api/workouts").json()["workouts"]]  # listed while it is picked
        r = c.put("/api/plan/options", json={"days": 4, "minutes": None}).json()["active"]
        assert (r["days"], r["minutes"]) == (4, None) and len(r["weeks"][0]["sessions"]) == 4
        assert c.get("/api/plan/next").json()["workout_id"] == "endurance-45"
        assert c.put("/api/plan/options", json={"days": 1}).status_code == 400


def test_battery_and_balance_reach_the_screens(client):
    import time

    time.sleep(1.5)
    state = client.get("/api/state").json()
    batteries = {d["id"]: d["battery"] for d in state["devices"]}
    assert batteries["sim-trainer"] is None and batteries["sim-arm"] == 14 and batteries["sim-pm"] == 64
    assert next(d for d in state["devices"] if d["id"] == "sim-pm")["balance"] is not None
    assert {d["id"]: d["battery"] for d in client.get("/api/devices").json()["devices"]}["sim-hrm"] == 88
    while client.get("/api/state").json()["workout"]["elapsed"] < 40:
        time.sleep(0.1)
    ride_id = client.post("/api/ride/stop").json()["id"]
    bal = client.get(f"/api/rides/{ride_id}/summary").json()["balance"]
    assert bal["device_id"] == "sim-pm" and 45 < bal["left"] < 51
    assert "SIM Power meter balance left (%)" in client.get(f"/api/rides/{ride_id}/export.csv").text.splitlines()[0]


def test_history_rows_carry_what_rides_are_compared_by(client):
    import time

    time.sleep(1.0)
    ride_id = client.post("/api/ride/stop").json()["id"]
    [row] = client.get("/api/rides?include_sim=true").json()
    assert row["id"] == ride_id
    for key in ("max_hr", "avg_cadence", "intensity", "vi", "on_target_pct", "hr_drift_pct", "workout_id", "aet_hr",
                "balance_left", "bests"):
        assert key in row, key
    assert row["workout_id"] == "sweet-spot-3x4"


def test_train_goes_back_to_the_finished_ride_until_it_is_dealt_with(tmp_path):
    c, ride = idle_client(tmp_path)
    with c:
        assert c.get("/train", follow_redirects=False).headers["location"] == "/ready.html"
        ride_id = c.post("/api/ride/start").json()["id"]
        assert c.get("/train", follow_redirects=False).headers["location"] == "/workout.html"
        c.post("/api/ride/stop")
        assert c.get("/train", follow_redirects=False).headers["location"] == f"/summary.html?ride={ride_id}"
        assert c.get("/api/state").json()["ride"]["review_id"] == ride_id
        import time
        time.sleep(1.1)  # a new ride id
        second = c.post("/api/ride/start").json()["id"]
        assert c.get("/api/state").json()["ride"]["review_id"] is None  # a new ride clears it
        c.post("/api/ride/stop")
        assert c.post("/api/ride/review/done").json() == {"review_id": None}
        assert c.get("/train", follow_redirects=False).headers["location"] == "/ready.html"
        time.sleep(1.1)
        c.post("/api/ride/start")
        c.post("/api/ride/stop")
        third = ride.review_id
        assert third and third != second
        c.delete(f"/api/rides/{third}")  # discarded: TRAIN is free again
        assert c.get("/train", follow_redirects=False).headers["location"] == "/ready.html"


def test_review_survives_a_restart(tmp_path):
    import json

    (tmp_path / "rides").mkdir()
    (tmp_path / "rides" / "20261101-120000.jsonl").write_text(json.dumps({"type": "meta", "id": "20261101-120000"}) + "\n")
    (tmp_path / "review.json").write_text(json.dumps({"ride": "20261101-120000"}))
    ride = Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), data_dir=tmp_path, autostart=False)
    assert ride.review_id == "20261101-120000"
    (tmp_path / "review.json").write_text(json.dumps({"ride": "20261101-130000"}))  # gone: nothing to review
    assert Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), data_dir=tmp_path, autostart=False).review_id is None


def test_recent_average_skips_gaps():
    from collections import deque

    from wattdisco.server import recent_average

    values = deque([100] * 50 + [None, 200, 300], maxlen=600)
    assert recent_average(values, 2) == 250
    assert recent_average(values, 3) == 250  # a dropout is a gap, not a zero
    assert recent_average(values, 600) == (100 * 50 + 500) / 52
    assert recent_average(deque([None]), 30) is None


def test_ride_holds_the_screen_while_riding(client):
    ride = client.app.state.ride
    assert ride.awake.held  # the test ride is running
    client.post("/api/ride/stop")
    assert not ride.awake.held


def _pedal(ride, watts, rpm):
    from dataclasses import replace as _replace

    real = ride.session.snapshot
    ride.session.snapshot = lambda: _replace(real(), power_raw=watts, cadence=rpm)
    return real


def test_auto_pause_when_pedalling_stops_and_resume_when_it_starts(client):
    ride = client.app.state.ride
    real = _pedal(ride, 0, 0)
    ride.active = True  # the step only runs inside the ride loop; drive it by hand
    for _ in range(4):
        ride._auto_pause_step()
    assert not ride.paused
    ride._auto_pause_step()
    assert ride.paused and ride.auto_paused == "stopped"
    assert client.get("/api/state").json()["ride"]["auto_paused"] == "stopped"
    ride.session.snapshot = real
    _pedal(ride, 150, 85)
    ride._auto_pause_step()
    assert ride.paused
    ride._auto_pause_step()
    assert not ride.paused and ride.auto_paused is None
    ride.session.snapshot = real


def test_a_pause_the_rider_chose_waits_for_the_rider(client):
    ride = client.app.state.ride
    client.post("/api/ride/pause")
    real = _pedal(ride, 150, 85)
    for _ in range(5):
        ride._auto_pause_step()
    assert ride.paused and ride.auto_paused is None
    ride.session.snapshot = real


def test_auto_pause_can_be_switched_off(client):
    ride = client.app.state.ride
    assert client.put("/api/settings", json={"auto_pause": False}).json()["auto_pause"] is False
    real = _pedal(ride, 0, 0)
    for _ in range(8):
        ride._auto_pause_step()
    assert not ride.paused
    ride.session.snapshot = real


def test_sound_can_be_muted_and_every_screen_is_told(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["settings"]["sound"] is True
        assert client.put("/api/settings", json={"sound": False}).json()["sound"] is False
        while (msg := ws.receive_json())["type"] != "hello":
            pass
        assert msg["settings"]["sound"] is False
    assert client.get("/api/settings").json()["sound"] is False  # saved with the app, not the browser


def test_start_when_the_rider_pedals(client):
    ride = client.app.state.ride
    client.post("/api/ride/stop")
    real = _pedal(ride, 0, 0)
    client.post("/api/ride/start", json={"wait_for_pedal": True})
    assert ride.paused and ride.auto_paused == "waiting"
    for _ in range(5):
        ride._auto_pause_step()
    assert ride.paused
    ride.session.snapshot = real
    _pedal(ride, 120, 80)
    ride._auto_pause_step()
    ride._auto_pause_step()
    assert not ride.paused
    ride.session.snapshot = real


def test_settings_take_partial_changes(client):
    before = client.get("/api/settings").json()
    after = client.put("/api/settings", json={"units": "imperial"}).json()
    assert after["units"] == "imperial" and after["ftp"] == before["ftp"]
    assert client.put("/api/settings", json={"units": "furlongs"}).status_code == 400


def _crashed_ride(rides_dir, seconds=90):
    """A ride file as a crash leaves it: meta, ticks, no end line."""
    import json as _json
    from dataclasses import asdict
    from datetime import datetime, timezone

    rides_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).replace(microsecond=0)
    ride_id = started.astimezone().strftime("%Y%m%d-%H%M%S")
    lines = [{"type": "meta", "id": ride_id, "started": started.isoformat(),
              "workout": {**asdict(WORKOUT), "seconds": WORKOUT.seconds}, "workout_id": None, "ftp": 240,
              "weight_kg": 75.0, "hr_zones": {"method": "max", "reference": 190}, "speed": 1, "erg_ease": False,
              "sim": False}]
    lines += [{"type": "tick", "t": t, "elapsed": t, "index": 0, "kind": "warmup", "target": 120, "mode": "erg",
               "power": 150, "hr": 120, "cadence": 88} for t in range(1, seconds + 1)]
    (rides_dir / f"{ride_id}.jsonl").write_text("\n".join(_json.dumps(x) for x in lines) + '\n{"type":"po')
    return ride_id


def test_a_ride_the_app_never_stopped_can_be_continued(tmp_path):
    ride_id = _crashed_ride(tmp_path / "rides")
    ride = Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), data_dir=tmp_path, autostart=False)
    with TestClient(create_app(ride)) as c:
        info = c.get("/api/state").json()["ride"]["interrupted"]
        assert info["id"] == ride_id and info["t"] == 90
        assert c.post("/api/ride/continue").json() == {"id": ride_id}
        state = c.get("/api/state").json()
        assert state["ride"]["active"] and state["ride"]["auto_paused"] == "waiting"
        assert state["ride"]["interrupted"] is None
        assert 89 <= state["workout"]["elapsed"] <= 91
        c.post("/api/ride/resume")
        import time

        time.sleep(0.5)
        c.post("/api/ride/stop")
        summary = c.get(f"/api/rides/{ride_id}/summary").json()
        assert summary["duration"] >= 90


def test_an_interrupted_ride_can_be_saved_as_it_is(tmp_path):
    ride_id = _crashed_ride(tmp_path / "rides")
    ride = Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), data_dir=tmp_path, autostart=False)
    with TestClient(create_app(ride)) as c:
        assert c.post("/api/ride/interrupted/save").json() == {"id": ride_id}
        state = c.get("/api/state").json()["ride"]
        assert state["interrupted"] is None and state["review_id"] == ride_id
        assert c.get(f"/api/rides/{ride_id}/summary").status_code == 200
        assert c.post("/api/ride/interrupted/save").status_code == 404


def test_starting_a_new_ride_keeps_the_interrupted_one(tmp_path):
    ride_id = _crashed_ride(tmp_path / "rides")
    ride = Ride(SimSource(WORKOUT), WORKOUT, Clock(speed=20), data_dir=tmp_path, autostart=False)
    with TestClient(create_app(ride)) as c:
        c.post("/api/ride/start", json={})
        assert ride.interrupted is None
        assert any(r["id"] == ride_id for r in c.get("/api/rides").json())


def test_home_band_without_rides_or_plan(client):
    home = client.get("/api/home").json()
    assert home["week"] == {"rides": 0, "seconds": 0, "load": 0}
    assert home["last"] is None and home["plan"] is None and home["form"] is None


def test_favourite_workouts_are_kept(client):
    assert client.put("/api/settings", json={"favourites": ["vo2-5x3", "endurance-90"]}).json()["favourites"] == ["vo2-5x3", "endurance-90"]
    assert client.get("/api/settings").json()["favourites"] == ["vo2-5x3", "endurance-90"]
    assert client.put("/api/settings", json={"favourites": ["x"] * 201}).status_code == 400


def test_notes_delete_undo_and_trash(client):
    import time

    time.sleep(0.3)
    ride_id = client.post("/api/ride/stop").json()["id"]
    assert client.get(f"/api/rides/{ride_id}/notes").json() == {}
    assert client.put(f"/api/rides/{ride_id}/notes", json={"rpe": 7, "note": " Legs heavy "}).json() == {"rpe": 7, "note": "Legs heavy"}
    assert client.put(f"/api/rides/{ride_id}/notes", json={"rpe": 11}).status_code == 400
    ride = client.app.state.ride
    row = ride.store._row(ride.store.summary(ride_id))
    assert row["rpe"] == 7 and row["note"] == "Legs heavy"
    client.delete(f"/api/rides/{ride_id}")
    assert client.get(f"/api/rides/{ride_id}/summary").status_code == 404
    assert (ride.store.trash / f"{ride_id}.jsonl").exists()
    assert client.post(f"/api/rides/{ride_id}/restore").json() == {"restored": ride_id}
    assert client.get(f"/api/rides/{ride_id}/notes").json()["rpe"] == 7
    assert client.post(f"/api/rides/{ride_id}/restore").status_code == 404
    client.delete(f"/api/rides/{ride_id}")
    ride.store.purge_deleted(days=-1)  # everything, whatever the file system's clock says
    assert not any(ride.store.trash.iterdir())


def test_strava_title_and_description_carry_the_ride():
    from wattdisco.server import default_description, default_title

    ride = {"name": "Sweet spot 3 × 4", "duration": 1680, "workout": {}, "climb": None, "ftp_estimate": None,
            "basic": {"avg_power": 201.4, "avg_hr": 142.2, "max_hr": 168, "avg_cadence": 88.4, "distance_km": 25.31},
            "nerd": {"wp": 215.2, "intensity": 0.874, "load": 58.3, "on_target_pct": 92.4},
            "bests": {"5": 612.0, "60": 380.2, "300": 290.0, "1200": 240.6}}
    assert default_title(ride) == "Watt Disco: Sweet spot 3 × 4"
    text = default_description(ride)
    assert text.splitlines() == [
        "28:00 · 25.3 km (estimated)",
        "Power: avg 201 W · weighted 215 W · intensity 0.87 · training load 58",
        "Heart rate avg 142, max 168 bpm · cadence 88 rpm",
        "Best: 5 s 612 W · 1 min 380 W · 5 min 290 W · 20 min 241 W",
        "On target: 92 % of the time within ±5 %",
        "Indoor ride with Watt Disco",
    ]
    assert "15.7 mi" in default_description(ride, "imperial")

    climb = {**ride, "name": "Alpine-style climb", "duration": 2400, "nerd": {"wp": 230.0}, "bests": {},
             "climb": {"length": 9000, "ascent": 615, "climbed": 615, "distance": 9000, "top": True, "seconds": 2290,
                       "vam": 967, "w_per_kg": 3.05, "difficulty": 0.75}}
    lines = default_description(climb).splitlines()
    assert lines[0] == "40:00 · 25.3 km (virtual)"
    assert lines[1] == "Climb: 9.0 km, 615 m up · to the top in 38:10 · VAM 967 m/h · 3.05 W/kg · trainer at 75 % of the gradient"
    assert not any(line.startswith("On target") for line in lines)  # a climb has no target
    test = {**ride, "workout": {"test": "ramp"}, "ftp_estimate": {"ftp": 256}}
    assert "FTP test: 256 W (ramp test)" in default_description(test)


def test_upload_screen_gets_the_watt_disco_title(client):
    import time

    time.sleep(0.4)
    ride_id = client.post("/api/ride/stop").json()["id"]
    d = client.get(f"/api/rides/{ride_id}/description").json()
    assert d["name"].startswith("Watt Disco: ") and d["description"].endswith("Indoor ride with Watt Disco")
