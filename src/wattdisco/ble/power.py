"""Parse the Cycling Power Measurement characteristic (0x2A63) and derive cadence."""

import struct
from dataclasses import dataclass

# Flag bits of the optional fields, in the order they appear in the packet.
_PEDAL_BALANCE = 1 << 0
_BALANCE_REFERENCE_LEFT = 1 << 1  # the balance is the left pedal's share (else: reference unknown)
_ACCUMULATED_TORQUE = 1 << 2
_WHEEL_REVS = 1 << 4
_CRANK_REVS = 1 << 5

CRANK_TIME_UNIT = 1024  # last crank event time is in 1/1024 s
CADENCE_TIMEOUT = 2.0  # no new crank event for this long means cadence 0
MAX_CADENCE = 250.0


@dataclass(frozen=True)
class CyclingPowerMeasurement:
    watts: int
    balance: float | None  # the left pedal's share of the power, % (see parse_cycling_power)
    crank_revs: int | None  # cumulative, 16-bit
    crank_event_time: int | None  # 1/1024 s, 16-bit


def parse_cycling_power(data: bytes | bytearray) -> CyclingPowerMeasurement:
    if len(data) < 4:
        raise ValueError(f"cycling power packet too short: {bytes(data).hex()}")
    flags, watts = struct.unpack_from("<Hh", data, 0)
    i = 4

    balance = None
    if flags & _PEDAL_BALANCE:
        # in 1/2 %. With the reference bit it is the left pedal's share; without it the spec leaves the
        # side open, and meters that leave it out (most) send the left pedal's share as well.
        raw = data[i] / 2
        balance = raw if 0 < raw < 100 else None  # 0 or 100: one side only, nothing to balance
        i += 1
    if flags & _ACCUMULATED_TORQUE:
        i += 2
    if flags & _WHEEL_REVS:
        i += 6

    crank_revs = crank_time = None
    if flags & _CRANK_REVS:
        crank_revs, crank_time = struct.unpack_from("<HH", data, i)

    return CyclingPowerMeasurement(
        watts=watts, balance=balance, crank_revs=crank_revs, crank_event_time=crank_time
    )


class CadenceCalculator:
    """Cadence in rpm from cumulative crank revolutions, with 16-bit rollover.

    `now` is the local arrival time in seconds; it is only used to fall back to 0
    when the crank stops sending new events.
    """

    def __init__(self) -> None:
        self._revs: int | None = None
        self._event_time: int | None = None
        self._last_change: float | None = None
        self.cadence: float | None = None

    def update(self, revs: int, event_time: int, now: float) -> float | None:
        if self._revs is None or self._event_time is None:
            self._revs, self._event_time, self._last_change = revs, event_time, now
            return self.cadence

        d_revs = (revs - self._revs) & 0xFFFF
        d_ticks = (event_time - self._event_time) & 0xFFFF

        if d_revs == 0 or d_ticks == 0:
            if self._last_change is not None and now - self._last_change >= CADENCE_TIMEOUT:
                self.cadence = 0.0
            return self.cadence

        rpm = d_revs * 60 * CRANK_TIME_UNIT / d_ticks
        self._revs, self._event_time, self._last_change = revs, event_time, now
        if rpm <= MAX_CADENCE:
            self.cadence = rpm
        return self.cadence


# Cycling Power Control Point (0x2A66): zero offset ("offset compensation") of a power meter.
OP_START_OFFSET_COMPENSATION = 0x0C
OP_CONTROL_RESPONSE = 0x20
CONTROL_RESULTS = {1: "ok", 2: "not supported", 3: "invalid parameter", 4: "operation failed"}


def start_offset_compensation() -> bytes:
    return bytes([OP_START_OFFSET_COMPENSATION])


@dataclass(frozen=True)
class PowerControlResponse:
    request_op: int
    result: int
    value: int | None  # for offset compensation: the offset, in the meter's own raw units

    @property
    def ok(self) -> bool:
        return self.result == 1

    @property
    def text(self) -> str:
        return CONTROL_RESULTS.get(self.result, f"result {self.result:#04x}")


def parse_power_control_response(data: bytes | bytearray) -> PowerControlResponse:
    if len(data) < 3 or data[0] != OP_CONTROL_RESPONSE:
        raise ValueError(f"not a cycling power control response: {bytes(data).hex()}")
    value = struct.unpack_from("<h", data, 3)[0] if len(data) >= 5 else None
    return PowerControlResponse(request_op=data[1], result=data[2], value=value)
