import pytest

from wattdisco.core import plans
from wattdisco.core.workouts import BUILTIN


def ride(workout_id, ride_id="r1", seconds=None, sim=False, on_target=95.0, ftp_source=None):
    planned = sum(p["seconds"] for p in BUILTIN[workout_id]["phases"])
    return {"id": ride_id, "workout_id": workout_id, "sim": sim, "duration": planned if seconds is None else seconds,
            "workout": {"seconds": planned}, "nerd": {"on_target_pct": on_target},
            "ftp_estimate": {"source": ftp_source, "ftp": 260} if ftp_source else None}


def state(plan_id="short-4"):
    return plans.new_state(plan_id, 250)


def session(s, week, n):
    return plans.PLANS[s["plan"]]["weeks"][week]["sessions"][n]


@pytest.mark.parametrize("plan_id", list(plans.PLANS))
def test_plans_use_builtins_and_follow_the_training_logic(plan_id):
    plan = plans.PLANS[plan_id]
    weeks = plan["weeks"]
    for week in weeks:
        assert all(w in BUILTIN for w in week["sessions"])
        key = [w for w in week["sessions"] if plans.is_key(w)]
        assert len(key) <= 3  # mostly easy
        for a, b in zip(week["sessions"], week["sessions"][1:]):  # never two hard days written back to back
            assert not (plans.is_key(a) and plans.is_key(b) and BUILTIN[b].get("test") is None), (a, b)
    # test, train, retest
    assert weeks[0]["sessions"][0] == "ftp-ramp-test" and weeks[-1]["sessions"][-1] == "ftp-ramp-test"
    assert weeks[-1]["light"]

    info = plans.plan_info(plan_id, 250)
    load = [w["load"] for w in info["weeks"]]  # tests left out
    for n in range(1, len(weeks) - 1):
        if weeks[n]["light"]:
            assert load[n] <= 0.6 * load[n - 1]  # a real lighter week
        elif not weeks[n - 1]["light"] and n > 1:
            assert load[n - 1] <= load[n] <= 1.15 * load[n - 1]  # progressive, never a big jump


def test_next_session_and_ticking_off():
    s = state()
    assert plans.current(s) == (0, 0) and plans.next_workout(s) == "ftp-ramp-test"
    assert plans.counts(s, ride("ftp-ramp-test", ftp_source="ramp")) is None
    assert plans.record_ride(s, ride("ftp-ramp-test", ftp_source="ramp"))
    assert plans.current(s) == (0, 1)
    v = plans.view(s, 250)
    assert [x["status"] for x in v["weeks"][0]["sessions"]] == ["done", "next", "todo"]
    assert v["weeks"][0]["sessions"][0]["ride"] == "r1" and v["done"] == 1 and v["total"] == 12
    assert v["next"]["workout_id"] == "endurance-45" and v["next"]["week"] == 1 and v["next"]["session"] == 2
    assert plans.ride_entry(s, "r1") == {"week": 1, "weeks": 4, "session": 1, "sessions": 3}


@pytest.mark.parametrize(("summary", "reason"), [
    (ride("sweet-spot-4x8"), "other"),  # a different workout is recorded, not counted
    (ride("ftp-ramp-test", sim=True, ftp_source="ramp"), "sim"),
    (ride("ftp-ramp-test"), "short"),  # a test without a result
])
def test_rides_that_do_not_count(summary, reason):
    s = state()
    assert plans.counts(s, summary) == reason
    assert not plans.record_ride(s, summary) and plans.current(s) == (0, 0)


def test_a_ride_needs_most_of_the_workout():
    s = state()
    plans.skip(s)
    planned = ride("endurance-45")["duration"]
    assert plans.counts(s, ride("endurance-45", seconds=int(planned * 0.7))) == "short"
    assert plans.counts(s, ride("endurance-45", seconds=int(planned * 0.85))) is None


def test_skip_elsewhere_undo_and_finish():
    s = state()
    plans.skip(s)
    plans.mark_done(s)
    assert plans.current(s) == (0, 2)
    statuses = [x["status"] for x in plans.view(s, 250)["weeks"][0]["sessions"]]
    assert statuses == ["skipped", "elsewhere", "next"]
    plans.undo(s)
    assert plans.current(s) == (0, 1)
    while plans.current(s) is not None:
        plans.mark_done(s)
    v = plans.view(s, 250)
    assert v["finished"] and v["next"] is None and v["done"] == 11  # 12 sessions, the first skipped
    with pytest.raises(ValueError):
        plans.skip(s)


def test_a_bad_week_suggests_repeating_it():
    s = state()
    plans.skip(s)
    plans.skip(s)
    assert plans.suggestion(s) is None  # still inside the week
    plans.mark_done(s)
    assert plans.suggestion(s) == {"week": 1, "reason": "2 sessions of week 1 were skipped"}
    plans.repeat_week(s)
    assert plans.current(s) == (0, 0) and s["rounds"] == {"0": 1}
    assert [x["status"] for x in plans.view(s, 250)["weeks"][0]["sessions"]] == ["next", "todo", "todo"]


