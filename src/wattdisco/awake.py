"""Keep the screen awake while a ride runs, with the system's own tools (nothing to install).

macOS: `caffeinate -d -i -w <pid>` (stops on its own if the app goes away).
Windows: SetThreadExecutionState from kernel32 (released again from the same thread).
Elsewhere: nothing; the page also asks the browser for a screen wake lock where it can.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class KeepAwake:
    """hold() while riding, release() after. Both are safe to call more than once."""

    def __init__(self, platform: str = sys.platform) -> None:
        self.platform = platform
        self._proc: subprocess.Popen | None = None
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def hold(self) -> None:
        if self._held:
            return
        try:
            if self.platform == "darwin":
                self._proc = subprocess.Popen(["/usr/bin/caffeinate", "-d", "-i", "-w", str(os.getpid())],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif self.platform == "win32":
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            self._held = True
        except Exception as exc:  # never let this stop a ride
            log.warning("could not keep the screen awake: %s", exc)

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            if self._proc is not None:
                self._proc.terminate()
                self._proc = None
            elif self.platform == "win32":
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception as exc:
            log.warning("could not release the screen: %s", exc)


class NoKeepAwake(KeepAwake):
    """For tests and the browser-only server: the page's wake lock is enough."""

    def hold(self) -> None:
        self._held = True

    def release(self) -> None:
        self._held = False
