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
