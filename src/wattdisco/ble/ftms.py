"""Trainer control over the Fitness Machine Service (FTMS, 0x1826).

ERG: Set Target Power. Free ride: Indoor Bike Simulation on a flat road, so the
trainer stops holding power and the rider sets it with gears and cadence. A climb:
the same simulation with the road's gradient, so the trainer gets harder uphill.
Spin-down calibration: Spin Down Control, then the trainer's Fitness Machine Status
says when to stop pedalling and whether it worked; Indoor Bike Data gives the speed.
Encoders and parsers are pure functions; FtmsController talks to a connected client.
"""

import asyncio
import contextlib
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
FITNESS_MACHINE_FEATURE = normalize_uuid_16(0x2ACC)
FITNESS_MACHINE_STATUS = normalize_uuid_16(0x2ADA)
INDOOR_BIKE_DATA = normalize_uuid_16(0x2AD2)
TARGET_SIMULATION_BIT = 13  # target setting features: "Indoor Bike Simulation Parameters Supported"
TARGET_SPIN_DOWN_BIT = 15  # target setting features: "Spin Down Control Supported"
NO_SIMULATION = "this trainer can't simulate climbs"
NO_SPIN_DOWN = "this trainer can't be calibrated over Bluetooth; use its maker's app"
SPIN_DOWN_TIMEOUT = 120.0  # s for the whole calibration

STATUS_SPIN_DOWN = 0x14  # Fitness Machine Status op code
SPIN_DOWN_STATUS = {0x01: "requested", 0x02: "success", 0x03: "error", 0x04: "stop"}

OP_REQUEST_CONTROL = 0x00
OP_RESET = 0x01
OP_SET_TARGET_POWER = 0x05
OP_START = 0x07
OP_SET_SIMULATION = 0x11
OP_SPIN_DOWN = 0x13
SPIN_DOWN_START = 0x01
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


def spin_down_start() -> bytes:
    return bytes([OP_SPIN_DOWN, SPIN_DOWN_START])


@dataclass(frozen=True)
class ControlResponse:
    request_op: int
    result: int
    params: bytes = b""  # what follows the result (spin-down: the target speed band)

    @property
    def ok(self) -> bool:
        return self.result == RESULT_SUCCESS

    @property
    def text(self) -> str:
        return RESULT_TEXT.get(self.result, f"result {self.result:#04x}")


def parse_control_response(data: bytes | bytearray) -> ControlResponse:
    if len(data) < 3 or data[0] != OP_RESPONSE:
        raise ValueError(f"not a control point response: {bytes(data).hex()}")
    return ControlResponse(request_op=data[1], result=data[2], params=bytes(data[3:]))


def parse_spin_down_targets(params: bytes) -> tuple[float, float] | None:
    """The speed band (km/h) to ride up to before stopping, from a Spin Down Control answer."""
    if len(params) < 4:
        return None
    lo, hi = struct.unpack_from("<HH", params, 0)
    return lo / 100, hi / 100


def parse_spin_down_status(data: bytes | bytearray) -> str | None:
    """From Fitness Machine Status: "requested", "success", "error" or "stop" (stop pedalling), else None."""
    if len(data) >= 2 and data[0] == STATUS_SPIN_DOWN:
        return SPIN_DOWN_STATUS.get(data[1])
    return None


def parse_bike_speed(data: bytes | bytearray) -> float | None:
    """Instantaneous speed (km/h) from Indoor Bike Data, when this packet carries it (flag bit 0 clear)."""
    if len(data) < 4:
        return None
    flags = struct.unpack_from("<H", data, 0)[0]
    if flags & 1:
        return None  # "more data": this packet has no speed
    return struct.unpack_from("<H", data, 2)[0] / 100


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


def supports_simulation(data: bytes | bytearray) -> bool:
    """From the Fitness Machine Feature characteristic: machine features (uint32), then target settings (uint32)."""
    _machine, targets = struct.unpack_from("<II", data, 0)
    return bool(targets >> TARGET_SIMULATION_BIT & 1)


