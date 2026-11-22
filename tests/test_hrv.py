import random

import pytest

from wattdisco.core import hrv
from wattdisco.core.recorder import RideData


def white(n, seed=1):
    rnd = random.Random(seed)
    return [800 + rnd.gauss(0, 30) for _ in range(n)]


def brown(n, seed=2):
    rnd = random.Random(seed)
    x, out = 800.0, []
    for _ in range(n):
        x += rnd.gauss(0, 8)
        out.append(x)
    return out


def test_alpha1_tells_random_from_correlated():
    assert 0.35 < hrv.alpha1(white(2000)) < 0.65  # uncorrelated: about 0.5
    assert hrv.alpha1(brown(2000)) > 1.2  # strongly correlated: about 1.5
    assert hrv.alpha1([800.0] * 10) is None


def test_artefacts_are_dropped():
    b = hrv.beats([(0.0, [0.8, 0.81]), (1.6, [0.4]), (2.0, [0.8, 2.5])])
    assert [ok for *_, ok in b] == [True, True, False, True, False]


def window(hr, a, power=None):
    return {"t": 0, "hr": hr, "alpha1": a, "power": power, "artefacts_pct": 0}


def test_threshold_where_alpha1_crosses_075():
    windows = [window(120 + 5 * i, 1.2 - 0.075 * i, 150 + 10 * i) for i in range(10)]  # 0.75 at hr 150, 210 W
    out = {"threshold": None}
    assert hrv._threshold(windows, out) is None
    assert out["threshold"]["hr"] == 150 and out["threshold"]["power"] == 210 and out["threshold"]["correlation"] < -0.9


@pytest.mark.parametrize(("alphas", "reason"), [
    ([1.0] * 8, "above 0.75"),
    ([0.5] * 8, "below 0.75"),
    ([1.0, 0.5] * 4, "did not fall clearly"),
    ([1.0] * 3, "too few"),
])
def test_no_threshold_says_why(alphas, reason):
    windows = [window(130 + i, a) for i, a in enumerate(alphas)]
    out = {"threshold": None}
    assert reason in hrv._threshold(windows, out) and out["threshold"] is None


def test_dfa_ride_from_recorded_rr():
    rr = white(1200, seed=3)
    t, samples = 0.0, []
    for x in rr:
        t += x / 1000
        samples.append((t, [x / 1000]))
    ride = RideData(meta={"id": "r"}, ticks=[{"t": i + 1, "main_hr_id": "strap", "power": 200} for i in range(int(t))],
                    rr={"strap": samples})
    d = hrv.dfa_ride(ride, [200] * (int(t) + 1))
    assert d["device_id"] == "strap" and d["windows"] and d["artefacts_pct"] == 0
    assert all(0.2 < w["alpha1"] < 0.8 and w["power"] == 200 for w in d["windows"])
    assert "below 0.75" in d["reason"]
    assert hrv.dfa_ride(RideData(meta={"id": "r"}), []) is None
