"""Command line entry point.

`wattdisco` (or `wattdisco app`) opens the app window, `wattdisco serve` runs only the
server for a normal browser, `wattdisco scan` prints live sensor values. All take `--sim`.
"""

import argparse
import asyncio
import sys

from wattdisco import __version__
from wattdisco.core.smoothing import SMOOTHING_WINDOWS


async def _scan(args: argparse.Namespace) -> int:
    from wattdisco.console import print_devices, print_live
    from wattdisco.core.bus import Bus
    from wattdisco.core.clock import Clock
    from wattdisco.core.session import Session

    workout = None
    duration = args.duration
    if args.sim:
        from wattdisco.config import load_settings
        from wattdisco.core.workouts import default_workout
        from wattdisco.sim.simulator import SimSource

        source = SimSource(default_workout(load_settings().ftp))
        workout = source.workout
        if duration is None:
            duration = workout.seconds
        print(f"simulated sensors, workout '{workout.name}' ({workout.seconds // 60} min, FTP {workout.ftp} W)")
        print_devices(source.devices)
    else:
        from wattdisco.ble.scanner import discover
        from wattdisco.ble.sources import BleManager

        print(f"scanning for {args.timeout:g} s ...")
        found = await discover(args.timeout)
        if not found:
            print("no power, heart rate or trainer devices found")
            print("wake the sensors (pedal, wet the strap) and try a longer --timeout")
            return 1
        print_devices([d for d, _, _ in found], {d.id: rssi for d, _, rssi in found})
        usable = [(d, b, r) for d, b, r in found if d.roles & {"power", "heart_rate", "control"}]
        if not usable:
            return 0
        source = BleManager(wanted=[d.id for d, _, _ in usable], found=usable)

    if args.list_only:
        return 0

    bus = Bus()
    clock = Clock(speed=args.speed if args.sim else 1.0)
    session = Session(bus, clock, smoothing=args.smoothing)
    task = asyncio.create_task(source.run(bus, clock))
    try:
        await print_live(session, clock, duration, workout)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_scan(args))
    except KeyboardInterrupt:
        return 0


def _log_to_file() -> None:
    """The diagnostics log in the data folder (core/diagnostics.py)."""
    from wattdisco.config import DATA_DIR
    from wattdisco.core.diagnostics import setup_logging

    try:
        setup_logging(DATA_DIR)
    except OSError as e:  # a read-only or full disk must not stop the app
        print(f"No log file: {e}", file=sys.stderr)


def _cmd_serve(args: argparse.Namespace) -> int:
    """Server only, for development in a normal browser."""
    import uvicorn

    from wattdisco.app import build_ride
    from wattdisco.server import create_app

    _log_to_file()
    ride = build_ride(args.sim, args.speed, args.timeout, args.smoothing)
    print(f"Watt Disco on http://{args.host}:{args.port}  ({'simulated sensors' if args.sim else 'Bluetooth'})")
    uvicorn.run(create_app(ride), host=args.host, port=args.port, log_level="warning")
    return 0


def _cmd_app(args: argparse.Namespace) -> int:
    """The app in its own window."""
    from wattdisco.app import run_window

    _log_to_file()
    return run_window(args.sim, args.speed, args.timeout, args.smoothing, fullscreen=args.fullscreen)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--sim", action="store_true", help="use simulated sensors")
    p.add_argument("--timeout", type=float, default=10.0, help="Bluetooth scan time in seconds (default 10)")
    p.add_argument(
        "--smoothing", choices=list(SMOOTHING_WINDOWS), default="3s", help="power display smoothing (default 3s)"
    )
    p.add_argument(
        "--speed", type=float, default=1.0, help="with --sim: run the ride this many times faster than real time"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wattdisco",
        description="Watt Disco – Built with AI, by cyclists for cyclists.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="find power and heart rate sensors and print live values")
    scan.add_argument("--sim", action="store_true", help="use simulated sensors riding a fake workout")
    scan.add_argument("--timeout", type=float, default=20.0, help="scan time in seconds (default 20)")
    scan.add_argument(
        "--duration",
        type=float,
        default=None,
        help="stop printing live values after this many seconds "
        "(default: until Ctrl+C, or the end of the workout with --sim)",
    )
    scan.add_argument("--list-only", action="store_true", help="only list devices, do not connect")
    scan.add_argument(
        "--smoothing", choices=list(SMOOTHING_WINDOWS), default="3s", help="power display smoothing (default 3s)"
    )
    scan.add_argument(
        "--speed", type=float, default=1.0, help="with --sim: run the ride this many times faster than real time"
    )
    scan.set_defaults(func=_cmd_scan)

    app = sub.add_parser("app", help="open Watt Disco in its own window (the default)")
    _common(app)
    app.add_argument("--fullscreen", action="store_true", help="full screen, for the bike display")
    app.set_defaults(func=_cmd_app)

    serve = sub.add_parser("serve", help="run only the server, for development in a browser")
    _common(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    # No command (or only options such as --sim): open the app window.
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv = ["app", *argv]
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
