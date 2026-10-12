"""Spikes in blocked DNS queries from AdGuard Home's query log."""
from __future__ import annotations

import re
from collections import deque
from datetime import datetime

import httpx

from ..events import Event
from .base import MonitorDisabled, PollingMonitor

_EXTRA_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_querylog_times(payload: dict) -> list[datetime]:
    times = []
    for entry in payload.get("data") or []:
        stamp = _EXTRA_FRACTION.sub(r"\1", entry["time"]).replace("Z", "+00:00")
        times.append(datetime.fromisoformat(stamp))
    return times


async def adguard_blocked_times(url: str, user: str, password: str, limit: int = 500) -> list[datetime]:
    async with httpx.AsyncClient(timeout=10, auth=(user, password)) as client:
        resp = await client.get(f"{url.rstrip('/')}/control/querylog",
                                params={"response_status": "blocked", "limit": limit})
        resp.raise_for_status()
        return parse_querylog_times(resp.json())


MIN_HISTORY = 5  # no baseline yet right after a restart: never alert on the first polls
COOLDOWN_POLLS = 15  # after an alert, stay quiet for 15 polls (15 min at interval_s=60)


class SpikeDetector:
    def __init__(self):
        self._history: deque[int] = deque(maxlen=60)
        self._cooldown = 0

    def feed(self, count: int, min_blocked: int, factor: float) -> bool:
        if len(self._history) < MIN_HISTORY:
            self._history.append(count)
            return False
        if self._cooldown > 0:
            # No alerts, and every sample joins the baseline: a rate that stays high becomes the new normal.
            self._cooldown -= 1
            self._history.append(count)
            return False
        avg = sum(self._history) / len(self._history)
        spike = count > min_blocked and count > factor * avg
        if spike:
            self._cooldown = COOLDOWN_POLLS
        else:  # a lone spike stays out of the baseline it is measured against
            self._history.append(count)
        return spike


class AdguardMonitor(PollingMonitor):
    name = section = "adguard"

    def __init__(self, settings, clock, fetch=adguard_blocked_times):
        super().__init__(settings, clock)
        self.fetch = fetch
        self.detector = SpikeDetector()

    async def check(self) -> list[Event]:
        cfg = self.cfg
        if not (cfg["username"] and cfg["password"]):
            raise MonitorDisabled("set the AdGuard username and password under Rules → Settings")
        times = await self.fetch(cfg["url"], cfg["username"], cfg["password"])
        now = self.clock.now()
        count = sum(1 for t in times if 0 <= (now - t).total_seconds() < 60)
        if self.detector.feed(count, cfg["min_blocked"], cfg["factor"]):
            return [Event("adguard.block_spike", "oneshot", source=self.name, data={"blocked_last_min": count})]
        return []
