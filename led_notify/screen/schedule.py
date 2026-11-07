"""When the rack screen is on, and in which theme, from quiet hours, the sun and recent alerts."""
from __future__ import annotations

from datetime import timedelta

from ..engine.quiet import in_window
from .sun import sun_times


class ScreenSchedule:
    def __init__(self, settings, clock):
        self.settings = settings
        self.clock = clock
        self._wake_until: float | None = None  # monotonic
        self._last_alert: dict | None = None

    def _quiet(self) -> bool:
        q = self.settings.data["quiet_hours"]
        return bool(q["enabled"]) and in_window(self.clock.now(), q["start"], q["end"])

    def note_alert(self, name: str, rule: dict) -> None:
        """Called for every alert the strip shows; during quiet hours that is only `dim` rules."""
        self._last_alert = {"name": name, "time": self.clock.now().isoformat(timespec="seconds"),
                            "priority": rule.get("priority")}
        if self._quiet():
            self._wake_until = self.clock.monotonic() + self.settings.data["screen"]["wake_min"] * 60

    def _awake(self) -> bool:
        return self._wake_until is not None and self.clock.monotonic() < self._wake_until

    def _daylight(self) -> bool:
        now = self.clock.now()
        cfg = self.settings.data["screen"]
        times = sun_times(now.date(), cfg["latitude"], cfg["longitude"], now.tzinfo)
        return times is not None and times[0] <= now < times[1]

    def state(self) -> dict:
        quiet, awake = self._quiet(), self._awake()
        until = None
        if awake:
            left = timedelta(seconds=self._wake_until - self.clock.monotonic())
            until = (self.clock.now() + left).isoformat(timespec="seconds")
        if quiet:
            power, theme = ("on" if awake else "off"), "dark"
        else:
            power, theme = "on", ("light" if self._daylight() else "dark")
        # The panel has no DDC/CI, so brightness is a black film the page draws over itself (percent).
        brightness = self.settings.data["screen"]["brightness_day" if theme == "light" else "brightness_night"]
        return {"power": power, "theme": theme, "brightness": brightness, "quiet": quiet, "awake_until": until,
                "last_alert": self._last_alert}
