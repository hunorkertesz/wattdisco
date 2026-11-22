import pytest

from wattdisco.core.workout_import import WorkoutFileError, import_workout

ZWO = """<workout_file>
    <author>Someone</author>
    <name>Over-unders</name>
    <description>Classic</description>
    <sportType>bike</sportType>
    <workout>
        <Warmup Duration="600" PowerLow="0.40" PowerHigh="0.75"/>
        <SteadyState Duration="300" Power="0.88" Cadence="90"/>
        <IntervalsT Repeat="3" OnDuration="60" OffDuration="30" OnPower="1.20" OffPower="0.50" Cadence="100" CadenceResting="85">
            <textevent timeoffset="0" message="Go!"/>
        </IntervalsT>
        <Ramp Duration="120" PowerLow="0.60" PowerHigh="0.90"/>
        <FreeRide Duration="300" FlatRoad="1"/>
        <MaxEffort Duration="15"/>
        <Cooldown Duration="300" PowerLow="0.40" PowerHigh="0.65"/>
    </workout>
</workout_file>"""


def kinds(spec):
    return [p["kind"] for p in spec["phases"]]


def test_zwo():
    spec, notes = import_workout("over-unders.zwo", ZWO, 250)
    assert spec["name"] == "Over-unders" and spec["category"] == "custom"
    assert kinds(spec) == ["warmup", "sweetspot", "vo2", "recovery", "vo2", "recovery", "vo2", "recovery",
                           "sweetspot", "endurance", "sprint", "cooldown"]
    warm, steady, on, off = spec["phases"][:4]
    assert (warm["seconds"], warm["pct"], warm["pct_end"]) == (600, 0.4, 0.75)
    assert (steady["pct"], steady["pct_end"], steady["cadence"]) == (0.88, None, 90)
    assert (on["seconds"], on["pct"], on["cadence"]) == (60, 1.2, 100)
    assert (off["seconds"], off["pct"], off["cadence"]) == (30, 0.5, 85)
    ramp = spec["phases"][8]
    assert (ramp["pct"], ramp["pct_end"]) == (0.6, 0.9)
    cool = spec["phases"][-1]
    assert (cool["pct"], cool["pct_end"]) == (0.65, 0.4)  # written low to high, ridden downwards
    assert any("free ride" in n for n in notes) and any("max effort" in n for n in notes)
    assert sum(p["seconds"] for p in spec["phases"]) == 600 + 300 + 3 * 90 + 120 + 300 + 15 + 300


ERG = """[COURSE HEADER]
VERSION = 2
UNITS = ENGLISH
DESCRIPTION = Threshold 2x10
FILE NAME = threshold.erg
FTP = 300
MINUTES WATTS
[END COURSE HEADER]
[COURSE DATA]
0.00\t120
10.00\t210
10.00\t300
20.00\t300
20.00\t150
25.00\t150
25.00\t300
35.00\t300
35.00\t150
40.00\t90
[END COURSE DATA]
"""


def test_erg_uses_the_file_ftp():
    spec, notes = import_workout("threshold.erg", ERG, 250)
    assert spec["name"] == "Threshold 2x10"
    assert kinds(spec) == ["warmup", "threshold", "recovery", "threshold", "cooldown"]
    warm, work = spec["phases"][:2]
    assert (warm["seconds"], warm["pct"], warm["pct_end"]) == (600, 0.4, 0.7)
    assert (work["seconds"], work["pct"], work["pct_end"]) == (600, 1.0, None)
    assert spec["phases"][-1]["pct_end"] == 0.3
    assert notes == []


def test_erg_without_ftp_uses_the_riders():
    text = ERG.replace("FTP = 300\n", "")
    spec, _ = import_workout("threshold.erg", text, 250)
    assert spec["phases"][1]["pct"] == 1.2  # 300 W at FTP 250


MRC = """[COURSE HEADER]
VERSION = 2
UNITS = ENGLISH
DESCRIPTION = Sweet spot
MINUTES PERCENT
[END COURSE HEADER]
[COURSE DATA]
0\t50
5\t50
5\t90
15\t90
15\t90
25\t90
25\t50
30\t50
[END COURSE DATA]
"""


def test_mrc_percent_and_steady_blocks_joined():
    spec, _ = import_workout("ss.MRC", MRC, 250)
    assert kinds(spec) == ["warmup", "sweetspot", "cooldown"]
    assert spec["phases"][1]["seconds"] == 1200  # two 10 min points at 90 % are one block


def test_short_steps_are_joined():
    text = MRC.replace("15\t90\n15\t90\n", "15\t90\n15\t120\n15.05\t120\n15.05\t90\n")
    spec, notes = import_workout("ss.mrc", text, 250)
    assert all(p["seconds"] >= 10 for p in spec["phases"])
    assert sum(p["seconds"] for p in spec["phases"]) == 1800
    assert any("10 s" in n for n in notes)


@pytest.mark.parametrize("name, text, message", [
    ("plan.fit", "x", "reads .zwo"),
    ("bad.zwo", "<workout_file><workout>", "not valid XML"),
    ("empty.zwo", "<workout_file><name>x</name></workout_file>", "no <workout>"),
    ("none.zwo", "<workout_file><workout><textevent/></workout></workout_file>", "no workout steps"),
    ("none.erg", "[COURSE HEADER]\n[END COURSE HEADER]\n", "no \\[COURSE DATA\\]"),
    ("huge.zwo", "x" * 2_000_001, "too big"),
])
def test_unreadable_files(name, text, message):
    with pytest.raises(WorkoutFileError, match=message):
        import_workout(name, text, 250)


def test_too_many_steps():
    points = "\n".join(f"{i}\t{100 + (i % 2) * 100}\n{i + 1}\t{100 + (i % 2) * 100}" for i in range(150))
    with pytest.raises(WorkoutFileError, match="up to 100"):
        import_workout("long.erg", f"MINUTES WATTS\n[COURSE DATA]\n{points}\n", 250)


def test_hard_off_part_keeps_its_zone():
    zwo = ('<workout_file><workout><IntervalsT Repeat="2" OnDuration="120" OffDuration="60" OnPower="0.95" '
           'OffPower="1.05"/><IntervalsT Repeat="1" OnDuration="60" OffDuration="60" OnPower="1.2" OffPower="0.65"/>'
           '</workout></workout_file>')
    spec, _ = import_workout("ou.zwo", zwo, 250)
    assert kinds(spec) == ["threshold", "threshold", "threshold", "threshold", "vo2", "cooldown"]
