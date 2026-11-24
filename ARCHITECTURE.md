# Watt Disco – Architecture plan

This file is the plan to start from. Read `CLAUDE.md` for the product rules and screens. This file says how the code is organised and in what order to build it.

## 1. Big picture

```
 Sensors (BLE)            Python backend (one process)                Browser
┌────────────┐   ┌───────────────────────────────────────────┐   ┌──────────────┐
│ KICKR      │   │ ble/ ──► core/bus ──► core/session ──┐    │   │ web pages    │
│ Power mtr  │──►│ (bleak)   (events)    (state, smoothing)│    │   │ live.js      │
│ HR strap   │   │                                       ▼    │   │              │
└────────────┘   │ core/workout (targets, ERG) ──► ble/ftms   │◄─►│ WebSocket    │
                 │ core/recorder ──► export/fit ──► export/strava │ │ + REST       │
                 │ server.py (FastAPI)                          │   └──────────────┘
                 └───────────────────────────────────────────┘
```

Rules that keep it simple:
1. **One direction of data.** Sensors produce samples, samples go on an event bus, everything else listens. Nothing in `core/` imports `bleak`.
2. **Hardware is replaceable.** Real sensors and the simulator both implement the same `SensorSource` interface. Tests and UI work run on the simulator.
3. **The browser is a view.** It only displays state and sends commands. All logic lives in Python.
4. **Raw data is sacred.** Smoothing happens only for display. The recorder stores raw samples from every connected sensor.

## 2. Folder layout

