"""Test doubles shared across the suite."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")


class FakeClock:
    """Deterministic clock: sleep() advances time instantly unless `gate` is set."""

    def __init__(self, start: datetime | None = None):
        self._start = start or datetime(2026, 10, 11, 12, 0, tzinfo=TZ)
        self._mono = 1000.0
        self.sleeps: list[float] = []
        self.gate: asyncio.Event | None = None

    def monotonic(self) -> float:
        return self._mono

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._mono - 1000.0)

    def advance(self, seconds: float) -> None:
        self._mono += seconds

    def set_time(self, hhmm: str) -> None:
        """Move the wall clock to HH:MM on the same date (monotonic time is untouched)."""
        h, m = map(int, hhmm.split(":"))
        target = self.now().replace(hour=h, minute=m, second=0, microsecond=0)
        self._start += target - self.now()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.gate is not None:
            await self.gate.wait()
        self._mono += seconds
        await asyncio.sleep(0)


async def pump(n: int = 40) -> None:
    """Let background tasks (e.g. a looping steady sequence) run for a few loop iterations."""
    for _ in range(n):
        await asyncio.sleep(0)


async def settle(arbiter, rounds: int = 20) -> None:
    """Run arbiter ticks until queued one-shots have played (FakeClock makes them instant).

    Only one-shots are awaited: a steady Sequence loops forever in its own task, so it is
    never awaited here (use pump() to let it advance)."""
    for _ in range(rounds):
        await arbiter.tick()
        task = arbiter.playing_task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
