from wattdisco.awake import KeepAwake, NoKeepAwake


def test_no_keep_awake_tracks_state():
    a = NoKeepAwake()
    a.hold()
    assert a.held
    a.release()
    a.release()
    assert not a.held


def test_other_platforms_do_nothing_but_count_as_held():
    a = KeepAwake(platform="linux")
    a.hold()
    assert a.held and a._proc is None
    a.release()
    assert not a.held

