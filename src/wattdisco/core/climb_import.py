"""Read a climb from a GPX file: the road's distance and gradients, never where it is.

A GPX track (or route) is a list of points with latitude, longitude and height. The distance between
points is measured on the earth's surface, the heights are spaced every STEP_M along the road and
smoothed over SMOOTH_M (GPS heights are noisy and would give fake 25 % spikes), and the result becomes
segments of SEGMENT_M with their gradient, kept between MIN_GRADE and MAX_GRADE. Only those segments
are saved: the GPS positions are dropped, so the climb says nothing about where the rider rides.
"""

import math
import xml.etree.ElementTree as ET
from pathlib import PurePath

from wattdisco.core.workouts import validate

MAX_BYTES = 20_000_000
EARTH_M = 6_371_000.0
STEP_M = 10.0
SMOOTH_M = 100.0
SEGMENT_M = 100.0
MIN_GRADE = -10.0
MAX_GRADE = 20.0
MIN_LENGTH_M = 300.0
MAX_LENGTH_M = 150_000.0


class ClimbFileError(ValueError):
    """The file can't be read as a climb; the message says why, in plain words."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]  # GPX 1.0 and 1.1 use different namespaces: compare names only


def _points(text: str) -> tuple[str | None, list[tuple[float, float, float | None]]]:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:  # a GPX file never needs them; refuse entity tricks
        raise ClimbFileError("that file has XML features a GPX file doesn't use")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ClimbFileError("that file isn't valid GPX") from e
    if _local(root.tag) != "gpx":
        raise ClimbFileError("that file isn't a GPX file")
    name = None
    for node in root.iter():
        if _local(node.tag) == "name" and node.text and node.text.strip():
            name = node.text.strip()
            break
    points = []
    for kind in ("trkpt", "rtept"):  # a track if there is one, else a route
        for node in root.iter():
            if _local(node.tag) != kind:
                continue
            try:
                lat, lon = float(node.get("lat")), float(node.get("lon"))
            except (TypeError, ValueError):
                continue
            ele = next((c.text for c in node if _local(c.tag) == "ele"), None)
            try:
                height = None if ele is None else float(ele)
            except ValueError:
                height = None
            points.append((lat, lon, height))
        if points:
            break
    return name, points


def _metres(a: tuple[float, float, float | None], b: tuple[float, float, float | None]) -> float:
    """Distance on the earth's surface (haversine)."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(h)))


def segments_from_points(points: list[tuple[float, float, float | None]]) -> tuple[list[list[float]], list[str]]:
    """[[metres, gradient %], ...] from (lat, lon, height) points, and notes about what was changed."""
    notes = []
    with_height = [p for p in points if p[2] is not None]
    if len(with_height) < 2:
        raise ClimbFileError("that file has no heights, so its gradients are unknown")
    if len(with_height) < len(points):
        notes.append(f"{len(points) - len(with_height)} points without a height were left out")
    dist, heights = [0.0], [with_height[0][2]]
    for prev, p in zip(with_height, with_height[1:]):
        step = _metres(prev, p)
        if step < 0.5:  # the same spot twice (a pause on the device)
            continue
        dist.append(dist[-1] + step)
        heights.append(p[2])
    length = dist[-1]
    if length < MIN_LENGTH_M:
        raise ClimbFileError(f"that route is only {round(length)} m long; a climb needs at least {round(MIN_LENGTH_M)} m")
    if length > MAX_LENGTH_M:
        raise ClimbFileError(f"that route is {length / 1000:.0f} km long; Watt Disco takes climbs up to {MAX_LENGTH_M / 1000:.0f} km")

    # heights every STEP_M along the road
    grid, j = [], 0
    n = int(length // STEP_M)
    for i in range(n + 1):
        d = i * STEP_M
        while j < len(dist) - 2 and dist[j + 1] < d:
            j += 1
        span = dist[j + 1] - dist[j]
        f = 0.0 if span <= 0 else min(1.0, max(0.0, (d - dist[j]) / span))
        grid.append(heights[j] + (heights[j + 1] - heights[j]) * f)
    if length - n * STEP_M > 1:
        grid.append(heights[-1])
    # smooth over SMOOTH_M (a centred moving average)
    half = int(SMOOTH_M / STEP_M / 2)
    smooth = [sum(grid[max(0, i - half):i + half + 1]) / len(grid[max(0, i - half):i + half + 1]) for i in range(len(grid))]
    notes.append(f"heights smoothed over {round(SMOOTH_M)} m to take out GPS noise")

    marks = [min(length, i * STEP_M) for i in range(len(smooth))]
    marks[-1] = length
    per = int(SEGMENT_M / STEP_M)
    segments, steep, downhill = [], 0, 0
    for k in range(0, len(smooth) - 1, per):
        end = min(k + per, len(smooth) - 1)
        metres = marks[end] - marks[k]
        if metres < 1:
            continue
        grade = 100 * (smooth[end] - smooth[k]) / metres
        if grade > MAX_GRADE:
            steep += 1
        if grade < MIN_GRADE:
            downhill += 1
        segments.append([round(metres, 1), round(max(MIN_GRADE, min(MAX_GRADE, grade)), 2)])
    if steep:
        notes.append(f"{steep} stretch{'es' if steep > 1 else ''} steeper than {MAX_GRADE:.0f} % kept at {MAX_GRADE:.0f} %")
    if downhill:
        notes.append(f"{downhill} descent{'s' if downhill > 1 else ''} steeper than {abs(MIN_GRADE):.0f} % kept at {MIN_GRADE:.0f} %")
    notes.append("only the distance and the gradients were kept, not the GPS positions")
    return segments, notes


def import_climb(filename: str, text: str) -> tuple[dict, list[str]]:
    """A validated climb spec (one of the rider's own) from a GPX file's text, and notes about the changes."""
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise ClimbFileError("that file is too big for a climb")
    if PurePath(filename).suffix.lower() != ".gpx":
        raise ClimbFileError("Watt Disco reads climbs from .gpx files")
    name, points = _points(text)
    if not points:
        raise ClimbFileError("no track or route points found in that file")
    segments, notes = segments_from_points(points)
    name = (name or PurePath(filename).stem or "Imported climb").strip()[:80]
    try:
        spec = validate({"name": name, "category": "climb", "climb": {"segments": segments}})
    except ValueError as e:
        raise ClimbFileError(str(e)) from e
    return spec, notes
