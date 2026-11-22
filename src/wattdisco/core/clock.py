"""One clock for the whole ride. `t` everywhere is seconds since the clock started."""

import asyncio
import time


class Clock:
    """Monotonic ride clock. `speed` > 1 runs simulated rides faster than real time."""

    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed
        self._start = time.perf_counter()  # also monotonic, but not in 16 ms steps on Windows

    def now(self) -> float:
        return (time.perf_counter() - self._start) * self.speed

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds) / self.speed)


class ManualClock(Clock):
    """Clock for tests: time only moves when someone sleeps or calls `advance`."""

    def __init__(self, t: float = 0.0) -> None:
        self.speed = 1.0
        self.t = t

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    async def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)
        await asyncio.sleep(0)
