"""Owns the single BLE connection to the strip: connect, keep alive, reconnect, replay."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 60.0
RELEASE_TIMEOUT_S = 5.0
NOT_FOUND = "Device not found"


def _default_factory(address: str):
    from .banlanx_ctl import Strip

    return Strip(address, family="6xx")


def bluez_device_path(address: str, adapter: str = "hci0") -> str:
    return f"/org/bluez/{adapter}/dev_{address.upper().replace(':', '_')}"


async def bluez_release(address: str) -> None:
    """Ask BlueZ to drop any connection it still holds to the strip (org.bluez.Device1.Disconnect).

    After a container restart the old process never released the link: BlueZ keeps it
    "Connected", the strip stops advertising and the scan-based connect never finds it.
    """
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(destination="org.bluez", path=bluez_device_path(address),
                                       interface="org.bluez.Device1", member="Disconnect"))
        if reply.message_type == MessageType.ERROR:
            log.debug("bluez disconnect %s: %s %s", address, reply.error_name, reply.body)
        else:
            log.info("released stale BlueZ connection to %s", address)
    finally:
        bus.disconnect()


class StripLink:
    def __init__(self, address: str, clock, strip_factory=None, keepalive_s: float = 30.0, release=None):
        self.address = address
        self.clock = clock
        self.keepalive_s = keepalive_s
        self._factory = strip_factory or _default_factory
        self._release = release or bluez_release
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
        await self._release_stale()  # a previous process may have left BlueZ holding the link
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
                if not self._broken:  # a failed write already recorded the root cause
                    self.last_error = f"{type(e).__name__}: {e}"
                log.warning("strip link: %s", self.last_error)
                if isinstance(e, SystemExit) and NOT_FOUND in str(e):
                    await self._release_stale()  # not advertising: BlueZ may still hold a stale link
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
        # Hold the lock for the whole replay and read _desired live at each step, so a
        # command arriving mid-replay is never overwritten by a stale snapshot.
        async with self._lock:
            s = self._strip
            d = self._desired
            if "power" in d:
                await (s.on() if d["power"] else s.off())
            mode = d.get("mode")
            if mode and mode[0] == "color":
                await s.color(*mode[1])
            elif mode:
                await s.effect(mode[1])
                if "speed" in d:
                    await s.speed(d["speed"])
                if "brightness" in d:
                    await s.brightness(d["brightness"])

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

    async def _release_stale(self) -> None:
        try:
            await asyncio.wait_for(self._release(self.address), RELEASE_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except (Exception, SystemExit) as e:  # never let the release step break the link loop
            log.debug("release of %s failed: %s: %s", self.address, type(e).__name__, e)

    async def _safe_disconnect(self) -> None:
        if self._strip is None:
            return
        try:
            await self._strip.disconnect()
        except Exception:
            pass
