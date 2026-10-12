"""Named light patterns and parsing of ad-hoc looks from rules, the UI and webhooks."""
from __future__ import annotations

from dataclasses import dataclass

from .driver.banlanx_ctl import SIXXX_EFFECTS_DYNAMIC

MIN_STEP_MS = 150
MAX_EFFECT = max(SIXXX_EFFECTS_DYNAMIC)
LOOK_KEYS = ("pattern", "effect", "color")


class PatternError(ValueError):
    pass


@dataclass(frozen=True)
class Color:
    """Solid colour, held until something else is shown."""
    rgb: tuple[int, int, int]


@dataclass(frozen=True)
class Effect:
    """Firmware effect, held until something else is shown."""
    fx: int
    speed: int | None = None


@dataclass(frozen=True)
class Solid:
    rgb: tuple[int, int, int]
    ms: int


@dataclass(frozen=True)
class FxStep:
    fx: int
    ms: int


@dataclass(frozen=True)
class Off:
    ms: int


@dataclass(frozen=True)
class Sequence:
    """Timed steps played by the service itself; ends after `repeat` passes."""
    steps: tuple
    repeat: int = 1

    def __post_init__(self):
        if not self.steps:
            raise ValueError("a sequence needs at least one step")
        if any(step.ms < MIN_STEP_MS for step in self.steps):
            raise ValueError(f"sequence steps must be at least {MIN_STEP_MS} ms")
        if self.repeat < 1:
            raise ValueError("repeat must be >= 1")


Pattern = Color | Effect | Sequence

RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)
PURPLE, WHITE = (160, 0, 255), (255, 255, 255)

NAMED: dict[str, Pattern] = {
    "red_breath": Effect(0x62, 5),
    "purple_breath": Effect(0x67, 5),
    "red_white_snake": Effect(0x7B),
    "yellow_breath": Effect(0x65, 3),
    "yellow_stacking": Effect(0x6C),
    "fire_red_yellow": Effect(0x09),
    "intruder": Effect(0x77),
    "welcome": Effect(0x68),
    "yellow_white_stars": Effect(0x5E),
    "rainbow": Effect(0x01),
    "red_comet": Effect(0x0F),
    "green_comet": Effect(0x10),
    "blue_comet": Effect(0x11),
    "new_device": Sequence((FxStep(0x7F, 4000), FxStep(0x7E, 4000))),
    "purple_flash_x3": Sequence((Solid(PURPLE, 300), Off(200)), repeat=3),
    "green_flash_x2": Sequence((Solid(GREEN, 400), Off(250)), repeat=2),
    "green_flash": Sequence((Solid(GREEN, 400), Off(250))),
    "red_flash_x3": Sequence((Solid(RED, 300), Off(200)), repeat=3),
    "blue_flash": Sequence((Solid(BLUE, 400), Off(250))),
    "white_flash": Sequence((Solid(WHITE, 300), Off(200)), repeat=2),
}


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def resolve(spec: dict) -> Pattern:
    if not isinstance(spec, dict):
        raise PatternError("look must be an object")
    keys = [k for k in LOOK_KEYS if spec.get(k) is not None]
    if len(keys) != 1:
        raise PatternError("give exactly one of pattern, effect, color")
    key = keys[0]
    value = spec[key]
    if key == "pattern":
        if value not in NAMED:
            raise PatternError(f"unknown pattern {value!r}")
        return NAMED[value]
    if key == "effect":
        if not _is_int(value) or not 1 <= value <= MAX_EFFECT:
            raise PatternError(f"effect must be a number from 1 to {MAX_EFFECT}")
        speed = spec.get("speed")
        if speed is not None and (not _is_int(speed) or not 1 <= speed <= 10):
            raise PatternError("speed must be a number from 1 to 10")
        return Effect(value, speed)
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(_is_int(v) and 0 <= v <= 255 for v in value):
        raise PatternError("color must be [r, g, b] with values 0-255")
    return Color(tuple(value))


def describe(spec: dict) -> str:
    if spec.get("pattern") is not None:
        return spec["pattern"]
    if spec.get("effect") is not None:
        fx = spec["effect"]
        return f"effect {fx} ({SIXXX_EFFECTS_DYNAMIC.get(fx, '?')})"
    return "color #" + "".join(f"{v:02x}" for v in spec["color"])


def sequence_ms(seq: Sequence) -> int:
    return sum(step.ms for step in seq.steps) * seq.repeat


def catalog() -> dict:
    return {
        "named": sorted(NAMED),
        "effects": [{"fx": fx, "name": name} for fx, name in SIXXX_EFFECTS_DYNAMIC.items()],
    }
