"""Rider settings (FTP, weight) and the effort zones derived from FTP.

Settings live in settings.json in the data folder (see DATA_DIR). Zones are not stored: they are always
computed from FTP, so changing FTP moves every zone with it.
"""

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Watt Disco"
PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR / "web"  # the pages, shipped inside the package
CHECKOUT_DIR = PACKAGE_DIR.parents[1]  # the repository, when running from a source checkout

# Rides, settings and the Strava sign-in live in the rider's own app folder:
# macOS ~/Library/Application Support/Watt Disco, Windows %LOCALAPPDATA%\Watt Disco,
# Linux ~/.local/share/Watt Disco. WATTDISCO_DATA overrides it.
DATA_DIR = Path(os.environ.get("WATTDISCO_DATA") or user_data_dir(APP_NAME, appauthor=False))
# Before the app was installable, a source checkout kept everything in data/ next to the code.
# None when installed (no checkout around the package).
LEGACY_DATA_DIR = CHECKOUT_DIR / "data" if (CHECKOUT_DIR / "pyproject.toml").is_file() else None


def _env_file() -> Path:
    """Own Strava keys (never committed): .env in the data folder, else in the source checkout."""
    for path in (DATA_DIR / ".env", CHECKOUT_DIR / ".env"):
        if path.is_file():
            return path
    return DATA_DIR / ".env"


ENV_FILE = _env_file()


def adopt_legacy_data(data_dir: Path = DATA_DIR, legacy: Path | None = LEGACY_DATA_DIR) -> bool:
    """Copy a source checkout's old data/ folder into the app folder, once.

    Only when the app folder doesn't exist yet. The old folder is left as it was.
    """
    if legacy is None or data_dir.exists() or not legacy.is_dir() or not any(legacy.iterdir()):
        return False
    shutil.copytree(legacy, data_dir)
    return True
SETTINGS_FILE = "settings.json"

DEFAULT_FTP = 240
DEFAULT_WEIGHT_KG = 75.0
FTP_RANGE = (50, 600)
WEIGHT_RANGE = (30.0, 200.0)

# Upper bound of zones 1–5 as a fraction of FTP. Zone 6 is everything above.
ZONE_TOPS = (0.55, 0.75, 0.87, 0.94, 1.05)
ZONE_NAMES = ("Recovery", "Endurance", "Tempo", "Sweet spot", "Threshold", "VO2 max")


def zone_of(watts: float, ftp: int) -> int:
    """Zone number 1–6 for a power, relative to FTP."""
    pct = watts / ftp
    return next((i + 1 for i, top in enumerate(ZONE_TOPS) if pct <= top + 1e-9), len(ZONE_TOPS) + 1)


def zones(ftp: int) -> list[dict]:
    """Zones with their % FTP range and watt range for this FTP."""
    result = []
    low_pct = 0.0
    for i, name in enumerate(ZONE_NAMES):
        top_pct = ZONE_TOPS[i] if i < len(ZONE_TOPS) else None
        result.append(
            {
                "n": i + 1,
                "name": name,
                "from_pct": round(low_pct * 100),
                "to_pct": None if top_pct is None else round(top_pct * 100),
                "from_w": 0 if i == 0 else round(low_pct * ftp) + 1,
                "to_w": None if top_pct is None else round(top_pct * ftp),
            }
        )
        if top_pct is not None:
            low_pct = top_pct
    return result


# Heart rate zones: five zones, either from max HR or from threshold HR (LTHR, Friel style).
# Each entry: upper bound of zones 1–4 as a fraction of the reference; zone 5 is everything above.
HR_METHODS = {
    "max": {"label": "Max HR", "tops": (0.60, 0.70, 0.80, 0.90), "default": 190, "range": (120, 230)},
    "lthr": {"label": "Threshold HR", "tops": (0.81, 0.89, 0.93, 0.99), "default": 170, "range": (100, 220)},
}
HR_ZONE_NAMES = ("Recovery", "Endurance", "Tempo", "Threshold", "VO2 max")


def hr_zone_tops(method: str, reference: int) -> list[int]:
    """Upper bound in bpm of HR zones 1–4."""
    return [round(top * reference) for top in HR_METHODS[method]["tops"]]


def hr_zone_of(bpm: float, tops: list[int]) -> int:
    return next((i + 1 for i, top in enumerate(tops) if bpm <= top), len(tops) + 1)


def hr_zones(method: str, reference: int) -> list[dict]:
    tops = hr_zone_tops(method, reference)
    pcts = HR_METHODS[method]["tops"]
    result = []
    for i, name in enumerate(HR_ZONE_NAMES):
        result.append({
            "n": i + 1,
            "name": name,
            "from_pct": 0 if i == 0 else round(pcts[i - 1] * 100) + 1,
            "to_pct": round(pcts[i] * 100) if i < len(tops) else None,
            "from_bpm": 0 if i == 0 else tops[i - 1] + 1,
            "to_bpm": tops[i] if i < len(tops) else None,
        })
    return result


UNITS = ("metric", "imperial")
MAX_FAVOURITES = 200


