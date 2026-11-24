import logging
import sys

from fastapi.testclient import TestClient

from wattdisco import __version__
from wattdisco.core import diagnostics
from wattdisco.core.clock import Clock
from wattdisco.core.workouts import default_workout
from wattdisco.server import Ride, create_app
from wattdisco.sim.simulator import SimSource


def _drop_file_handler():
    logger = logging.getLogger("wattdisco")
    for h in list(logger.handlers):
        if getattr(h, "_wattdisco", False):
            logger.removeHandler(h)
            h.close()


def test_log_file_rotates_and_hides_secrets(tmp_path):
    hook = sys.excepthook
    try:
        path = diagnostics.setup_logging(tmp_path)
        assert path == tmp_path / "logs" / "wattdisco.log"
        diagnostics.setup_logging(tmp_path)  # twice: still one handler, one line per message
        log = logging.getLogger("wattdisco.test")
        log.warning("strava answered access_token=abcdef1234567890 refresh_token: zyxw98765432")
        log.info("Authorization Bearer QWERTY123456789")
        text = path.read_text()
        assert "abcdef1234567890" not in text and "zyxw98765432" not in text and "QWERTY123456789" not in text
        assert text.count("starting on") == 2 and text.count("strava answered") == 1
    finally:
        _drop_file_handler()
        sys.excepthook = hook


def test_report_and_copy_endpoint(tmp_path):
    hook = sys.excepthook
    try:
        diagnostics.setup_logging(tmp_path)
        workout = default_workout()
        ride = Ride(SimSource(workout), workout, Clock(speed=40), data_dir=tmp_path, autostart=False)
        with TestClient(create_app(ride)) as c:
            import time
            time.sleep(0.3)
            c.post("/api/devices/sim-hrm/connect")
            time.sleep(0.3)
            text = c.get("/api/diagnostics").json()["text"]
            assert c.get("/connect.html").status_code == 200
    finally:
        _drop_file_handler()
        sys.excepthook = hook
    assert f"App: {__version__}" in text and "simulated sensors" in text
    assert "SIM HR strap · connected" in text  # devices by name and state
    assert "sim-hrm" not in text  # never device ids (Bluetooth addresses)
    assert "device SIM HR strap: connected" in text  # the log's end is in it
