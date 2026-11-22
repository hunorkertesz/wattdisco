"""What every sensor source (real Bluetooth or simulated) looks like to the rest of the app."""

from typing import Protocol

from wattdisco.core.bus import Bus
from wattdisco.core.clock import Clock
from wattdisco.models import Device


class SensorSource(Protocol):
    devices: list[Device]  # everything found, connected or not
    scanning: bool

    async def run(self, bus: Bus, clock: Clock) -> None:
        """Publish "device", "power", "hr" and "metric" events until cancelled."""
        ...

    def scan(self) -> None:
        """Look for devices again (returns at once; `scanning` is True meanwhile)."""
        ...

    def connect(self, device_id: str) -> None: ...

    def disconnect(self, device_id: str) -> None: ...

    async def zero_offset(self, device_id: str) -> int:
        """Zero a power meter; returns the offset it reports. Raises ValueError if it cannot."""
        ...
