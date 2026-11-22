"""Write a recorded ride as a FIT activity file (fit-tool).

Records once per second (power, heart rate, cadence, core temperature, and the
estimated flat-road speed and distance), one lap per workout phase, one session
with weighted power, intensity and training load, then the activity. Strava builds its analysis charts on the
distance stream, so indoor files need the estimated one.
"""

from datetime import datetime, timedelta, timezone

from fit_tool import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (
    Activity, Event, EventType, FileType, LapTrigger, Manufacturer, SessionTrigger, Sport, SubSport,
)

from wattdisco.core import stats
from wattdisco.core.recorder import RideData

# Strava takes the activity type from the file: cycling + virtual_activity is a Virtual Ride,
# cycling + indoor_cycling a Ride (marked as trainer).
SUB_SPORTS = {"VirtualRide": SubSport.VIRTUAL_ACTIVITY, "Ride": SubSport.INDOOR_CYCLING}
PRODUCT_NAME = "Watt Disco"
FIT_EPOCH_S = 631065600
CREATOR = 0  # device_index of the device that made the file


def _message(cls, fields: dict):
    """A fit-tool message with the fields we know; anything None is left out."""
    message = cls()
    for k, v in fields.items():
        if v is None:
            continue
        if not hasattr(cls, k):  # setattr would quietly add a plain attribute
            raise AttributeError(f"{cls.__name__} has no field {k}")
        setattr(message, k, round(v) if isinstance(v, float) and k not in FLOAT_FIELDS else v)
    return message


# Fields with a scale in the FIT profile: keep the decimals, fit-tool scales them.
FLOAT_FIELDS = {
    "total_elapsed_time", "total_timer_time", "intensity_factor", "training_stress_score", "core_temperature",
    "speed", "distance", "avg_speed", "max_speed", "total_distance",
}


def _ms(when: datetime) -> int:
    """fit-tool timestamps: milliseconds since the Unix epoch."""
    return round(when.timestamp() * 1000)


def _local_timestamp(ms: int) -> int:
    """FIT local_timestamp: seconds since the FIT epoch in local time."""
    utc = datetime.fromtimestamp(ms / 1000, timezone.utc)
    offset = utc.astimezone().utcoffset() or timedelta()
    return int(utc.timestamp()) - FIT_EPOCH_S + int(offset.total_seconds())


