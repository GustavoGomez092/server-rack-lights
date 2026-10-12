import pytest

from led_notify.events import Event
from led_notify.monitors.base import Debouncer, Hysteresis, PollingMonitor
from led_notify.monitors.dns import DnsMonitor, answered
from led_notify.monitors.internet import InternetMonitor, parse_ping

TARGETS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")


def pinger(results: dict):
    async def ping(host, timeout_s=1.0):
        return results.get(host)
    return ping


def names(events):
    return [(e.name, e.kind) for e in events]


# ---- base -----------------------------------------------------------------------
def test_debouncer():
    d = Debouncer()
    assert [d.feed(False, 3, 2) for _ in range(3)] == [None, None, "start"]
    assert d.feed(False, 3, 2) is None
    assert d.feed(True, 3, 2) is None
    assert d.feed(False, 3, 2) is None  # a failure resets the ok streak
    assert [d.feed(True, 3, 2) for _ in range(2)] == [None, "clear"]
    assert d.active is False


def test_hysteresis_with_sustain():
    h = Hysteresis()
    assert h.feed(86, 0, 85, 75, 120) is None
    assert h.feed(86, 60, 85, 75, 120) is None
    assert h.feed(86, 120, 85, 75, 120) == "start"
    assert h.feed(80, 150, 85, 75, 120) is None
    assert h.feed(74, 180, 85, 75, 120) == "clear"
    assert h.feed(86, 200, 85, 75, 120) is None
    assert h.feed(84, 250, 85, 75, 120) is None  # dipped below: sustain restarts
    assert h.feed(86, 300, 85, 75, 120) is None


class Counting(PollingMonitor):
    name = section = "dns"

    def __init__(self, settings, clock):
        super().__init__(settings, clock)
        self.n = 0

    async def check(self):
        self.n += 1
        if self.n > 2:
            raise RuntimeError("stop")
        return [Event("x", source="t")]


async def test_polling_monitor_emits_heartbeats_and_sleeps(settings, clock):
    m = Counting(settings, clock)
    beats, got = [], []
    m.heartbeat = lambda: beats.append(1)

    async def emit(ev):
        got.append(ev.name)

    with pytest.raises(RuntimeError):
        await m.run(emit)
    assert got == ["x", "x"] and beats == [1, 1] and clock.sleeps == [30, 30]


# ---- internet -------------------------------------------------------------------
def test_parse_ping():
    assert parse_ping("64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=12.4 ms") == 12.4
    assert parse_ping("64 bytes from 192.168.0.1: icmp_seq=1 ttl=64 time<1 ms") == 1.0
    assert parse_ping("1 packets transmitted, 0 received") is None


async def test_internet_down_after_three_failed_rounds_and_up_after_two(settings, clock):
    res = {}
    m = InternetMonitor(settings, clock, ping=pinger(res))
    assert await m.check() == []
    assert await m.check() == []
    evs = await m.check()
    assert names(evs) == [("internet.down", "start")] and evs[0].data == {"gateway_reachable": False}
    res["1.1.1.1"] = 12.0
    assert await m.check() == []
    assert names(await m.check()) == [("internet.down", "clear")]


async def test_internet_down_records_reachable_gateway(settings, clock):
    m = InternetMonitor(settings, clock, ping=pinger({"192.168.0.1": 1.0}))
    for _ in range(2):
        await m.check()
    assert (await m.check())[0].data == {"gateway_reachable": True}


async def test_one_good_target_keeps_internet_up(settings, clock):
    m = InternetMonitor(settings, clock, ping=pinger({"8.8.8.8": 20.0}))
    for _ in range(5):
        assert [e for e in await m.check() if e.name == "internet.down"] == []


async def test_internet_slow_after_full_window_and_clears_after_five_good_minutes(settings, clock):
    res = {h: 200.0 for h in TARGETS}
    m = InternetMonitor(settings, clock, ping=pinger(res))
    events = []
    for _ in range(31):
        events += await m.check()
        clock.advance(10)
    assert names(events) == [("internet.slow", "start")]
    res.update({h: 20.0 for h in TARGETS})
    events = []
    # The 200 ms samples leave the window after ~15 rounds; the window is then within limits
    # for 5 min, so the clear lands on the 46th post-recovery round (was 31st with the old rule).
    for _ in range(45):
        events += await m.check()
        clock.advance(10)
    assert events == []
    assert names(await m.check()) == [("internet.slow", "clear")]


