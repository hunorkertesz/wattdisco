import pytest

from wattdisco.core.workout import WorkoutPlayer
from wattdisco.models import Phase, TrainerCommand, Workout

WORKOUT = Workout(
    "test",
    250,
    [
        Phase("warmup", 60, 100, watts_end=160, cadence=85),
        Phase("sweetspot", 120, 225, cadence=90),
        Phase("recovery", 60, 125),
        Phase("threshold", 60, 250),
        Phase("cooldown", 60, 120, watts_end=100),
    ],
)


def test_position_in_ramp():
    p = WorkoutPlayer(WORKOUT).position(30)
    assert (p.kind, p.target, p.cadence) == ("warmup", 130, 85)
    assert p.seconds_left == 30
    assert p.next.kind == "sweetspot"
    assert p.interval == (0, 2)


def test_phase_transition():
    player = WorkoutPlayer(WORKOUT)
    assert player.position(59.9).kind == "warmup"
    p = player.position(60)
    assert (p.kind, p.target, p.seconds_left) == ("sweetspot", 225, 120)
    assert p.interval == (1, 2)
    assert player.position(240).interval == (2, 2)


def test_done():
    p = WorkoutPlayer(WORKOUT).position(360)
    assert p.done and p.target is None and p.next is None
    assert p.to_dict()["interval"] is None


def test_last_phase_has_no_next():
    assert WorkoutPlayer(WORKOUT).position(330).next is None


def test_skip_phase_moves_workout_time_only():
    player = WorkoutPlayer(WORKOUT)
    player.skip_phase(now=20)
    p = player.position(20)
    assert p.kind == "sweetspot" and p.elapsed == 60
    assert player.position(21).elapsed == 61
    player.skip_phase(now=21)
    assert player.position(21).kind == "recovery"


def test_skip_after_done_does_nothing():
    player = WorkoutPlayer(WORKOUT)
    player.skip_phase(400)
    assert player.position(400).elapsed == 400


def test_erg_sends_first_target_then_only_changes():
    player = WorkoutPlayer(WORKOUT)
    assert player.command(60) == TrainerCommand("erg", 225)
    assert player.command(61) is None
    assert player.command(100) is None


def test_erg_ramp_updates_at_most_once_a_second():
    player = WorkoutPlayer(WORKOUT)
    assert player.command(0) == TrainerCommand("erg", 100)
    assert player.command(0.5) is None  # 100.5 rounds to 100
    assert player.command(0.9) is None  # 101 W but < 1 s since last send
    assert player.command(1.0) == TrainerCommand("erg", 101)


def test_erg_sends_a_big_step_at_once():
    player = WorkoutPlayer(WORKOUT)
    assert player.command(59.5) == TrainerCommand("erg", 160)
    assert player.command(60.0) == TrainerCommand("erg", 225)  # the new phase's target, no ramp
    assert player.erg_state == "holding"
    assert player.command(60.5) is None


def test_erg_eases_into_a_big_step_over_three_seconds_when_asked():
    player = WorkoutPlayer(WORKOUT, ease=True)
    assert player.command(59.5) == TrainerCommand("erg", 160)
    assert player.command(60.0) is None  # easing starts from what was sent
    assert player.command(60.5) == TrainerCommand("erg", 171)
    assert player.command(60.75) is None  # at most one step every 0.5 s
    assert player.erg_state == "easing"
    sent = [player.command(t / 4) for t in range(244, 253)]
    assert [c.watts for c in sent if c] == [182, 192, 203, 214, 225]
    assert player.erg_state == "holding"
    assert player.command(64) is None


def test_erg_lets_go_at_low_cadence_and_eases_back_in():
    player = WorkoutPlayer(WORKOUT)
    assert player.command(90, cadence=90, power=225) == TrainerCommand("erg", 225)
    assert player.command(91, cadence=40, power=150) is None  # one slow second is fine
    assert player.command(93, cadence=35, power=120) == TrainerCommand("free")
    assert player.erg_state == "released" and player.released
    assert player.command(94, cadence=65, power=110) is None  # back up, but not for long enough
    assert player.command(95, cadence=70, power=120) is None
    assert player.command(96, cadence=75, power=130) == TrainerCommand("erg", 130)  # eased in from the rider's power
    assert player.erg_state == "easing"
    assert player.command(99.0, cadence=85, power=200) == TrainerCommand("erg", 225)
    assert player.erg_state == "holding"


