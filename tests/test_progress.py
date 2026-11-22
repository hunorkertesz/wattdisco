from datetime import date

import pytest

from wattdisco.core import progress, stats
from wattdisco.core.ftp_history import FtpHistory, rebuild


def row(day, load=50.0, sim=False, bests=None, sources=None, ftp=250, test=None, est=None, rid=None):
    return {"id": rid or day.replace("-", "") + "-180000", "started": f"{day}T18:00:00+00:00", "duration": 3600,
            "sim": sim, "load": load, "bests": bests or {}, "sources": sources, "ftp": ftp, "test": test,
            "ftp_estimate": est, "name": "Ride"}


def test_best_efforts():
    power = [100] * 600 + [400] * 60 + [100] * 600
    b = stats.best_efforts(power)
    assert b["60"] == 400 and b["5"] == 400 and b["1"] == 400
    assert b["300"] == pytest.approx((400 * 60 + 100 * 240) / 300)
    assert "1800" not in b  # the ride is shorter than 30 min
    assert stats.best_efforts([]) == {}


def test_fitness_fatigue_form():
    rows = [row("2026-11-01", 100), row("2026-11-02", 100, sim=True), row("2026-11-03", 70)]
    f = progress.fitness(rows, today=date(2026, 11, 5))
    days = f["days"]
    assert [d["date"] for d in days] == ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    assert days[0]["fitness"] == pytest.approx(100 / 42, abs=0.05) and days[0]["fatigue"] == pytest.approx(100 / 7, abs=0.05)
    assert days[1]["load"] == 0  # simulated rides do not count
    assert days[0]["form"] == 0 and days[1]["form"] == pytest.approx(days[0]["fitness"] - days[0]["fatigue"], abs=0.1)
    assert f["now"]["label"] == progress.form_label(f["now"]["form"])
    assert progress.fitness([], today=date(2026, 11, 5)) == {"days": [], "now": None}


def test_power_curve_all_and_recent():
    rows = [row("2026-10-01", bests={"5": 900, "60": 400}), row("2026-11-20", bests={"5": 700, "60": 420}),
            row("2026-11-21", bests={"5": 1200}, sim=True)]
    all_time = {c["seconds"]: c for c in progress.power_curve(rows)}
    assert all_time[5]["watts"] == 900 and all_time[60]["watts"] == 420 and all_time[5]["ride"] == "20261001-180000"
    recent = {c["seconds"]: c["watts"] for c in progress.power_curve(rows, since=date(2026, 11, 1))}
    assert recent == {5: 700, 60: 420}


def test_sensor_trend_keeps_one_direction():
    def src(a, b, diff, pct, lag=1.0):
        return {"a_id": a, "a_name": a.upper(), "b_id": b, "b_name": b.upper(), "mean_diff": diff,
                "mean_diff_pct": pct, "lag_s": lag, "correlation": 0.99, "seconds": 3000}

    rows = [row("2026-11-01", sources=src("kickr", "pm", -5, -2.5)), row("2026-11-08", sources=src("kickr", "pm", -6, -3.0)),
            row("2026-11-15", sources=src("pm", "kickr", 8, 4.0)), row("2026-11-16", sources=None)]
    (pair,) = progress.sensor_trend(rows)
    assert (pair["a_name"], pair["b_name"], pair["count"]) == ("KICKR", "PM", 3)
    flipped = pair["rides"][-1]
    assert flipped["diff_w"] == -8 and flipped["lag_s"] == -1.0
    assert flipped["diff_pct"] == pytest.approx(-100 * 0.04 / 1.04, abs=0.01)  # pm 4 % above kickr: kickr 3.85 % below pm
    assert pair["trend_pct_per_week"] < 0 and pair["latest_pct"] == flipped["diff_pct"]


def test_ftp_history_rebuilt_from_rides():
    rows = [row("2026-11-01", ftp=250), row("2026-11-03", ftp=250, test="ramp", est={"source": "ramp", "ftp": 262}),
            row("2026-11-05", ftp=262), row("2026-11-09", ftp=270), row("2026-11-10", ftp=270, sim=True, test="ramp")]
    entries = rebuild(rows, 275)
    assert [(e["ftp"], e["from"], e["source"]) for e in entries] == [
        (250, None, "start"), (262, 250, "ramp"), (270, 262, "manual"), (275, 270, "manual")]
    assert entries[1]["ride"] == "20261103-180000" and entries[1]["date"].startswith("2026-11-03")
    assert rebuild([], 240)[0]["source"] == "start"


def test_ftp_history_store(tmp_path):
    h = FtpHistory(tmp_path)
    h.add(262, 250, "ramp", "r1")
    h.add(255, 262, "nonsense")
    assert [(e["ftp"], e["source"]) for e in h.load()] == [(250, "start"), (262, "ramp"), (255, "manual")]


def cp_rows(bests, day="2026-11-20"):
    return [row(day, bests={str(s): w for s, w in bests.items()})]


def test_critical_power_fit():
    # made from CP 250 W and W' 20 kJ: P = 250 + 20000 / t
    bests = {s: 250 + 20000 / s for s in (180, 300, 600, 1200)}
    cp = progress.critical_power(cp_rows(bests), ftp=240, today=date(2026, 11, 21))
    assert cp["status"] == "ok" and cp["cp"] == 250 and cp["w_prime_kj"] == pytest.approx(20, abs=0.1)
    assert not cp["suggest_test"] and len(cp["points"]) == 4
    high = progress.critical_power(cp_rows(bests), ftp=225, today=date(2026, 11, 21))
    assert high["suggest_test"] and "ramp test" in high["message"]


def test_critical_power_needs_all_out_and_recent_bests():
    flat = progress.critical_power(cp_rows({180: 252, 300: 251, 600: 250, 1200: 249}), 240, date(2026, 11, 21))
    assert flat["status"] == "not_all_out"  # W' near 0: ERG intervals, not all-out
    few = progress.critical_power(cp_rows({180: 400, 300: 330}), 240, date(2026, 11, 21))
    assert few["status"] == "few"
    old = progress.critical_power(cp_rows({s: 250 + 20000 / s for s in (180, 300, 600, 1200)}, day="2026-05-01"),
                                  240, date(2026, 11, 21))
    assert old["status"] == "few"  # older than 90 days


def test_critical_power_well_under_ftp_is_not_trusted():
    bests = {s: 230 + 12000 / s for s in (180, 300, 600, 1200)}  # a clean line, but 18 % under the FTP
    cp = progress.critical_power(cp_rows(bests), ftp=280, today=date(2026, 11, 21))
    assert cp["status"] == "below_ftp" and cp["cp"] == 230 and "not all-out" in cp["message"]
