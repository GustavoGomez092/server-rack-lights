"""Samples everything the rack screen shows every couple of seconds; each part fails on its own."""
from __future__ import annotations

import logging

from .host import cpu_pct, cpu_times, disk, memory, uptime_s

log = logging.getLogger(__name__)
TOP_APPS = 5


class StatsSampler:
    def __init__(self, clock, apps, disks: list[tuple[str, str]], interval_s: float = 2.0, *,
                 read_cpu=cpu_times, read_mem=memory, read_cpu_temp=None, read_drive_temp=None,
                 read_uptime=uptime_s):
        self.clock = clock
        self.apps = apps
        self.disks = disks
        self.interval_s = interval_s
        self.read_cpu, self.read_mem, self.read_uptime = read_cpu, read_mem, read_uptime
        self.read_cpu_temp, self.read_drive_temp = read_cpu_temp, read_drive_temp
        self._cpu_prev = None
        self._failing: set[str] = set()
        self.latest: dict | None = None

    def _part(self, name: str, fn):
        if fn is None:
            return None
        try:
            value = fn()
        except Exception as e:
            if name not in self._failing:  # log once per outage, not every 2 s
                log.warning("screen stats: %s failed: %s", name, e)
                self._failing.add(name)
            return None
        self._failing.discard(name)
        return value

    def _cpu(self) -> float:
        cur = self.read_cpu()
        prev, self._cpu_prev = self._cpu_prev, cur
        return round(cpu_pct(prev, cur), 1) if prev else 0.0

    async def sample_once(self) -> None:
        try:
            apps = (await self.apps.sample())[:TOP_APPS]
        except Exception as e:
            log.warning("screen stats: apps failed: %s", e)
            apps = None
        cpu_t = self._part("cpu temp", self.read_cpu_temp)
        drive_t = self._part("drive temp", self.read_drive_temp)
        self.latest = {
            "cpu_pct": self._part("cpu", self._cpu),
            "memory": self._part("memory", self.read_mem),
            "temps": {"cpu_c": None if cpu_t is None else round(cpu_t, 1),
                      "drive_c": None if drive_t is None else round(drive_t, 1)},
            "disks": [self._part(f"disk {label}", lambda l=label, p=path: disk(l, p)) for label, path in self.disks],
            "apps": apps,
            "uptime_s": self._part("uptime", self.read_uptime),
        }

    async def run(self) -> None:
        while True:
            try:
                await self.sample_once()
            except Exception:
                log.exception("screen stats sample failed")
            await self.clock.sleep(self.interval_s)
