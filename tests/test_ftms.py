import asyncio

import pytest

from wattdisco.ble import ftms
from wattdisco.ble.ftms import FtmsController, PowerRange, parse_control_response, parse_power_range
from wattdisco.models import TrainerCommand


def test_encoders():
    assert ftms.request_control() == b"\x00"
    assert ftms.start() == b"\x07"
    assert ftms.set_target_power(250) == bytes.fromhex("05fa00")
    assert ftms.set_target_power(-1) == bytes.fromhex("05ffff")
    # wind 0, grade 0, crr 0.004 -> 40, cw 0.51 -> 51
    assert ftms.set_simulation() == bytes.fromhex("11" "0000" "0000" "28" "33")
    assert ftms.set_simulation(grade_pct=-2.5)[3:5] == (-250).to_bytes(2, "little", signed=True)


def test_parse_response():
    r = parse_control_response(bytes([0x80, 0x05, 0x01]))
    assert r.ok and r.request_op == 0x05
    r = parse_control_response(bytes([0x80, 0x05, 0x05]))
    assert not r.ok and r.text == "control not permitted"
    with pytest.raises(ValueError):
        parse_control_response(bytes([0x05, 0x01]))


def test_power_range():
    r = parse_power_range(bytes.fromhex("0000" "d007" "0100"))
    assert r == PowerRange(0, 2000, 1)
    assert r.clamp(2500) == 2000 and r.clamp(-5) == 0


class FakeTrainer:
    """Acts like a connected BleakClient for the FTMS control point."""

    def __init__(self, answers=None, power_range=bytes.fromhex("0000" "e803" "0100")):
        self.written: list[bytes] = []
        self.answers = answers or {}  # opcode -> list of result codes, used in order
        self.power_range = power_range
        self.callback = None
        self.silent = False

    async def read_gatt_char(self, _uuid):
        return self.power_range

    async def start_notify(self, _uuid, callback):
        self.callback = callback

    async def write_gatt_char(self, _uuid, data, response=True):
        self.written.append(bytes(data))
        if self.silent:
            return
        op = data[0]
        queue = self.answers.get(op, [])
        result = queue.pop(0) if queue else 0x01
        loop = asyncio.get_running_loop()
        loop.call_soon(self.callback, None, bytearray([0x80, op, result]))


def run(coro):
    return asyncio.run(coro)


def test_setup_requests_control_and_starts():
    trainer = FakeTrainer()
    c = FtmsController(trainer, lambda *a: None)
    run(c.setup())
    assert trainer.written == [b"\x00", b"\x07"]
    assert c.power_range == PowerRange(0, 1000, 1)


def test_setup_fails_when_control_refused():
    trainer = FakeTrainer(answers={0x00: [0x05]})
    with pytest.raises(ConnectionError):
        run(FtmsController(trainer, lambda *a: None).setup())


def test_apply_erg_and_free_report_results():
    results = []

    async def go():
        trainer = FakeTrainer()
        c = FtmsController(trainer, lambda ok, cmd, msg: results.append((ok, cmd, msg)))
        await c.setup()
        assert await c.apply(TrainerCommand("erg", 1500))  # clamped to 1000
        assert await c.apply(TrainerCommand("free"))
        return trainer.written[2:]

    written = run(go())
    assert written == [ftms.set_target_power(1000), ftms.set_simulation()]
    assert [r[0] for r in results] == [True, True]


def test_apply_retakes_control_when_not_permitted():
    async def go():
        trainer = FakeTrainer(answers={0x05: [0x05, 0x01]})
        c = FtmsController(trainer, lambda *a: None)
        await c.setup()
        ok = await c.apply(TrainerCommand("erg", 200))
        return ok, trainer.written[2:]

    ok, written = run(go())
    assert ok
    assert written == [ftms.set_target_power(200), b"\x00", ftms.set_target_power(200)]


def test_apply_reports_timeout():
    results = []

    async def go():
        trainer = FakeTrainer()
        c = FtmsController(trainer, lambda ok, cmd, msg: results.append((ok, msg)))
        c.RESPONSE_TIMEOUT = 0.05
        await c.setup()
        trainer.silent = True
        return await c.apply(TrainerCommand("erg", 200))

    assert run(go()) is False
    assert results == [(False, "trainer did not answer")]


