import asyncio

import pytest

from led_notify.driver.link import StripLink


class FakeClient:
    def __init__(self):
        self.is_connected = True


class FakeBleStrip:
    def __init__(self, fail: bool):
        self.fail = fail
        self.client = None
        self.calls = []
        self.raise_on_write = False

    async def connect(self):
        if self.fail:
            raise SystemExit("Device not found. Is it powered, in range, and NOT connected to the phone app?")
        self.client = FakeClient()

    async def disconnect(self):
        if self.client:
            self.client.is_connected = False

    async def _rec(self, *call):
        if self.raise_on_write:
            raise OSError("write failed")
        self.calls.append(call)

    async def on(self):
        await self._rec("on")

    async def off(self):
        await self._rec("off")

    async def color(self, r, g, b, level=None):
        await self._rec("color", r, g, b, level)

    async def effect(self, fx):
        await self._rec("effect", fx)

    async def speed(self, v):
        await self._rec("speed", v)

    async def brightness(self, level):
        await self._rec("brightness", level)

    async def status(self, timeout=3.0):
        self.calls.append(("status",))
        return {}


def make_factory(fail_first=0):
    made = []

    def factory(address):
        s = FakeBleStrip(fail=len(made) < fail_first)
        made.append(s)
        return s

    return factory, made


async def spin(n=40):
    for _ in range(n):
        await asyncio.sleep(0)


@pytest.fixture
async def running():
    tasks = []

    def start(link):
        tasks.append(asyncio.create_task(link.run()))

    yield start
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_offline_strip_never_raises_and_reports_error(clock, running):
    factory, made = make_factory(fail_first=1000)
    link = StripLink("A6:15:50:62:0A:9D", clock, strip_factory=factory)
    running(link)
    await spin()
    await link.power(True)
    await link.color(1, 2, 3, 100)
    st = link.status()
    assert st["connected"] is False and st["attempt"] >= 2 and "Device not found" in st["last_error"]
    assert clock.sleeps[:4] == [2.0, 4.0, 8.0, 16.0]


async def test_connects_after_retries_and_replays_desired_state(clock, running):
    factory, made = make_factory(fail_first=2)
    link = StripLink("AA", clock, strip_factory=factory)
    await link.power(True)
    await link.effect(0x62)
    await link.speed(5)
    await link.brightness(200)
    running(link)
    await spin()
    assert link.status()["connected"] is True and link.status()["attempt"] == 0
    assert len(made) == 3
    assert made[-1].calls[:4] == [("on",), ("effect", 0x62), ("speed", 5), ("brightness", 200)]


async def test_commands_go_to_connected_strip(clock, running):
    factory, made = make_factory()
    link = StripLink("AA", clock, strip_factory=factory)
    running(link)
    await spin()
    await link.color(9, 8, 7, 50)
    assert ("color", 9, 8, 7, 50) in made[-1].calls


async def test_reconnects_when_link_drops_and_replays(clock, running):
    factory, made = make_factory()
    link = StripLink("AA", clock, strip_factory=factory)
    running(link)
    await spin()
    await link.color(1, 1, 1, 10)
    made[-1].client.is_connected = False
    await spin()
    assert len(made) == 2 and link.status()["connected"] is True
    assert made[-1].calls[0] == ("color", 1, 1, 1, 10)


async def test_write_failure_triggers_reconnect(clock, running):
    factory, made = make_factory()
    link = StripLink("AA", clock, strip_factory=factory)
    running(link)
    await spin()
    made[-1].raise_on_write = True
    await link.effect(3)
    assert "write failed" in link.status()["last_error"]
    await spin()
    assert len(made) == 2 and made[-1].calls[0] == ("effect", 3)


async def test_keepalive_queries_status(clock, running):
    factory, made = make_factory()
    link = StripLink("AA", clock, strip_factory=factory, keepalive_s=5)
    running(link)
    await spin(60)
    assert ("status",) in made[-1].calls
