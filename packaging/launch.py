"""Entry point of the packaged app (PyInstaller): the same as the `wattdisco` command.

Started from the Finder, the Start menu or a double click, the app has no terminal
(on Windows no stdout at all), so its output goes to wattdisco.log in the data folder.
That log is also what to attach to a bug report.
"""

import multiprocessing
import sys


def _output_to_log() -> None:
    if sys.stdout is not None and sys.stdout.isatty():
        return  # started from a terminal: print there
    from wattdisco.config import DATA_DIR

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = open(DATA_DIR / "wattdisco.log", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        return
    sys.stdout = sys.stderr = log


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _output_to_log()
    from wattdisco.__main__ import main

    sys.exit(main())
