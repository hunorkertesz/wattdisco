"""Workout player: position in the workout, current target, and trainer commands.

Pure logic. Time comes in as `now` (ride clock seconds), so tests never wait.
Workout time can run ahead of ride time when a phase is skipped.

ERG sends a new phase's target at once, so the trainer is on target as the
phase starts. With `ease` (the rider's choice on the ready screen) a big change
is eased in over a few seconds instead. When cadence drops below LOW_CADENCE the
trainer lets go (no "death spiral" where resistance locks the cranks). It takes
hold again, always eased in from the rider's power, once cadence is back up.
"""

import math
from dataclasses import asdict, dataclass

from wattdisco.models import Phase, TrainerCommand, Workout

MODES = ("erg", "free")
REST_KINDS = {"warmup", "cooldown", "recovery"}
ERG_MIN_INTERVAL = 1.0  # seconds between small ERG target updates (ramps)
ERG_JUMP = 10  # W; a change this big is sent at once (or eased in, with `ease`)
ERG_EASE_S = 3.0  # seconds to ease into a new target
ERG_EASE_STEP = 0.5  # seconds between trainer updates while easing
LOW_CADENCE = 50  # rpm; below this for LOW_CADENCE_S, ERG lets go
LOW_CADENCE_S = 2.0
RESUME_CADENCE = 60  # rpm; above this for RESUME_S, ERG takes hold again
RESUME_S = 2.0
RAMP_FAIL_S = 4.0  # ramp test: seconds below LOW_CADENCE that end the test
ADJUST_STEP = 5  # W; one press of + or − on the current interval
MIN_TARGET = 20  # W; an adjusted target never goes below this


@dataclass(frozen=True)
class Position:
    elapsed: float  # workout seconds
    total: int
    done: bool
    index: int | None
    kind: str | None
    target: int | None
    cadence: int | None
    seconds_left: float
    next: Phase | None
    interval: tuple[int, int] | None  # (n, of) counting the work phases
    paused: bool = False
    adjust: int = 0  # W added to this phase by the rider

    def to_dict(self) -> dict:
        d = asdict(self)
        d["interval"] = None if self.interval is None else {"n": self.interval[0], "of": self.interval[1]}
        return d


