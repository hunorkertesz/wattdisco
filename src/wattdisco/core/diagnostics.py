"""The app's log file and the diagnostics report a rider can copy into a bug report.

The log lives in `<data folder>/logs/wattdisco.log` (at most 1 MB, with 3 older files) and stays on the
rider's computer. It records what helps find a problem: app start, devices found, connecting, lost and
back, trainer commands that failed, rides started and ended, uploads that failed. It never records
sensor values, tokens or anything typed in. The report adds the app and system versions and the current
device list, and is only copied when the rider presses "Copy diagnostics"; nothing is sent anywhere.
"""

import logging
import logging.handlers
import platform
import re
import sys
from collections import deque
from pathlib import Path

from wattdisco import __version__

LOG_NAME = "wattdisco.log"
MAX_BYTES = 1_000_000
BACKUPS = 3
REPORT_LINES = 300
CONSOLE_NAME = "wattdisco.log"  # packaging/launch.py: the installed app's output, new at each start
CONSOLE_LINES = 80
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Anything that looks like a secret is blanked before it reaches the file.
_SECRETS = re.compile(r"(?i)(access_token|refresh_token|client_secret|code|token|authorization|bearer)"
                      r"([\"']?\s*[:=]\s*[\"']?|\s+)([A-Za-z0-9._\-]{8,})")


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = _SECRETS.sub(lambda m: f"{m.group(1)}{m.group(2)}…", message)
        if clean != message:
            record.msg, record.args = clean, None
        return True


def log_path(data_dir: Path) -> Path:
    return Path(data_dir) / "logs" / LOG_NAME


def setup_logging(data_dir: Path) -> Path:
    """Send the app's log to the rotating file in the data folder (once per process)."""
    path = log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("wattdisco")
    if not any(getattr(h, "_wattdisco", False) for h in logger.handlers):
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler._wattdisco = True
        handler.setFormatter(logging.Formatter(FORMAT))
        handler.addFilter(_Redact())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        previous = sys.excepthook

        def crash(kind, value, tb):  # an uncaught error ends up in the log too
            logger.critical("uncaught error", exc_info=(kind, value, tb))
            previous(kind, value, tb)

        sys.excepthook = crash
    logger.info("Watt Disco %s starting on %s, Python %s", __version__, platform.platform(), platform.python_version())
    return path


def tail(path: Path, lines: int = REPORT_LINES) -> list[str]:
    """The last lines of a log (also from the previous rotated file when the current one is short)."""
    files = [path.with_name(f"{path.name}.1"), path]
    out: deque[str] = deque(maxlen=lines)
    for f in files:
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                out.extend(line.rstrip("\n") for line in fh)
        except OSError:
            continue
    return list(out)


def _home(text: str) -> str:
    home = str(Path.home())
    return text.replace(home, "~") if home and home != "/" else text


def report(data_dir: Path, *, sim: bool, devices: list[dict], trainer: dict | None, ride: dict,
           settings: dict, strava_connected: bool, rides: int) -> str:
    """Plain text for a bug report: versions, devices, the ride state and the end of the log."""
    frozen = "installed app" if getattr(sys, "frozen", False) else "from source"
    lines = [
        "Watt Disco diagnostics",
        f"App: {__version__} ({frozen}{', simulated sensors' if sim else ''})",
        f"System: {platform.platform()} · {platform.machine()} · Python {platform.python_version()}",
        f"Data folder: {_home(str(data_dir))}",
        f"Settings: FTP {settings.get('ftp')} W · units {settings.get('units')} · auto-pause "
        f"{'on' if settings.get('auto_pause') else 'off'} · sound {'on' if settings.get('sound') else 'off'}",
        f"Strava: {'connected' if strava_connected else 'not connected'} · rides saved: {rides}",
        f"Ride: {'running' if ride.get('active') else 'none running'}{' (paused)' if ride.get('paused') else ''}",
        "",
        "Devices:",
    ]
    if not devices:
        lines.append("  none found")
    for d in devices:
        extra = [f"battery {d['battery']} %" if d.get("battery") is not None else None,
                 f"signal {d['rssi']} dBm" if d.get("rssi") is not None else None,
                 f"transport {d.get('transport')}" if d.get("transport") else None]
        roles = ", ".join(sorted(d.get("roles") or [])) or "no role"
        lines.append(f"  {d.get('name')} · {d.get('state')} · {roles} · can {', '.join(sorted(d.get('capabilities') or []))}"
                     + "".join(f" · {e}" for e in extra if e))
    if trainer:
        lines.append(f"Trainer: {'ok' if trainer.get('ok') else 'problem'} · mode {trainer.get('mode')} · "
                     f"{trainer.get('message') or ''}".rstrip(" ·"))
    lines += ["", f"Log (last {REPORT_LINES} lines):"]
    lines += [_home(line) for line in tail(log_path(data_dir))] or ["  (empty)"]
    console = tail(Path(data_dir) / CONSOLE_NAME, CONSOLE_LINES)  # the installed app's output since its last start
    if console:
        lines += ["", f"App output since the last start (last {CONSOLE_LINES} lines):"] + [_home(line) for line in console]
    return "\n".join(lines) + "\n"