```
wattdisco/
├── CLAUDE.md                 product rules, screens, design
├── ARCHITECTURE.md           this file
├── README.md                 how to install and run
├── pyproject.toml
├── .env.example              STRAVA_CLIENT_ID=, STRAVA_CLIENT_SECRET=
├── .gitignore                .venv, .env, data/, __pycache__
├── .github/workflows/release.yml   per version tag: tests, Mac .dmg and Windows .exe, draft GitHub Release
├── packaging/                PyInstaller spec, launcher (output to wattdisco.log when there's no terminal), app icon, make_dmg.sh,
│                             licenses.py + native-licenses/ + package-licenses/ (licence texts copied into the app)
├── design/                   exported concept screens (reference only, never served or shipped)
├── relay/                    Strava relay (Cloudflare Worker): holds the shared app's client secret, swaps sign-in codes and refreshes tokens
├── src/wattdisco/
│   ├── __main__.py           CLI: `wattdisco` (opens the app window), `wattdisco serve` (browser only), `--sim`, `scan`
│   ├── app.py                desktop window: server on a free port in a thread, pywebview window, size remembered in window.json
│   ├── console.py            terminal output for `scan`: device list, live line per second
│   ├── config.py             settings (FTP, weight, HR method, max HR, threshold HR) in settings.json, power zones from FTP, HR zones; DATA_DIR (per-user app folder, WATTDISCO_DATA overrides, a checkout's old data/ copied once) and WEB_DIR
│   ├── models.py             dataclasses: Sample types, Device, Phase, Workout, Ride
│   ├── ble/
│   │   ├── scanner.py        discover devices, identify roles from advertised services
│   │   ├── manager.py        connect, auto-reconnect, dropout detection, per-device tasks
│   │   ├── heart_rate.py     parse 0x2A37
│   │   ├── power.py          parse 0x2A63 (power, crank revs -> cadence), zero offset
│   │   ├── ftms.py           trainer control: set target power, resistance, read trainer data
│   │   └── sources.py        BleManager (scan, connect, disconnect, zero offset) + LiveSensor (per-device reconnect, trainer control)
│   ├── ant/                  optional ANT+ support (needs a USB ANT+ stick)
│   │   ├── stick.py          open the stick with openant, detect if present
│   │   ├── profiles.py       ANT+ Power (0x0B), Heart Rate (0x78), FE-C trainer (0x11) parsers
│   │   └── source.py         AntSource implementing SensorSource
│   ├── sim/
│   │   ├── simulator.py      SimSource: fake power, HR, cadence following the workout
│   │   └── replay.py         replay a recorded ride file as live data
│   ├── core/
│   │   ├── bus.py            tiny sync pub/sub (topics in section 5)
│   │   ├── clock.py          ride clock (speed factor for sim) + ManualClock for tests
│   │   ├── sources.py        SensorSource protocol (here, not in ble/, so sim never imports bleak)
│   │   ├── metrics.py        catalog of extra metrics (core temp, skin temp, heat strain, W/kg)
│   │   ├── session.py        live state: main sensors, latest values, smoothed values
│   │   ├── smoothing.py      RAW / 3 s / 10 s windows (display only)
│   │   ├── workouts.py       workout library: built-ins + workouts/*.json in the data folder, in % FTP; validate, describe
│   │   ├── workout_import.py .zwo / .erg / .mrc files -> workout spec in % FTP
│   │   ├── plans.py         training plans (weeks of built-ins) and the rider's progress
│   │   ├── hrv.py           DFA alpha 1 from RR intervals, aerobic threshold per ride
│   │   ├── speed.py          estimated speed and distance from power and weight, flat or on a gradient
│   │   ├── climb_import.py   a .gpx file -> a climb: distance on the earth's surface, heights every 10 m smoothed over 100 m, 100 m segments, gradients −10 to +20 %; positions dropped
│   │   ├── climb.py          climbs: profile from segments, built-in climbs, ClimbPlayer (position by distance, gradient to the trainer)
│   │   ├── workout.py        workout player: current phase, countdown, target, ERG commands
│   │   ├── stats.py          on-target %, difference, weighted power, intensity, training load, VI, HR drift
│   │   ├── compare.py        sensor comparison: up to 3 sources per kind, per second, each vs A: delta, mean, lag (detrended), correlation, CSV
│   │   └── recorder.py       writes all samples to rides/<id>.jsonl in the data folder while riding
│   ├── export/
│   │   ├── fit.py            jsonl -> .fit
│   │   └── strava.py         OAuth once (shared app through relay/, or own keys from .env), token refresh, upload, set type and visibility
│   ├── server.py             FastAPI: REST + WebSocket, serves web/
│   └── web/                  the pages, shipped inside the package
│       ├── theme.css         neon tokens, buttons, titles (shared by all pages)
│       ├── components.js     strip/tabs, sensor chips, neon buttons
│       ├── live.js           WebSocket client, state store
│       ├── charts.js         workout chart, HR/power curves, live traces and heartbeat rings (canvas)
│       ├── workout.css  zones.css   page styles (one .css per page when needed)
│       ├── zones.html        FTP, weight, power zones, heart rate zones
│       ├── launch.html  connect.html  sensors.html  editor.html  ready.html
│       └── workout.html  big.html  summary.html  upload.html  compare.html  history.html
└── tests/
    ├── test_parsers.py       power / HR byte parsing with known byte strings
    ├── test_smoothing.py
    ├── test_workout.py       phase transitions, targets
    ├── test_stats.py         on-target, weighted power / intensity / load against hand-checked values
    └── test_recorder_fit.py  round trip jsonl -> fit
```

## 3. Core data types (`models.py`)

```python
@dataclass(frozen=True)
class PowerSample:   t: float; device_id: str; watts: int; cadence: float | None
@dataclass(frozen=True)
class HrSample:      t: float; device_id: str; bpm: int
@dataclass
class Device:        id: str; name: str; roles: set[str]; state: str; transport: str   # transport: ble | ant
@dataclass
class Phase:         kind: str; seconds: int; watts: int; watts_end: int | None; cadence: int | None
@dataclass
class Workout:       name: str; ftp: int; phases: list[Phase]
```

`t` is seconds since the ride started, taken from one monotonic clock on arrival.

## 4. Event bus and session state

- `bus.publish("power", PowerSample)` from the BLE parsers (or the simulator).
- `session.py` subscribes and keeps the latest value per device, applies the chosen main power source and main HR source, and computes smoothed values.
- `recorder.py` subscribes to everything and appends one JSON line per sample.
- `server.py` subscribes to the session and pushes a state snapshot to the browser at 4 Hz. It does not push every raw sample.
- `compare.py` subscribes to all devices. It starts and stops with the ride and skips pauses; `CompareBench.from_ride` rebuilds a finished ride's comparison from the recorded samples (`RideStore.compare`).

