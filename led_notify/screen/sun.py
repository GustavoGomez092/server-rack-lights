"""Local sunrise/sunset without network access (NOAA solar-position approximation)."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone, tzinfo

_ZENITH = 90.833  # sun's upper limb on the horizon, with refraction


def sun_times(day: date, lat: float, lon: float, tz: tzinfo) -> tuple[datetime, datetime] | None:
    """Local (sunrise, sunset) for `day`, or None when the sun never rises or never sets."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)  # fractional year at midnight UTC is accurate enough here
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    cos_ha = math.cos(math.radians(_ZENITH)) / (math.cos(phi) * math.cos(decl)) - math.tan(phi) * math.tan(decl)
    if not -1 <= cos_ha <= 1:
        return None
    ha = math.degrees(math.acos(cos_ha))
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rise = midnight + timedelta(minutes=720 - 4 * (lon + ha) - eqtime)
    sset = midnight + timedelta(minutes=720 - 4 * (lon - ha) - eqtime)
    return rise.astimezone(tz), sset.astimezone(tz)
