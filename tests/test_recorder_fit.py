import json
from datetime import datetime, timezone

import pytest
from fit_tool.profile.profile_type import Event, EventType, FileType, Sport, SubSport
from fitread import read_fit

from wattdisco.core.recorder import Recorder, load_ride, new_ride_id, ride_path
from wattdisco.core.rides import RideStore
from wattdisco.core.summary import build_summary
from wattdisco.core.workouts import default_workout
from wattdisco.export.fit import build_fit
from wattdisco.models import Device, HrSample, MetricSample, PowerSample

VIRTUAL = SubSport.VIRTUAL_ACTIVITY.value
STARTED = datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc)


def record_ride(rides_dir, seconds=900):
    """A synthetic ride: trainer + power meter + strap + core sensor following the default workout."""
    workout = default_workout(240)
    ride_id = new_ride_id(STARTED)
    rec = Recorder(ride_path(rides_dir, ride_id), {
        "id": ride_id, "started": STARTED.isoformat(), "workout": {"name": workout.name, "ftp": 240,
        "phases": [vars(p) for p in workout.phases], "seconds": workout.seconds},
        "ftp": 240, "weight_kg": 75.0, "speed": 1.0, "sim": True,
    })
    rec.record("device", Device("kickr", "KICKR", {"power", "control"}, "connected"), 0)
    rec.record("device", Device("pm", "POWER METER", {"power"}, "connected"), 0)
    for t in range(1, seconds + 1):
        pos = workout.phase_at(t - 1)
        index, phase, into = pos
        target = phase.target_at(into)
        watts = target + (2 if t % 2 else -2)
        rec.record("power", PowerSample(t - 0.5, "kickr", watts, 90.0), t - 0.5)
        rec.record("power", PowerSample(t - 0.5, "pm", round(watts * 1.02), 90.0), t - 0.5)
        rec.record("hr", HrSample(t - 0.5, "hrm", 100 + t // 20), t - 0.5)
        rec.record("metric", MetricSample(t - 0.5, "core", "core_temp", 37.0 + t / 1000), t - 0.5)
        rec.write({"type": "tick", "t": t, "elapsed": t, "index": index, "kind": phase.kind, "target": target,
                   "mode": "erg", "main_power_id": "kickr", "main_hr_id": "hrm", "main_cadence_id": None,
                   "power": watts, "hr": 100 + t // 20, "cadence": 90})
    rec.write({"type": "end", "t": seconds})
    rec.close()
    return ride_id


def test_recorder_writes_every_line_and_loads(tmp_path):
    ride_id = record_ride(tmp_path, seconds=60)
    lines = ride_path(tmp_path, ride_id).read_text().splitlines()
    assert json.loads(lines[0])["type"] == "meta"
    ride = load_ride(ride_path(tmp_path, ride_id))
    assert len(ride.ticks) == 60
    assert set(ride.power) == {"kickr", "pm"}  # every sensor is recorded, not only the main one
    assert len(ride.power["pm"]) == 60
    assert ride.device_name("pm") == "POWER METER"
    assert ride.ended_t == 60


def test_load_survives_a_half_written_last_line(tmp_path):
    ride_id = record_ride(tmp_path, seconds=10)
    path = ride_path(tmp_path, ride_id)
    path.write_text(path.read_text() + '{"type": "tick", "t": 11, "pow')
    assert len(load_ride(path).ticks) == 10


def test_bad_ride_id_is_refused(tmp_path):
    with pytest.raises(ValueError):
        ride_path(tmp_path, "../../etc/passwd")


def test_summary_of_recorded_ride(tmp_path):
    ride = load_ride(ride_path(tmp_path, record_ride(tmp_path)))
    s = build_summary(ride)
    assert s["duration"] == 900
    assert s["nerd"]["on_target_pct"] == 100  # ±2 W around every target
    work = [i for i in s["intervals"] if i["work"]]
    assert [i["kind"] for i in work] == ["sweetspot", "sweetspot"]
    assert work[0]["target"] == 216
    assert s["sources"]["a_name"] == "KICKR" and s["sources"]["b_name"] == "POWER METER"
    assert s["sources"]["mean_diff_pct"] == pytest.approx(-2, abs=0.3)
    assert s["sources"]["lag_s"] == pytest.approx(0, abs=0.2)


def test_fit_round_trip(tmp_path):
    ride = load_ride(ride_path(tmp_path, record_ride(tmp_path)))
    summary = build_summary(ride)
    data = build_fit(ride, summary)

    messages = read_fit(data)

    assert messages["file_id"][0].type == FileType.ACTIVITY.value
    records = messages["record"]
    assert len(records) == 900
    assert records[0].power == ride.ticks[0]["power"]
    assert records[0].heart_rate == 100
    assert records[0].cadence == 90
    assert records[0].core_temperature == pytest.approx(37.0, abs=0.01)
    assert records[1].timestamp - records[0].timestamp == 1000  # ms
    # estimated flat-road speed and distance, so Strava has a distance stream to chart against
    assert records[-1].distance == pytest.approx(summary["basic"]["distance_km"] * 1000, abs=1)
    assert all(r.distance >= p.distance for p, r in zip(records, records[1:]))
    assert records[-1].speed > 5

    laps = messages["lap"]
    assert len(laps) == len(summary["intervals"])
    assert sum(lap.total_timer_time for lap in laps) == pytest.approx(900)

    session = messages["session"][0]
    assert (session.sport, session.sub_sport) == (Sport.CYCLING.value, VIRTUAL)  # Strava: Virtual Ride
    assert {lap.sub_sport for lap in laps} == {VIRTUAL}
    assert session.avg_power == round(summary["basic"]["avg_power"])
    # FIT profile field names stay as the format defines them
    assert session.normalized_power == round(summary["nerd"]["wp"])
    assert session.intensity_factor == pytest.approx(summary["nerd"]["intensity"], abs=0.001)
    assert session.training_stress_score == pytest.approx(summary["nerd"]["load"], abs=0.1)
    assert session.threshold_power == 240
    assert session.total_timer_time == 900
    assert session.total_distance == pytest.approx(summary["basic"]["distance_km"] * 1000, abs=1)
    assert session.avg_speed * 3.6 == pytest.approx(summary["basic"]["avg_speed_kmh"], abs=0.01)
    assert sum(lap.total_distance for lap in laps) == pytest.approx(session.total_distance, abs=1)
    assert messages["activity"][0].num_sessions == 1


def test_store_caches_and_deletes(tmp_path):
    store = RideStore(tmp_path)
    ride_id = record_ride(tmp_path, seconds=120)
    store.finish(ride_id)
    assert ride_path(tmp_path, ride_id, ".summary.json").exists()
    assert store.fit_path(ride_id).stat().st_size > 1000
    assert store.list() == []  # a simulated ride is never in the history
    assert store.list(include_sim=True)[0]["id"] == ride_id
    store.delete(ride_id)
    assert not any(tmp_path.iterdir())


def test_old_summary_cache_is_rebuilt(tmp_path):
    store = RideStore(tmp_path)
    ride_id = record_ride(tmp_path, seconds=60)
    store.finish(ride_id)
    cache = ride_path(tmp_path, ride_id, ".summary.json")
    old = json.loads(cache.read_text())
    del old["version"]
    cache.write_text(json.dumps(old))
    assert store.summary(ride_id)["basic"]["distance_km"] > 0
    assert json.loads(cache.read_text())["version"] >= 2


def test_paused_ride_fit_has_timer_events_and_moving_time(tmp_path):
    from wattdisco.core.recorder import load_ride

    workout = default_workout(240)
    ride_id = new_ride_id(STARTED)
    rec = Recorder(ride_path(tmp_path, ride_id), {
        "id": ride_id, "started": STARTED.isoformat(),
        "workout": {"name": workout.name, "ftp": 240, "phases": [vars(p) for p in workout.phases], "seconds": workout.seconds},
        "ftp": 240, "weight_kg": 75.0, "speed": 1.0, "sim": True,
    })
    # ride 60 s, pause 120 s, ride 60 s more
    t_values = list(range(1, 61)) + list(range(181, 241))
    for i, t in enumerate(t_values):
        rec.write({"type": "tick", "t": t, "elapsed": i + 1, "index": 0, "kind": "warmup", "target": 100,
                   "power": 100, "hr": 110, "cadence": 85, "main_power_id": "kickr"})
        if t == 60:
            rec.write({"type": "pause", "t": 60.5})
            rec.write({"type": "resume", "t": 180.5})
    rec.close()

    ride = load_ride(ride_path(tmp_path, ride_id))
    assert ride.pauses == [(60.5, 180.5)]
    summary = build_summary(ride)
    assert (summary["duration"], summary["elapsed"], summary["paused"]) == (120, 240, 120)

    messages = read_fit(build_fit(ride, summary))
    timer = [EventType(e.event_type) for e in messages["event"] if e.event == Event.TIMER.value]
    assert timer == [EventType.START, EventType.STOP_ALL, EventType.START, EventType.STOP_ALL]
    session = messages["session"][0]
    assert session.total_timer_time == 120 and session.total_elapsed_time == 240
    assert messages["lap"][0].total_timer_time == 120


def test_plain_ride_file_is_indoor_cycling(tmp_path):
    store = RideStore(tmp_path)
    ride_id = "20261103-101500"
    rec = Recorder(ride_path(tmp_path, ride_id), {"id": ride_id, "started": "2026-11-03T10:15:00+00:00", "ftp": 240,
                                                  "workout": {"name": "x", "ftp": 240, "phases": []}})
    for t in range(1, 31):
        rec.write({"type": "tick", "t": t, "elapsed": t, "kind": "endurance", "target": 150, "power": 150, "hr": 120})
    rec.close()
    virtual, plain = store.fit_path(ride_id), store.fit_path(ride_id, "Ride")
    assert virtual != plain
    for path, sub_sport in ((virtual, SubSport.VIRTUAL_ACTIVITY), (plain, SubSport.INDOOR_CYCLING)):
        assert read_fit(path.read_bytes())["session"][0].sub_sport == sub_sport.value
    store.delete(ride_id)
    assert not list(tmp_path.iterdir())


def test_balance_summary_from_a_dual_sided_meter():
    from wattdisco.core.recorder import RideData
    from wattdisco.core.summary import _balance

    # left 50 % for the first half, 48 % for the second; power 200 W, then 300 W in an interval
    samples = [(t, 50.0 if t < 50 else 48.0, 200 if t < 70 else 300) for t in range(100)]
    ride = RideData(meta={"id": "r"}, ticks=[{"t": t + 1, "main_power_id": "pm"} for t in range(100)],
                    balance={"pm": samples}, devices={"pm": {"name": "Assioma"}})
    intervals = [{"index": 0, "kind": "warmup", "work": False, "start_t": 0, "end_t": 70},
                 {"index": 1, "kind": "threshold", "work": True, "start_t": 70, "end_t": 100}]
    b = _balance(ride, intervals)
    assert b["device"] == "Assioma" and b["first_half_left"] == 50.0 and b["second_half_left"] == 48.0
    assert b["drift"] == -2.0 and 48.0 < b["left"] < 50.0 and b["right"] == round(100 - b["left"], 1)
    assert b["intervals"] == [{"index": 1, "kind": "threshold", "left": 48.0, "right": 52.0}]
    assert _balance(RideData(meta={"id": "r"}), []) is None