## 4b. Recording and ride files

- `core/recorder.py` writes `rides/<YYYYMMDD-HHMMSS>.jsonl` in the data folder from a writer thread: a `meta` line, every raw sample of every sensor (`power`, `hr`, `metric`, `device`, `trainer`), one `tick` per second (workout position, target, mode, main sensor ids, main power/HR/cadence) and an `end` line. `t` is seconds since the ride started.
- `core/summary.py` builds the summary from the file with `core/stats.py`; `core/rides.py` caches it as `<id>.summary.json` and writes `<id>.fit` with `export/fit.py`.
- Sensors keep running between rides. A ride starts from the Ready screen; starting one remembers the connected devices for the next launch.
- A ride the app never stopped (crash, power cut) has no `end` line. At start the newest real ride from the last 24 hours without one is offered on the Ready screen: continue it (the same file is appended to, `ride_start` set so `t` goes on, the player at the last `elapsed`, paused until the rider pedals) or save it as it is (an `end` line at its last second). Starting another ride saves it as it is.
- `<id>.notes.json`: how the ride felt (`rpe` 1–10) and a note. Deleting a ride moves all its files to `deleted/` next to `rides/` (`POST /api/rides/{id}/restore` brings them back); files there go for good after 7 days, at start.

## 5. Server API

