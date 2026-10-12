"""Checks that AdGuard (local DNS) answers."""
from __future__ import annotations

from ..events import Event
from .base import Debouncer, PollingMonitor


async def dns_ok(server: str, name: str, timeout_s: float = 2.0) -> bool:
    import dns.asyncresolver

    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = [server]
    resolver.lifetime = timeout_s
    try:
        await resolver.resolve(name, "A")
        return True
    except Exception:
        return False


class DnsMonitor(PollingMonitor):
    name = section = "dns"

    def __init__(self, settings, clock, probe=dns_ok):
        super().__init__(settings, clock)
        self.probe = probe
        self.down = Debouncer()

    async def check(self) -> list[Event]:
        cfg = self.cfg
        ok = await self.probe(cfg["server"], cfg["query"])
        change = self.down.feed(ok, cfg["fail_rounds"], cfg["ok_rounds"])
        return [Event("dns.down", change, source=self.name)] if change else []
