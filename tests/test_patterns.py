import pytest

from led_notify.driver.banlanx_ctl import SIXXX_EFFECTS_DYNAMIC as FX
from led_notify.patterns import (
    NAMED, Color, Effect, FxStep, Off, PatternError, Sequence, Solid,
    catalog, describe, resolve, sequence_ms,
)


def test_every_named_pattern_is_valid():
    for name, p in NAMED.items():
        assert isinstance(p, (Color, Effect, Sequence)), name
        if isinstance(p, Effect):
            assert p.fx in FX, name


def test_effect_numbers_match_firmware_names():
    expected = {
        "red_breath": "Red Breath", "purple_breath": "Purple Breath",
        "red_white_snake": "Red/White Snake", "yellow_breath": "Yellow Breath",
        "yellow_stacking": "Yellow Stacking", "fire_red_yellow": "Red/Yellow Fire",
        "intruder": "Red/Blue/White Snake", "welcome": "White Breath",
        "yellow_white_stars": "Yellow/White Background Stars", "rainbow": "Rainbow",
        "red_comet": "Red Comet", "green_comet": "Green Comet", "blue_comet": "Blue Comet",
    }
    for name, fx_name in expected.items():
        assert FX[NAMED[name].fx] == fx_name, name
    assert [FX[s.fx] for s in NAMED["new_device"].steps] == ["Blue Comet Spin", "Green Comet Spin"]


def test_sequence_rejects_short_steps_and_empty():
    with pytest.raises(ValueError):
        Sequence((Solid((1, 2, 3), 100),))
    with pytest.raises(ValueError):
        Sequence(())


def test_sequence_ms():
    assert sequence_ms(NAMED["purple_flash_x3"]) == 1500
    assert sequence_ms(Sequence((FxStep(1, 4000), Off(200)))) == 4200


def test_resolve_valid_specs():
    assert resolve({"pattern": "red_breath"}) == NAMED["red_breath"]
    assert resolve({"effect": 15}) == Effect(15)
    assert resolve({"effect": 15, "speed": 7}) == Effect(15, 7)
    assert resolve({"color": [255, 0, 16]}) == Color((255, 0, 16))
    assert resolve({"pattern": None, "color": [0, 0, 0]}) == Color((0, 0, 0))


@pytest.mark.parametrize("spec", [
    {},
    {"pattern": "red_breath", "effect": 1},
    {"pattern": "nope"},
    {"effect": 0},
    {"effect": 0x93},
    {"effect": "5"},
    {"effect": True},
    {"effect": 5, "speed": 11},
    {"color": [256, 0, 0]},
    {"color": [1, 2]},
    {"color": "red"},
    "red",
])
def test_resolve_rejects_bad_specs(spec):
    with pytest.raises(PatternError):
        resolve(spec)


def test_describe():
    assert describe({"pattern": "red_breath"}) == "red_breath"
    assert describe({"effect": 15}) == "effect 15 (Red Comet)"
    assert describe({"color": [255, 0, 16]}) == "color #ff0010"


def test_catalog_lists_named_patterns_and_all_firmware_effects():
    c = catalog()
    assert "red_breath" in c["named"] and c["named"] == sorted(c["named"])
    assert len(c["effects"]) == len(FX) == 146
    assert c["effects"][0] == {"fx": 1, "name": "Rainbow"}
