"""Rolling averages for display. The recorded data always stays raw."""

from collections import deque

SMOOTHING_WINDOWS = {"raw": 0.0, "3s": 3.0, "10s": 10.0}


class Smoother:
    """Keeps the last `max_window` seconds of one value so any window can be read."""

    def __init__(self, max_window: float = max(SMOOTHING_WINDOWS.values())) -> None:
        self.max_window = max_window
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, t: float, value: float) -> None:
        self._samples.append((t, value))
        while self._samples[0][0] <= t - self.max_window:
            self._samples.popleft()

    def value(self, window: float) -> float | None:
        """Mean of the samples newer than `window` seconds before the latest one."""
        if not self._samples:
            return None
        latest_t, latest = self._samples[-1]
        if window <= 0:
            return latest
        values = [v for t, v in self._samples if t > latest_t - window]
        return sum(values) / len(values)
