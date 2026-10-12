"""Turns a Pattern into strip commands."""
from __future__ import annotations

from ..patterns import Color, Effect, FxStep, Off, Sequence, Solid

DEFAULT_SPEED = 5  # sent for effects without a speed, so they never inherit the previous one


class Player:
    def __init__(self, strip, clock):
        self.strip = strip
        self.clock = clock
        self._on: bool | None = None

    async def _power(self, on: bool) -> None:
        if self._on != on:
            await self.strip.power(on)
            self._on = on

    async def show(self, pattern, level: int) -> None:
        """Effects and colours return once sent; sequences return when finished."""
        level = max(0, min(255, int(level)))
        if level == 0:
            await self._power(False)
            return
        await self._power(True)
        if isinstance(pattern, Color):
            await self.strip.color(*pattern.rgb, level)
        elif isinstance(pattern, Effect):
            await self.strip.effect(pattern.fx)
            await self.strip.speed(DEFAULT_SPEED if pattern.speed is None else pattern.speed)
            await self.strip.brightness(level)
        elif isinstance(pattern, Sequence):
            for _ in range(pattern.repeat):
                for step in pattern.steps:
                    await self._step(step, level)
        else:
            raise TypeError(f"not a pattern: {pattern!r}")

    async def _step(self, step, level: int) -> None:
        if isinstance(step, Solid):
            await self.strip.color(*step.rgb, level)
        elif isinstance(step, FxStep):
            await self.strip.effect(step.fx)
            await self.strip.brightness(level)
        elif isinstance(step, Off):
            await self.strip.color(0, 0, 0, level)
        await self.clock.sleep(step.ms / 1000)
