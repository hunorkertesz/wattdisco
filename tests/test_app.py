import json

from wattdisco import __main__ as cli
from wattdisco.app import APP_PORT, DEFAULT_SIZE, MIN_SIZE, free_port, load_geometry, save_geometry


def test_no_command_opens_the_window(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_cmd_app", lambda args: calls.append(args) or 0)
    assert cli.main([]) == 0
    assert cli.main(["--sim", "--speed", "5"]) == 0
    assert calls[0].sim is False and calls[1].sim is True and calls[1].speed == 5


def test_serve_and_scan_still_parse():
    args = cli.build_parser().parse_args(["serve", "--sim", "--port", "8123"])
    assert args.func is cli._cmd_serve and args.port == 8123
    assert cli.build_parser().parse_args(["scan", "--sim"]).func is cli._cmd_scan


def test_free_port():
    assert 1024 < free_port() < 65536


def test_free_port_prefers_the_same_port_and_falls_back_when_busy():
    import socket

    port = free_port()
    assert free_port(port) == port  # the same address every start, so the page keeps its storage
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", port))
        busy.listen()
        other = free_port(port)
    assert other != port and 1024 < other < 65536
    assert 1024 < APP_PORT < 49152  # outside the range the system hands out on its own


def test_window_geometry_round_trip(tmp_path):
    path = tmp_path / "window.json"
    assert load_geometry(path) == {"width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1], "x": None, "y": None}

    class Win:
        width, height, x, y = 1400, 900, 40, 60

    save_geometry(Win(), path)
    assert load_geometry(path) == {"width": 1400, "height": 900, "x": 40, "y": 60}
    path.write_text(json.dumps({"width": 100, "height": 100}))
    assert load_geometry(path)["width"] == MIN_SIZE[0]


def test_files_saves_a_download_where_the_rider_chose(tmp_path):
    from wattdisco.app import Files

    asked = []
    target = tmp_path / "picked.csv"
    files = Files("http://x", lambda name: asked.append(name) or target,
                  fetch=lambda url: (b"second,power\n", 'attachment; filename="wattdisco-compare-1.csv"'))
    assert files.save_file("/api/compare/export.csv") == {"ok": True, "path": str(target)}
    assert asked == ["wattdisco-compare-1.csv"] and target.read_bytes() == b"second,power\n"


def test_files_cancel_and_foreign_paths(tmp_path):
    from wattdisco.app import Files

    files = Files("http://x", lambda name: None, fetch=lambda url: (b"x", ""))
    assert files.save_file("/api/rides/1/export.fit") == {"ok": False, "cancelled": True}
    assert files.save_file("https://evil.example/x")["ok"] is False
    assert not [a for a in dir(files) if not a.startswith("_") and a != "save_file"]  # all the page can call
