"""Owns the single BLE connection to the strip: connect, keep alive, reconnect, replay."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 60.0


def _default_factory(address: str):
    from .banlanx_ctl import Strip

    return Strip(address, family="6xx")


class StripLink:
    def __init__(self, address: str, clock, strip_factory=None, keepalive_s: float = 30.0):
        self.address = address
        self.clock = clock
        self.keepalive_s = keepalive_s
        self._factory = strip_factory or _default_factory
        self._strip = None
        self._lock = asyncio.Lock()
        self._desired: dict = {}
        self._broken = False
        self.connected = False
        self.attempt = 0
        self.last_error = ""

    def status(self) -> dict:
        return {"connected": self.connected, "address": self.address, "attempt": self.attempt,
                "last_error": self.last_error}

    # ---- StripPort ---------------------------------------------------------------
    async def power(self, on: bool) -> None:
        self._desired["power"] = on
        await self._do(lambda s: s.on() if on else s.off())

    async def color(self, r: int, g: int, b: int, level: int) -> None:
        self._desired["mode"] = ("color", (r, g, b, level))
        await self._do(lambda s: s.color(r, g, b, level))

    async def effect(self, fx: int) -> None:
        self._desired["mode"] = ("effect", fx)
        await self._do(lambda s: s.effect(fx))

    async def speed(self, v: int) -> None:
        self._desired["speed"] = v
        await self._do(lambda s: s.speed(v))

    async def brightness(self, level: int) -> None:
        self._desired["brightness"] = level
        await self._do(lambda s: s.brightness(level))

    # ---- connection lifecycle -------------------------------------------------------
    async def run(self) -> None:
        backoff = BACKOFF_START_S
        while True:
            try:
                self._strip = self._factory(self.address)
                self._broken = False
                await self._strip.connect()
                self.connected = True
                self.attempt = 0
                self.last_error = ""
                backoff = BACKOFF_START_S
                log.info("strip %s connected", self.address)
                await self._replay()
                await self._watch()
            except asyncio.CancelledError:
                self.connected = False
                await self._safe_disconnect()
                raise
            except (Exception, SystemExit) as e:  # banlanx_ctl raises SystemExit when the strip isn't found
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("strip link: %s", self.last_error)
            self.connected = False
            await self._safe_disconnect()
            self.attempt += 1
            await self.clock.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def _watch(self) -> None:
        last = self.clock.monotonic()
        while not self._broken and self._strip.client is not None and self._strip.client.is_connected:
            await self.clock.sleep(1.0)
            if self.clock.monotonic() - last >= self.keepalive_s:
                last = self.clock.monotonic()
                async with self._lock:
                    await self._strip.status(timeout=3.0)
        raise ConnectionError("strip disconnected")

    async def _replay(self) -> None:
        d = dict(self._desired)
        if "power" in d:
            on = d["power"]
            await self._do(lambda s: s.on() if on else s.off())
        mode = d.get("mode")
        if mode and mode[0] == "color":
            await self._do(lambda s: s.color(*mode[1]))
        elif mode:
            await self._do(lambda s: s.effect(mode[1]))
            if "speed" in d:
                await self._do(lambda s: s.speed(d["speed"]))
            if "brightness" in d:
                await self._do(lambda s: s.brightness(d["brightness"]))

    async def _do(self, fn) -> None:
        if not self.connected or self._strip is None:
            return
        try:
            async with self._lock:
                await fn(self._strip)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            self._broken = True
            self.connected = False

    async def _safe_disconnect(self) -> None:
        if self._strip is None:
            return
        try:
            await self._strip.disconnect()
        except Exception:
            pass
