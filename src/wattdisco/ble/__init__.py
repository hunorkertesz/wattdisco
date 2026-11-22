"""Bluetooth LE layer. The only package that imports bleak."""

from bleak.uuids import normalize_uuid_16

CYCLING_POWER_SERVICE = normalize_uuid_16(0x1818)
CYCLING_POWER_MEASUREMENT = normalize_uuid_16(0x2A63)
CYCLING_POWER_CONTROL_POINT = normalize_uuid_16(0x2A66)
HEART_RATE_SERVICE = normalize_uuid_16(0x180D)
HEART_RATE_MEASUREMENT = normalize_uuid_16(0x2A37)
FTMS_SERVICE = normalize_uuid_16(0x1826)
BATTERY_LEVEL = normalize_uuid_16(0x2A19)  # in the battery service (0x180F): one byte, 0-100 %
