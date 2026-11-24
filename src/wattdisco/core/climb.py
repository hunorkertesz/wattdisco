"""Climbs: ride up a road whose gradient sets the trainer's resistance.

A climb is a list of segments (metres, gradient %). Every ride second the rider's
power moves them along the road (`core/speed.Road`, with the real gradient and
the rider's real weight), and the trainer is told the gradient just ahead, in
its simulation mode (FTMS "indoor bike simulation"). The rider chooses power,
cadence and gears, as on a real road; nothing holds watts like ERG.

Pure logic: time comes in as `now`, distance from the road, so tests never wait.
"""

import math
from collections import deque
from dataclasses import dataclass
from functools import lru_cache

from wattdisco.core.speed import BIKE_KG, Road, steady_speed
from wattdisco.core.workout import Position, WorkoutPlayer
from wattdisco.models import TrainerCommand, Workout

STEP_M = 10.0  # profile resolution
BLEND_M = 60.0  # a change of gradient between segments is spread over this distance, like a real road
GRADE_STEP = 0.2  # %; a smaller change is not sent to the trainer
MIN_INTERVAL = 1.0  # s between gradient updates
LOOKAHEAD_S = 1.0  # trainers take a moment to change resistance: send the gradient just ahead
MAX_TRAINER_GRADE = 20.0  # %; steeper than most trainers can simulate at climbing speed
DOWNHILL_SHARE = 0.5  # a trainer can't push the pedals: descents are sent at half their gradient
MIN_TRAINER_GRADE = -5.0
DIFFICULTIES = (0.25, 0.5, 0.75, 1.0)  # share of the uphill gradient the trainer simulates
PACE = 0.75  # share of FTP behind the estimated time on the Ready screen
ESTIMATE_KG = 75.0
VAM_WINDOW_S = 60  # live VAM: metres climbed over the last minute of riding
POWER_WINDOW_S = 30  # live W/kg and the time to the top use the last 30 s of power
MIN_LIVE_S = 10  # seconds of riding before the live numbers show
MIN_PROJECT_W = 30  # below this the rider is coasting: no time to the top


