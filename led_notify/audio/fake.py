"""Records chimes instead of playing them (tests)."""
from __future__ import annotations


class FakeChimes:
    def __init__(self, library=None):
        self.library = library
        self.chimes: list[tuple[str, int]] = []

    def chime(self, ref: str, volume_pct: int) -> bool:
        self.chimes.append((ref, volume_pct))
        return True

    def status(self) -> dict:
        return {"fake": True}
