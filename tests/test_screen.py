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