@dataclass(frozen=True)
class Profile:
    """A climb sampled every STEP_M: gradient and height at each point."""

    length: float
    grades: tuple[float, ...]  # % at i * STEP_M
    altitudes: tuple[float, ...]  # m above the start
    ascent: tuple[float, ...]  # metres climbed up to each point (descents don't count)

    @classmethod
    def from_segments(cls, segments: list) -> "Profile":
        parts = [(float(m), float(g)) for m, g in segments]
        if not parts or any(m <= 0 for m, _ in parts):
            raise ValueError("a climb needs segments of positive length")
        length = sum(m for m, _ in parts)
        bounds, start = [], 0.0
        for m, g in parts:
            bounds.append((start, start + m, g))
            start += m

        def raw(d: float) -> float:
            for lo, hi, g in bounds:
                if d < hi:
                    return g
            return bounds[-1][2]

        def blended(d: float) -> float:  # average over BLEND_M: gentle changes, the same height in the end
            n = 6
            return sum(raw(min(length, max(0.0, d - BLEND_M / 2 + BLEND_M * (k + 0.5) / n))) for k in range(n)) / n

        count = int(math.ceil(length / STEP_M)) + 1
        grades = [blended(min(length, i * STEP_M)) for i in range(count)]
        altitudes, ascent = [0.0], [0.0]
        for i in range(1, count):
            d = min(length, i * STEP_M) - min(length, (i - 1) * STEP_M)
            rise = d * (grades[i - 1] + grades[i]) / 200
            altitudes.append(altitudes[-1] + rise)
            ascent.append(ascent[-1] + max(0.0, rise))
        return cls(length, tuple(grades), tuple(altitudes), tuple(ascent))

    def _at(self, values: tuple[float, ...], d: float) -> float:
        if d <= 0:
            return values[0]
        if d >= self.length:
            return values[-1]
        i = int(d // STEP_M)
        f = d / STEP_M - i
        return values[i] + (values[min(i + 1, len(values) - 1)] - values[i]) * f

    def grade_at(self, d: float) -> float:
        """Gradient in % at `d` metres; flat after the top."""
        return 0.0 if d >= self.length else self._at(self.grades, d)

    def altitude_at(self, d: float) -> float:
        return self._at(self.altitudes, d)

    def ascent_at(self, d: float) -> float:
        return self._at(self.ascent, d)

    @property
    def total_ascent(self) -> float:
        return self.ascent[-1]

    @property
    def avg_grade(self) -> float:
        return 100 * self.altitudes[-1] / self.length

    def points(self, every: float = 50.0) -> list[list[float]]:
        """[distance, altitude, grade] every `every` m, for the climb chart."""
        n = int(math.ceil(self.length / every))
        return [[round(min(self.length, i * every), 1), round(self.altitude_at(min(self.length, i * every)), 1),
                 round(self._at(self.grades, min(self.length, i * every)), 1)] for i in range(n + 1)]


def trainer_grade(grade: float, difficulty: float = 1.0) -> float:
    """What the trainer is asked to simulate for a road gradient (uphill scaled, downhill halved and capped)."""
    if grade >= 0:
        g = min(MAX_TRAINER_GRADE, grade * difficulty)
    else:
        g = max(MIN_TRAINER_GRADE, grade * DOWNHILL_SHARE)
    return round(g, 1)


def estimate_seconds(profile: Profile, watts: float, rider_kg: float = ESTIMATE_KG) -> int:
    """Time to the top at steady watts (for the Ready screen; the ride itself uses the real power)."""
    total = 0.0
    for i in range(1, len(profile.grades)):
        d = min(profile.length, i * STEP_M) - min(profile.length, (i - 1) * STEP_M)
        grade = (profile.grades[i - 1] + profile.grades[i]) / 2
        total += d / max(0.5, steady_speed(watts, rider_kg, BIKE_KG, grade))
    return max(60, round(total))


@lru_cache(maxsize=4096)
def _speed(watts: int, rider_kg: float, grade: float) -> float:
    return max(0.5, steady_speed(watts, rider_kg, BIKE_KG, grade))


def seconds_to_top(profile: Profile, distance: float, watts: float, rider_kg: float = ESTIMATE_KG) -> int:
    """Time from `distance` to the top at steady watts, on the real gradients ahead."""
    w = int(round(watts))
    total, d = 0.0, max(0.0, distance)
    while d < profile.length - 1e-6:
        end = min(profile.length, (math.floor(d / STEP_M) + 1) * STEP_M)
        grade = round(profile.grade_at((d + end) / 2), 1)  # 0.1 % steps: the speeds are cached
        total += (end - d) / _speed(w, rider_kg, grade)
        d = end
    return round(total)


def describe(climb: dict) -> str:
    """e.g. '6.0 KM · 7.0 % AVERAGE · 421 M UP'."""
    profile = Profile.from_segments(climb["segments"])
    return f"{profile.length / 1000:.1f} KM · {profile.avg_grade:.1f} % AVERAGE · {round(profile.total_ascent)} M UP"


class ClimbPlayer(WorkoutPlayer):
    """A ride up a climb: position by distance, the trainer told the gradient.

    It keeps the WorkoutPlayer's clock (pause, resume, elapsed time). The ride is
    done at the top; the trainer then goes back to a flat road.
    """

    def __init__(self, workout: Workout, start: float = 0.0, rider_kg: float = ESTIMATE_KG,
                 difficulty: float = 1.0, distance: float = 0.0) -> None:
        if not workout.climb:
            raise ValueError("this workout is not a climb")
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"difficulty must be one of {list(DIFFICULTIES)}")
        self.profile = Profile.from_segments(workout.climb["segments"])
        self.difficulty = difficulty
        self.road = Road(rider_kg, grade_at=self.profile.grade_at)
        self.road.distance = max(0.0, distance)
        self.rider_kg = rider_kg
        self._climbed = deque(maxlen=VAM_WINDOW_S + 1)  # metres climbed at each riding second
        self._power = deque(maxlen=POWER_WINDOW_S)
        self._top_in: tuple[tuple[int, int], int] | None = None  # (watts, 10 m step) -> seconds, cached
        super().__init__(workout, mode="climb", start=start)

    def set_mode(self, mode: str) -> None:
        if mode != "climb":
            raise ValueError("a climb is ridden in climb mode")
        self.mode = mode

    @property
    def distance(self) -> float:
        return min(self.road.distance, self.profile.length)

    @property
    def done(self) -> bool:
        return self.road.distance >= self.profile.length

    def position(self, now: float) -> Position:
        elapsed = self.elapsed(now)
        if self.done:
            return Position(elapsed, self.workout.seconds, True, None, None, None, None, 0.0, None, None, self.paused)
        left = self.profile.length - self.road.distance
        speed = self.road.speed
        return Position(
            elapsed=elapsed,
            total=self.workout.seconds,
            done=False,
            index=0,
            kind="climb",
            target=None,  # no target: the rider rides by feel, the road sets the resistance
            cadence=None,
            seconds_left=left / speed if speed > 0.5 else 3600.0,
            next=None,
            interval=None,
            paused=self.paused,
        )

    def adjust_phase(self, now: float, steps: int) -> int:
        return 0  # a climb has no target to adjust

    def skip_phase(self, now: float) -> None:
        return None  # nothing to skip on a road

    @property
    def erg_state(self) -> str:
        return "off"

    def command(self, now: float, cadence: float | None = None, power: float | None = None) -> TrainerCommand | None:
        """The gradient to send now, or None if the trainer is already right (flat while paused or at the top)."""
        if self.paused or self.done:
            want = TrainerCommand("free")
        else:
            ahead = self.road.distance + self.road.speed * LOOKAHEAD_S
            grade = trainer_grade(self.profile.grade_at(ahead), self.difficulty)
            sent = self._sent
            if sent is not None and sent.mode == "sim" and (
                    abs(grade - (sent.grade or 0.0)) < GRADE_STEP or now - self._sent_at < MIN_INTERVAL):
                return None
            want = TrainerCommand("sim", grade=grade)
        if want == self._sent:
            return None
        return self._send(want, now)

    def note(self, power: float | None) -> None:
        """One riding second (after the road moved): feeds the live VAM, W/kg and time to the top."""
        self._climbed.append(self.profile.ascent_at(self.distance))
        self._power.append(max(0.0, power or 0.0))

    def live(self) -> dict:
        """VAM over the last minute, W/kg and time to the top at the last 30 s of power (None until known)."""
        out = {"vam": None, "w_kg": None, "power": None, "top_in": None}
        if len(self._power) < MIN_LIVE_S or self.done:
            return out
        watts = sum(self._power) / len(self._power)
        out["power"] = round(watts)
        out["w_kg"] = round(watts / self.rider_kg, 2)
        span = len(self._climbed) - 1
        out["vam"] = round((self._climbed[-1] - self._climbed[0]) / span * 3600) if span else None
        if watts >= MIN_PROJECT_W:
            key = (int(round(watts / 5) * 5), int(self.distance // STEP_M))
            if self._top_in is None or self._top_in[0] != key:
                self._top_in = (key, seconds_to_top(self.profile, self.distance, key[0], self.rider_kg))
            out["top_in"] = self._top_in[1]
        return out

    def climb_state(self) -> dict:
        d = self.distance
        sent = self._sent
        return {
            "distance": round(d, 1),
            "length": round(self.profile.length, 1),
            "left": round(self.profile.length - d, 1),
            "grade": round(self.profile.grade_at(d), 1),
            "trainer_grade": sent.grade if sent is not None and sent.mode == "sim" else 0.0,
            "difficulty": self.difficulty,
            "speed": round(self.road.speed, 2),  # m/s
            "altitude": round(self.profile.altitude_at(d), 1),
            "climbed": round(self.profile.ascent_at(d), 1),
            "ascent": round(self.profile.total_ascent, 1),
            "top": self.done,
            **self.live(),
        }


# ---------- built-in climbs (made up, so no route data or licences are involved) ----------
def _even(km: float, grades: list[float]) -> list[list[float]]:
    """Segments of equal length covering `km`."""
    m = km * 1000 / len(grades)
    return [[m, g] for g in grades]


BUILTIN_CLIMBS = {
    "climb-steady-6k": ("Steady climb · 6 km at 7 %",
                        _even(6, [6.5, 7, 7.5, 7, 6.5, 7.5, 7, 7, 6.5, 7.5, 7, 7])),
    "climb-alpine-9k": ("Alpine-style climb · 9 km, 5–10 %",
                        _even(9, [5, 6, 7, 8, 9, 8, 6, 4, 3, 5, 8, 9, 10, 9, 8, 7, 6, 5])),
    "climb-short-steep-2k": ("Short and steep · 2 km at 10 %",
                             _even(2, [8, 10, 11, 12, 10, 9, 11, 9])),
    "climb-rolling-8k": ("Rolling hills · 8 km",
                         _even(8, [3, 5, 2, -3, -4, 1, 4, 6, 4, -2, -5, 0, 3, 5, 6, 2])),
}