@dataclass
class Settings:
    ftp: int = DEFAULT_FTP
    weight_kg: float = DEFAULT_WEIGHT_KG
    # Strava upload preferences (the sign-in itself lives in data/strava_tokens.json)
    strava_auto_upload: bool = False
    strava_sport_type: str = "VirtualRide"
    strava_private: bool = True  # "only me" until the rider chooses otherwise
    # Heart rate zones
    hr_method: str = "max"  # "max" or "lthr"
    max_hr: int = HR_METHODS["max"]["default"]
    lthr: int = HR_METHODS["lthr"]["default"]
    auto_pause: bool = True  # pause when pedalling stops for 5 s, go on when it starts again
    sound: bool = True  # the ride's sound cues (a setting: the window's browser storage does not outlive the app)
    units: str = "metric"  # "metric" (km/h, km, kg) or "imperial" (mph, mi, lb); stored values stay metric
    setup_done: bool = False  # the first-start questions (FTP, weight, heart rate) were answered or skipped
    favourites: list[str] = field(default_factory=list)  # workout ids marked with a star, listed first

    def __post_init__(self) -> None:
        if not FTP_RANGE[0] <= self.ftp <= FTP_RANGE[1]:
            raise ValueError(f"FTP must be between {FTP_RANGE[0]} and {FTP_RANGE[1]} W")
        if not WEIGHT_RANGE[0] <= self.weight_kg <= WEIGHT_RANGE[1]:
            raise ValueError(f"weight must be between {WEIGHT_RANGE[0]:g} and {WEIGHT_RANGE[1]:g} kg")
        if len(self.favourites) > MAX_FAVOURITES:
            raise ValueError(f"at most {MAX_FAVOURITES} favourites")
        if self.units not in UNITS:
            raise ValueError(f"units must be one of {list(UNITS)}")
        if self.hr_method not in HR_METHODS:
            raise ValueError(f"HR zones method must be one of {list(HR_METHODS)}")
        for key, method in (("max_hr", "max"), ("lthr", "lthr")):
            lo, hi = HR_METHODS[method]["range"]
            if not lo <= getattr(self, key) <= hi:
                raise ValueError(f"{HR_METHODS[method]['label']} must be between {lo} and {hi} bpm")

    @property
    def hr_reference(self) -> int:
        return self.max_hr if self.hr_method == "max" else self.lthr

    @property
    def hr_tops(self) -> list[int]:
        return hr_zone_tops(self.hr_method, self.hr_reference)

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "w_per_kg": round(self.ftp / self.weight_kg, 2),
            "zone_tops": list(ZONE_TOPS),
            "zones": zones(self.ftp),
            "hr_methods": {k: {"label": v["label"], "range": v["range"]} for k, v in HR_METHODS.items()},
            "hr_zone_tops": self.hr_tops,
            "hr_zones": hr_zones(self.hr_method, self.hr_reference),
        }


def load_settings(data_dir: Path = DATA_DIR) -> Settings:
    """Saved settings, or the defaults when there is no (valid) settings file yet."""
    path = data_dir / SETTINGS_FILE
    try:
        raw = json.loads(path.read_text())
        defaults = Settings()
        return Settings(
            ftp=int(raw["ftp"]),
            weight_kg=float(raw["weight_kg"]),
            strava_auto_upload=bool(raw.get("strava_auto_upload", defaults.strava_auto_upload)),
            strava_sport_type=str(raw.get("strava_sport_type", defaults.strava_sport_type)),
            strava_private=bool(raw.get("strava_private", defaults.strava_private)),
            hr_method=str(raw.get("hr_method", defaults.hr_method)),
            max_hr=int(raw.get("max_hr", defaults.max_hr)),
            lthr=int(raw.get("lthr", defaults.lthr)),
            auto_pause=bool(raw.get("auto_pause", defaults.auto_pause)),
            sound=bool(raw.get("sound", defaults.sound)),
            units=str(raw.get("units", defaults.units)),
            # settings saved before the first-start questions existed: that rider has set up already
            setup_done=bool(raw.get("setup_done", True)),
            favourites=[str(x) for x in raw.get("favourites", [])][:MAX_FAVOURITES],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return Settings()


def save_settings(settings: Settings, data_dir: Path = DATA_DIR) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / SETTINGS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2))
    tmp.replace(path)


DEVICES_FILE = "devices.json"


class KnownDevices:
    """Devices used last time (reconnected at launch), their roles and last zero offset."""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.path = data_dir / DEVICES_FILE
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raw = {}
        self.auto_connect: bool = bool(raw.get("auto_connect", True))
        self.devices: dict[str, dict] = {d["id"]: d for d in raw.get("devices", []) if isinstance(d, dict) and "id" in d}

    @property
    def ids(self) -> list[str]:
        return list(self.devices)

    def remember(self, devices, save: bool = True) -> None:
        """The devices of the ride that is starting become the ones to reconnect next time."""
        kept = {}
        for d in devices:
            old = self.devices.get(d.id, {})
            kept[d.id] = {**old, "id": d.id, "name": d.name, "roles": sorted(d.roles),
                          "capabilities": sorted(d.capabilities)}
        self.devices = kept
        if save:
            self.save()

    def update(self, device_id: str, save: bool = True, **fields) -> None:
        if device_id in self.devices:
            self.devices[device_id].update(fields)
            if save:
                self.save()

    def roles(self, device_id: str) -> set[str] | None:
        d = self.devices.get(device_id)
        return None if d is None else set(d.get("roles", []))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"auto_connect": self.auto_connect, "devices": list(self.devices.values())}, indent=2))
        tmp.replace(self.path)
