import pytest

from led_notify.clock import SystemClock
from led_notify.events import PRIORITIES, Event
from led_notify.hub import Hub


def test_priority_order():
    assert sorted(PRIORITIES, key=PRIORITIES.get) == ["idle", "low", "normal", "manual", "high", "critical"]


def test_event_defaults():
    ev = Event("backup.done")
    assert (ev.kind, ev.source, ev.data, ev.force, ev.look) == ("oneshot", "webhook", {}, False, None)


@pytest.mark.parametrize("kwargs", [
    dict(name=""),
    dict(name="has space"),
    dict(name="x" * 65),
    dict(name="ok", kind="bogus"),
    dict(name="ok", priority="urgent"),
])
def test_event_rejects_bad_fields(kwargs):
    with pytest.raises(ValueError):
        Event(**kwargs)


def test_event_accepts_dots_colons_dashes():
    assert Event("ci:build-1.done").name == "ci:build-1.done"


def test_duration_is_clamped():
    assert Event("a", duration_s=600).duration_s == 60.0
    assert Event("a", duration_s=0).duration_s == 0.5


def test_hub_delivers_and_drops_oldest_when_full():
    hub = Hub()
    q = hub.subscribe(maxsize=2)
    for i in (1, 2, 3):
        hub.publish("log", i)
    assert [q.get_nowait(), q.get_nowait()] == [("log", 2), ("log", 3)]
    hub.unsubscribe(q)
    hub.publish("log", 4)
    assert q.empty()


def test_system_clock_now_is_timezone_aware():
    assert SystemClock("America/Los_Angeles").now().tzinfo is not None
    assert SystemClock().now().tzinfo is not None


def test_fake_clock_set_time_and_advance(clock):
    clock.set_time("23:30")
    assert clock.now().strftime("%H:%M") == "23:30"
    clock.advance(60)
    assert clock.now().strftime("%H:%M") == "23:31"
