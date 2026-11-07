from datetime import date, datetime

import pytest

from led_notify.screen.sun import sun_times
from led_notify.store.settings import SettingsError
from tests.fakes import TZ

LA = (34.05, -118.24)


def test_screen_defaults(settings):
    assert settings.data["screen"] == {"wake_min": 5, "latitude": 34.05, "longitude": -118.24}
    with pytest.raises(SettingsError):
        settings.update({"screen": {"wake_min": 0}})
    with pytest.raises(SettingsError):
        settings.update({"screen": {"latitude": 91}})


def _near(actual: datetime, hh: int, mm: int, tol_min: float = 3) -> bool:
    target = actual.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return abs((actual - target).total_seconds()) <= tol_min * 60


def test_sun_times_la():
    rise, sset = sun_times(date(2026, 6, 21), *LA, TZ)
    assert rise.tzinfo is not None
    assert _near(rise, 5, 42) and _near(sset, 20, 8)
    rise, sset = sun_times(date(2026, 12, 21), *LA, TZ)
    assert _near(rise, 6, 55) and _near(sset, 16, 47)


def test_sun_times_polar_night_is_none():
    assert sun_times(date(2026, 12, 21), 80.0, 0.0, TZ) is None


# ---- schedule ------------------------------------------------------------------
from led_notify.screen.schedule import ScreenSchedule  # noqa: E402

DIM = {"quiet_behavior": "dim", "priority": "critical"}


def _sched(settings, clock, hhmm):
    clock.set_time(hhmm)
    return ScreenSchedule(settings, clock)


def test_day_light(settings, clock):
    st = _sched(settings, clock, "12:00").state()
    assert (st["power"], st["theme"], st["quiet"]) == ("on", "light", False)


def test_evening_dark(settings, clock):
    st = _sched(settings, clock, "21:00").state()
    assert (st["power"], st["theme"]) == ("on", "dark")


def test_quiet_off(settings, clock):
    st = _sched(settings, clock, "23:30").state()
    assert (st["power"], st["quiet"]) == ("off", True)


def test_wake_at_night(settings, clock):
    s = _sched(settings, clock, "02:00")
    s.note_alert("internet.down", DIM)
    st = s.state()
    assert (st["power"], st["theme"]) == ("on", "dark")
    assert st["last_alert"]["name"] == "internet.down"
    clock.advance(299)
    assert s.state()["power"] == "on"
    clock.advance(2)
    assert s.state()["power"] == "off"


def test_wake_restarts(settings, clock):
    s = _sched(settings, clock, "02:00")
    s.note_alert("internet.down", DIM)
    clock.advance(240)
    s.note_alert("dns.down", DIM)
    clock.advance(240)
    assert s.state()["power"] == "on"
    clock.advance(61)
    assert s.state()["power"] == "off"


def test_wake_length_from_settings(settings, clock):
    settings.update({"screen": {"wake_min": 1}})
    s = _sched(settings, clock, "02:00")
    s.note_alert("internet.down", DIM)
    clock.advance(61)
    assert s.state()["power"] == "off"


def test_wake_runs_into_morning(settings, clock):
    s = _sched(settings, clock, "06:58")
    s.note_alert("internet.down", DIM)
    clock.advance(180)  # 07:01
    st = s.state()
    assert (st["power"], st["quiet"]) == ("on", False)
    assert st["theme"] == "light"  # sunrise on 2026-10-11 in LA is ~06:54


def test_quiet_disabled_never_off(settings, clock):
    s = _sched(settings, clock, "23:30")
    assert s.state()["power"] == "off"
    settings.update({"quiet_hours": {"enabled": False}})
    assert s.state()["power"] == "on"
    settings.update({"quiet_hours": {"enabled": True, "start": "00:00", "end": "01:00"}})
    assert s.state()["power"] == "on"


def test_day_alert_does_not_set_wake(settings, clock):
    s = _sched(settings, clock, "12:00")
    s.note_alert("container.died", {"quiet_behavior": "suppress", "priority": "normal"})
    st = s.state()
    assert st["last_alert"]["name"] == "container.died"
    assert st["awake_until"] is None
