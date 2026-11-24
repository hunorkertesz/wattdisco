import json
import random

import pytest
from fastapi.testclient import TestClient

from wattdisco.core.climb import Profile
from wattdisco.core.climb_import import ClimbFileError, import_climb, segments_from_points
from wattdisco.core.clock import Clock
from wattdisco.core.workouts import default_workout, to_workout
from wattdisco.server import Ride, create_app
from wattdisco.sim.simulator import SimSource

M_PER_DEG_LAT = 6_371_000 * 3.141592653589793 / 180  # metres per degree north on the GPX earth


def gpx(parts, every=20.0, noise=1.5, name="Test climb", with_ele=True, seed=3):
    """A GPX track going north: `parts` is [(metres, gradient %)], heights with GPS-like noise."""
    rng = random.Random(seed)
    lat, height, rows = 46.0, 700.0, []
    rows.append((lat, height))
    for metres, grade in parts:
        for _ in range(int(metres / every)):
            lat += every / M_PER_DEG_LAT
            height += every * grade / 100
            rows.append((lat, height))
    pts = "".join(
        f'<trkpt lat="{la:.7f}" lon="7.0000000">' + (f"<ele>{h + rng.gauss(0, noise):.1f}</ele>" if with_ele else "") + "</trkpt>"
        for la, h in rows)
    return (f'<?xml version="1.0"?><gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
            f"<trk><name>{name}</name><trkseg>{pts}</trkseg></trk></gpx>")


def test_gpx_becomes_distance_and_smoothed_gradients():
    spec, notes = import_climb("ride.gpx", gpx([(2000, 8), (1000, 3)]))
    profile = Profile.from_segments(spec["climb"]["segments"])
    assert spec["name"] == "Test climb" and spec["category"] == "climb" and spec["mode"] == "climb"
    assert profile.length == pytest.approx(3000, abs=25)
    assert profile.total_ascent == pytest.approx(190, abs=6)
    assert profile.grade_at(1000) == pytest.approx(8, abs=0.8)
    assert profile.grade_at(2600) == pytest.approx(3, abs=0.8)
    assert max(g for _m, g in spec["climb"]["segments"]) < 10  # the noise is smoothed out, no fake spikes
    assert any("not the GPS positions" in n for n in notes)
    assert "lat" not in json.dumps(spec)  # where the road is never leaves the file


def test_gpx_limits_and_plain_errors():
    segments, notes = segments_from_points([(46.0, 7.0, 500.0), (46.0 + 600 / M_PER_DEG_LAT, 7.0, 680.0)])  # 30 %
    assert max(g for _m, g in segments) == 20.0 and any("steeper than 20 %" in n for n in notes)
    with pytest.raises(ClimbFileError, match="no heights"):
        import_climb("flat.gpx", gpx([(2000, 5)], with_ele=False))
    with pytest.raises(ClimbFileError, match="at least"):
        import_climb("short.gpx", gpx([(100, 5)]))
    with pytest.raises(ClimbFileError, match=".gpx"):
        import_climb("ride.fit", gpx([(2000, 5)]))
    with pytest.raises(ClimbFileError, match="XML features"):
        import_climb("bomb.gpx", '<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "aaaa">]><gpx>&a;</gpx>')
    with pytest.raises(ClimbFileError, match="valid GPX"):
        import_climb("broken.gpx", "<gpx><trk>")
    with pytest.raises(ClimbFileError, match="no track"):
        import_climb("empty.gpx", '<gpx xmlns="http://www.topografix.com/GPX/1/0"></gpx>')
    spec, _ = import_climb("route.gpx", gpx([(1500, 6)]).replace("trkpt", "rtept").replace("<trk>", "<rte>").replace("</trk>", "</rte>").replace("<trkseg>", "").replace("</trkseg>", ""))
    assert Profile.from_segments(spec["climb"]["segments"]).length == pytest.approx(1500, abs=25)  # a route works too


def test_import_ride_and_delete_an_own_climb(tmp_path):
    workout = default_workout()
    ride = Ride(SimSource(workout), workout, Clock(speed=40), data_dir=tmp_path, autostart=False)
    with TestClient(create_app(ride)) as c:
        r = c.post("/api/climbs/import", json={"filename": "evening.gpx", "text": gpx([(2000, 7)], name="Hill near home")})
        assert r.status_code == 200
        climb = r.json()["workout"]
        assert climb["name"] == "Hill near home" and not climb["builtin"] and climb["climb"] and climb["profile"]
        assert climb["seconds"] > 0 and "M UP" in climb["text"]
        saved = (tmp_path / "workouts" / f"{climb['id']}.json").read_text()
        assert "lat" not in saved and "lon" not in saved
        listed = [w for w in c.get("/api/workouts").json()["workouts"] if w["id"] == climb["id"]]
        assert listed and listed[0]["category"] == "climb"
        assert c.post("/api/climbs/import", json={"filename": "x.gpx", "text": "<gpx/>"}).status_code == 400

        start = c.post("/api/ride/start", json={"workout_id": climb["id"], "mode": "climb"})
        assert start.status_code == 200
        c.post("/api/ride/stop")
        assert c.delete(f"/api/workouts/{climb['id']}").status_code == 200
        assert not (tmp_path / "workouts" / f"{climb['id']}.json").exists()


def test_own_climbs_are_checked_like_workouts(tmp_path):
    from wattdisco.core.workouts import WorkoutStore

    store = WorkoutStore(tmp_path)
    good = store.save({"name": "Mine", "climb": {"segments": [[500, 6], [500, 9]]}})
    assert to_workout(store.get(good), 240).climb == {"segments": [[500.0, 6.0], [500.0, 9.0]]}
    for bad in ([], [[0, 5]], [[100, 45]], [["x", 5]], [[10_000, 5]] * 21):
        with pytest.raises(ValueError):
            store.save({"name": "Bad", "climb": {"segments": bad}})
