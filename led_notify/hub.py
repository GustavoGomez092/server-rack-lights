"""Tiny in-process pub/sub used to push live updates to SSE clients."""
from __future__ import annotations

import asyncio


class Hub:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self, maxsize: int = 100) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, type_: str, data=None) -> None:
        for q in list(self._subs):
            if q.full():
                q.get_nowait()  # a slow client loses its oldest message, never blocks us
            q.put_nowait((type_, data))
