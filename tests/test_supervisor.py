import asyncio

from led_notify.events import Event
from led_notify.hub import Hub
from led_notify.monitors.base import MonitorDisabled
from led_notify.monitors.supervisor import Supervisor


class Flaky:
    name = "flaky"

    def __init__(self):
        self.runs = 0

    async def run(self, emit):
        self.runs += 1
        await emit(Event("x", source="flaky"))
        raise RuntimeError("boom")


class Unconfigured:
    name = "adguard"

    def __init__(self):
        self.runs = 0

    async def run(self, emit):
        self.runs += 1
        raise MonitorDisabled("set credentials")


class Beating:
    name = "beating"

    async def run(self, emit):
        self.heartbeat()
        await asyncio.Event().wait()


async def spin(n=10):
    for _ in range(n):
        await asyncio.sleep(0)


async def test_restarts_with_backoff_and_reports_error(clock):
    got = []

    async def emit(ev):
        got.append(ev.name)

    m = Flaky()
    sup = Supervisor([m], emit, clock)
    task = asyncio.create_task(sup.run_one(m))
    await spin()
    assert m.runs >= 3 and clock.sleeps[:3] == [5.0, 10.0, 20.0] and got[:3] == ["x", "x", "x"]
    assert sup.health["flaky"]["status"] == "error" and "boom" in sup.health["flaky"]["detail"]
    task.cancel()


async def test_disabled_monitor_retries_every_minute_without_flapping(clock):
    hub = Hub()
    q = hub.subscribe(maxsize=100)
    m = Unconfigured()
    sup = Supervisor([m], None, clock, hub)
    task = asyncio.create_task(sup.run_one(m))
    await spin()
    assert m.runs >= 2 and clock.sleeps[:2] == [60, 60]
    assert sup.health["adguard"] == {"status": "disabled", "detail": "set credentials",
                                     "since": sup.health["adguard"]["since"]}
    assert q.qsize() == 1      # one change: starting -> disabled
    task.cancel()


async def test_heartbeat_marks_ok(clock):
    m = Beating()
    sup = Supervisor([m], None, clock)
    task = asyncio.create_task(sup.run())
    await spin()
    assert sup.health["beating"]["status"] == "ok"
    task.cancel()


async def test_run_with_no_monitors_returns(clock):
    await Supervisor([], None, clock).run()
