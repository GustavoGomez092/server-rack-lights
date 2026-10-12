"""Runs monitors, restarts the ones that crash, and tracks their health."""
from __future__ import annotations

import asyncio
import logging

from .base import MonitorDisabled

log = logging.getLogger(__name__)

BACKOFF_START_S = 5.0
BACKOFF_MAX_S = 300.0
DISABLED_RETRY_S = 60
HEALTHY_RUN_S = 60.0


class Supervisor:
    def __init__(self, monitors, emit, clock, hub=None):
        self.monitors = list(monitors)
        self.emit = emit
        self.clock = clock
        self.hub = hub
        self.health = {m.name: {"status": "starting", "detail": "", "since": ""} for m in self.monitors}
        for m in self.monitors:
            m.heartbeat = lambda name=m.name: self._set(name, "ok")

    def _set(self, name: str, status: str, detail: str = "") -> None:
        h = self.health[name]
        if (h["status"], h["detail"]) == (status, detail):
            return
        self.health[name] = {"status": status, "detail": detail,
                             "since": self.clock.now().isoformat(timespec="seconds")}
        if self.hub:
            self.hub.publish("state", None)

    async def run(self) -> None:
        await asyncio.gather(*(self.run_one(m) for m in self.monitors))

    async def run_one(self, m) -> None:
        backoff = BACKOFF_START_S
        while True:
            started = self.clock.monotonic()
            try:
                await m.run(self.emit)
                raise RuntimeError("monitor stopped unexpectedly")
            except asyncio.CancelledError:
                raise
            except MonitorDisabled as e:
                self._set(m.name, "disabled", str(e))
                await self.clock.sleep(DISABLED_RETRY_S)
            except Exception as e:
                log.warning("monitor %s failed: %s", m.name, e)
                if self.clock.monotonic() - started > HEALTHY_RUN_S:
                    backoff = BACKOFF_START_S
                self._set(m.name, "error", f"{type(e).__name__}: {e}")
                await self.clock.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