REST:
- `GET /api/devices` (found devices with capabilities, active roles, RSSI, remembered, last zero; scanning; auto-connect), `POST /api/scan`, `POST /api/devices/{id}/connect`, `.../disconnect`
- `POST /api/devices/{id}/role` ({roles}: a subset of what the device can do) and `POST /api/main-sensors` (choose main power and HR)
- `POST /api/devices/{id}/zero-offset` (power meters, Cycling Power Control Point 0x2A66), `PUT /api/devices/auto-connect`
- Last used devices, their roles and last zero offset are kept in `devices.json` in the data folder (`config.KnownDevices`) and reconnected at launch
- `GET/PUT /api/settings` (FTP, weight, hr_method, max_hr, lthr, auto_pause, units metric/imperial, setup_done, favourites; a PUT changes only the fields it sends; response includes power and HR zones), `GET /api/zones?ftp=` and `GET /api/hr-zones?method=&reference=` (previews). `/settings.html` and `/testbench.html` redirect to `/zones.html` and `/compare.html`.
- `GET /api/workouts` (library with duration, average, intensity, training load, text, plus phase defaults), `GET /api/workouts/{id}`, `POST /api/workouts`, `PUT /api/workouts/{id}` (a built-in is saved as the rider's copy), `DELETE /api/workouts/{id}`, `POST /api/workouts/{id}/select`
- `/` opens `launch.html` (or the workout mid-ride); `/train` opens the ride or `ready.html`
- `POST /api/ride/start` ({workout_id, mode, smoothing, power_source, ease, wait_for_pedal}, all optional), `POST /api/ride/pause`, `/resume`, `POST /api/ride/stop` (stop writes the summary and the .fit). Auto-pause: 5 ride seconds under 20 W and 20 rpm pause the ride (`auto_paused: "stopped"`), 2 s of pedalling resume it; `wait_for_pedal` starts paused (`"waiting"`). A pause the rider chose is only resumed by the rider; FTP tests never auto-pause. `POST /api/ride/continue`, `POST /api/ride/interrupted/save` (the interrupted ride, see 4b). `GET /api/home` (TRAIN home band: this week or the plan's week, form, last ride)
- `GET /api/rides` (newest first; one history line per ride: headline numbers, sim and FTP-test flags, Strava link; kept in memory until the ride's files change), `GET /api/rides/{id}/summary`, `GET /api/rides/{id}/export.fit`, `DELETE /api/rides/{id}` (to `deleted/`), `POST /api/rides/{id}/restore`, `GET/PUT /api/rides/{id}/notes` ({rpe, note})
- Progress: `GET /api/progress` (fitness per day, power curve all / 90 / 28 days, FTP history, power sources across rides), `GET /api/ftp-history`, `GET /api/critical-power` (CP and W′ from 3–20 min bests, status ok / few / not_all_out / below_ftp, ramp test suggestion); `PUT /api/settings` takes `ftp_source` and `ride_id` so an FTP change is logged with where it came from. `GET /api/rides/{id}/export.csv`: the whole ride, one row per second, every sensor in its own columns
- Training plan: `GET /api/plans?days=&minutes=` (previews tailored), `POST /api/plan` ({plan, days, minutes}), `PUT /api/plan/options` ({days, minutes}, from the current week); fitted sessions are workouts with the id `<built-in>--<minutes>m`, made on the fly. `GET /api/plans` (the plans and the active one with each session's status), `POST /api/plan` ({plan}), `DELETE /api/plan`, `POST /api/plan/skip`, `/done` (ridden elsewhere), `/undo`, `/repeat`, `GET /api/plan/next`, `GET /api/rides/{id}/plan` (the session a ride ticked off, or why it did not count)
- Sensor comparison of the running ride: `GET /api/compare`, `POST /api/compare/reference` ({kind, id}: which source is A), `GET /api/compare/export.csv` (one row per second). A finished ride: `GET /api/rides/{id}/compare` and `GET /api/rides/{id}/compare.csv` (`?power=&hr=` pick A)
- Strava: `GET /api/strava/status`, `GET /api/strava/connect?return_to=` (redirects to Strava), `GET /api/strava/callback`, `POST /api/strava/disconnect`, `PUT /api/strava/options` (auto-upload, activity type, visibility)
- `POST /api/rides/{id}/upload` (starts a background upload; simulated rides need `allow_sim`), `GET /api/rides/{id}/upload` (status: none, uploading, done, duplicate, error)

WebSocket `/ws`:
- server to browser: `state` (devices, main values, smoothed values, workout position, target, alerts)
- server to browser: `compare` (sensor comparison of the running ride), only to browsers that sent `{cmd: "watch_compare", value: true}`
- browser to server: `{cmd: "set_smoothing" | "set_main_power" | "set_main_hr" | "set_main_cadence" | "set_mode" (erg|free) | "skip_phase" | "adjust_phase" (steps, ±5 W each, current phase only)}`

Bus topics: `power`, `hr`, `metric` (MetricSample for extra values, keys in `core/metrics.py`), `device` (sensors); `workout` (player position, 4 Hz); `trainer` (TrainerCommand: erg + watts, or free); `trainer_status` (TrainerStatus from the trainer or the sim).

## 6. Workout player (`workout.py`)

- Pure logic with an injected clock, so it can be tested without waiting.
- Output each second: current phase, seconds left, target watts, next phase.
- In ERG mode it publishes a `trainer` command when the target changes: small ramp steps at most once a second, jumps of 10 W or more eased in over 3 s (a step every 0.5 s).
- Low cadence: below 50 rpm for 2 s, ERG lets go (`free`) so the cranks never lock up; at 60 rpm or more for 2 s it takes hold again, eased in from the rider's current power. No cadence data means it never lets go. `erg_state` (off, holding, easing, released) goes to the screen.
- FTP tests (`Workout.test`): the ramp test goes straight to the cool-down after 4 s under 50 rpm in a step. `stats.ftp_estimate` gives the result (ramp: 75 % of the best minute of the steps; 20 min test: 95 % of the best 20 min; any other ride: 95 % of the best 20 min, only when that beats the FTP). A finished test sets the FTP (never from a simulated ride); the summary shows the result with undo, or the suggestion with a button. `ble/sources.py` passes it to `ftms.FtmsController` (Set Target Power); the simulated trainer obeys it too. The last command is re-sent when a trainer reconnects.
- In free ride mode it sends one `free` command: FTMS indoor bike simulation on a flat road, so the trainer stops holding power. The target is only shown.
- Workout time can run ahead of ride time (skip phase). The chart history uses workout time.
- `WorkoutPlayer.adjustments` holds the rider's per-phase changes (W by phase index). `Position.target` includes them, so ERG, the sim and the recorded target follow; the state's `workout.adjustments` lets the chart draw them.
- Ramps (warm-up, cool-down) interpolate from `watts` to `watts_end`.

## 6b. ANT+ support (optional transport)

- Needs a USB ANT+ stick (for example a Garmin or Suunto stick). Without a stick the app runs BLE-only and the Connect screen shows "No ANT+ stick found".
- Library: `openant`, which needs `libusb` (macOS: `brew install libusb`). Check on your Mac early, USB access can need extra permissions.
- Profiles: power meter (ANT+ Bike Power), heart rate (ANT+ HR), trainer control (ANT+ FE-C). Cadence comes from the power profile.
- `AntSource` and `BleSource` both implement `SensorSource` and publish the same `PowerSample` and `HrSample` on the bus. Nothing in `core/` knows which transport a sample came from, except `Device.transport` for display.
- The scanner merges both transports into one device list. The same physical sensor may appear twice (BLE and ANT+). Match by name or ID where possible, and let the user pick which transport to use. Never use both for the same role at once.
- The UI shows a small `BLE` or `ANT+` tag on every device row and in the multiple sensors screen.
- Trainer control over ANT+ FE-C is a separate, later step. Start with receive-only (power, HR, cadence).

## 7. Reliability rules for BLE

- Each device runs in its own asyncio task with its own reconnect loop (backoff 1, 2, 5, 10 s).
- If no data arrives for 5 s, mark the device `lost`, publish an alert, keep trying to reconnect.
- Connect the power meter and HR strap before the trainer. The KICKR allows only a few connections.
- Cadence from crank revolutions: use deltas of cumulative revolutions and event time (1/1024 s), handle 16-bit rollover, and treat no change for 2 s as cadence 0.
- Never block the event loop: file writes go through a queue or a thread.

## 8. Frontend structure

- No framework. Each page loads `theme.css`, `components.js` and `live.js`.
- `live.js` keeps one state object updated by the WebSocket and calls `render(state)` on the page.
- Charts are drawn on canvas in `charts.js`. The workout chart takes the workout plus the recorded power and HR series.
- Neon rules live only in `theme.css` (tokens, glow, buttons, straight titles). The workout data area uses a `.clean` scope with its own palette.
- `sound.js`: cues as Web Audio tones (no sound files): ticks in the last 3 s of a phase, a tone at the next one, three notes at the end, on the workout, big and compare screens; a 3-2-1 count-in on the ready screen. Muted with the speaker in the top strip, M, or on the ready screen (`settings.sound`, so it outlives the window). Silent until the rider clicks, taps or presses a key in that window, when the ride clock is sped up (`--speed` above 1), and as soon as the page closes.
- `units.js`: metric or imperial display (speed, distance, weight); stored values stay metric.
- Keep-awake: `awake.py` holds the display on while a ride runs (macOS `caffeinate`, Windows `SetThreadExecutionState`); in a browser the pages ask for a screen wake lock.

## 8b. Desktop app window and start-up flow

- `wattdisco` (no arguments) starts the FastAPI server on a free local port in a background thread and opens a native window with `pywebview` pointing at it. No browser bar, no tab. Closing the window stops the app cleanly (stop recording, disconnect sensors).
- `wattdisco serve` runs only the server, for development in a normal browser.
- Start-up flow: window opens on `launch.html` (logo, tagline). Meanwhile Python starts the Bluetooth scan and reconnects the last used devices. After about 2 seconds, or when the scan is ready, the page moves on to `connect.html` (on the very first start to `setup.html` first: units, FTP or "start with a ramp test", weight, max heart rate). Then sensors, ready, workout, summary, upload, as in the screens.
- Navigation is page to page inside the one window. State lives in Python, so a page reload never loses the ride.
- Window: fixed minimum size, remembers size and position, optional full screen for the bike display.
- Known caveat: pywebview uses the system web view (WebKit on macOS). Check that Web Bluetooth is NOT needed, since all Bluetooth goes through Python (it does).
- Later, packaging into a double-click app: PyInstaller or briefcase builds `Watt Disco.app` for macOS (and an .exe for Windows). Bluetooth permission text goes into the app's `Info.plist`.

## 9. Build order (each step runs on its own)

The UI is built step by step together with the logic. Each screen is built in the step where it first has real data to show. The `.dc.html` files in `design/` are the visual reference for that screen. Open the file and read its inline styles for exact colours, sizes and spacing. If a PNG with the same name exists next to it, look at that too. Do not design everything first.

| Step | Deliverable | UI built in this step (reference in `design/`) | Done when |
|---|---|---|---|
| 0 | repo, `pyproject.toml`, `.gitignore`, `README`, `wattdisco scan` stub | none | `uv run wattdisco --help` works |
| 1 | `ble/scanner.py`, `heart_rate.py`, `power.py`, parser tests | none (terminal output only) | scan shows your real sensors, live values print |
| 2 | `bus`, `session`, `sim/simulator`, `smoothing` | none | `--sim` prints a fake ride |
| 3 | `server.py`, `theme.css`, `components.js`, `charts.js` | **shared neon style first** (`theme.css`, `components.js`: colours, buttons, straight titles, top strip with logo and tabs), then `workout.html` from `Main.dc.html` | the workout screen runs live on simulated data |
| 4 | `workout.py`, `ftms.py` | ERG toggle and target display on the workout screen | ERG changes the trainer target on a real KICKR |
| 5 | `recorder`, `stats`, `export/fit` | `summary.html` basic and nerd (`Summary.dc.html`, `SummaryNerd.dc.html`) | a finished ride shows both summaries, .fit opens in another tool |
| 6 | `export/strava` | `upload.html` (`Upload.dc.html`) | one ride uploads to Strava |
| 7 | `app.py` (desktop window with pywebview), start-up flow launch to connect, device list and role endpoints, workout storage | `launch.html`, `connect.html`, `sensors.html`, `ready.html`, `editor.html` (`Launch`, `Connect`, `MultiSensor`, `Ready`, `Editor`) | full flow from launch to upload |
| 8 | `compare.py` | `compare.html` (`Compare.dc.html`) | up to three power sources and three HR sensors compared live and after the ride |
| 8b | `ant/` receive-only: ANT+ power, HR and cadence | transport tag and ANT+ stick status on the connect and multiple sensors screens | an ANT+ power meter and HR strap show live values |
| 9 | polish | `big.html` (`Big.dc.html`), nerd mode and zoom on the workout screen (`MainNerd.dc.html`) | feature list in CLAUDE.md complete |

Rules for UI work:
- Step 3 starts with `theme.css` and `components.js`. Every later screen reuses them, so the look stays consistent.
- Match the designs, but do not copy their markup. They use fixed 1280 px sizes and inline styles. Take the exact values from them and build responsive pages.
- Build each screen against the simulator first, then check it with real sensors.

## 10. Testing approach

- Parsers: feed known byte strings, check watts, bpm and cadence.
- Workout and stats: deterministic, no hardware, no real time.
- Replay: record a real ride once, then replay it in tests and for UI work.
- Manual hardware checklist in `README.md`: connect, dropout and reconnect, ERG step, zero offset.

## 11. Open decisions (decide when you reach the step)

- ANT+ trainer control (FE-C): add after receive-only ANT+ works.
- FIT writing library: `fit-tool` (BSD-3-Clause, reads and writes FIT activity files; tests read every export back). It replaced `garmin-fit-sdk`; Garmin's decoder reads the files without errors.
- Packaging: PyInstaller (`packaging/wattdisco.spec`): a folder .app in a .dmg on macOS (Apple silicon, unsigned, Bluetooth usage text in Info.plist), one .exe on Windows. Built by `.github/workflows/release.yml` for each `v*` tag. Every bundled package's licence, Python's and the runtime's C libraries' go into the app's `licenses/` folder (`packaging/licenses.py`; the build fails on a package without one).
- Storage: files in the data folder are enough for now. Move to SQLite only if listing rides gets slow.
