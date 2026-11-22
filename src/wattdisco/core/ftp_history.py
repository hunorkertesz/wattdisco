"""Every FTP change with its date and source, in DATA_DIR/ftp_history.json.

Sources: "ramp" and "20min" (an FTP test set it), "best20" (the rider took the summary's suggestion
from a ride's best 20 minutes), "manual" (typed on the zones screen, or an undo), "start" (the FTP
the history begins with).

A rider who had the app before the history existed gets one rebuilt from the rides: each ride
records the FTP it started with, so a change shows up between two rides. When the ride before the
change was a real FTP test, the test set it; otherwise it was set by hand. Those entries are marked
"rebuilt", their date is the earliest the change can be placed at.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

SOURCES = ("start", "ramp", "20min", "best20", "manual")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def rebuild(rows: list[dict], current_ftp: int) -> list[dict]:
    """A history from ride history lines (any order), ending at the current FTP."""
    entries: list[dict] = []
    prev = None
    for row in sorted((r for r in rows if r.get("ftp")), key=lambda r: r["started"]):
        if prev is None:
            entries.append({"date": row["started"], "ftp": row["ftp"], "from": None, "source": "start",
                            "ride": None, "rebuilt": True})
        elif row["ftp"] != prev["ftp"]:
            test = prev.get("test") if not prev.get("sim") else None
            est = prev.get("ftp_estimate") or {}
            by_test = test and est.get("source") == test and est.get("ftp") == row["ftp"]
            entries.append({"date": prev["started"] if by_test else row["started"], "ftp": row["ftp"],
                            "from": prev["ftp"], "source": test if by_test else "manual",
                            "ride": prev["id"] if by_test else None, "rebuilt": True})
        prev = row
    if not entries:
        entries.append({"date": _now(), "ftp": current_ftp, "from": None, "source": "start", "ride": None})
    elif entries[-1]["ftp"] != current_ftp:
        entries.append({"date": _now(), "ftp": current_ftp, "from": entries[-1]["ftp"], "source": "manual",
                        "ride": None, "rebuilt": True})
    return entries


class FtpHistory:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "ftp_history.json"

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> list[dict]:
        try:
            entries = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        return entries if isinstance(entries, list) else []

    def save(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, indent=2))
        tmp.replace(self.path)

    def add(self, ftp: int, before: int, source: str = "manual", ride: str | None = None) -> dict:
        """Log one change. Without a history yet, it starts with the FTP before it."""
        if source not in SOURCES:
            source = "manual"
        entries = self.load()
        if not entries:
            entries.append({"date": _now(), "ftp": before, "from": None, "source": "start", "ride": None})
        entry = {"date": _now(), "ftp": ftp, "from": before, "source": source, "ride": ride}
        entries.append(entry)
        self.save(entries)
        return entry
