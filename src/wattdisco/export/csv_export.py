"""The whole ride as CSV: one row per ridden second, the main values and every sensor on its own.

Columns: ride time, workout time, phase, target, mode, the main power / heart rate / cadence
(what the screen showed, raw), estimated speed and distance, then each sensor's own values
averaged into the same second ("<sensor> power (W)", "<sensor> cadence (rpm)", "<sensor> heart rate
(bpm)") and any extra metric (core temperature and the like). Paused time has no rows.
"""

import csv
import io

from wattdisco.core import stats
from wattdisco.core.recorder import RideData


def ride_csv(ride: RideData, summary: dict) -> str:
    ticks = ride.ticks
    span = int(ticks[-1]["t"]) + 1 if ticks else 0
    speed = summary["series"].get("speed") or []
    distance = summary["series"].get("distance") or []

    names: dict[str, str] = {}
    for device_id in [*ride.power, *ride.hr, *ride.cadence]:
        if device_id not in names:
            name = ride.device_name(device_id)
            names[device_id] = name if name not in names.values() else f"{name} {device_id[-4:]}"

    sensor_columns: list[tuple[str, list]] = []  # (header, value per second of ride time)
    for device_id, samples in ride.power.items():
        sensor_columns.append((f"{names[device_id]} power (W)", stats.per_second(samples, span)))
        if device_id in ride.cadence:
            sensor_columns.append((f"{names[device_id]} cadence (rpm)", stats.per_second(ride.cadence[device_id], span)))
        if device_id in ride.balance:
            left = [(t, b) for t, b, _ in ride.balance[device_id]]
            sensor_columns.append((f"{names[device_id]} balance left (%)", stats.per_second(left, span)))
    for device_id, samples in ride.hr.items():
        sensor_columns.append((f"{names[device_id]} heart rate (bpm)", stats.per_second(samples, span)))
    for key, samples in ride.metrics.items():
        sensor_columns.append((key, stats.per_second(samples, span)))

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["time (s)", "workout time (s)", "phase", "target (W)", "mode", "power (W)", "heart rate (bpm)",
                     "cadence (rpm)", "speed est. (km/h)", "distance est. (m)", *(h for h, _ in sensor_columns)])

    def cell(v, digits=1):
        return "" if v is None else round(v, digits) if isinstance(v, float) else v

    for i, t in enumerate(ticks):
        second = int(t["t"]) - 1  # a tick at t covers the second before it
        writer.writerow([
            t["t"], cell(t.get("elapsed")), t.get("kind") or "", cell(t.get("target")), t.get("mode") or "",
            cell(t.get("power")), cell(t.get("hr")), cell(t.get("cadence")),
            cell(speed[i] * 3.6 if i < len(speed) and speed[i] is not None else None, 2),
            cell(distance[i] if i < len(distance) else None),
            *(cell(col[second]) if 0 <= second < len(col) else "" for _, col in sensor_columns),
        ])
    return out.getvalue()
