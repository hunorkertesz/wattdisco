"""Extra metrics the workout screen can show as tiles.

Sensor metrics arrive as MetricSample on the "metric" topic. Derived metrics
(computed from other values) are added by the server.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricInfo:
    key: str
    label: str
    unit: str
    decimals: int
    role: str | None = None  # device role that provides it; None for derived metrics


CATALOG = {
    m.key: m
    for m in (
        MetricInfo("core_temp", "Core temp", "°C", 2, role="temperature"),
        MetricInfo("skin_temp", "Skin temp", "°C", 1, role="temperature"),
        MetricInfo("heat_strain", "Heat strain", "", 1, role="temperature"),
        MetricInfo("w_per_kg", "W/kg", "", 1),
        MetricInfo("speed", "Speed (est.)", "km/h", 1),
        MetricInfo("distance", "Distance (est.)", "km", 2),
    )
}


@dataclass
class MetricValue:
    key: str
    label: str
    unit: str
    decimals: int
    value: float | None  # None while the source is silent
    device_id: str | None


def metric_value(key: str, value: float | None, device_id: str | None = None) -> MetricValue:
    info = CATALOG[key]
    return MetricValue(info.key, info.label, info.unit, info.decimals, value, device_id)
