from wattdisco.core.bus import Bus


def test_publish_reaches_topic_subscribers_only():
    bus = Bus()
    got_power, got_hr = [], []
    bus.subscribe("power", got_power.append)
    bus.subscribe("hr", got_hr.append)
    bus.publish("power", 1)
    assert got_power == [1]
    assert got_hr == []


def test_subscribe_all_gets_topic_and_payload():
    bus = Bus()
    got = []
    bus.subscribe_all(lambda topic, payload: got.append((topic, payload)))
    bus.publish("hr", 140)
    assert got == [("hr", 140)]


def test_unsubscribe():
    bus = Bus()
    got = []
    unsubscribe = bus.subscribe("power", got.append)
    unsubscribe()
    bus.publish("power", 1)
    assert got == []


def test_failing_handler_does_not_stop_others():
    bus = Bus()
    got = []

    def broken(_payload):
        raise RuntimeError("boom")

    bus.subscribe("power", broken)
    bus.subscribe("power", got.append)
    bus.publish("power", 1)
    assert got == [1]
