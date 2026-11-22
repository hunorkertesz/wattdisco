import asyncio

import pytest

from wattdisco.ble import sources
from wattdisco.ble.sources import BleManager
from wattdisco.core.bus import Bus
from wattdisco.core.clock import ManualClock
from wattdisco.models import Device


@pytest.fixture(autouse=True)
def no_bluetooth(monkeypatch):
    """LiveSensor.run without a radio: connect at once, then wait until cancelled."""
    async def fake_run(self):
        self.client = object()
        self._set_state("connected")
        await asyncio.Event().wait()

    monkeypatch.setattr(sources.LiveSensor, "run", fake_run)
    monkeypatch.setattr(sources, "CONNECT_STAGGER", 0)


def found():
    return [
        (Device("kickr", "KICKR", {"power", "cadence", "control"}), object(), -50),
        (Device("pm", "PM", {"power", "cadence"}), object(), -60),
        (Device("hrm", "HRM", {"heart_rate"}), object(), -70),
    ]


def test_reconnects_wanted_devices_only_and_connects_on_demand():
    async def go():
        bus = Bus()
        events = []
        bus.subscribe("device", lambda d: events.append((d.id, d.state)))
        manager = BleManager(wanted=["hrm", "kickr"], found=found())
        task = asyncio.create_task(manager.run(bus, ManualClock()))
        for _ in range(10):
            await asyncio.sleep(0)
        states = {d.id: d.state for d in manager.devices}
        assert states == {"kickr": "connected", "pm": "found", "hrm": "connected"}
        assert [e for e in events if e[1] == "connecting"][0][0] == "hrm"  # straps before trainers

        manager.connect("pm")
        await asyncio.sleep(0)
        assert manager._device("pm").state == "connected"
        manager.disconnect("kickr")
        assert manager._device("kickr").state == "disconnected"
        with pytest.raises(KeyError):
            manager.connect("nope")
        with pytest.raises(ValueError):
            await manager.zero_offset("hrm")  # not a power meter
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())


def test_battery_level_updates_the_device():
    from wattdisco.ble.sources import LiveSensor
    from wattdisco.core.bus import Bus
    from wattdisco.core.clock import Clock
    from wattdisco.models import Device

    bus, seen = Bus(), []
    bus.subscribe("device", lambda d: seen.append(d.battery))
    sensor = LiveSensor(Device(id="pm", name="Assioma", roles={"power"}), None, bus, Clock())
    sensor._on_battery(None, bytearray([57]))
    sensor._on_battery(None, bytearray([57]))  # unchanged: no new message
    sensor._on_battery(None, bytearray([250]))  # not a percentage: ignored
    assert sensor.device.battery == 57 and seen == [57]
