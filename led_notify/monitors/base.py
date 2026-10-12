"""Shared monitor plumbing: debouncers and the polling loop."""
from __future__ import annotations

from typing import Awaitable, Callable

from ..events import Event

Emit = Callable[[Event], Awaitable[object]]


class MonitorDisabled(Exception):
    """Raised by a monitor that isn't configured yet; the supervisor retries later."""


class Debouncer:
    """Consecutive-sample debounce for a boolean health signal."""

    def __init__(self):
        self.active = False
        self._fails = 0
        self._oks = 0

    def feed(self, ok: bool, fail_n: int, ok_n: int) -> str | None:
        if ok:
            self._oks += 1
            self._fails = 0
        else:
            self._fails += 1
            self._oks = 0
        if not self.active and self._fails >= fail_n:
            self.active = True
            return "start"
        if self.active and self._oks >= ok_n:
            self.active = False
            return "clear"
        return None


class Hysteresis:
    """Start when value >= hi for sustain_s; clear when value < lo."""

    def __init__(self):
        self.active = False
        self._above_since: float | None = None

    def feed(self, value: float, now: float, hi: float, lo: float, sustain_s: float = 0.0) -> str | None:
        if value >= hi:
            if self._above_since is None:
                self._above_since = now
        else:
            self._above_since = None
        if not self.active and self._above_since is not None and now - self._above_since >= sustain_s:
            self.active = True
            return "start"
        if self.active and value < lo:
            self.active = False
            return "clear"
        return None


class PollingMonitor:
    name = "monitor"
    section = ""

    def __init__(self, settings, clock):
        self.settings = settings
        self.clock = clock
        self.heartbeat: Callable[[], None] = lambda: None

    @property
    def cfg(self) -> dict:
        return self.settings.data[self.section]

    async def check(self) -> list[Event]:
        raise NotImplementedError

    def interval_s(self) -> float:
        return self.cfg["interval_s"]

    async def run(self, emit: Emit) -> None:
        while True:
            events = await self.check()
            self.heartbeat()
            for ev in events:
                await emit(ev)
            await self.clock.sleep(self.interval_s())
