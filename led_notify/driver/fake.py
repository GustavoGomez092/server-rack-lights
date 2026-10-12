"""In-memory StripPort for tests and --fake-strip runs; records every command."""
from __future__ import annotations


class FakeStrip:
    def __init__(self):
        self.calls: list[tuple] = []

    async def power(self, on: bool) -> None:
        self.calls.append(("power", on))

    async def color(self, r: int, g: int, b: int, level: int) -> None:
        self.calls.append(("color", (r, g, b), level))

    async def effect(self, fx: int) -> None:
        self.calls.append(("effect", fx))

    async def speed(self, v: int) -> None:
        self.calls.append(("speed", v))

    async def brightness(self, level: int) -> None:
        self.calls.append(("brightness", level))

    def status(self) -> dict:
        return {"connected": True, "fake": True, "attempt": 0, "last_error": ""}
