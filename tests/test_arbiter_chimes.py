import pytest

from led_notify.audio.fake import FakeChimes
from led_notify.driver.fake import FakeStrip
from led_notify.engine.arbiter import Arbiter
from led_notify.engine.player import Player
from led_notify.events import Event
from tests.fakes import settle


@pytest.fixture
def chimes():
    return FakeChimes()


@pytest.fixture
def arb(clock, settings, chimes):
    return Arbiter(Player(FakeStrip(), clock), settings, clock, chimes=chimes)


async def test_oneshot_chimes_when_shown(arb, chimes):
    await arb.submit(Event("device.new", source="devices", data={"key": "a"}))
    await settle(arb)
    assert chimes.chimes == [("default", 70)]


async def test_oneshot_skipped_by_priority_is_silent(arb, chimes):
    await arb.submit(Event("dns.down", "start"))
    await settle(arb)
    chimes.chimes.clear()
    await arb.submit(Event("device.new", data={"key": "a"}))
    await settle(arb)
    assert chimes.chimes == []


async def test_state_chimes_once(arb, settings, chimes):
    await arb.submit(Event("dns.down", "start"))
    await settle(arb)
    settings.update({"rules": {"dns.down": {"pattern": "red_breath"}}})   # forces a re-render
    await settle(arb)
    await arb.submit(Event("container.died", data={"key": "web"}))       # high state hides normal one-shot
    await settle(arb)
    assert chimes.chimes == [("error", 70)]


async def test_hidden_state_chimes_when_it_surfaces(arb, chimes):
    await arb.submit(Event("internet.down", "start"))     # critical
    await settle(arb)
    await arb.submit(Event("dns.down", "start"))          # high, hidden behind critical
    await settle(arb)
    assert chimes.chimes == [("error", 70)]
    await arb.submit(Event("internet.down", "clear"))     # dns.down surfaces; internet.up is lower priority
    await settle(arb)
    assert chimes.chimes == [("error", 70), ("error", 70)]


async def test_recovery_followup_plays_success(arb, chimes):
    await arb.submit(Event("internet.down", "start"))
    await settle(arb)
    await arb.submit(Event("internet.down", "clear"))
    await settle(arb)
    assert chimes.chimes == [("error", 70), ("success", 70)]


async def test_night_only_critical_at_night_volume(arb, clock, chimes):
    clock.set_time("23:30")
    await arb.submit(Event("dns.down", "start"))          # high: light dims, but no sound at night
    await settle(arb)
    assert chimes.chimes == []
    await arb.submit(Event("internet.down", "start"))     # critical: chimes at night volume
    await settle(arb)
    assert chimes.chimes == [("error", 30)]


async def test_forced_test_uses_day_volume_at_night(arb, clock, chimes):
    clock.set_time("23:30")
    await arb.submit(Event("device.new", source="test", force=True))
    await settle(arb)
    assert chimes.chimes == [("default", 70)]


async def test_audio_disabled_or_rule_silent(arb, settings, chimes):
    settings.update({"rules": {"device.new": {"sound": None}}})
    await arb.submit(Event("device.new", data={"key": "a"}))
    await settle(arb)
    settings.update({"audio": {"enabled": False}})
    await arb.submit(Event("dns.down", "start"))
    await settle(arb)
    assert chimes.chimes == []


async def test_webhook_sounds(arb, chimes):
    await arb.submit(Event("hook.a", data={"key": "1"}))
    await arb.submit(Event("hook.b", sound="", data={"key": "2"}))
    await arb.submit(Event("hook.c", sound="success", data={"key": "3"}))
    await settle(arb)
    assert chimes.chimes == [("default", 70), ("success", 70)]


async def test_manual_and_idle_never_chime(arb, chimes):
    await arb.tick()
    arb.set_manual({"color": [255, 0, 0]})
    await settle(arb)
    assert chimes.chimes == []


async def test_chime_exception_never_breaks_lights(clock, settings):
    class Boom:
        def chime(self, ref, volume_pct):
            raise RuntimeError("speaker on fire")

    strip = FakeStrip()
    arb = Arbiter(Player(strip, clock), settings, clock, chimes=Boom())
    await arb.submit(Event("dns.down", "start"))
    await settle(arb)
    assert ("effect", 0x67) in strip.calls
