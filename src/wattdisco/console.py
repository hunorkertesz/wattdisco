"""Terminal output for `wattdisco scan`: device list and one live line per second."""

from wattdisco.core.clock import Clock
from wattdisco.core.session import DeviceState, Session
from wattdisco.models import Device, Workout


def print_devices(devices: list[Device], rssi: dict[str, int] | None = None) -> None:
    for d in devices:
        signal = f"rssi {rssi[d.id]:4d}" if rssi and d.id in rssi else " " * 9
        print(f"  {d.name:<24} {', '.join(sorted(d.roles)):<28} {signal}  {d.id}")


def _fmt(value: float | None, width: int, unit: str) -> str:
    return f"{'--':>{width}}{unit}" if value is None else f"{value:{width}.0f}{unit}"


def _device_part(d: DeviceState) -> str:
    if d.state != "connected":
        return f"{d.name} {d.state.upper()}"
    if d.no_data:
        return f"{d.name} NO DATA"
    values = []
    if d.watts is not None:
        values.append(f"{d.watts} W")
    if d.bpm is not None:
        values.append(f"{d.bpm} bpm")
    return f"{d.name} {' '.join(values) or '--'}"


def live_line(session: Session, workout: Workout | None = None) -> str:
    s = session.snapshot()
    minutes, seconds = divmod(int(s.t), 60)
    parts = [f"{minutes:3d}:{seconds:02d}"]
    if workout is not None:
        found = workout.phase_at(s.t)
        if found:
            _i, phase, into = found
            parts.append(f"{phase.kind:<9} target {phase.target_at(into):3d} W")
    parts.append(
        f"POWER {_fmt(s.power, 4, ' W')} ({s.smoothing:>3})"
        f"  cad {_fmt(s.cadence, 3, '')}  HR {_fmt(s.hr, 3, '')}"
    )
    line = "  ".join(parts) + "  |  " + "  ".join(_device_part(d) for d in s.devices)
    if s.alerts:
        line += "  !! " + "; ".join(s.alerts)
    return line


async def print_live(
    session: Session, clock: Clock, duration: float | None, workout: Workout | None = None
) -> None:
    if duration is None:
        print("live values, Ctrl+C to stop")
    else:
        print(f"live values for {duration:g} s, Ctrl+C to stop early")
    tick = 1
    while duration is None or tick <= duration:
        # Print just after each sensor second so the sim sample for that second is in.
        await clock.sleep(tick + 0.05 - clock.now())
        print(live_line(session, workout))
        tick += 1
