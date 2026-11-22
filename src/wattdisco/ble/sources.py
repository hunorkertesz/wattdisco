"""Real sensors over Bluetooth, published on the bus like any other source.

BleManager finds devices and connects or disconnects them one by one; each
connected device runs in its own LiveSensor task with its own reconnect loop.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterable

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from wattdisco.ble import BATTERY_LEVEL, CYCLING_POWER_CONTROL_POINT, CYCLING_POWER_MEASUREMENT, HEART_RATE_MEASUREMENT
from wattdisco.ble.ftms import FtmsController
from wattdisco.ble.heart_rate import parse_heart_rate
from wattdisco.ble.power import (
    OP_START_OFFSET_COMPENSATION,
    CadenceCalculator,
    parse_cycling_power,
    parse_power_control_response,
    start_offset_compensation,
)
from wattdisco.ble.scanner import connect_order, discover
from wattdisco.core.bus import Bus
from wattdisco.core.clock import Clock
from wattdisco.models import Device, HrSample, PowerSample, TrainerCommand, TrainerStatus

log = logging.getLogger(__name__)

RECONNECT_BACKOFF = (1, 2, 5, 10)
CONNECT_STAGGER = 1.0  # seconds between starting connections, straps first
SCAN_SECONDS = 10.0
ZERO_TIMEOUT = 15.0  # a power meter can take a few seconds to zero


class LiveSensor:
    """One device: owns its connection and reconnect loop, publishes parsed samples."""

    def __init__(self, device: Device, ble_device: BLEDevice, bus: Bus, clock: Clock) -> None:
        self.device = device
        self.ble_device = ble_device
        self.bus = bus
        self.clock = clock
        self.cadence = CadenceCalculator()
        self.client = None  # the BleakClient while connected
        # Trainer control: the latest wanted command survives reconnects and is re-applied.
        self._wanted: TrainerCommand | None = None
        self._wake = asyncio.Event()
        if "control" in device.capabilities:
            bus.subscribe("trainer", self._on_trainer_command)

    def _on_trainer_command(self, cmd: TrainerCommand) -> None:
        if "control" not in self.device.roles:
            return  # the rider switched control off for this trainer
        self._wanted = cmd
        self._wake.set()

    async def zero_offset(self) -> int:
        """Ask the power meter to zero itself. Do it with the crank unloaded."""
        client = self.client
        if client is None:
            raise ValueError(f"{self.device.name} is not connected")
        future = asyncio.get_running_loop().create_future()

        def on_indication(_char, data: bytearray) -> None:
            try:
                response = parse_power_control_response(data)
            except ValueError:
                return
            if response.request_op == OP_START_OFFSET_COMPENSATION and not future.done():
                future.set_result(response)

        try:
            await client.start_notify(CYCLING_POWER_CONTROL_POINT, on_indication)
            await client.write_gatt_char(CYCLING_POWER_CONTROL_POINT, start_offset_compensation(), response=True)
            response = await asyncio.wait_for(future, ZERO_TIMEOUT)
        except (BleakError, KeyError) as e:
            raise ValueError(f"{self.device.name} does not support zero offset") from e
        except (TimeoutError, asyncio.TimeoutError) as e:
            raise ValueError(f"{self.device.name} did not answer") from e
        finally:
            with contextlib.suppress(Exception):
                await client.stop_notify(CYCLING_POWER_CONTROL_POINT)
        if not response.ok:
            raise ValueError(f"zero offset failed: {response.text}")
        return response.value if response.value is not None else 0

    def _report(self, ok: bool, cmd: TrainerCommand | None, message: str) -> None:
        self.bus.publish(
            "trainer_status",
            TrainerStatus(
                device_id=self.device.id,
                ok=ok,
                mode=cmd.mode if cmd else None,
                watts=cmd.watts if cmd else None,
                message=message,
            ),
        )

    async def _control(self, client) -> None:
        """Apply trainer commands for as long as this connection lasts."""
        controller = FtmsController(client, self._report)
        try:
            await controller.setup()
        except (BleakError, TimeoutError, ConnectionError, OSError) as e:
            self._report(False, self._wanted, f"no trainer control: {e}")
            return
        applied: TrainerCommand | None = None
        while True:
            self._wake.clear()
            wanted = self._wanted
            if wanted is not None and wanted != applied:
                try:
                    if await controller.apply(wanted):
                        applied = wanted
                except (BleakError, OSError) as e:
                    self._report(False, wanted, str(e))
                    await asyncio.sleep(1.0)  # avoid a tight loop on a failing trainer
                continue
            await self._wake.wait()

    def _set_state(self, state: str) -> None:
        self.device.state = state
        self.bus.publish("device", self.device)

    def _on_power(self, _char, data: bytearray) -> None:
        now = self.clock.now()
        try:
            m = parse_cycling_power(data)
        except ValueError:
            return
        cadence = None
        if m.crank_revs is not None and m.crank_event_time is not None:
            cadence = self.cadence.update(m.crank_revs, m.crank_event_time, now)
        self.bus.publish("power", PowerSample(t=now, device_id=self.device.id, watts=m.watts, cadence=cadence,
                                              balance=m.balance))

    def _on_battery(self, _char, data: bytearray) -> None:
        if data and 0 <= data[0] <= 100 and data[0] != self.device.battery:
            self.device.battery = data[0]
            self.bus.publish("device", self.device)

    async def _battery(self, client) -> None:
        """Battery level, if the device has the battery service (mains-powered trainers don't)."""
        try:
            self._on_battery(None, await client.read_gatt_char(BATTERY_LEVEL))
            await client.start_notify(BATTERY_LEVEL, self._on_battery)
        except (BleakError, ValueError, OSError, TimeoutError) as e:
            log.debug("%s: no battery level (%s)", self.device.name, e)

    def _on_hr(self, _char, data: bytearray) -> None:
        now = self.clock.now()
        try:
            m = parse_heart_rate(data)
        except ValueError:
            return
        self.bus.publish("hr", HrSample(t=now, device_id=self.device.id, bpm=m.bpm, rr=m.rr))

    async def run(self) -> None:
        attempt = 0
        while True:
            disconnected = asyncio.Event()
            try:
                async with BleakClient(
                    self.ble_device, disconnected_callback=lambda _c: disconnected.set()
                ) as client:
                    # Subscribe to everything the device can send: all sensors are recorded,
                    # even those whose role is switched off.
                    if "power" in self.device.capabilities:
                        await client.start_notify(CYCLING_POWER_MEASUREMENT, self._on_power)
                    if "heart_rate" in self.device.capabilities:
                        await client.start_notify(HEART_RATE_MEASUREMENT, self._on_hr)
                    self.client = client
                    self._set_state("connected")
                    await self._battery(client)
                    attempt = 0
                    control = (
                        asyncio.create_task(self._control(client)) if "control" in self.device.capabilities else None
                    )
                    try:
                        await disconnected.wait()
                    finally:
                        self.client = None
                        if control:
                            control.cancel()
                            await asyncio.gather(control, return_exceptions=True)
            except (BleakError, TimeoutError, OSError) as e:
                print(f"connect failed: {self.device.name}: {e}")
            self._set_state("lost")
            delay = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
            attempt += 1
            await asyncio.sleep(delay)


class BleManager:
    """SensorSource for real devices: scan on demand, connect and disconnect one by one.

    `wanted` devices (last used) are connected as soon as a scan finds them.
    `found` skips the first scan (the `scan` command has already scanned).
    """

    def __init__(
        self,
        wanted: Iterable[str] = (),
        scan_seconds: float = SCAN_SECONDS,
        found: list[tuple[Device, BLEDevice, int]] | None = None,
    ) -> None:
        self.scan_seconds = scan_seconds
        self.devices: list[Device] = []
        self.scanning = False
        self._wanted = set(wanted)
        self._ble: dict[str, BLEDevice] = {}
        self._sensors: dict[str, LiveSensor] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._preloaded = found
        self._scan_task: asyncio.Task | None = None
        self.bus: Bus | None = None
        self.clock: Clock | None = None

    def _device(self, device_id: str) -> Device:
        for d in self.devices:
            if d.id == device_id:
                return d
        raise KeyError(f"unknown device {device_id}")

    def _publish(self, device: Device) -> None:
        if self.bus is not None:
            self.bus.publish("device", device)

    async def run(self, bus: Bus, clock: Clock) -> None:
        self.bus, self.clock = bus, clock
        try:
            if self._preloaded is not None:
                await self._add_found(self._preloaded)
            else:
                self.scan()
            await asyncio.Event().wait()  # until cancelled
        finally:
            tasks = list(self._tasks.values()) + ([self._scan_task] if self._scan_task else [])
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def scan(self) -> None:
        if not self.scanning:
            self.scanning = True
            self._scan_task = asyncio.create_task(self._scan())

    async def _scan(self) -> None:
        try:
            found = await discover(self.scan_seconds)
        except (BleakError, OSError) as e:
            log.warning("Bluetooth scan failed: %s", e)
            found = []
        finally:
            self.scanning = False
        await self._add_found(found)

    async def _add_found(self, found: list[tuple[Device, BLEDevice, int]]) -> None:
        for device, ble_device, rssi in found:
            self._ble[device.id] = ble_device
            try:
                known = self._device(device.id)
                known.rssi = rssi
            except KeyError:
                device.rssi = rssi
                self.devices.append(device)
                self._publish(device)
        # Reconnect the last used devices: straps first, trainers last (few KICKR connection slots).
        for device in sorted(self.devices, key=connect_order):
            if device.id in self._wanted and device.id not in self._tasks:
                self.connect(device.id)
                await asyncio.sleep(CONNECT_STAGGER)

    def connect(self, device_id: str) -> None:
        device = self._device(device_id)
        self._wanted.add(device_id)
        task = self._tasks.get(device_id)
        if task and not task.done():
            return
        device.state = "connecting"
        self._publish(device)
        if device_id not in self._sensors:
            self._sensors[device_id] = LiveSensor(device, self._ble[device_id], self.bus, self.clock)
        self._tasks[device_id] = asyncio.create_task(self._sensors[device_id].run())

    def disconnect(self, device_id: str) -> None:
        device = self._device(device_id)
        self._wanted.discard(device_id)
        task = self._tasks.pop(device_id, None)
        if task:
            task.cancel()
        device.state = "disconnected"
        self._publish(device)

    async def zero_offset(self, device_id: str) -> int:
        device = self._device(device_id)
        if "power" not in device.capabilities or device_id not in self._sensors:
            raise ValueError(f"{device.name} cannot be zeroed")
        return await self._sensors[device_id].zero_offset()