def test_erg_never_lets_go_without_cadence_data():
    player = WorkoutPlayer(WORKOUT)
    player.command(90)
    for t in range(91, 100):
        assert player.command(t, cadence=None) is None
    assert player.erg_state == "holding"


def test_low_cadence_in_free_ride_changes_nothing():
    player = WorkoutPlayer(WORKOUT, mode="free")
    assert player.command(90, cadence=0) == TrainerCommand("free")
    assert player.command(95, cadence=0) is None
    assert player.erg_state == "off"


def test_ramp_test_ends_when_the_rider_cannot_hold_the_step():
    ramp = Workout("ramp", 250, [Phase("warmup", 60, 100), *[Phase("test", 60, 125 + 15 * i) for i in range(10)],
                                 Phase("cooldown", 120, 110)], test="ramp")
    player = WorkoutPlayer(ramp)
    player.command(200, cadence=90)
    assert player.position(200).kind == "test"
    player.command(201, cadence=40)
    player.command(204, cadence=30)  # released, ramp not over yet
    assert player.position(204).kind == "test" and not player.test_ended
    player.command(205.5, cadence=30)
    assert player.test_ended
    assert player.position(205.5).kind == "cooldown"


def test_free_ride_sends_free_once_and_back_to_erg():
    player = WorkoutPlayer(WORKOUT, mode="free")
    assert player.command(10) == TrainerCommand("free")
    assert player.command(11) is None
    player.set_mode("erg")
    assert player.command(12) == TrainerCommand("erg", 112)


def test_erg_holds_last_target_after_the_end():
    player = WorkoutPlayer(WORKOUT)
    assert player.command(500) == TrainerCommand("erg", 100)
    assert player.command(501) is None


def test_bad_mode():
    with pytest.raises(ValueError):
        WorkoutPlayer(WORKOUT, mode="slope")


def test_set_workout_keeps_position():
    player = WorkoutPlayer(WORKOUT)
    harder = Workout("test", 300, [Phase(p.kind, p.seconds, p.watts + 50, p.watts_end and p.watts_end + 50) for p in WORKOUT.phases])
    player.command(90)
    player.set_workout(harder)
    assert player.position(90).target == 275
    assert player.command(90.1) == TrainerCommand("erg", 275)  # 50 W more: sent at once


def test_pause_freezes_workout_time_and_releases_the_trainer():
    player = WorkoutPlayer(WORKOUT)
    assert player.command(90) == TrainerCommand("erg", 225)
    player.pause(100)
    assert player.paused and player.position(160).elapsed == 100 and player.position(160).paused
    assert player.command(101) == TrainerCommand("free")  # the rider can spin down
    assert player.command(102) is None
    player.resume(160)  # one minute off the bike
    assert not player.paused
    assert player.position(160).elapsed == 100
    assert player.position(170).elapsed == 110
    assert player.command(160) == TrainerCommand("erg", 225)  # ERG takes over again


def test_pause_twice_and_resume_without_pause_are_harmless():
    player = WorkoutPlayer(WORKOUT)
    player.pause(10)
    player.pause(20)
    player.resume(30)
    player.resume(40)
    assert player.position(40).elapsed == 20


def test_adjust_changes_only_the_current_phase():
    player = WorkoutPlayer(WORKOUT)
    assert player.adjust_phase(70, +2) == 10
    p = player.position(70)
    assert p.target == 235 and p.adjust == 10
    assert player.position(200).target == 125 and player.position(200).adjust == 0  # recovery as planned
    assert player.adjust_phase(70, -2) == 0 and player.adjustments == {}
    player.adjust_phase(10, +1)  # ramp: both ends move
    assert player.position(0).target == 105 and player.position(59.9).target == 165


def test_adjust_never_drops_below_the_floor():
    player = WorkoutPlayer(WORKOUT)
    player.adjust_phase(200, -100)
    assert player.position(200).target == 20
    assert player.adjust_phase(400, +1) == 0  # done: nothing to adjust


def test_erg_follows_an_adjusted_target():
    player = WorkoutPlayer(WORKOUT)
    assert player.command(70).watts == 225
    player.adjust_phase(71, +1)
    assert player.command(72).watts == 230
