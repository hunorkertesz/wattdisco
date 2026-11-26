import copy
import json

import pytest

from wattdisco.core.workouts import BUILTIN, WorkoutStore, default_workout, describe, to_workout, validate


def test_default_workout_unchanged():
    w = default_workout(240)
    assert [(p.kind, p.seconds, p.watts, p.watts_end) for p in w.phases[:3]] == [
        ("warmup", 300, 96, 168), ("sweetspot", 240, 216, None), ("recovery", 120, 120, None)]
    assert w.seconds == 1680


def test_workouts_scale_with_ftp():
    spec = BUILTIN["threshold-2x15"]
    assert to_workout(spec, 200).phases[1].watts == 200
    assert to_workout(spec, 300).phases[1].watts == 300


def test_describe():
    d = describe(BUILTIN["sweet-spot-3x4"], 240)
    assert d["seconds"] == 1680
    assert d["intervals"] == 3
    assert d["text"] == "3 × 4 MIN @ 90 % FTP · 2 MIN RECOVERY"
    assert 0.6 < d["intensity"] < 0.9
    assert d["load"] == pytest.approx(d["seconds"] / 3600 * d["intensity"] ** 2 * 100, rel=0.01)


@pytest.mark.parametrize("bad, message", [
    ({"name": "", "phases": [{"kind": "endurance", "seconds": 60, "pct": 0.6}]}, "name"),
    ({"name": "x", "phases": []}, "phases"),
    ({"name": "x", "phases": [{"kind": "sprinting", "seconds": 60, "pct": 0.6}]}, "unknown type"),
    ({"name": "x", "phases": [{"kind": "vo2", "seconds": 5, "pct": 1.2}]}, "duration"),
    ({"name": "x", "phases": [{"kind": "vo2", "seconds": 60, "pct": 4}]}, "power"),
    ({"name": "x", "phases": [{"kind": "vo2", "seconds": 60, "pct": 1.1, "cadence": 300}]}, "cadence"),
    ({"name": "x", "phases": [{"kind": "vo2"}]}, "incomplete"),
])
def test_validation(bad, message):
    with pytest.raises(ValueError, match=message):
        validate(bad)


def test_store_save_list_update_delete(tmp_path):
    store = WorkoutStore(tmp_path)
    assert [w["id"] for w in store.list()][: len(BUILTIN)] == list(BUILTIN)
    spec = {"name": "My ramp", "phases": [{"kind": "warmup", "seconds": 600, "pct": 0.4, "pct_end": 0.8, "cadence": 90}]}
    wid = store.save(spec)
    assert wid.startswith("my-ramp-")
    assert store.get(wid)["builtin"] is False
    store.save({**spec, "name": "My ramp v2"}, wid)
    assert store.get(wid)["name"] == "My ramp v2"
    assert store.list()[-1]["id"] == wid
    store.delete(wid)
    with pytest.raises(KeyError):
        store.get(wid)


def test_saving_a_builtin_makes_a_copy(tmp_path):
    store = WorkoutStore(tmp_path)
    spec = {**BUILTIN["vo2-5x3"], "name": "VO2 max 5 × 3 (mine)"}
    wid = store.save(spec, "vo2-5x3")
    assert wid != "vo2-5x3" and store.get("vo2-5x3")["name"] == "VO2 max 5 × 3"
    with pytest.raises(ValueError):
        store.delete("vo2-5x3")


def test_bad_ids_and_broken_files(tmp_path):
    store = WorkoutStore(tmp_path)
    with pytest.raises(ValueError):
        store.get("../settings")
    (tmp_path / "broken.json").write_text("{nope")
    (tmp_path / "invalid.json").write_text(json.dumps({"name": "", "phases": []}))
    assert len(store.list()) == len(BUILTIN)


def test_builtin_library_by_type():
    from collections import Counter

    from wattdisco.core.workouts import CATEGORIES

    counts = Counter(w["category"] for w in BUILTIN.values())
    six = {"endurance", "sweetspot", "threshold", "vo2", "sprint"}
    expected = {"test": 2, "tempo": 4, "strength": 5, "climb": 4, "ride": 1}
    assert counts == {c: 6 if c in six else expected[c] for c in CATEGORIES if c != "custom"}
    for wid, spec in BUILTIN.items():  # strength stays seated above the 50 rpm where ERG lets go
        if spec["category"] == "strength":
            assert all(p["cadence"] >= 55 for p in spec["phases"] if p["kind"] not in ("warmup", "cooldown")), wid
    for wid, spec in BUILTIN.items():
        if spec.get("climb"):  # a climb is a road, not phases: never edited, timed by its estimate
            d = describe(spec, 250)
            assert 10 * 60 <= d["seconds"] <= 2 * 3600 and "M UP" in d["text"], wid
            continue
        if spec.get("open"):  # Just ride: no workout, never edited or validated as one
            continue
        validate(spec)  # every built-in passes the editor's own rules
        d = describe(spec, 250)
        assert 20 * 60 <= d["seconds"] <= 2 * 3600, wid
        assert d["intervals"] >= 1, wid


def test_category_of_own_workouts(tmp_path):
    store = WorkoutStore(tmp_path)
    base = {"name": "x", "phases": [{"kind": "tempo", "seconds": 600, "pct": 0.82}]}
    assert store.get(store.save(base))["category"] == "custom"
    assert store.get(store.save({**base, "category": "tempo"}))["category"] == "tempo"
    with pytest.raises(ValueError, match="category"):
        validate({**base, "category": "sprints"})


def test_sprints_are_ridden_free_and_described_in_seconds():
    spec = BUILTIN["sprint-6x15"]
    assert spec["mode"] == "free"
    assert {p["kind"] for p in spec["phases"]} >= {"sprint"}
    assert describe(spec, 250)["text"] == "6 × 15 S @ 200 % FTP · 4:45 MIN RECOVERY"


def test_describe_is_kept_but_follows_changes():
    from wattdisco.core.workouts import BUILTIN, describe

    spec = copy.deepcopy(next(s for s in BUILTIN.values() if not s.get("open") and not s.get("climb")))
    first = describe(spec, 240)
    first["load"] = -1  # a page changing its answer never changes the kept one
    assert describe(spec, 240)["load"] != -1
    assert describe(spec, 300)["avg_power"] > describe(spec, 240)["avg_power"]
    spec["phases"][0]["seconds"] += 60  # an edited workout is worked out again
    assert describe(spec, 240)["seconds"] == describe(copy.deepcopy(spec), 240)["seconds"] == first["seconds"] + 60
