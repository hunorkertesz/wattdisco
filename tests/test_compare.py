import csv
import io

import pytest

from wattdisco.core.bus import Bus
from wattdisco.core.clock import ManualClock
from wattdisco.core.compare import CompareBench
from wattdisco.core.recorder import Recorder, load_ride, ride_path
from wattdisco.models import HrSample, PowerSample

NAMES = {"pm": "POWER METER", "kickr": "KICKR", "pedals": "PEDALS", "strap": "STRAP", "arm": "ARM"}


def bench_with_ride(seconds=200, third=False):
    """Power meter reads 2 % high, pedals 3 % low; armband is 2 s behind the strap."""
    bus, clock = Bus(), ManualClock()
    bench = CompareBench(bus, clock, name_of=NAMES.get)
    bench.start(clock.now(), "r1", {"power": "pm", "hr": "strap"})
    hr = [120 + (i // 10) % 7 * 4 for i in range(seconds + 5)]
    for i in range(seconds):
        t = clock.now()
        watts = 200 + (40 if (i // 15) % 2 else 0)
        bus.publish("power", PowerSample(t + 0.2, "kickr", watts, 90.0))
        bus.publish("power", PowerSample(t + 0.4, "pm", round(watts * 1.02), 90.0))
        if third:
            bus.publish("power", PowerSample(t + 0.5, "pedals", round(watts * 0.97), 90.0))
        bus.publish("hr", HrSample(t + 0.1, "strap", hr[i + 2]))
        bus.publish("hr", HrSample(t + 0.3, "arm", hr[i]))
        clock.advance(1)
    return bench, bus, clock


def test_mean_difference_lag_and_correlation():
    bench, _, _ = bench_with_ride()
    s = bench.snapshot()
    p = s["power"]
    assert [x["name"] for x in p["sources"]] == ["POWER METER", "KICKR"]  # reference (main sensor) first
    assert [x["label"] for x in p["sources"]] == ["A", "B"]
    d = p["diffs"][0]
    assert d["label"] == "A − B" and d["mean_diff_pct"] == pytest.approx(2.0, abs=0.1)
    assert abs(d["lag_s"]) < 0.2 and d["correlation"] > 0.99
    assert d["diff_now"] == p["sources"][0]["now"] - p["sources"][1]["now"]
    assert p["sources"][1]["mean"] == pytest.approx(220, abs=1)
    assert p["sources"][0]["mean"] == pytest.approx(p["sources"][1]["mean"] * 1.02, abs=1)
    assert s["hr"]["diffs"][0]["lag_s"] == pytest.approx(2.0, abs=0.3)  # B (armband) reports later
    assert s["running"] and s["elapsed"] == 200 and s["ride_id"] == "r1"


def test_three_power_sources_each_against_a():
    bench, _, _ = bench_with_ride(third=True)
    p = bench.snapshot()["power"]
    assert [x["label"] for x in p["sources"]] == ["A", "B", "C"]
    assert [d["label"] for d in p["diffs"]] == ["A − B", "A − C"]
    assert p["diffs"][1]["mean_diff_pct"] == pytest.approx(100 * (1.02 / 0.97 - 1), abs=0.2)
    assert len(p["series"]["values"]) == 3


def test_reference_can_change():
    bench, _, _ = bench_with_ride()
    bench.set_reference("power", "kickr")
    p = bench.snapshot()["power"]
    assert p["sources"][0]["id"] == "kickr"
    assert p["diffs"][0]["mean_diff_pct"] == pytest.approx(-100 * (1 - 1 / 1.02), abs=0.1)
    with pytest.raises(ValueError):
        bench.set_reference("power", "nope")


def test_at_most_three_sources():
    bus, clock = Bus(), ManualClock()
    bench = CompareBench(bus, clock)
    bench.start(0.0)
    for i in range(5):
        bus.publish("power", PowerSample(0.5, f"pm{i}", 200, 90.0))
    clock.advance(1)
    assert len(bench.snapshot()["power"]["sources"]) == 3


def test_live_chart_shows_last_two_minutes():
    bench, _, _ = bench_with_ride(300)
    series = bench.snapshot()["power"]["series"]
    assert len(series["t"]) == len(series["values"][0]) == 120
    assert series["t"][-1] == 299


def test_pause_skips_samples_and_stop_freezes():
    bench, bus, clock = bench_with_ride(60)
    bench.paused = True
    bus.publish("power", PowerSample(clock.now(), "pm", 999, 90.0))
    bench.paused = False
    assert bench.snapshot()["power"]["sources"][0]["max"] < 999
    bench.stop()
    before = bench.snapshot()
    bus.publish("power", PowerSample(clock.now(), "pm", 999, 90.0))
    clock.advance(30)
    after = bench.snapshot()
    assert not after["running"] and after["elapsed"] == before["elapsed"] == 60
    assert after["power"]["diffs"] == before["power"]["diffs"]
    assert all(s["now"] is None for s in after["power"]["sources"])
    assert len(after["power"]["series"]["t"]) == 60  # a finished comparison shows the whole ride


def test_one_source_is_not_a_comparison_yet():
    bus, clock = Bus(), ManualClock()
    bench = CompareBench(bus, clock)
    bench.start(0.0)
    bus.publish("power", PowerSample(0.5, "pm", 200, 90.0))
    clock.advance(1)
    s = bench.snapshot()
    assert len(s["power"]["sources"]) == 1 and s["power"]["diffs"] == []
    assert s["hr"] is None


def test_not_enough_data_yet():
    bench, _, _ = bench_with_ride(10)
    d = bench.snapshot()["power"]["diffs"][0]
    assert d["mean_diff"] is None and d["lag_s"] is None


def test_csv_export():
    bench, _, _ = bench_with_ride(30, third=True)
    rows = list(csv.reader(io.StringIO(bench.to_csv())))
    assert rows[0] == ["second", "power A POWER METER (W)", "power B KICKR (W)", "power C PEDALS (W)",
                       "power A-B (W)", "power A-C (W)", "hr A STRAP (bpm)", "hr B ARM (bpm)", "hr A-B (bpm)"]
    assert len(rows) == 31
    assert rows[1][0] == "0" and float(rows[1][1]) == 204 and float(rows[1][4]) == 4


def test_finished_ride_rebuilt_from_its_recording(tmp_path):
    rec = Recorder(ride_path(tmp_path, "20261104-180000"), {"id": "20261104-180000", "started": "2026-11-04T17:00:00+00:00"})
    for t in range(1, 121):
        rec.write({"type": "tick", "t": t, "main_power_id": "kickr", "main_hr_id": "strap", "power": 200})
        rec.write({"type": "power", "t": t + 0.2, "device_id": "pm", "watts": 204, "cadence": 90})
        rec.write({"type": "power", "t": t + 0.3, "device_id": "kickr", "watts": 200, "cadence": 90})
        rec.write({"type": "hr", "t": t + 0.1, "device_id": "strap", "bpm": 130})
    rec.write({"type": "device", "t": 0, "id": "pm", "name": "POWER METER"})
    rec.write({"type": "end", "t": 121})
    rec.close()
    ride = load_ride(ride_path(tmp_path, "20261104-180000"))
    s = CompareBench.from_ride(ride).snapshot()
    assert not s["running"] and s["elapsed"] == 121 and s["ride_id"] == "20261104-180000"
    assert [x["id"] for x in s["power"]["sources"]] == ["kickr", "pm"]  # the ride's main sensor is A
    assert s["power"]["sources"][1]["name"] == "POWER METER"
    assert s["power"]["diffs"][0]["mean_diff"] == pytest.approx(-4)
    assert s["power"]["sources"][0]["mean"] == 200 and s["power"]["sources"][1]["max"] == 204
    assert len(s["hr"]["sources"]) == 1
    swapped = CompareBench.from_ride(ride, {"power": "pm"}).snapshot()
    assert swapped["power"]["sources"][0]["id"] == "pm"
