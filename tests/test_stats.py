import math

import pytest

from wattdisco.core import stats


def test_mean_max_ignore_missing():
    assert stats.mean([100, None, 200]) == 150
    assert stats.mean([None]) is None
    assert stats.maximum([1, None, 3]) == 3


def test_weighted_power_steady_equals_power():
    assert stats.weighted_power([200] * 600) == pytest.approx(200)


def test_weighted_power_above_average_for_intervals():
    # 1 min at 300 W, 1 min at 100 W, ten times: average 200 W, weighted power clearly higher
    power = ([300] * 60 + [100] * 60) * 10
    wp = stats.weighted_power(power)
    assert stats.mean(power) == 200
    assert 230 < wp < 260


def test_weighted_power_short_ride_falls_back_to_average():
    assert stats.weighted_power([100, 200]) == 150
    assert stats.weighted_power([]) is None


def test_intensity_load_variability_hand_checked():
    # 1 h at exactly FTP is intensity 1.0 and training load 100 by definition
    assert stats.intensity(250, 250) == 1.0
    assert stats.training_load(3600, 250, 250) == pytest.approx(100)
    # 30 min at 200 W with FTP 250: intensity 0.8, load = 0.5 h × 0.64 × 100 = 32
    assert stats.training_load(1800, 200, 250) == pytest.approx(32)
    assert stats.variability_index(220, 200) == pytest.approx(1.1)
    assert stats.training_load(0, 200, 250) is None


def test_on_target():
    power = [250, 262, 263, 237, 200, None]
    target = [250, 250, 250, 250, 250, 250]
    # 250, 262 (+4.8 %), 237 (−5.2 %) → 262 in, 263 (+5.2 %) out, 237 out, 200 out
    assert stats.on_target_pct(power, target) == pytest.approx(40)
    assert stats.on_target_pct([100], [None]) is None


def test_hr_drift():
    power = [200] * 600
    hr = [140] * 300 + [147] * 300  # HR up 5 % at the same power
    assert stats.hr_drift_pct(power, hr) == pytest.approx(100 * (1 - 140 / 147), abs=0.01)
    assert stats.hr_drift_pct([200] * 100, [140] * 100) is None  # too short


def test_time_in_zones():
    z = stats.time_in_zones([100, 216, 216, 300, None], 240)
    assert [x["seconds"] for x in z] == [1, 0, 0, 2, 0, 1]
    assert z[3]["name"] == "Sweet spot"


def test_per_second():
    samples = [(0.2, 100), (0.7, 110), (2.1, 200), (5.0, 999)]
    assert stats.per_second(samples, 3) == [105, None, 200]


def test_source_difference_offset_and_lag():
    import math

    base = [200 + 40 * math.sin(i / 7) + (15 if i % 23 < 4 else 0) for i in range(300)]
    a = base
    b = [None] + [x * 0.98 for x in base[:-1]]  # reads 2 % low, 1 s later
    d = stats.source_difference(a, b)
    assert d["lag_s"] == pytest.approx(1.0, abs=0.15)
    assert d["correlation"] > 0.99
    assert d["mean_diff"] == pytest.approx(0.02 * 200 / 0.98, rel=0.3)
    assert d["mean_diff_pct"] == pytest.approx(2.0, abs=0.6)


def test_source_difference_needs_overlap():
    assert stats.source_difference([200] * 10, [200] * 10) is None


def test_interval_stats_and_split():
    ticks = [{"index": 0, "power": 100, "target": 100, "hr": 120, "cadence": 85}] * 40 + [
        {"index": 1, "power": 240, "target": 250, "hr": 150, "cadence": 0}
    ] * 60
    groups = stats.split_phases(ticks)
    assert [len(g) for g in groups] == [40, 60]
    s = stats.interval_stats(groups[1])
    assert (s["target"], s["avg_power"], s["diff"]) == (250, 240, -10)
    assert s["diff_pct"] == pytest.approx(-4.0)
    assert s["on_target_pct"] == 100
    assert s["avg_cadence"] is None  # not pedalling
    assert s["wp"] == pytest.approx(240)


