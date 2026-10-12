from datetime import datetime

import pytest

from led_notify.engine.quiet import in_window, parse_hhmm


def at(hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, 11, h, m)


@pytest.mark.parametrize("t,expected", [
    ("23:00", True), ("23:30", True), ("00:00", True), ("06:59", True),
    ("07:00", False), ("12:00", False), ("22:59", False),
])
def test_in_window_across_midnight(t, expected):
    assert in_window(at(t), "23:00", "07:00") is expected


@pytest.mark.parametrize("t,expected", [("09:00", True), ("12:00", True), ("08:59", False), ("17:00", False)])
def test_in_window_same_day(t, expected):
    assert in_window(at(t), "09:00", "17:00") is expected


def test_in_window_equal_start_end_is_never():
    assert in_window(at("10:00"), "10:00", "10:00") is False


@pytest.mark.parametrize("bad", ["25:00", "7", "07:60", "", "ab:cd"])
def test_parse_hhmm_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_hhmm(bad)
