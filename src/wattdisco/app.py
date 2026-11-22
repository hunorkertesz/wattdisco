"""Watt Disco in its own window.

The server runs on a free local port in a background thread; pywebview shows it in
a native window (WebKit on macOS). Closing the window stops the server, which saves
a running ride and disconnects the sensors. All Bluetooth stays in Python.
"""

import json
import re
import socket
import threading
import time
import urllib.request
from pathlib import Path

from wattdisco.awake import KeepAwake
from wattdisco.config import DATA_DIR, ENV_FILE, LEGACY_DATA_DIR, KnownDevices, adopt_legacy_data, load_settings
from wattdisco.core.clock import Clock
from wattdisco.core.workouts import default_workout
from wattdisco.export.strava import Strava, load_keys
from wattdisco.server import Ride, create_app

WINDOW_FILE = "window.json"
DEFAULT_SIZE = (1280, 848)
MIN_SIZE = (520, 520)  # small enough to share the screen side by side with another window
START_TIMEOUT = 15.0


def build_ride(sim: bool, speed: float = 1.0, scan_seconds: float = 10.0, smoothing: str = "3s") -> Ride:
    """Everything the server needs: sensors (real or simulated), settings, Strava, last devices."""
    if adopt_legacy_data():
        print(f"Watt Disco now keeps rides and settings in {DATA_DIR}")
        print(f"(copied from {LEGACY_DATA_DIR}, which is left as it was and no longer used)")
    settings = load_settings()
    workout = default_workout(settings.ftp)
    known = KnownDevices(DATA_DIR)
    reconnect = known.ids if known.auto_connect else []  # last used devices, connected at launch
    if sim:
        from wattdisco.sim.simulator import SimSource

        source = SimSource(workout, connect=reconnect)
        clock = Clock(speed=speed)
    else:
        from wattdisco.ble.sources import BleManager

        source = BleManager(wanted=reconnect, scan_seconds=scan_seconds)
        clock = Clock()
    strava = Strava(load_keys(ENV_FILE), DATA_DIR)
    if not strava.configured:
        print("Strava upload off: no shared Strava app in this version; add your own keys to .env (see README)")
    return Ride(
        source, workout, clock, smoothing=smoothing, settings=settings,
        workout_factory=default_workout, strava=strava, known=known, autostart=False, awake=KeepAwake(),
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_geometry(path=DATA_DIR / WINDOW_FILE) -> dict:
    """Last window size and position, or the default size."""
    try:
        g = json.loads(path.read_text())
        width, height = max(MIN_SIZE[0], int(g["width"])), max(MIN_SIZE[1], int(g["height"]))
        x = None if g.get("x") is None else int(g["x"])
        y = None if g.get("y") is None else int(g["y"])
        return {"width": width, "height": height, "x": x, "y": y}
    except (OSError, ValueError, KeyError, TypeError):
        return {"width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1], "x": None, "y": None}


def save_geometry(window, path=DATA_DIR / WINDOW_FILE) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"width": window.width, "height": window.height, "x": window.x, "y": window.y}))
    except (OSError, AttributeError, TypeError):
        pass  # not worth failing the close over


class Files:
    """Saves the app's own downloads (.fit, comparison CSV) through a native Save dialog.

    In the window a download link would open the file in place, with no way back, so
    the page hands the link to Python instead: Python fetches it from the local server
    and writes it where the rider chose. Exposed to the page as window.pywebview.api.
    """

    def __init__(self, base_url: str, ask_path, fetch=None) -> None:
        # Private names: pywebview exposes every public attribute to the page.
        self._base_url = base_url
        self._ask_path = ask_path  # (suggested filename) -> chosen path or None
        self._get = fetch or self._fetch

    def _fetch(self, url: str) -> tuple[bytes, str]:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read(), r.headers.get("Content-Disposition", "")

    def save_file(self, path: str) -> dict:
        if not path.startswith("/api/"):  # only the app's own downloads
            return {"ok": False, "error": "not a Watt Disco download"}
        try:
            data, disposition = self._get(self._base_url + path)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        found = re.search(r'filename="?([^";]+)"?', disposition)
        name = found.group(1) if found else Path(path.split("?")[0]).name
        target = self._ask_path(name)
        if not target:
            return {"ok": False, "cancelled": True}
        try:
            Path(target).write_bytes(data)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": str(target)}


def ask_save_path(window, name: str) -> str | None:
    import webview

    chosen = window.create_file_dialog(webview.FileDialog.SAVE, directory=str(Path.home() / "Downloads"), save_filename=name)
    if isinstance(chosen, (list, tuple)):
        chosen = chosen[0] if chosen else None
    return chosen or None


def run_window(sim: bool = False, speed: float = 1.0, scan_seconds: float = 10.0, smoothing: str = "3s",
               fullscreen: bool = False) -> int:
    import uvicorn
    import webview

    ride = build_ride(sim, speed, scan_seconds, smoothing)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(ride), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="wattdisco-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + START_TIMEOUT
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            print("Watt Disco could not start its local server")
            return 1
        time.sleep(0.05)

    print(f"Watt Disco running at http://127.0.0.1:{port}  ({'simulated sensors' if sim else 'Bluetooth'})")
    geometry = load_geometry()
    window = None
    files = Files(f"http://127.0.0.1:{port}", lambda name: ask_save_path(window, name))
    window = webview.create_window(
        "Watt Disco", f"http://127.0.0.1:{port}/", width=geometry["width"], height=geometry["height"],
        x=geometry["x"], y=geometry["y"], min_size=MIN_SIZE, background_color="#0A0612", fullscreen=fullscreen,
        js_api=files,
    )
    window.events.closing += lambda: save_geometry(window)
    # Not private: keep the page's own preferences (zoom, nerd mode, metric tiles) between runs.
    webview.start(private_mode=False, storage_path=str(DATA_DIR / "webview"))

    server.should_exit = True  # window closed: stop the server, which saves a running ride
    thread.join(timeout=START_TIMEOUT)
    return 0
