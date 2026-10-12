"""Time source, injectable so tests can control it."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def now(self) -> datetime: ...
    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def __init__(self, tz: str | None = None):
        self.tz = ZoneInfo(tz) if tz else None

    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(self.tz) if self.tz else datetime.now().astimezone()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
