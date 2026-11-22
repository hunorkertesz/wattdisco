import json

import pytest

from wattdisco.config import Settings, load_settings, save_settings, zone_of, zones
from wattdisco.core.workouts import default_workout


def test_defaults():
    s = Settings()
    assert (s.ftp, s.weight_kg) == (240, 75.0)
    assert s.to_dict()["w_per_kg"] == 3.2


def test_zone_of_boundaries_at_240():
    assert zone_of(132, 240) == 1  # 55 %
    assert zone_of(133, 240) == 2
    assert zone_of(180, 240) == 2  # 75 %
    assert zone_of(216, 240) == 4  # 90 % sweet spot
    assert zone_of(240, 240) == 5  # threshold
    assert zone_of(252, 240) == 5  # 105 %
    assert zone_of(253, 240) == 6  # VO2 max


def test_zones_follow_ftp():
    z = zones(240)
    assert [x["n"] for x in z] == [1, 2, 3, 4, 5, 6]
    assert (z[0]["from_w"], z[0]["to_w"]) == (0, 132)
    assert (z[1]["from_w"], z[1]["to_w"]) == (133, 180)
    assert (z[5]["from_w"], z[5]["to_w"]) == (253, None)
    assert zones(300)[3]["to_w"] == 282  # 94 % of 300


@pytest.mark.parametrize("kwargs", [{"ftp": 20}, {"ftp": 900}, {"weight_kg": 10}])
def test_validation(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_save_and_load(tmp_path):
    save_settings(Settings(ftp=265, weight_kg=68.5), tmp_path)
    assert load_settings(tmp_path) == Settings(ftp=265, weight_kg=68.5)


def test_load_missing_or_broken_gives_defaults(tmp_path):
    assert load_settings(tmp_path) == Settings()
    (tmp_path / "settings.json").write_text(json.dumps({"ftp": 5, "weight_kg": 70}))
    assert load_settings(tmp_path) == Settings()


def test_workout_scales_with_ftp():
    w = default_workout(240)
    assert w.ftp == 240
    assert w.phases[1].watts == 216  # 90 %
    assert default_workout(300).phases[1].watts == 270


def test_hr_zones_from_max_and_threshold():
    from wattdisco.config import hr_zone_of, hr_zone_tops, hr_zones

    assert hr_zone_tops("max", 190) == [114, 133, 152, 171]
    z = hr_zones("max", 190)
    assert [(x["from_bpm"], x["to_bpm"]) for x in z] == [(0, 114), (115, 133), (134, 152), (153, 171), (172, None)]
    assert hr_zone_tops("lthr", 170) == [138, 151, 158, 168]
    assert hr_zone_of(150, hr_zone_tops("lthr", 170)) == 2
    assert hr_zone_of(175, hr_zone_tops("lthr", 170)) == 5


def test_hr_settings_validate_and_round_trip(tmp_path):
    s = Settings(hr_method="lthr", lthr=165)
    assert s.hr_reference == 165 and s.to_dict()["hr_zones"][0]["to_bpm"] == 134
    save_settings(s, tmp_path)
    assert load_settings(tmp_path) == s
    with pytest.raises(ValueError):
        Settings(hr_method="karvonen")
    with pytest.raises(ValueError):
        Settings(max_hr=300)


def test_legacy_data_is_copied_once_and_left_in_place(tmp_path):
    from wattdisco.config import adopt_legacy_data

    legacy, data = tmp_path / "checkout-data", tmp_path / "app-folder"
    assert adopt_legacy_data(data, legacy) is False  # no old folder: nothing to do
    assert adopt_legacy_data(data, None) is False  # installed: no checkout at all
    (legacy / "rides" / "r1").mkdir(parents=True)
    (legacy / "rides" / "r1" / "ride.json").write_text("{}")
    (legacy / "settings.json").write_text('{"ftp": 250}')
    assert adopt_legacy_data(data, legacy) is True
    assert (data / "rides" / "r1" / "ride.json").read_text() == "{}"
    assert (data / "settings.json").read_text() == '{"ftp": 250}'
    assert (legacy / "settings.json").exists()  # the old folder stays
    (legacy / "settings.json").write_text('{"ftp": 300}')
    assert adopt_legacy_data(data, legacy) is False  # only once: the app folder wins
    assert (data / "settings.json").read_text() == '{"ftp": 250}'


def test_pages_ship_inside_the_package():
    from wattdisco.config import PACKAGE_DIR, WEB_DIR

    assert WEB_DIR.parent == PACKAGE_DIR
    assert (WEB_DIR / "workout.html").is_file() and (WEB_DIR / "theme.css").is_file()
    assert not (WEB_DIR / "design").exists()  # concept screens stay out of the download


def test_first_start_asks_the_setup_questions_older_settings_do_not(tmp_path):
    import json

    from wattdisco.config import Settings, load_settings, save_settings

    assert load_settings(tmp_path).setup_done is False  # nothing saved yet
    (tmp_path / "settings.json").write_text(json.dumps({"ftp": 250, "weight_kg": 70}))
    assert load_settings(tmp_path).sound is True  # saved before the mute setting existed
    assert load_settings(tmp_path).setup_done is True  # a rider from before the questions existed
    save_settings(Settings(ftp=260, weight_kg=70, units="imperial", setup_done=True, auto_pause=False, sound=False), tmp_path)
    s = load_settings(tmp_path)
    assert (s.units, s.setup_done, s.auto_pause, s.sound) == ("imperial", True, False, False)
