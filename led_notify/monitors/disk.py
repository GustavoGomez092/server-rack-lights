"""Disk usage and SMART health."""
from __future__ import annotations

import asyncio
import os
import re

from ..events import Event
from .base import Debouncer, Hysteresis, PollingMonitor

_ATA = re.compile(r"overall-health self-assessment test result:\s*(\w+)")
_SCSI = re.compile(r"SMART Health Status:\s*(\w+)")


def disk_usage_pct(path: str) -> float:
    """Used space as `df` reports it (reserved blocks count as unavailable)."""
    st = os.statvfs(path)
    used = st.f_blocks - st.f_bfree
    total = used + st.f_bavail
    return 100.0 * used / total if total else 0.0


def parse_smart(text: str) -> bool | None:
    m = _ATA.search(text)
    if m:
        return m.group(1).upper() == "PASSED"
    m = _SCSI.search(text)
    if m:
        return m.group(1).upper() == "OK"
    return None


SMART_TIMEOUT_S = 30.0


async def smart_health(device: str) -> bool | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "smartctl", "-H", device, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("smartctl not found on PATH") from exc
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), SMART_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"smartctl timed out on {device}") from exc
    return parse_smart(out.decode(errors="replace"))


class DiskMonitor(PollingMonitor):
    name = section = "disk"

    def __init__(self, settings, clock, usage=disk_usage_pct, smart=smart_health):
        super().__init__(settings, clock)
        self.usage = usage
        self.smart = smart
        self.full = Hysteresis()
        self.smart_bad = Debouncer()
        self._last_smart: float | None = None

    def interval_s(self) -> float:
        return self.cfg["usage_interval_s"]

    async def check(self) -> list[Event]:
        events = self.check_usage()
        now = self.clock.monotonic()
        if self._last_smart is None or now - self._last_smart >= self.cfg["smart_interval_s"]:
            events += await self.check_smart()
            self._last_smart = now
        return events

    def check_usage(self) -> list[Event]:
        cfg = self.cfg
        path, pct = max(((p, self.usage(p)) for p in cfg["paths"]), key=lambda kv: kv[1])
        change = self.full.feed(pct, self.clock.monotonic(), cfg["full_pct"], cfg["clear_pct"])
        return [Event("disk.full", change, source=self.name, data={"path": path, "pct": round(pct, 1)})] if change else []

    async def check_smart(self) -> list[Event]:
        devices = self.cfg["smart_devices"]
        if not devices:
            return []
        results = [(d, await self.smart(d)) for d in devices]
        failed = [d for d, verdict in results if verdict is None]
        if failed:
            raise RuntimeError(f"smartctl returned no health verdict for {', '.join(failed)} (check device access)")
        change = self.smart_bad.feed(all(verdict for _, verdict in results), 1, 1)
        return [Event("disk.smart_error", change, source=self.name)] if change else []
