import asyncio
import contextlib

import pytest

from led_notify.driver.fake import FakeStrip
from led_notify.engine.arbiter import Arbiter
from led_notify.engine.player import Player
from led_notify.events import Event
from led_notify.hub import Hub
from led_notify.patterns import PatternError
from tests.fakes import settle

IDLE = ("color", (0, 179, 164), 12)


@pytest.fixture
def strip():
    return FakeStrip()


@pytest.fixture
def hub():
    return Hub()


@pytest.fixture
def arb(strip, clock, settings, hub):
    return Arbiter(Player(strip, clock), settings, clock, hub)


def outcomes(arb):
    return [(e["name"], e["outcome"]) for e in arb.log]


def effects(strip):
    return [c[1] for c in strip.calls if c[0] == "effect"]


# ---- idle + persistent states ---------------------------------------------
async def test_idle_glow_when_nothing_active(arb, strip):
    await arb.tick()
    assert strip.calls == [("power", True), IDLE]
    assert arb.snapshot()["current"]["reason"] == "idle"


async def test_no_resend_when_nothing_changed(arb, strip):
    await arb.tick()
    n = len(strip.calls)
    await arb.tick()
    await arb.tick()
    assert len(strip.calls) == n


async def test_state_shows_pattern_and_clear_returns_to_idle(arb, strip):
    assert await arb.submit(Event("dns.down", "start", source="dns")) == "active"
    await settle(arb)
    assert strip.calls[-3:] == [("effect", 0x67), ("speed", 5), ("brightness", 255)]
    assert arb.snapshot()["active"][0]["name"] == "dns.down"
    assert await arb.submit(Event("dns.down", "clear", source="dns")) == "cleared"
    await settle(arb)
    assert strip.calls[-1] == IDLE


async def test_highest_priority_state_wins_then_falls_back(arb, strip):
    await arb.submit(Event("internet.slow", "start"))
    await arb.submit(Event("dns.down", "start"))
    await settle(arb)
    assert effects(strip)[-1] == 0x67
    await arb.submit(Event("dns.down", "clear"))
    await settle(arb)
    assert effects(strip)[-1] == 0x65


async def test_same_priority_most_recent_wins(arb, strip):
    await arb.submit(Event("disk.full", "start"))
    await arb.submit(Event("cpu.hot", "start"))
    await settle(arb)
    assert effects(strip)[-1] == 0x09


async def test_duplicate_start_and_unknown_clear_are_ignored(arb):
    await arb.submit(Event("dns.down", "start"))
    assert await arb.submit(Event("dns.down", "start")) == "ignored:already_active"
    assert await arb.submit(Event("cpu.hot", "clear")) == "ignored:not_active"


async def test_disabled_rule_is_skipped(arb, settings):
    settings.update({"rules": {"dns.down": {"enabled": False}}})
    assert await arb.submit(Event("dns.down", "start")) == "skipped:disabled"


async def test_rule_pattern_change_rerenders_active_state(arb, settings, strip):
    await arb.submit(Event("dns.down", "start"))
    await settle(arb)
    settings.update({"rules": {"dns.down": {"pattern": "red_breath"}}})
    await settle(arb)
    assert effects(strip)[-1] == 0x62


async def test_clear_followup_plays_recovery_flash_then_idle(arb, strip):
    await arb.submit(Event("internet.down", "start", source="internet"))
    await settle(arb)
    await arb.submit(Event("internet.down", "clear", source="internet"))
    await settle(arb)
    assert ("internet.up", "shown") in outcomes(arb)
    assert ("color", (0, 255, 0), 255) in strip.calls
    assert strip.calls[-1] == IDLE


async def test_max_brightness_scales_idle_and_states(arb, settings, strip):
    settings.update({"max_brightness": 128})
    await arb.tick()
    assert strip.calls[-1] == ("color", (0, 179, 164), 6)
    await arb.submit(Event("dns.down", "start"))
    await settle(arb)
    assert strip.calls[-1] == ("brightness", 128)


# ---- manual override --------------------------------------------------------
async def test_manual_beats_normal_but_not_high(arb, strip):
    await arb.submit(Event("internet.slow", "start"))
    arb.set_manual({"color": [255, 0, 0]}, brightness=100)
    await settle(arb)
    assert strip.calls[-1] == ("color", (255, 0, 0), 100)
    await arb.submit(Event("dns.down", "start"))
    await settle(arb)
    assert effects(strip)[-1] == 0x67
    await arb.submit(Event("dns.down", "clear"))
    await settle(arb)
    assert strip.calls[-1] == ("color", (255, 0, 0), 100)


