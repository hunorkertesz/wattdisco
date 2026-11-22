"""Trainer control over the Fitness Machine Service (FTMS, 0x1826).

ERG: Set Target Power. Free ride: Indoor Bike Simulation on a flat road, so the
trainer stops holding power and the rider sets it with gears and cadence.
Encoders and parsers are pure functions; FtmsController talks to a connected client.
"""

import asyncio
import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass

from bleak.exc import BleakError
from bleak.uuids import normalize_uuid_16

from wattdisco.models import TrainerCommand

log = logging.getLogger(__name__)

CONTROL_POINT = normalize_uuid_16(0x2AD9)
SUPPORTED_POWER_RANGE = normalize_uuid_16(0x2AD8)

OP_REQUEST_CONTROL = 0x00
OP_RESET = 0x01
OP_SET_TARGET_POWER = 0x05
OP_START = 0x07
OP_SET_SIMULATION = 0x11
OP_RESPONSE = 0x80

RESULT_SUCCESS = 0x01
RESULT_TEXT = {
    0x01: "ok",
    0x02: "not supported",
    0x03: "invalid parameter",
    0x04: "operation failed",
    0x05: "control not permitted",
}
RESULT_NOT_PERMITTED = 0x05

# Flat road for free ride.
FREE_RIDE_CRR = 0.004
FREE_RIDE_CW = 0.51  # kg/m


def request_control() -> bytes:
    return bytes([OP_REQUEST_CONTROL])


def start() -> bytes:
    return bytes([OP_START])


def set_target_power(watts: int) -> bytes:
    return struct.pack("<Bh", OP_SET_TARGET_POWER, watts)


def set_simulation(grade_pct: float = 0.0, wind_mps: float = 0.0, crr: float = FREE_RIDE_CRR, cw: float = FREE_RIDE_CW) -> bytes:
    return struct.pack(
        "<BhhBB", OP_SET_SIMULATION, round(wind_mps * 1000), round(grade_pct * 100), round(crr * 10000), round(cw * 100)
    )


@dataclass(frozen=True)
class ControlResponse:
    request_op: int
    result: int

    @property
    def ok(self) -> bool:
        return self.result == RESULT_SUCCESS

    @property
    def text(self) -> str:
        return RESULT_TEXT.get(self.result, f"result {self.result:#04x}")


def parse_control_response(data: bytes | bytearray) -> ControlResponse:
    if len(data) < 3 or data[0] != OP_RESPONSE:
        raise ValueError(f"not a control point response: {bytes(data).hex()}")
    return ControlResponse(request_op=data[1], result=data[2])


@dataclass(frozen=True)
class PowerRange:
    min: int
    max: int
    step: int

    def clamp(self, watts: int) -> int:
        return max(self.min, min(self.max, watts))


def parse_power_range(data: bytes | bytearray) -> PowerRange:
    lo, hi, step = struct.unpack_from("<hhH", data, 0)
    return PowerRange(lo, hi, step or 1)


class FtmsController:
    """Sends commands to the control point one at a time and waits for each answer.

    `client` is a connected BleakClient (or anything with the same three methods).
    `on_result(ok, command, message)` reports every outcome.
    """

    RESPONSE_TIMEOUT = 2.0

    def __init__(self, client, on_result: Callable[[bool, TrainerCommand | None, str], None]) -> None:
        self.client = client
        self.on_result = on_result
        self.power_range: PowerRange | None = None
        self._pending: tuple[int, asyncio.Future] | None = None
        self._lock = asyncio.Lock()

    def _on_indication(self, _char, data: bytearray) -> None:
        try:
            response = parse_control_response(data)
        except ValueError:
            return
        if self._pending and self._pending[0] == response.request_op and not self._pending[1].done():
            self._pending[1].set_result(response)

    async def _call(self, payload: bytes) -> ControlResponse:
        async with self._lock:
            future = asyncio.get_running_loop().create_future()
            self._pending = (payload[0], future)
            try:
                await self.client.write_gatt_char(CONTROL_POINT, payload, response=True)
                return await asyncio.wait_for(future, self.RESPONSE_TIMEOUT)
            finally:
                self._pending = None

    async def setup(self) -> None:
        try:
            self.power_range = parse_power_range(await self.client.read_gatt_char(SUPPORTED_POWER_RANGE))
        except (BleakError, ValueError, struct.error, KeyError):
            self.power_range = None  # optional characteristic
        await self.client.start_notify(CONTROL_POINT, self._on_indication)
        response = await self._call(request_control())
        if not response.ok:
            raise ConnectionError(f"trainer refused control: {response.text}")
        await self._call(start())  # some trainers answer "not supported"; that is fine

    async def apply(self, cmd: TrainerCommand) -> bool:
        if cmd.mode == "erg":
            watts = cmd.watts if self.power_range is None else self.power_range.clamp(cmd.watts)
            payload = set_target_power(watts)
        else:
            payload = set_simulation()
        try:
            response = await self._call(payload)
            if response.result == RESULT_NOT_PERMITTED:  # trainer forgot us, e.g. after its own reset
                await self._call(request_control())
                response = await self._call(payload)
        except (TimeoutError, asyncio.TimeoutError):
            self.on_result(False, cmd, "trainer did not answer")
            return False
        self.on_result(response.ok, cmd, response.text)
        return response.ok
