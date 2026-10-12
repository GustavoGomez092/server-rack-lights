"""Time-of-day windows (quiet hours, late-night device alerts)."""
from __future__ import annotations

import re
from datetime import datetime, time

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(s: str) -> time:
    m = _HHMM.match(s or "")
    if not m:
        raise ValueError(f"not a HH:MM time: {s!r}")
    return time(int(m.group(1)), int(m.group(2)))


def in_window(now: datetime, start: str, end: str) -> bool:
    s, e, t = parse_hhmm(start), parse_hhmm(end), now.time().replace(second=0, microsecond=0)
    if s == e:
        return False
    if s < e:
        return s <= t < e
    return t >= s or t < e