async def test_manual_expires(arb, clock, strip):
    arb.set_manual({"color": [255, 0, 0]})
    await settle(arb)
    clock.advance(30 * 60 + 1)
    await settle(arb)
    assert strip.calls[-1] == IDLE and arb.snapshot()["manual"] is None


async def test_resume_auto(arb, strip):
    arb.set_manual({"effect": 15})
    await settle(arb)
    arb.clear_manual()
    await settle(arb)
    assert strip.calls[-1] == IDLE


async def test_manual_power_off(arb, strip):
    arb.set_manual(None, power=False)
    await settle(arb)
    assert strip.calls[-1] == ("power", False)
    assert arb.snapshot()["manual"]["desc"] == "off"


async def test_manual_brightness_only_reuses_previous_look(arb, strip):
    arb.set_manual({"effect": 15, "speed": 3})
    await settle(arb)
    arb.set_manual(None, brightness=40)
    await settle(arb)
    assert strip.calls[-3:] == [("effect", 15), ("speed", 3), ("brightness", 40)]


async def test_manual_rejects_bad_spec_without_changing_state(arb):
    with pytest.raises(PatternError):
        arb.set_manual({"effect": 999})
    assert arb.snapshot()["manual"] is None


# ---- one-shots ----------------------------------------------------------------
async def test_oneshot_plays_then_restores_previous_look(arb, strip, clock):
    await arb.tick()
    assert await arb.submit(Event("device.new", source="devices", data={"key": "aa"})) == "queued"
    await settle(arb)
    assert effects(strip) == [0x7F, 0x7E]
    assert clock.sleeps.count(4.0) == 2
    assert strip.calls[-1] == IDLE
    assert ("device.new", "shown") in outcomes(arb)


async def test_effect_oneshot_held_for_rule_duration(arb, clock, strip):
    await arb.submit(Event("device.new_late", source="devices", data={"key": "aa"}))
    await settle(arb)
    assert 0x77 in effects(strip) and 8 in clock.sleeps


async def test_lower_priority_oneshot_skipped_during_higher_state(arb, strip):
    await arb.submit(Event("dns.down", "start"))
    await arb.submit(Event("device.new", data={"key": "aa"}))
    await settle(arb)
    assert ("device.new", "skipped:priority") in outcomes(arb)
    assert 0x7F not in effects(strip)


async def test_equal_priority_oneshot_plays_over_state_then_restores(arb, strip):
    await arb.submit(Event("internet.slow", "start"))
    await settle(arb)
    await arb.submit(Event("container.died", data={"key": "web"}))
    await settle(arb)
    assert ("color", (160, 0, 255), 255) in strip.calls
    assert effects(strip)[-1] == 0x65 and strip.calls[-1] == ("brightness", 255)


async def test_duplicate_oneshot_within_30s_is_skipped(arb, clock):
    assert await arb.submit(Event("device.new", data={"key": "aa"})) == "queued"
    assert await arb.submit(Event("device.new", data={"key": "aa"})) == "skipped:duplicate"
    assert await arb.submit(Event("device.new", data={"key": "bb"})) == "queued"
    clock.advance(31)
    assert await arb.submit(Event("device.new", data={"key": "aa"})) == "queued"


async def test_test_events_bypass_dedup(arb):
    assert await arb.submit(Event("device.new", source="test")) == "queued"
    assert await arb.submit(Event("device.new", source="test")) == "queued"


async def test_queue_overflow_drops_oldest(arb):
    for i in range(6):
        await arb.submit(Event("ping", data={"key": str(i)}))
    assert outcomes(arb).count(("ping", "dropped:queue_full")) == 1
    assert len(arb.snapshot()["queue"]) == 5


async def test_higher_priority_state_preempts_playing_oneshot(arb, clock, strip):
    clock.gate = asyncio.Event()
    await arb.submit(Event("device.new", data={"key": "x"}))
    await arb.tick()
    await asyncio.sleep(0)
    assert arb.snapshot()["playing"] == "device.new"
    await arb.submit(Event("internet.down", "start"))
    clock.gate = None
    await settle(arb)
    assert ("device.new", "preempted") in outcomes(arb)
    assert effects(strip)[-1] == 0x62 and strip.calls[-1] == ("brightness", 255)


async def test_webhook_event_without_rule_uses_its_own_look(arb, strip, clock):
    await arb.submit(Event("backup.done", look={"color": [0, 0, 255]}, duration_s=2))
    await settle(arb)
    assert ("color", (0, 0, 255), 255) in strip.calls and 2.0 in clock.sleeps


async def test_webhook_event_without_look_flashes_white(arb, strip):
    await arb.submit(Event("hello"))
    await settle(arb)
    assert ("color", (255, 255, 255), 255) in strip.calls


