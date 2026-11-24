"""Estimated speed on a road with no wind, from power and weight: flat, or a climb's gradient.

Force balance: drive force = gravity on the slope + rolling resistance + air drag,
with the bike's inertia so speed rises and falls smoothly (coasting slows down, it
does not stop at once; downhill it speeds up). Values are typical for a road bike
ridden on the hoods.
"""

import math
from collections.abc import Callable

G = 9.81  # m/s²
RHO = 1.225  # air density, kg/m³ (sea level, 15 °C)
CDA = 0.38  # drag area, m² (typical amateur on the hoods)
CRR = 0.005  # rolling resistance, road tyres on everyday asphalt
DRIVETRAIN = 0.976  # share of crank power that reaches the wheel
BIKE_KG = 8.0
MIN_DRIVE_SPEED = 1.0  # m/s; below this, force = power / 1 m/s (avoids dividing by zero at a standstill)


def resistance(v: float, mass: float, grade: float = 0.0) -> float:
    """Force in N needed to hold speed v (m/s) on a road of `grade` % (negative downhill)."""
    angle = math.atan(grade / 100)
    return mass * G * (math.sin(angle) + CRR * math.cos(angle)) + 0.5 * RHO * CDA * v * v


def steady_speed(power: float, rider_kg: float, bike_kg: float = BIKE_KG, grade: float = 0.0) -> float:
    """Speed in m/s where the given power exactly holds the bike on the flat (or on `grade` %)."""
    mass = rider_kg + bike_kg
    target = max(0.0, power) * DRIVETRAIN
    lo, hi = 0.0, 30.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if resistance(mid, mass, grade) * mid < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class Road:
    """A rider on a road. Feed power once per second; read speed and distance.

    `grade_at(distance)` gives the gradient in % at a point (None: flat everywhere).
    """

    SUBSTEPS = 10

    def __init__(self, rider_kg: float, bike_kg: float = BIKE_KG,
                 grade_at: Callable[[float], float] | None = None) -> None:
        self.mass = rider_kg + bike_kg
        self.grade_at = grade_at
        self.speed = 0.0  # m/s
        self.distance = 0.0  # m

    def step(self, power: float | None, dt: float = 1.0) -> float:
        p = max(0.0, power or 0.0) * DRIVETRAIN  # no data counts as coasting
        h = dt / self.SUBSTEPS
        for _ in range(self.SUBSTEPS):
            grade = self.grade_at(self.distance) if self.grade_at else 0.0
            drive = p / max(self.speed, MIN_DRIVE_SPEED)
            accel = (drive - resistance(self.speed, self.mass, grade)) / self.mass
            self.speed = max(0.0, self.speed + accel * h)
            self.distance += self.speed * h
        return self.speed


FlatRoad = Road  # a road with no grade_at is flat


def virtual_ride(power: list[float | None], rider_kg: float) -> tuple[list[float], list[float]]:
    """Speed (m/s) and cumulative distance (m) for each second of a 1 Hz power series."""
    road = FlatRoad(rider_kg)
    speeds, distances = [], []
    for p in power:
        speeds.append(road.step(p))
        distances.append(road.distance)
    return speeds, distances