def test_lag_found_on_a_rising_trend():
    import math

    # warm-up heart rate: a steady climb with small bumps; B is 3 s late
    hr = [100 + 0.3 * i + 2 * math.sin(i / 4) for i in range(200)]
    a = hr[3:]
    b = hr[:-3]
    d = stats.source_difference(a, b)
    assert d["lag_s"] == pytest.approx(3.0, abs=0.3)
    assert d["correlation"] > 0.99


def test_detrend_removes_a_straight_line_and_keeps_gaps():
    out = stats.detrend([1, 2, 3, None, 5])
    assert out[3] is None
    assert all(abs(v) < 1e-9 for v in out if v is not None)


def test_deviation_figures():
    power = [250] * 10 + [270] * 5 + [230] * 5  # 10 s on, 5 s 8 % over, 5 s 8 % under
    target = [250] * 20
    d = stats.deviation(power, target)
    assert d["seconds"] == 20 and d["mean_w"] == 0
    assert d["above_pct"] == 25 and d["below_pct"] == 25 and d["within_pct"] == 50
    assert d["max_over_w"] == 20 and d["max_under_w"] == -20
    assert d["mean_abs_w"] == 10
    assert d["sd_w"] == pytest.approx(math.sqrt((5 * 400 + 5 * 400) / 20))
    assert d["longest_off_s"] == 10
    assert stats.deviation([100], [None]) is None


def test_time_in_hr_zones():
    z = stats.time_in_hr_zones([100, 120, 140, 160, 180, None], [114, 133, 152, 171])
    assert [x["seconds"] for x in z] == [1, 1, 1, 1, 1]


def test_best_average():
    assert stats.best_average([100, 200, 300, None, 300], 2) == 250
    assert stats.best_average([100, None], 2) == 50  # a missing second counts as 0 W
    assert stats.best_average([100], 2) is None


def test_ftp_from_ramp_test_uses_the_best_minute_of_the_steps():
    kinds = ["warmup"] * 300 + ["test"] * 600 + ["cooldown"] * 300
    power = [500] * 60 + [150] * 240 + [200 + i // 60 * 20 for i in range(600)] + [100] * 300
    est = stats.ftp_estimate(power, kinds, "ramp", 250)
    assert est["source"] == "ramp" and est["best_w"] == 380  # the warm-up sprint does not count
    assert est["ftp"] == 285 and est["change"] == 35


def test_ramp_test_too_short_says_nothing():
    assert stats.ftp_estimate([300] * 200, ["test"] * 200, "ramp", 250) is None


def test_ftp_from_20_min_test_even_when_lower():
    kinds = ["warmup"] * 600 + ["test"] * 1200 + ["cooldown"] * 300
    power = [150] * 600 + [240] * 1200 + [120] * 300
    est = stats.ftp_estimate(power, kinds, "20min", 250)
    assert est["source"] == "20min" and est["ftp"] == 228 and est["change"] == -22


def test_20_min_test_stopped_early_only_counts_when_better():
    kinds = ["warmup"] * 600 + ["test"] * 900
    assert stats.ftp_estimate([240] * 1500, kinds, "20min", 250) is None
    est = stats.ftp_estimate([300] * 1500, kinds, "20min", 250)
    assert est["source"] == "best20" and est["ftp"] == 285


def test_any_ride_suggests_ftp_only_when_the_best_20_min_beats_it():
    assert stats.ftp_estimate([250] * 1800, ["endurance"] * 1800, None, 250) is None  # 95 % of 250 < 250
    est = stats.ftp_estimate([280] * 1800, ["threshold"] * 1800, None, 250)
    assert est["source"] == "best20" and est["ftp"] == 266
    assert stats.ftp_estimate([400] * 600, ["vo2"] * 600, None, 250) is None  # shorter than 20 min
