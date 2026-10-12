import asyncio

import pytest

from led_notify.driver.fake import FakeStrip
from led_notify.engine.player import Player
from led_notify.patterns import Color, Effect, FxStep, Off, Sequence, Solid


@pytest.fixture
def strip():
    return FakeStrip()


@pytest.fixture
def player(strip, clock):
    return Player(strip, clock)


async def test_color_powers_on_once(player, strip):
    await player.show(Color((1, 2, 3)), 100)
    await player.show(Color((4, 5, 6)), 50)
    assert strip.calls == [("power", True), ("color", (1, 2, 3), 100), ("color", (4, 5, 6), 50)]


async def test_effect_sets_effect_speed_and_brightness(player, strip):
    await player.show(Effect(0x62, 5), 200)
    assert strip.calls == [("power", True), ("effect", 0x62), ("speed", 5), ("brightness", 200)]


async def test_effect_without_speed_sends_default_speed(player, strip):
    await player.show(Effect(0x01, 2), 200)
    await player.show(Effect(0x01), 200)  # must not inherit speed 2 from the previous effect
    assert strip.calls[-3:] == [("effect", 0x01), ("speed", 5), ("brightness", 200)]


async def test_level_zero_powers_off_and_next_show_powers_on(player, strip):
    await player.show(Color((1, 2, 3)), 100)
    await player.show(Color((9, 9, 9)), 0)
    assert strip.calls[-1] == ("power", False)
    await player.show(Color((1, 2, 3)), 100)
    assert strip.calls[-2:] == [("power", True), ("color", (1, 2, 3), 100)]


async def test_level_is_clamped(player, strip):
    await player.show(Color((1, 2, 3)), 999)
    assert strip.calls[-1] == ("color", (1, 2, 3), 255)


async def test_sequence_plays_steps_with_timing(player, strip, clock):
    await player.show(Sequence((Solid((255, 0, 0), 300), Off(200)), repeat=2), 128)
    red, black = ("color", (255, 0, 0), 128), ("color", (0, 0, 0), 128)
    assert strip.calls == [("power", True), red, black, red, black]
    assert clock.sleeps == [0.3, 0.2, 0.3, 0.2]


async def test_fx_step_sets_effect_and_brightness(player, strip, clock):
    await player.show(Sequence((FxStep(0x7F, 4000),)), 90)
    assert strip.calls == [("power", True), ("effect", 0x7F), ("brightness", 90)]
    assert clock.sleeps == [4.0]


async def test_sequence_can_be_cancelled_mid_way(player, strip, clock):
    clock.gate = asyncio.Event()
    task = asyncio.create_task(player.show(Sequence((Solid((1, 1, 1), 300), Off(200)), repeat=3), 50))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [c for c in strip.calls if c[0] == "color"] == [("color", (1, 1, 1), 50)]