def test_key_sessions_off_target_suggest_repeating():
    s = state()
    plans.mark_done(s)  # week 2 holds two key sessions
    plans.mark_done(s)
    plans.mark_done(s)
    for n in range(3):
        workout_id = session(s, 1, n)
        assert plans.record_ride(s, ride(workout_id, f"w2-{n}", on_target=80.0 if plans.is_key(workout_id) else 99.0))
    assert plans.suggestion(s)["reason"] == "the key sessions of week 2 were 80 % on target"


def test_no_suggestion_after_good_or_light_weeks():
    s = state()
    for _ in range(3):
        plans.mark_done(s)
    assert plans.suggestion(s) is None
    s = state()
    while plans.current(s) != (3, 2):
        plans.mark_done(s)
    plans.skip(s)
    assert plans.suggestion(s) is None  # finished, and the last week is light


def test_store(tmp_path):
    store = plans.PlanStore(tmp_path)
    assert store.load() is None
    s = state()
    store.save(s)
    assert store.load() == s
    (tmp_path / "plan.json").write_text('{"plan": "gone"}')
    assert store.load() is None
    store.clear()
    assert not (tmp_path / "plan.json").exists()


def test_sessions_carry_their_breakdown():
    first = plans.plan_info("short-4", 250)["weeks"][0]["sessions"][2]
    assert first["workout_id"] == "sweet-spot-4x8" and first["text"].startswith("4 × 8 MIN")
    assert first["phases"][0]["kind"] == "warmup" and set(first["phases"][0]) == {"kind", "seconds", "pct", "pct_end"}


def test_a_deleted_ride_leaves_its_session_done():
    s = state()
    plans.record_ride(s, ride("ftp-ramp-test", "r1", ftp_source="ramp"))
    assert plans.forget_ride(s, "r1") and not plans.forget_ride(s, "r1")
    first = plans.view(s, 250)["weeks"][0]["sessions"][0]
    assert first["status"] == "done" and first.get("ride") is None and plans.ride_entry(s, "r1") is None


@pytest.mark.parametrize("plan_id", list(plans.PLANS))
def test_tailored_plans_keep_the_training_logic(plan_id):
    light = [w["light"] for w in plans.PLANS[plan_id]["weeks"]]
    for days in (None, 2, 3, 5, 7):
        for minutes in (None, 45, 75, 120):
            weeks = plans.plan_info(plan_id, 250, days, minutes)["weeks"]
            assert weeks[0]["sessions"][0]["test"] and weeks[-1]["sessions"][-1]["test"]  # test, train, retest
            if days:
                assert all(len(w["sessions"]) == days for w in weeks)
            load = [w["load"] for w in weeks]
            for n in range(1, len(weeks) - 1):
                if light[n]:
                    assert load[n] <= 0.75 * load[n - 1], (days, minutes, load)  # a lighter week stays lighter


def test_tailor_week_days_and_length():
    week = plans.PLANS["ftp-builder-6"]["weeks"][1]  # sweet spot 4x8, endurance, sweet spot 3x12, endurance 90
    assert plans.tailor_week(week, days=2) == ["sweet-spot-4x8", "sweet-spot-3x12"]  # easy rides go first
    assert plans.tailor_week(week, days=1 + 4)[-1] in plans.FILLERS
    fitted = plans.tailor_week(week, minutes=45)
    assert fitted[0] == "sweet-spot-4x8--45m" and all(w.endswith("--45m") for w in fitted)
    test_week = plans.PLANS["ftp-builder-6"]["weeks"][5]
    assert plans.tailor_week(test_week, days=5, minutes=90)[-1] == "ftp-ramp-test"  # the test stays last, as written
    with pytest.raises(ValueError):
        plans.check_options(1, None)
    with pytest.raises(ValueError):
        plans.check_options(None, 50)


def test_options_apply_from_the_current_week_and_a_fitted_ride_counts():
    s = plans.new_state("short-4", 250, days=3, minutes=60)
    plans.mark_done(s)
    nxt = plans.next_workout(s)
    assert nxt == "endurance-45--60m"
    assert plans.counts(s, ride("endurance-45", seconds=3600) | {"workout_id": nxt, "workout": {"seconds": 3600}}) is None
    assert plans.counts(s, ride("endurance-45")) is None  # the workout as written counts too
    plans.mark_done(s)
    plans.set_options(s, days=2, minutes=None)  # week 1 now has 2 sessions, 2 are done: on to week 2
    assert plans.current(s) == (1, 0) and plans.next_workout(s) == "sweet-spot-4x8"
    v = plans.view(s, 250)
    assert v["days"] == 2 and v["minutes"] is None and len(v["weeks"][0]["sessions"]) == 2  # from the current week on
    plans.set_options(s, days=4, minutes=45)  # a later change leaves the weeks before it alone
    assert len(plans.view(s, 250)["weeks"][0]["sessions"]) == 2 and len(plans.view(s, 250)["weeks"][1]["sessions"]) == 4


def test_every_plan_has_a_difficulty():
    for plan_id, plan in plans.PLANS.items():
        info = plans.plan_info(plan_id, 250)
        assert info["level"] in plans.LEVELS and info["level_name"] == plans.LEVELS[info["level"]]
        assert info["level_zone"] in (2, 3, 4, 5) and plan["level_why"]
    assert plans.PLANS["base-8"]["level"] < plans.PLANS["vo2-5"]["level"]  # easiest and hardest
