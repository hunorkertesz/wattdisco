"""Parse the Heart Rate Measurement characteristic (0x2A37)."""

import struct
from dataclasses import dataclass

_FLAG_HR_UINT16 = 0x01
_FLAG_CONTACT_SUPPORTED = 0x04
_FLAG_CONTACT_DETECTED = 0x02
_FLAG_ENERGY = 0x08
_FLAG_RR = 0x10


@dataclass(frozen=True)
class HeartRateMeasurement:
    bpm: int
    contact: bool | None  # None when the strap does not report skin contact
    energy_kj: int | None
    rr: tuple[float, ...]  # seconds


def parse_heart_rate(data: bytes | bytearray) -> HeartRateMeasurement:
    if len(data) < 2:
        raise ValueError(f"heart rate packet too short: {bytes(data).hex()}")
    flags = data[0]
    i = 1
    if flags & _FLAG_HR_UINT16:
        (bpm,) = struct.unpack_from("<H", data, i)
        i += 2
    else:
        bpm = data[i]
        i += 1

    contact = bool(flags & _FLAG_CONTACT_DETECTED) if flags & _FLAG_CONTACT_SUPPORTED else None

    energy = None
    if flags & _FLAG_ENERGY:
        (energy,) = struct.unpack_from("<H", data, i)
        i += 2

    rr: list[float] = []
    if flags & _FLAG_RR:
        while i + 2 <= len(data):
            (raw,) = struct.unpack_from("<H", data, i)
            rr.append(raw / 1024)
            i += 2

    return HeartRateMeasurement(bpm=bpm, contact=contact, energy_kj=energy, rr=tuple(rr))