def build_fit(ride: RideData, summary: dict, sport_type: str = "VirtualRide") -> bytes:
    sub_sport = SUB_SPORTS[sport_type]
    started = datetime.fromisoformat(ride.meta["started"]).astimezone(timezone.utc)
    at = lambda t: _ms(started + timedelta(seconds=t))  # noqa: E731
    seconds = summary["duration"]  # moving (timer) time
    elapsed = summary.get("elapsed", seconds)  # including pauses
    end = at(ride.ticks[-1]["t"] if ride.ticks else 0)
    core = stats.per_second(ride.metrics.get("core_temp", []), elapsed + 1)
    # pause / resume as timer stop / start events, written in time order between the records
    timer_events = sorted(
        [(p, EventType.STOP_ALL) for p, _ in ride.pauses] + [(r, EventType.START) for _, r in ride.pauses if r is not None],
        key=lambda event: event[0],  # by time only: a pause and its resume can share one, and the stop stays first
    )

    fit = FitFileBuilder(auto_define=True, min_string_size=len(PRODUCT_NAME) + 1)
    fit.add(_message(FileIdMessage, {
        "type": FileType.ACTIVITY, "manufacturer": Manufacturer.DEVELOPMENT.value, "product": 0,
        "serial_number": int(started.timestamp()) & 0x7FFFFFFF, "time_created": at(0),
    }))
    fit.add(_message(DeviceInfoMessage, {
        "timestamp": at(0), "device_index": CREATOR, "manufacturer": Manufacturer.DEVELOPMENT.value,
        "product": 0, "product_name": PRODUCT_NAME,
    }))
    fit.add(_message(EventMessage, {"timestamp": at(0), "event": Event.TIMER, "event_type": EventType.START, "event_group": 0}))

    speeds, distances = summary["series"]["speed"], summary["series"]["distance"]
    for i, tick in enumerate(ride.ticks):
        t = tick["t"]
        while timer_events and timer_events[0][0] <= t:
            when, kind = timer_events.pop(0)
            fit.add(_message(EventMessage, {"timestamp": at(when), "event": Event.TIMER, "event_type": kind, "event_group": 0}))
        fit.add(_message(RecordMessage, {
            "timestamp": at(t),
            "distance": distances[i],
            "speed": speeds[i],
            "power": tick.get("power"),
            "heart_rate": tick.get("hr"),
            "cadence": tick.get("cadence"),
            "core_temperature": core[t] if t < len(core) else None,
        }))

    fit.add(_message(EventMessage, {"timestamp": end, "event": Event.TIMER, "event_type": EventType.STOP_ALL, "event_group": 0}))

    for i, lap in enumerate(summary["intervals"]):
        duration = lap["end_t"] - lap["start_t"]  # wall time, pauses included
        fit.add(_message(LapMessage, {
            "message_index": i,
            "timestamp": at(lap["end_t"]),
            "start_time": at(lap["start_t"]),
            "event": Event.LAP,
            "event_type": EventType.STOP,
            "lap_trigger": LapTrigger.TIME,
            "sport": Sport.CYCLING,
            "sub_sport": sub_sport,
            "total_elapsed_time": float(duration),
            "total_timer_time": float(lap["seconds"]),
            "avg_power": lap["avg_power"],
            "max_power": lap["max_power"],
            "normalized_power": lap["wp"],
            "avg_heart_rate": lap["avg_hr"],
            "max_heart_rate": lap["max_hr"],
            "avg_cadence": lap["avg_cadence"],
            "max_cadence": lap["max_cadence"],
            "total_work": None if lap["avg_power"] is None else lap["avg_power"] * duration,
            "total_distance": lap["distance"],
            "avg_speed": lap["avg_speed"],
            "max_speed": lap["max_speed"],
        }))

    basic, nerd = summary["basic"], summary["nerd"]
    fit.add(_message(SessionMessage, {
        "message_index": 0,
        "timestamp": end,
        "start_time": at(0),
        "event": Event.SESSION,
        "event_type": EventType.STOP,
        "trigger": SessionTrigger.ACTIVITY_END,
        "sport": Sport.CYCLING,
        "sub_sport": sub_sport,
        "total_elapsed_time": float(elapsed),
        "total_timer_time": float(seconds),
        "avg_power": basic["avg_power"],
        "max_power": basic["max_power"],
        # the FIT profile's own field names for weighted power, intensity and training load
        "normalized_power": nerd["wp"],
        "intensity_factor": nerd["intensity"],
        "training_stress_score": nerd["load"],
        "threshold_power": summary["ftp"],
        "avg_heart_rate": basic["avg_hr"],
        "max_heart_rate": basic["max_hr"],
        "avg_cadence": basic["avg_cadence"],
        "max_cadence": basic["max_cadence"],
        "total_work": basic["work_kj"] * 1000,
        "total_distance": basic["distance_km"] * 1000,
        "avg_speed": None if basic["avg_speed_kmh"] is None else basic["avg_speed_kmh"] / 3.6,
        "max_speed": None if basic["max_speed_kmh"] is None else basic["max_speed_kmh"] / 3.6,
        "total_calories": basic["work_kj"],  # ≈ kcal: ~24 % efficiency cancels the kJ→kcal factor
        "first_lap_index": 0,
        "num_laps": len(summary["intervals"]),
    }))
    fit.add(_message(ActivityMessage, {
        "timestamp": end,
        "total_timer_time": float(seconds),
        "num_sessions": 1,
        "type": Activity.MANUAL,
        "event": Event.ACTIVITY,
        "event_type": EventType.STOP,
        "local_timestamp": _local_timestamp(end),
    }))
    return fit.build().to_bytes()
