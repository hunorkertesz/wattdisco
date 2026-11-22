import pytest

from wattdisco.core.speed import FlatRoad, steady_speed, virtual_ride


def test_steady_speed_hand_checked():
    # 200 W, 75 + 8 kg: 195.2 W at the wheel = (4.07 N + 0.233 v²) v  ->  about 8.81 m/s
    assert steady_speed(200, 75) == pytest.approx(8.81, abs=0.03)
    assert steady_speed(200, 75) * 3.6 == pytest.approx(31.7, abs=0.2)  # km/h


def test_more_power_faster_more_weight_slower():
    assert steady_speed(300, 75) > steady_speed(200, 75)
    assert steady_speed(200, 95) < steady_speed(200, 65)
    assert steady_speed(0, 75) == pytest.approx(0, abs=1e-6)


def test_flat_road_settles_on_steady_speed():
    road = FlatRoad(75)
    for _ in range(120):
        road.step(200)
    assert road.speed == pytest.approx(steady_speed(200, 75), abs=0.05)


def test_coasting_slows_down_gradually():
    road = FlatRoad(75)
    for _ in range(120):
        road.step(200)
    v = road.speed
    road.step(0)
    assert 0.9 * v < road.speed < v  # one second of coasting loses a little speed
    road.step(None)  # sensor dropout also counts as coasting
    assert road.speed < v


def test_virtual_ride_distance():
    speeds, distances = virtual_ride([200] * 600, 75)
    assert len(speeds) == len(distances) == 600
    assert distances[-1] == pytest.approx(sum(speeds), rel=0.01)
    assert distances[-1] / 600 * 3.6 == pytest.approx(31.2, abs=0.6)  # average incl. the start
