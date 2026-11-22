import pytest

from wattdisco.ble.heart_rate import parse_heart_rate
from wattdisco.ble.power import CadenceCalculator, parse_cycling_power


# Heart rate (0x2A37)

def test_hr_uint8():
    m = parse_heart_rate(bytes([0x00, 0x48]))
    assert m.bpm == 72
    assert m.contact is None
    assert m.rr == ()


def test_hr_uint16():
    m = parse_heart_rate(bytes([0x01, 0xB4, 0x00]))
    assert m.bpm == 180


def test_hr_contact_and_rr():
    # contact supported + detected, RR present, 75 bpm, RR 1024/1024 s and 768/1024 s
    m = parse_heart_rate(bytes([0x16, 0x4B, 0x00, 0x04, 0x00, 0x03]))
    assert m.bpm == 75
    assert m.contact is True
    assert m.rr == (1.0, 0.75)


def test_hr_contact_lost():
    m = parse_heart_rate(bytes([0x04, 0x40]))
    assert m.contact is False


def test_hr_energy_then_rr():
    m = parse_heart_rate(bytes([0x18, 0x50, 0x10, 0x00, 0x00, 0x02]))
    assert m.bpm == 80
    assert m.energy_kj == 16
    assert m.rr == (0.5,)


def test_hr_too_short():
    with pytest.raises(ValueError):
        parse_heart_rate(b"\x00")


# Cycling power (0x2A63)

def test_power_only():
    m = parse_cycling_power(bytes.fromhex("0000fa00"))
    assert m.watts == 250
    assert m.crank_revs is None


def test_power_negative():
    assert parse_cycling_power(bytes.fromhex("0000ffff")).watts == -1


def test_power_with_crank():
    m = parse_cycling_power(bytes.fromhex("2000c8000a000004"))
    assert m.watts == 200
    assert m.crank_revs == 10
    assert m.crank_event_time == 1024


def test_power_skips_optional_fields_before_crank():
    # balance (50 %), accumulated torque, wheel revs, then crank
    data = bytes.fromhex("3500" "2c01" "64" "0000" "01000000" "0000" "0500" "0008")
    m = parse_cycling_power(data)
    assert m.watts == 300
    assert m.balance == 50.0
    assert m.crank_revs == 5
    assert m.crank_event_time == 2048


def test_power_too_short():
    with pytest.raises(ValueError):
        parse_cycling_power(b"\x00\x00\x01")


# Cadence from crank revolutions

def test_cadence_basic():
    c = CadenceCalculator()
    assert c.update(10, 0, now=0.0) is None
    assert c.update(11, 1024, now=1.0) == pytest.approx(60.0)
    assert c.update(14, 3072, now=3.0) == pytest.approx(90.0)


def test_cadence_rollover():
    c = CadenceCalculator()
    c.update(65535, 65000, now=0.0)
    # 2 revs in 1536 ticks across both counters rolling over
    assert c.update(1, 1000, now=1.5) == pytest.approx(80.0)


def test_cadence_holds_then_drops_to_zero():
    c = CadenceCalculator()
    c.update(0, 0, now=0.0)
    c.update(1, 1024, now=1.0)
    assert c.update(1, 1024, now=2.0) == pytest.approx(60.0)
    assert c.update(1, 1024, now=3.1) == 0.0


def test_cadence_ignores_implausible_jump():
    c = CadenceCalculator()
    c.update(0, 0, now=0.0)
    c.update(1, 1024, now=1.0)
    assert c.update(50, 1100, now=1.1) == pytest.approx(60.0)


# Role detection from advertised services

def test_roles_from_services():
    from wattdisco.ble.scanner import roles_from_services

    kickr = ["00001818-0000-1000-8000-00805f9b34fb", "00001826-0000-1000-8000-00805F9B34FB"]
    assert roles_from_services(kickr) == {"power", "cadence", "control"}
    assert roles_from_services(["0000180d-0000-1000-8000-00805f9b34fb"]) == {"heart_rate"}
    assert roles_from_services([]) == set()


# Cycling power control point (zero offset)

def test_power_control_response():
    from wattdisco.ble.power import parse_power_control_response, start_offset_compensation

    assert start_offset_compensation() == b"\x0c"
    r = parse_power_control_response(bytes([0x20, 0x0C, 0x01, 0xFD, 0xFF]))
    assert r.ok and r.request_op == 0x0C and r.value == -3
    r = parse_power_control_response(bytes([0x20, 0x0C, 0x02]))
    assert not r.ok and r.text == "not supported" and r.value is None
    with pytest.raises(ValueError):
        parse_power_control_response(bytes([0x80, 0x0C, 0x01]))


def test_power_balance_left_share_and_one_sided():
    # flags: balance present + reference left; 0x61 = 97 half-percent = 48.5 % left
    m = parse_cycling_power(bytes.fromhex("0300" "2c01" "61"))
    assert m.balance == 48.5
    # 0 or 100 % (one side only) is no balance
    assert parse_cycling_power(bytes.fromhex("0100" "2c01" "00")).balance is None
    assert parse_cycling_power(bytes.fromhex("0100" "2c01" "c8")).balance is None