class WorkoutPlayer:
    def __init__(self, workout: Workout, mode: str = "erg", start: float = 0.0, ease: bool = False) -> None:
        self.workout = workout
        self.ease = ease  # ease big target changes in over ERG_EASE_S instead of a step
        self.set_mode(mode)
        self._offset = -start  # workout time minus clock time; `start` is when the workout began
        self._paused_at: float | None = None
        self._sent: TrainerCommand | None = None
        self._sent_at = -math.inf
        self._last_target: int | None = None  # workout target at the previous command
        self._ease: tuple[float, float] | None = None  # (from watts, start time) while easing
        self._low_since: float | None = None  # cadence below LOW_CADENCE since
        self._high_since: float | None = None  # cadence at or above RESUME_CADENCE since
        self.released = False  # ERG let go because of low cadence
        self.test_ended = False  # ramp test: the rider could not hold the step
        self.adjustments: dict[int, int] = {}  # phase index -> W the rider added (or took off)

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {list(MODES)}")
        self.mode = mode

    def set_workout(self, workout: Workout) -> None:
        """Swap the workout (e.g. rescaled to a new FTP) and keep the current position."""
        self.workout = workout

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    def pause(self, now: float) -> None:
        """Freeze workout time; the trainer is released until resume."""
        if self._paused_at is None:
            self._paused_at = now

    def resume(self, now: float) -> None:
        if self._paused_at is not None:
            self._offset -= now - self._paused_at  # the pause does not count as workout time
            self._paused_at = None

    def elapsed(self, now: float) -> float:
        if self._paused_at is not None:
            now = self._paused_at
        return max(0.0, now + self._offset)

    def position(self, now: float) -> Position:
        w = self.workout
        elapsed = self.elapsed(now)
        work = [i for i, p in enumerate(w.phases) if p.kind not in REST_KINDS]
        found = w.phase_at(elapsed)
        if found is None:
            return Position(elapsed, w.seconds, True, None, None, None, None, 0.0, None, None, self.paused)
        i, phase, into = found
        adjust = self.adjustments.get(i, 0)
        return Position(
            elapsed=elapsed,
            total=w.seconds,
            done=False,
            index=i,
            kind=phase.kind,
            target=max(MIN_TARGET, phase.target_at(into) + adjust) if adjust else phase.target_at(into),
            cadence=phase.cadence,
            seconds_left=phase.seconds - into,
            next=w.phases[i + 1] if i + 1 < len(w.phases) else None,
            interval=(sum(1 for j in work if j <= i), len(work)) if work else None,
            paused=self.paused,
            adjust=adjust,
        )

    def adjust_phase(self, now: float, steps: int) -> int:
        """Make the current phase harder (+) or easier (−) by ADJUST_STEP per step.

        Only this phase changes; the rest of the workout stays as planned. Returns the
        phase's total adjustment in W.
        """
        pos = self.position(now)
        if pos.done:
            return 0
        phase = self.workout.phases[pos.index]
        lowest = min(phase.watts, phase.watts if phase.watts_end is None else phase.watts_end)
        adjust = max(MIN_TARGET - lowest, self.adjustments.get(pos.index, 0) + steps * ADJUST_STEP)
        if adjust:
            self.adjustments[pos.index] = adjust
        else:
            self.adjustments.pop(pos.index, None)
        return adjust

    def skip_phase(self, now: float) -> None:
        pos = self.position(now)
        if not pos.done:
            self._offset += pos.seconds_left

    @property
    def erg_state(self) -> str:
        """off (free ride or paused), released (low cadence), easing, or holding."""
        if self.mode == "free" or self.paused:
            return "off"
        if self.released:
            return "released"
        return "easing" if self._ease else "holding"

    def _watch_cadence(self, now: float, cadence: float | None) -> None:
        if cadence is None:  # no cadence data: never let go on a guess
            self._low_since = self._high_since = None
            return
        self._low_since = (self._low_since if self._low_since is not None else now) if cadence < LOW_CADENCE else None
        self._high_since = (self._high_since if self._high_since is not None else now) if cadence >= RESUME_CADENCE else None

    def _end_ramp_test(self, now: float) -> None:
        """Ramp test: when the rider cannot hold the step, go straight to the cool-down."""
        if self.workout.test != "ramp" or self.test_ended or self.paused or self._low_since is None:
            return
        pos = self.position(now)
        if pos.done or pos.kind != "test" or now - self._low_since < RAMP_FAIL_S:
            return
        self.test_ended = True
        while not pos.done and pos.kind == "test":
            self.skip_phase(now)
            pos = self.position(now)

    def command(self, now: float, cadence: float | None = None, power: float | None = None) -> TrainerCommand | None:
        """The trainer command to send now, or None if the trainer is already right.

        `cadence` and `power` are the rider's current values (None when unknown).
        """
        self._watch_cadence(now, cadence)
        self._end_ramp_test(now)
        if self.mode == "free" or self.paused:  # paused: let the rider spin down freely
            self.released = False
            self._ease = None
            return self._send(TrainerCommand("free"), now) if self._sent != TrainerCommand("free") else None

        pos = self.position(now)
        if pos.done:  # hold the last target until the rider switches mode
            last = self.workout.phases[-1]
            target = last.watts_end if last.watts_end is not None else last.watts
        else:
            target = pos.target
        jumped = self._last_target is not None and abs(target - self._last_target) >= ERG_JUMP
        self._last_target = target

        if self.released:
            if self._high_since is None or now - self._high_since < RESUME_S:
                return self._send(TrainerCommand("free"), now) if self._sent != TrainerCommand("free") else None
            self.released = False  # pedalling again: ease back in from what the rider pushes now
            self._ease = (float(power if power else target / 2), now)
        elif self._low_since is not None and now - self._low_since >= LOW_CADENCE_S:
            self.released = True
            self._ease = None
            return self._send(TrainerCommand("free"), now)

        sent = self._sent
        if (sent is None or sent.mode != "erg") and self._ease is None:
            return self._send(TrainerCommand("erg", target), now)
        if jumped and self.ease and sent is not None and sent.mode == "erg":
            self._ease = (float(sent.watts), now)
        if self._ease is not None:
            start_watts, start = self._ease
            frac = min(1.0, (now - start) / ERG_EASE_S)
            watts = round(start_watts + (target - start_watts) * frac)
            if frac >= 1.0:
                self._ease = None
            if sent is not None and sent.mode == "erg" and watts == sent.watts:
                return None
            if self._ease is None or sent is None or sent.mode != "erg" or now - self._sent_at >= ERG_EASE_STEP:
                return self._send(TrainerCommand("erg", watts), now)
            return None
        if target == sent.watts:
            return None
        if abs(target - sent.watts) >= ERG_JUMP or now - self._sent_at >= ERG_MIN_INTERVAL:
            return self._send(TrainerCommand("erg", target), now)
        return None

    @property
    def last_command(self) -> TrainerCommand | None:
        return self._sent

    def _send(self, cmd: TrainerCommand, now: float) -> TrainerCommand:
        self._sent = cmd
        self._sent_at = now
        return cmd


class OpenRidePlayer(WorkoutPlayer):
    """"Just ride": no workout and no target. The trainer rides like a flat road (gears set the effort), the
    clock counts up, and the ride goes on until the rider ends it (at the latest after the one long phase)."""

    def __init__(self, workout: Workout, start: float = 0.0) -> None:
        super().__init__(workout, mode="free", start=start)

    def set_mode(self, mode: str) -> None:
        if mode != "free":
            raise ValueError("just riding has no ERG target")
        self.mode = mode

    def position(self, now: float) -> Position:
        elapsed = self.elapsed(now)
        limit = self.workout.seconds
        return Position(elapsed=elapsed, total=round(elapsed), done=elapsed >= limit, index=None if elapsed >= limit else 0,
                        kind="ride", target=None, cadence=None, seconds_left=max(0.0, limit - elapsed), next=None,
                        interval=None, paused=self.paused)

    def adjust_phase(self, now: float, steps: int) -> int:
        return 0  # nothing to make harder: change gear

    def skip_phase(self, now: float) -> None:
        return None
