import pytest

from wattdisco.core.bus import Bus
from wattdisco.core.clock import ManualClock
from wattdisco.core.session import Session
from wattdisco.models import Device, HrSample, PowerSample


@pytest.fixture
def rig():
    bus, clock = Bus(), ManualClock()
    session = Session(bus, clock, smoothing="3s")
    for d in (
        Device("kickr", "KICKR", {"power", "cadence", "control"}, "connected"),
        Device("pm", "PM", {"power", "cadence"}, "connected"),
        Device("hrm", "HRM", {"heart_rate"}, "connected"),
    ):
        bus.publish("device", d)
    return bus, clock, session


def ride(bus, clock, seconds, kickr=200, pm=210, bpm=140):
    for _ in range(seconds):
        t = clock.now()
        bus.publish("power", PowerSample(t, "kickr", kickr, 90.0))
        bus.publish("power", PowerSample(t, "pm", pm, 91.0))
        bus.publish("hr", HrSample(t, "hrm", bpm))
        clock.advance(1)


def test_first_seen_becomes_main(rig):
    bus, clock, session = rig
    ride(bus, clock, 3)
    s = session.snapshot()
    assert s.main_power_id == "kickr"
    assert s.main_hr_id == "hrm"
    assert (s.power, s.cadence, s.hr) == (200, 90.0, 140)


def test_switch_main_power(rig):
    bus, clock, session = rig
    ride(bus, clock, 3)
    session.set_main_power("pm")
    assert session.snapshot().power == 210


def test_main_must_have_role(rig):
    _bus, _clock, session = rig
    with pytest.raises(ValueError):
        session.set_main_power("hrm")
    with pytest.raises(KeyError):
        session.set_main_hr("nope")


def test_smoothing_is_display_only(rig):
    bus, clock, session = rig
    ride(bus, clock, 3, kickr=200)
    ride(bus, clock, 1, kickr=500)
    s = session.snapshot()
    assert s.power_raw == 500
    assert s.power == 300  # (200 + 200 + 500) / 3
    session.set_smoothing("raw")
    assert session.snapshot().power == 500


def test_unknown_smoothing(rig):
    with pytest.raises(ValueError):
        rig[2].set_smoothing("5s")


def test_hr_dropout_after_5_seconds(rig):
    bus, clock, session = rig
    ride(bus, clock, 3)
    for _ in range(6):  # power keeps coming, HR stops
        bus.publish("power", PowerSample(clock.now(), "kickr", 200, 90.0))
        bus.publish("power", PowerSample(clock.now(), "pm", 210, 91.0))
        clock.advance(1)
    s = session.snapshot()
    assert s.hr is None
    assert s.power == 200
    assert s.alerts == ["no data from HRM"]
    assert [d.no_data for d in s.devices] == [False, False, True]


def test_silent_since_connect_counts_as_dropout(rig):
    _bus, clock, session = rig
    clock.advance(4)
    assert session.snapshot().alerts == []
    clock.advance(2)
    assert len(session.snapshot().alerts) == 3


def test_lost_device_alert(rig):
    bus, clock, session = rig
    ride(bus, clock, 2)
    bus.publish("device", Device("hrm", "HRM", {"heart_rate"}, "lost"))
    s = session.snapshot()
    assert s.hr is None
    assert s.alerts == ["HRM lost, reconnecting"]


def test_cadence_falls_back_to_other_source(rig):
    bus, clock, session = rig
    bus.publish("power", PowerSample(0, "kickr", 200, None))
    bus.publish("power", PowerSample(0, "pm", 210, 88.0))
    assert session.snapshot().cadence == 88.0


def test_main_cadence_choice(rig):
    bus, clock, session = rig
    ride(bus, clock, 2)  # kickr 90 rpm, pm 91 rpm
    assert session.snapshot().cadence == 90.0
    session.set_main_cadence("pm")
    assert session.snapshot().cadence == 91.0
    session.set_main_cadence(None)
    assert session.snapshot().cadence == 90.0
    with pytest.raises(ValueError):
        session.set_main_cadence("hrm")


def test_extra_metrics_latest_and_silent(rig):
    from wattdisco.models import MetricSample

    bus, clock, session = rig
    bus.publish("device", Device("core", "CORE", {"temperature"}, "connected"))
    bus.publish("metric", MetricSample(clock.now(), "core", "core_temp", 37.4))
    bus.publish("metric", MetricSample(clock.now(), "core", "unknown_thing", 1.0))
    m = session.snapshot().metrics
    assert [(x.key, x.value, x.unit) for x in m] == [("core_temp", 37.4, "°C")]
    clock.advance(6)
    m = session.snapshot().metrics
    assert m[0].value is None  # sensor silent for more than 5 s
    assert "no data from CORE" in session.snapshot().alerts


def test_power_difference_between_sources(rig):
    bus, clock, session = rig
    ride(bus, clock, 5, kickr=200, pm=210)
    d = session.power_difference()
    assert (d["a_id"], d["b_id"]) == ("kickr", "pm")
    assert d["diff"] == pytest.approx(-10) and d["diff_pct"] == pytest.approx(-100 * 10 / 210)
    session.set_main_power("pm")
    assert session.power_difference()["diff"] == pytest.approx(10)