async def test_internet_slow_from_packet_loss(settings, clock):
    m = InternetMonitor(settings, clock, ping=pinger({"1.1.1.1": 20.0, "8.8.8.8": 20.0}))
    events = []
    for _ in range(31):
        events += await m.check()
        clock.advance(10)
    assert names(events) == [("internet.slow", "start")]


async def test_internet_down_supersedes_slow(settings, clock):
    res = {h: 200.0 for h in TARGETS}
    m = InternetMonitor(settings, clock, ping=pinger(res))
    for _ in range(31):
        await m.check()
        clock.advance(10)
    res.clear()
    events = []
    for _ in range(3):
        events += await m.check()
    assert ("internet.down", "start") in names(events) and ("internet.slow", "clear") in names(events)


# ---- dns --------------------------------------------------------------------------
async def test_dns_down_and_up(settings, clock):
    state = {"ok": False}
    seen = []

    async def probe(server, name, timeout_s=2.0):
        seen.append((server, name))
        return state["ok"]

    m = DnsMonitor(settings, clock, probe=probe)
    assert [names(await m.check()) for _ in range(3)] == [[], [], [("dns.down", "start")]]
    state["ok"] = True
    assert [names(await m.check()) for _ in range(2)] == [[], [("dns.down", "clear")]]
    assert seen[0] == ("127.0.0.1", "cloudflare.com")


@pytest.mark.parametrize("outage_rounds", [3, 35])  # 35 > the 30-round window: the outage fills it
async def test_outage_does_not_poison_slow_window(settings, clock, outage_rounds):
    res = {h: 20.0 for h in TARGETS}
    m = InternetMonitor(settings, clock, ping=pinger(res))
    events = []
    for _ in range(31):
        events += await m.check()
        clock.advance(10)
    res.clear()
    for _ in range(outage_rounds):
        events += await m.check()
        clock.advance(10)
    assert ("internet.down", "start") in names(events)
    res.update({h: 20.0 for h in TARGETS})
    for _ in range(2):
        events += await m.check()
        clock.advance(10)
    assert ("internet.down", "clear") in names(events)
    for _ in range(31):
        events += await m.check()
        clock.advance(10)
    assert ("internet.slow", "start") not in names(events)


async def test_slow_clears_despite_occasional_dropped_ping(settings, clock):
    res = {h: 200.0 for h in TARGETS}
    m = InternetMonitor(settings, clock, ping=pinger(res))
    events = []
    for _ in range(31):
        events += await m.check()
        clock.advance(10)
    assert names(events) == [("internet.slow", "start")]
    res.update({h: 20.0 for h in TARGETS})
    events = []
    for i in range(150):
        res["8.8.8.8"] = None if i % 10 == 0 else 20.0  # one dropped ping every 10 rounds
        events += await m.check()
        clock.advance(10)
    assert ("internet.down", "start") not in names(events)
    assert ("internet.slow", "clear") in names(events)


# ---- final fixes: AdGuard is up if it answers at all (F7) ---------------------------
def _dns_errors(with_response: bool):
    import dns.message
    import dns.rcode

    q = dns.message.make_query("cloudflare.com", "A")
    if with_response:
        r = dns.message.make_response(q)
        r.set_rcode(dns.rcode.SERVFAIL)
        return q, [("127.0.0.1", False, 53, "SERVFAIL", r)]
    return q, [("127.0.0.1", False, 53, ConnectionRefusedError(111, "Connection refused"), None)]


def test_any_dns_response_counts_as_up():
    import dns.message
    import dns.resolver

    q, errors = _dns_errors(with_response=True)
    assert answered(None)
    assert answered(dns.resolver.NoNameservers(request=q, errors=errors))  # SERVFAIL: upstream down
    assert answered(dns.resolver.NXDOMAIN(qnames=[q.question[0].name]))
    assert answered(dns.resolver.NoAnswer(response=dns.message.make_response(q)))


def test_timeouts_and_connection_errors_count_as_down():
    import dns.resolver

    q, errors = _dns_errors(with_response=False)
    assert not answered(dns.resolver.NoNameservers(request=q, errors=errors))  # connection refused
    assert not answered(dns.resolver.LifetimeTimeout(timeout=2.0, errors=[]))
    assert not answered(OSError("network unreachable"))


async def test_dns_ok_down_when_nothing_listens():
    from led_notify.monitors import dns as dns_mod

    # 127.0.0.1:53 may or may not run a resolver on a dev box; an unroutable TEST-NET address never answers.
    assert await dns_mod.dns_ok("192.0.2.1", "cloudflare.com", timeout_s=0.3) is False