def supports_spin_down(data: bytes | bytearray) -> bool:
    _machine, targets = struct.unpack_from("<II", data, 0)
    return bool(targets >> TARGET_SPIN_DOWN_BIT & 1)


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
        self.simulation: bool | None = None  # the trainer can simulate a gradient (None: it didn't say)
        self.spin_down: bool | None = None  # the trainer offers spin-down calibration (None: it didn't say)
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
        try:
            features = await self.client.read_gatt_char(FITNESS_MACHINE_FEATURE)
            self.simulation = supports_simulation(features)
            self.spin_down = supports_spin_down(features)
        except (BleakError, ValueError, struct.error, KeyError):
            self.simulation = None  # unknown: try, and let the trainer's answer say
            self.spin_down = None
        await self.client.start_notify(CONTROL_POINT, self._on_indication)
        response = await self._call(request_control())
        if not response.ok:
            raise ConnectionError(f"trainer refused control: {response.text}")
        await self._call(start())  # some trainers answer "not supported"; that is fine

    async def apply(self, cmd: TrainerCommand) -> bool:
        if cmd.mode == "erg":
            watts = cmd.watts if self.power_range is None else self.power_range.clamp(cmd.watts)
            payload = set_target_power(watts)
        elif cmd.mode == "sim":
            if self.simulation is False:
                self.on_result(False, cmd, NO_SIMULATION)
                return False
            payload = set_simulation(grade_pct=cmd.grade or 0.0)
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
        if cmd.mode == "sim" and response.text == "not supported":
            self.simulation = False
            self.on_result(False, cmd, NO_SIMULATION)
            return False
        self.on_result(response.ok, cmd, response.text)
        return response.ok

    async def calibrate(self, progress: Callable[[dict], None], timeout: float = SPIN_DOWN_TIMEOUT) -> None:
        """Spin-down calibration. `progress` gets {"stage": ..., "low", "high", "speed"} as it goes:
        "speed_up" (ride up past `high` km/h), "stop" (stop pedalling, let the wheel run down), "success".
        Raises ValueError with a plain message when the trainer can't or the calibration fails."""
        if self.spin_down is False:
            raise ValueError(NO_SPIN_DOWN)
        events: asyncio.Queue[str] = asyncio.Queue()

        def on_status(_char, data: bytearray) -> None:
            status = parse_spin_down_status(data)
            if status:
                events.put_nowait(status)

        def on_bike(_char, data: bytearray) -> None:
            speed = parse_bike_speed(data)
            if speed is not None:
                progress({"speed": speed})

        listening = []
        for uuid, handler in ((FITNESS_MACHINE_STATUS, on_status), (INDOOR_BIKE_DATA, on_bike)):
            try:
                await self.client.start_notify(uuid, handler)
                listening.append(uuid)
            except (BleakError, KeyError, ValueError):
                pass  # optional: without status, the trainer's answer alone has to do
        try:
            if FITNESS_MACHINE_STATUS not in listening:
                raise ValueError(NO_SPIN_DOWN)
            try:
                response = await self._call(spin_down_start())
                if response.result == RESULT_NOT_PERMITTED:
                    await self._call(request_control())
                    response = await self._call(spin_down_start())
            except (TimeoutError, asyncio.TimeoutError) as e:
                raise ValueError("the trainer did not answer") from e
            if response.text == "not supported":
                self.spin_down = False
                raise ValueError(NO_SPIN_DOWN)
            if not response.ok:
                raise ValueError(f"the trainer refused: {response.text}")
            band = parse_spin_down_targets(response.params)
            progress({"stage": "speed_up", "low": band[0] if band else None, "high": band[1] if band else None})
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while True:
                left = deadline - loop.time()
                if left <= 0:
                    raise ValueError("the trainer didn't finish the spin-down in time")
                try:
                    status = await asyncio.wait_for(events.get(), left)
                except (TimeoutError, asyncio.TimeoutError):
                    continue
                if status == "stop":
                    progress({"stage": "stop"})
                elif status == "success":
                    progress({"stage": "success"})
                    return
                elif status == "error":
                    raise ValueError("the trainer says the spin-down failed; try again, pedalling a little harder")
        finally:
            for uuid in listening:
                with contextlib.suppress(Exception):
                    await self.client.stop_notify(uuid)
