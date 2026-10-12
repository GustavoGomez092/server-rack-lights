"""Internet reachability and quality via ICMP ping."""
from __future__ import annotations

import asyncio
import re
import statistics
from collections import deque

from ..events import Event
from .base import Debouncer, PollingMonitor

_TIME = re.compile(r"time[=<]([\d.]+)\s*ms")


def parse_ping(text: str) -> float | None:
    m = _TIME.search(text)
    return float(m.group(1)) if m else None


async def ping(host: str, timeout_s: float = 1.0) -> float | None:
    """Round-trip time in ms, or None if the host didn't answer (Linux iputils ping)."""
    proc = await asyncio.create_subprocess_exec(
        "ping", "-n", "-c", "1", "-W", str(max(1, int(timeout_s))), host,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return parse_ping(out.decode(errors="replace")) if proc.returncode == 0 else None


class InternetMonitor(PollingMonitor):
    name = section = "internet"

    def __init__(self, settings, clock, ping=ping):
        super().__init__(settings, clock)
        self.ping = ping
        self.down = Debouncer()
        self.slow_active = False
        self._window: deque[tuple[float, list]] = deque()
        self._good_since: float | None = None

    async def check(self) -> list[Event]:
        cfg = self.cfg
        t = self.clock.monotonic()
        rtts = list(await asyncio.gather(*(self.ping(h) for h in cfg["targets"])))
        events: list[Event] = []
        change = self.down.feed(any(r is not None for r in rtts), cfg["fail_rounds"], cfg["ok_rounds"])
        if change == "start":
            gateway = await self.ping(cfg["gateway"])
            events.append(Event("internet.down", "start", source=self.name,
                                data={"gateway_reachable": gateway is not None}))
        elif change == "clear":
            events.append(Event("internet.down", "clear", source=self.name))
        events += self._check_slow(t, rtts, cfg)
        return events

    def _check_slow(self, t: float, rtts: list, cfg: dict) -> list[Event]:
        if self.down.active:
            # Outage samples must not count toward slow: judge only post-recovery data.
            self._window.clear()
            self._good_since = None
            return self._end_slow()
        self._window.append((t, rtts))
        while t - self._window[0][0] > cfg["slow_window_s"]:
            self._window.popleft()
        samples = [r for _, rs in self._window for r in rs]
        good = [r for r in samples if r is not None]
        loss_pct = 100 * (len(samples) - len(good)) / len(samples)
        median = statistics.median(good) if good else 0.0
        if not self.slow_active:
            covered = t - self._window[0][0] >= cfg["slow_window_s"] - cfg["interval_s"]
            if covered and (median > cfg["slow_rtt_ms"] or loss_pct > cfg["slow_loss_pct"]):
                self.slow_active = True
                self._good_since = None
                return [Event("internet.slow", "start", source=self.name,
                              data={"median_ms": round(median, 1), "loss_pct": round(loss_pct, 1)})]
            return []
        within = median <= cfg["slow_rtt_ms"] and loss_pct <= cfg["slow_loss_pct"]
        if not within:
            self._good_since = None
            return []
        if self._good_since is None:
            self._good_since = t
        if t - self._good_since >= cfg["slow_window_s"]:
            return self._end_slow()
        return []

    def _end_slow(self) -> list[Event]:
        self._good_since = None
        if not self.slow_active:
            return []
        self.slow_active = False
        return [Event("internet.slow", "clear", source=self.name)]
