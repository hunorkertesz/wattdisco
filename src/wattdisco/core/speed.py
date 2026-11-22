"""Estimated speed on a flat road with no wind, from power and weight.

Force balance: drive force = rolling resistance + air drag, with the bike's
inertia so speed rises and falls smoothly (coasting slows down, it does not stop
at once). Values are typical for a road bike ridden on the hoods.
"""

G = 9.81  # m/s²
RHO = 1.225  # air density, kg/m³ (sea level, 15 °C)
CDA = 0.38  # drag area, m² (typical amateur on the hoods)
CRR = 0.005  # rolling resistance, road tyres on everyday asphalt
DRIVETRAIN = 0.976  # share of crank power that reaches the wheel
BIKE_KG = 8.0
MIN_DRIVE_SPEED = 1.0  # m/s; below this, force = power / 1 m/s (avoids dividing by zero at a standstill)


def resistance(v: float, mass: float) -> float:
    """Force in N needed to hold speed v (m/s) on the flat."""
    return CRR * mass * G + 0.5 * RHO * CDA * v * v


def steady_speed(power: float, rider_kg: float, bike_kg: float = BIKE_KG) -> float:
    """Speed in m/s where the given power exactly holds the bike on the flat."""
    mass = rider_kg + bike_kg
    target = max(0.0, power) * DRIVETRAIN
    lo, hi = 0.0, 30.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if resistance(mid, mass) * mid < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class FlatRoad:
    """A rider on a flat road. Feed power once per second; read speed and distance."""

    SUBSTEPS = 10

    def __init__(self, rider_kg: float, bike_kg: float = BIKE_KG) -> None:
        self.mass = rider_kg + bike_kg
        self.speed = 0.0  # m/s
        self.distance = 0.0  # m

    def step(self, power: float | None, dt: float = 1.0) -> float:
        p = max(0.0, power or 0.0) * DRIVETRAIN  # no data counts as coasting
        h = dt / self.SUBSTEPS
        for _ in range(self.SUBSTEPS):
            drive = p / max(self.speed, MIN_DRIVE_SPEED)
            accel = (drive - resistance(self.speed, self.mass)) / self.mass
            self.speed = max(0.0, self.speed + accel * h)
            self.distance += self.speed * h
        return self.speed


def virtual_ride(power: list[float | None], rider_kg: float) -> tuple[list[float], list[float]]:
    """Speed (m/s) and cumulative distance (m) for each second of a 1 Hz power series."""
    road = FlatRoad(rider_kg)
    speeds, distances = [], []
    for p in power:
        speeds.append(road.step(p))
        distances.append(road.distance)
    return speeds, distances
