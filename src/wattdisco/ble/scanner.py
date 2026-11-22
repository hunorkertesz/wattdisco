"""Discover power, heart rate and trainer devices and work out their roles."""

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from wattdisco.ble import CYCLING_POWER_SERVICE, FTMS_SERVICE, HEART_RATE_SERVICE
from wattdisco.models import Device


def roles_from_services(service_uuids: list[str]) -> set[str]:
    uuids = {u.lower() for u in service_uuids}
    roles: set[str] = set()
    if CYCLING_POWER_SERVICE in uuids:
        roles |= {"power", "cadence"}
    if HEART_RATE_SERVICE in uuids:
        roles.add("heart_rate")
    if FTMS_SERVICE in uuids:
        roles.add("control")
    return roles


def connect_order(device: Device) -> int:
    """Straps first, then power meters, trainers last (a KICKR has few connection slots)."""
    if "heart_rate" in device.roles:
        return 0
    if "control" not in device.roles:
        return 1
    return 2


async def discover(scan_seconds: float) -> list[tuple[Device, BLEDevice, int]]:
    found: dict[str, tuple[BLEDevice, AdvertisementData]] = await BleakScanner.discover(
        timeout=scan_seconds, return_adv=True
    )
    result = []
    for ble_device, adv in found.values():
        roles = roles_from_services(adv.service_uuids)
        if not roles:
            continue
        name = adv.local_name or ble_device.name or ble_device.address
        result.append((Device(id=ble_device.address, name=name, roles=roles), ble_device, adv.rssi))
    result.sort(key=lambda r: (connect_order(r[0]), -r[2]))
    return result