async def test_webhook_state_without_rule(arb, strip):
    await arb.submit(Event("ci.failing", "start", priority="high", look={"pattern": "red_comet"}))
    await settle(arb)
    assert effects(strip)[-1] == 0x0F
    await arb.submit(Event("ci.failing", "clear"))
    await settle(arb)
    assert strip.calls[-1] == IDLE


# ---- quiet hours --------------------------------------------------------------
async def test_quiet_hours_idle_off_dim_high_suppress_normal(arb, clock, strip):
    clock.set_time("23:30")
    await arb.tick()
    assert strip.calls[-1] == ("power", False)
    assert await arb.submit(Event("internet.slow", "start")) == "skipped:quiet"
    assert await arb.submit(Event("device.new", data={"key": "a"})) == "skipped:quiet"
    await settle(arb)
    assert strip.calls[-1] == ("power", False)
    await arb.submit(Event("dns.down", "start"))
    await settle(arb)
    assert strip.calls[-1] == ("brightness", 51)


async def test_suppressed_state_shows_when_quiet_hours_end(arb, clock, strip):
    clock.set_time("23:30")
    await arb.submit(Event("internet.slow", "start"))
    await settle(arb)
    clock.set_time("07:00")
    await settle(arb)
    assert effects(strip)[-1] == 0x65


async def test_forced_oneshot_ignores_quiet_hours(arb, clock, strip):
    clock.set_time("23:30")
    await arb.submit(Event("device.new", source="test", force=True))
    await settle(arb)
    assert 0x7F in effects(strip) and ("brightness", 255) in strip.calls


# ---- snapshot, log, hub -------------------------------------------------------
async def test_snapshot_shape(arb):
    snap = arb.snapshot()
    assert set(snap) == {"current", "active", "queue", "playing", "manual", "quiet"}


async def test_log_is_capped_and_published(strip, clock, settings, hub):
    q = hub.subscribe(maxsize=500)
    arb = Arbiter(Player(strip, clock), settings, clock, hub, log_size=3)
    for i in range(5):
        await arb.submit(Event("ping", source="test", data={"key": str(i)}))
    assert len(arb.log) == 3
    kinds = []
    while not q.empty():
        kinds.append(q.get_nowait()[0])
    assert kinds.count("log") >= 5


async def test_run_loop_survives_player_errors(strip, clock, settings):
    class Boom:
        async def show(self, pattern, level):
            raise RuntimeError("ble write failed")

    arb = Arbiter(Boom(), settings, clock)
    task = asyncio.create_task(arb.run())
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ---- fix round 1 ----------------------------------------------------------------
async def test_oneshot_cancelled_before_start_does_not_freeze(arb, strip):
    await arb.submit(Event("device.new", data={"key": "a"}))
    await arb.tick()  # task created, not yet started
    await arb.submit(Event("internet.down", "start"))  # cancels with no yield in between
    await settle(arb)
    assert effects(strip)[-1] == 0x62
    assert outcomes(arb).count(("device.new", "preempted")) == 1
    await arb.submit(Event("ping", data={"key": "z"}))
    await settle(arb)
    assert arb.snapshot()["queue"] == [] and arb.snapshot()["playing"] is None
    assert ("ping", "shown") in outcomes(arb)


async def test_preempted_logged_once_when_started(arb, clock):
    clock.gate = asyncio.Event()
    await arb.submit(Event("device.new", data={"key": "x"}))
    await arb.tick()
    await asyncio.sleep(0)
    await arb.submit(Event("internet.down", "start"))
    clock.gate = None
    await settle(arb)
    assert outcomes(arb).count(("device.new", "preempted")) == 1


async def test_bad_look_oneshot_rejected_and_arbiter_keeps_working(arb, strip):
    assert await arb.submit(Event("hook.bad", look={"effect": 999})) == "rejected:invalid_look"
    assert arb.snapshot()["queue"] == []
    await arb.submit(Event("hello"))
    await settle(arb)
    assert ("color", (255, 255, 255), 255) in strip.calls


async def test_bad_look_start_rejected_and_not_active(arb, strip):
    assert await arb.submit(Event("ci.bad", "start", look={"effect": 999})) == "rejected:invalid_look"
    assert arb.snapshot()["active"] == []
    await arb.tick()
    assert strip.calls[-1] == IDLE


async def test_manual_brightness_capped_by_max_brightness(arb, settings, strip):
    settings.update({"max_brightness": 50})
    arb.set_manual({"color": [255, 0, 0]}, brightness=200)
    await settle(arb)
    assert strip.calls[-1] == ("color", (255, 0, 0), 50)