class SpinDownTrainer:
    """A trainer that runs a spin-down: answers 0x13 with a speed band, sends speed and status notifications."""

    def __init__(self, features=(0).to_bytes(4, "little") + (1 << 15 | 1 << 13).to_bytes(4, "little"), finish=0x02,
                 answer=0x01):
        self.features, self.finish, self.answer = features, finish, answer
        self.callbacks = {}
        self.written: list[bytes] = []

    async def read_gatt_char(self, uuid):
        if uuid == ftms.FITNESS_MACHINE_FEATURE:
            return self.features
        return bytes.fromhex("0000e8030100")

    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback

    async def stop_notify(self, uuid):
        self.callbacks.pop(uuid, None)

    async def write_gatt_char(self, _uuid, data, response=True):
        self.written.append(bytes(data))
        loop = asyncio.get_running_loop()
        cp = self.callbacks[ftms.CONTROL_POINT]
        if data[0] != ftms.OP_SPIN_DOWN:
            loop.call_soon(cp, None, bytearray([0x80, data[0], 0x01]))
            return
        band = (3000).to_bytes(2, "little") + (3500).to_bytes(2, "little")  # 30–35 km/h
        loop.call_soon(cp, None, bytearray([0x80, data[0], self.answer]) + band)
        if self.answer != 0x01:
            return
        bike, status = self.callbacks[ftms.INDOOR_BIKE_DATA], self.callbacks[ftms.FITNESS_MACHINE_STATUS]
        for i, kmh in enumerate((28.0, 33.0, 36.5)):
            loop.call_later(0.02 * (i + 1), bike, None, bytearray((0x0044).to_bytes(2, "little") + round(kmh * 100).to_bytes(2, "little")))
        loop.call_later(0.15, status, None, bytearray([0x14, 0x04]))  # stop pedalling
        loop.call_later(0.25, status, None, bytearray([0x14, self.finish]))


def test_spin_down_parsers():
    assert ftms.spin_down_start() == b"\x13\x01"
    assert ftms.parse_spin_down_targets(bytes.fromhex("b80bac0d")) == (30.0, 35.0)
    assert ftms.parse_spin_down_targets(b"") is None
    assert ftms.parse_spin_down_status(b"\x14\x04") == "stop" and ftms.parse_spin_down_status(b"\x14\x02") == "success"
    assert ftms.parse_spin_down_status(b"\x04") is None
    assert ftms.parse_bike_speed(bytes.fromhex("4400c409")) == 25.0
    assert ftms.parse_bike_speed(bytes.fromhex("4500c409")) is None  # "more data": no speed in this packet
    features = (0).to_bytes(4, "little") + (1 << 15).to_bytes(4, "little")
    assert ftms.supports_spin_down(features) and not ftms.supports_simulation(features)


def test_spin_down_runs_through_its_stages():
    trainer = SpinDownTrainer()
    c = FtmsController(trainer, lambda *a: None)
    seen = []

    async def go():
        await c.setup()
        await c.calibrate(seen.append)

    run(go())
    stages = [s for s in seen if "stage" in s]
    assert [s["stage"] for s in stages] == ["speed_up", "stop", "success"]
    assert stages[0] == {"stage": "speed_up", "low": 30.0, "high": 35.0}
    # speed can come in before the trainer's answer is handled (Windows timers are coarse): only the values count
    assert [s["speed"] for s in seen if "speed" in s] == [28.0, 33.0, 36.5]
    assert ftms.INDOOR_BIKE_DATA not in trainer.callbacks  # notifications stopped afterwards
    assert ftms.CONTROL_POINT in trainer.callbacks  # control stays


def test_spin_down_plain_errors():
    async def calibrate(trainer):
        c = FtmsController(trainer, lambda *a: None)
        await c.setup()
        await c.calibrate(lambda _s: None, timeout=1.0)

    no_feature = SpinDownTrainer(features=(0).to_bytes(8, "little"))
    with pytest.raises(ValueError, match="maker's app"):
        run(calibrate(no_feature))
    assert not any(w[0] == ftms.OP_SPIN_DOWN for w in no_feature.written)  # never asked
    with pytest.raises(ValueError, match="failed"):
        run(calibrate(SpinDownTrainer(finish=0x03)))
    with pytest.raises(ValueError, match="maker's app"):
        run(calibrate(SpinDownTrainer(features=b"", answer=0x02)))  # unknown features, then "not supported"
