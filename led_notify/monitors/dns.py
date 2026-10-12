"""Checks that AdGuard (local DNS) answers."""
from __future__ import annotations

from ..events import Event
from .base import Debouncer, PollingMonitor


def answered(exc: BaseException | None) -> bool:
    """Did the DNS server send back ANY response?

    During an internet outage AdGuard cannot reach its upstreams and answers SERVFAIL;
    that still means AdGuard itself is up. Only timeouts and connection errors mean down.
    """
    import dns.resolver

    if exc is None:
        return True
    if isinstance(exc, (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.YXDOMAIN)):
        return True
    if isinstance(exc, dns.resolver.NoNameservers):
        # Each error is (server, tcp, port, exception_or_rcode, response); a response means it answered.
        return any(err[-1] is not None for err in exc.kwargs.get("errors") or [])
    return False  # LifetimeTimeout, connection refused, other I/O errors


async def dns_ok(server: str, name: str, timeout_s: float = 2.0) -> bool:
    import dns.asyncresolver

    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = [server]
    resolver.lifetime = timeout_s
    try:
        await resolver.resolve(name, "A")
    except Exception as e:
        return answered(e)
    return True


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
