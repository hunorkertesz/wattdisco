import pytest

from wattdisco.core.smoothing import Smoother


def test_empty():
    assert Smoother().value(3.0) is None


def test_raw_is_latest():
    s = Smoother()
    for t, w in enumerate([100, 200, 300]):
        s.add(t, w)
    assert s.value(0) == 300


def test_three_second_window_at_1hz():
    s = Smoother()
    for t, w in enumerate([100, 200, 300, 400]):
        s.add(t, w)
    assert s.value(3.0) == pytest.approx(300)  # 200, 300, 400


def test_ten_second_window_drops_old_samples():
    s = Smoother()
    for t in range(20):
        s.add(t, 100 if t < 10 else 200)
    assert s.value(10.0) == pytest.approx(200)


def test_window_follows_sample_time_with_gaps():
    s = Smoother()
    s.add(0, 100)
    s.add(5, 300)
    assert s.value(3.0) == 300
    assert s.value(10.0) == pytest.approx(200)
