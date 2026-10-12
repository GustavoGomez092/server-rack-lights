# led-notify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A ZimaOS container app that drives a BanlanX SP548E Bluetooth LED strip to show network, device and server events, with a web UI and webhook API on port 8787.

**Architecture:** One asyncio Python process. Monitors emit `Event`s → `Arbiter` (priorities, one-shot queue, quiet hours, manual override) picks a `Pattern` → `Player` turns it into strip commands → `StripLink` owns the single BLE connection (wrapping the vendored `banlanx_ctl.Strip`). FastAPI serves the REST API, an SSE stream and a vanilla-JS single page. Settings and known devices persist as JSON under `/data`.

**Tech Stack:** Python 3.12, bleak (BLE via host BlueZ over D-Bus), FastAPI + Uvicorn, httpx, dnspython, pytest + pytest-asyncio, Docker (python:3.12-slim + arp-scan, smartmontools, iputils-ping), ZimaOS `casaos-cli`.

**Spec:** `docs/superpowers/specs/2026-10-11-led-notify-design.md`

## Global Constraints

- Python `>=3.12`; container base image `python:3.12-slim`. Local dev venv: `uv venv --python 3.12 .venv`.
- Strip BLE address `A6:15:50:62:0A:9D`, protocol family `6xx` (SP548E).
- Service port `8787`; persistent data dir in container `/data` ← host `/DATA/AppData/led-notify/data`.
- Server timezone `America/Los_Angeles` (container env `TZ`).
- Only `driver/link.py` (and the vendored `driver/banlanx_ctl.py`) may touch bleak; the controller accepts ONE BLE connection.
- `driver/banlanx_ctl.py` is a byte-for-byte copy of `/Users/gustavogomez/led-strip/banlanx_ctl.py` — never edit it.
- Minimum software-sequence step: `150 ms`.
- Priority scale: `idle 0 < low 10 < normal 20 < manual 25 < high 30 < critical 40`.
- One-shot defaults: dedup window `30 s`, queue max `5`, default hold `5 s`, durations clamped to `0.5–60 s`.
- Web UI: plain HTML/CSS/JS served as static files — no build step, no external CDN.
- All commands below run from the repo root `/Volumes/Content/projects/HomeLab/led-notify` unless stated.

## Spec Deltas (decided while planning — tell the user)

- **RSSI is not shown** in the UI header: bleak can't read RSSI of an established Linux connection without re-scanning, which would disturb the link.
- **CPU temperature is read from the container's own `/sys/class/hwmon`** (Docker exposes host sysfs read-only); no bind mount (mounting `/sys/class/hwmon` breaks its symlinks).
- **Container also gets `SYS_ADMIN`**: NVMe SMART (`smartctl -H /dev/nvme0`) uses the NVMe admin ioctl, which requires it. The container is already root-equivalent via the Docker socket.
- **Extra log/API outcomes** beyond the spec list: `queued`, `active`, `cleared`, `ignored:already_active`, `ignored:not_active`.
- **UI "Test" on a state rule** (e.g. `internet.down`) plays that rule's pattern as an 8 s one-shot through the arbiter rather than starting a real persistent state that would then need clearing.

## Review Focus

1. **Strip unreachable at startup** (unplugged, or the BanlanX phone app holds the single connection): the service must still start, the UI/API must work, commands must not raise, and the link must keep retrying and report `last_error` → Task 12 `test_offline_strip_never_raises_and_reports_error`.
2. **Garbage webhook payloads** (missing name, spaces in name, unknown pattern, effect 0/999, colour 300 or 2-element, two looks at once, bad kind/priority): must be HTTP 422 with a message, never 500 → Task 13 `test_bad_webhook_payloads_are_422`.
3. **Invalid or partial settings/rules edits** (out-of-range brightness, `25:00`, unknown keys, unknown pattern, clear ≥ full threshold): must be rejected with nothing saved → Task 4 `test_invalid_patches_are_rejected_and_nothing_changes`, Task 13 `test_rules_put_invalid_is_422_and_unchanged`.
4. **First ARP scan returns nothing** (interface glitch): must NOT complete the device baseline, otherwise every device alerts as "new" on the next scan → Task 8 `test_empty_first_scan_does_not_complete_baseline`.
5. **Quiet-hours window across midnight and start == end**: `23:00–07:00` must include 23:30 and 06:59 but not 07:00; equal start/end means never quiet → Task 5 `test_in_window_*`.

---

## File Structure

```
led-notify/
├── pyproject.toml              # package + deps + pytest config
├── .gitignore  .dockerignore
├── Dockerfile  docker-compose.yml  deploy.sh  README.md
├── led_notify/
│   ├── __init__.py
│   ├── clock.py                # Clock protocol + SystemClock
│   ├── events.py               # Event dataclass, PRIORITIES
│   ├── hub.py                  # in-process pub/sub for the SSE stream
│   ├── patterns.py             # Pattern types, NAMED catalogue, resolve()/describe()/catalog()
│   ├── app.py                  # wiring + uvicorn entry point
│   ├── smoke.py                # plays every named pattern through a running service
│   ├── driver/
│   │   ├── banlanx_ctl.py      # vendored, unchanged
│   │   ├── port.py             # StripPort protocol
│   │   ├── fake.py             # FakeStrip (records commands)
│   │   └── link.py             # StripLink: persistent BLE connection manager
│   ├── engine/
│   │   ├── quiet.py            # parse_hhmm(), in_window()
│   │   ├── player.py           # Player: Pattern → strip commands
│   │   └── arbiter.py          # Arbiter: what the strip shows
│   ├── store/
│   │   ├── jsonfile.py         # atomic JSON load/save with corrupt-file recovery
│   │   ├── settings.py         # DEFAULT_SETTINGS/RULES, validation, SettingsStore
│   │   └── devices.py          # DeviceStore
│   ├── monitors/
│   │   ├── base.py             # Debouncer, Hysteresis, PollingMonitor, MonitorDisabled
│   │   ├── internet.py  dns.py  devices.py  disk.py  thermal.py  docker.py  adguard.py
│   │   └── supervisor.py       # runs/restarts monitors, health
│   └── web/
│       ├── api.py              # FastAPI app factory
│       └── static/index.html  app.js  style.css
└── tests/
    ├── __init__.py  fakes.py  conftest.py
    └── test_*.py               # one per module
```

---

### Task 1: Project scaffold, clock, events, hub

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `led_notify/__init__.py`, `led_notify/clock.py`, `led_notify/events.py`, `led_notify/hub.py`
- Create: `tests/__init__.py`, `tests/fakes.py`, `tests/conftest.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces:
  - `Clock` protocol: `monotonic() -> float`, `now() -> datetime` (tz-aware), `async sleep(seconds: float) -> None`; `SystemClock(tz: str | None = None)`
  - `events.PRIORITIES: dict[str, int]`, `RULE_PRIORITIES = ("low","normal","high","critical")`, `KINDS = ("oneshot","start","clear")`
  - `Event(name, kind="oneshot", source="webhook", priority=None, look=None, duration_s=None, data={}, force=False)`; raises `ValueError` on bad fields; clamps `duration_s` to 0.5–60
  - `Hub()`: `subscribe(maxsize=100) -> asyncio.Queue`, `unsubscribe(q)`, `publish(type: str, data)`; queue items are `(type, data)`
  - tests: `tests.fakes.FakeClock` (`advance(s)`, `set_time("HH:MM")`, `sleeps: list[float]`, `gate: asyncio.Event | None`), fixture `clock`

- [ ] **Step 1: Create packaging files**

`pyproject.toml`:
```toml
[project]
name = "led-notify"
version = "0.1.0"
description = "Homelab notifications on a BanlanX BLE LED strip"
requires-python = ">=3.12"
dependencies = [
  "bleak>=0.22",
  "fastapi>=0.115",
  "uvicorn>=0.30",
  "httpx>=0.27",
  "dnspython>=2.6",
  "tzdata>=2024.1",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["led_notify*"]

[tool.setuptools.package-data]
led_notify = ["web/static/*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
*.egg-info/
data/
```

`led_notify/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

- [ ] **Step 2: Create the venv and install**

Run: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'`
Expected: ends with `Installed N packages` and no errors.

- [ ] **Step 3: Write the test doubles and the failing tests**

`tests/fakes.py`:
```python
"""Test doubles shared across the suite."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")


class FakeClock:
    """Deterministic clock: sleep() advances time instantly unless `gate` is set."""

    def __init__(self, start: datetime | None = None):
        self._start = start or datetime(2026, 10, 11, 12, 0, tzinfo=TZ)
        self._mono = 1000.0
        self.sleeps: list[float] = []
        self.gate: asyncio.Event | None = None

    def monotonic(self) -> float:
        return self._mono

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._mono - 1000.0)

    def advance(self, seconds: float) -> None:
        self._mono += seconds

    def set_time(self, hhmm: str) -> None:
        """Move the wall clock to HH:MM on the same date (monotonic time is untouched)."""
        h, m = map(int, hhmm.split(":"))
        target = self.now().replace(hour=h, minute=m, second=0, microsecond=0)
        self._start += target - self.now()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.gate is not None:
            await self.gate.wait()
        self._mono += seconds
        await asyncio.sleep(0)
```

`tests/conftest.py`:
```python
import pytest

from tests.fakes import FakeClock


@pytest.fixture
def clock():
    return FakeClock()
```

`tests/test_core.py`:
```python
import pytest

from led_notify.clock import SystemClock
from led_notify.events import PRIORITIES, Event
from led_notify.hub import Hub


def test_priority_order():
    assert sorted(PRIORITIES, key=PRIORITIES.get) == ["idle", "low", "normal", "manual", "high", "critical"]


def test_event_defaults():
    ev = Event("backup.done")
    assert (ev.kind, ev.source, ev.data, ev.force, ev.look) == ("oneshot", "webhook", {}, False, None)


@pytest.mark.parametrize("kwargs", [
    dict(name=""),
    dict(name="has space"),
    dict(name="x" * 65),
    dict(name="ok", kind="bogus"),
    dict(name="ok", priority="urgent"),
])
def test_event_rejects_bad_fields(kwargs):
    with pytest.raises(ValueError):
        Event(**kwargs)


def test_event_accepts_dots_colons_dashes():
    assert Event("ci:build-1.done").name == "ci:build-1.done"


def test_duration_is_clamped():
    assert Event("a", duration_s=600).duration_s == 60.0
    assert Event("a", duration_s=0).duration_s == 0.5


def test_hub_delivers_and_drops_oldest_when_full():
    hub = Hub()
    q = hub.subscribe(maxsize=2)
    for i in (1, 2, 3):
        hub.publish("log", i)
    assert [q.get_nowait(), q.get_nowait()] == [("log", 2), ("log", 3)]
    hub.unsubscribe(q)
    hub.publish("log", 4)
    assert q.empty()


def test_system_clock_now_is_timezone_aware():
    assert SystemClock("America/Los_Angeles").now().tzinfo is not None
    assert SystemClock().now().tzinfo is not None


def test_fake_clock_set_time_and_advance(clock):
    clock.set_time("23:30")
    assert clock.now().strftime("%H:%M") == "23:30"
    clock.advance(60)
    assert clock.now().strftime("%H:%M") == "23:31"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_core.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.clock'`

- [ ] **Step 5: Implement clock, events, hub**

`led_notify/clock.py`:
```python
"""Time source, injectable so tests can control it."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def now(self) -> datetime: ...
    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def __init__(self, tz: str | None = None):
        self.tz = ZoneInfo(tz) if tz else None

    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(self.tz) if self.tz else datetime.now().astimezone()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
```

`led_notify/events.py`:
```python
"""Event model and priority scale shared by monitors, the arbiter and the API."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PRIORITIES = {"idle": 0, "low": 10, "normal": 20, "manual": 25, "high": 30, "critical": 40}
RULE_PRIORITIES = ("low", "normal", "high", "critical")
KINDS = ("oneshot", "start", "clear")
MIN_DURATION_S = 0.5
MAX_DURATION_S = 60.0
_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


@dataclass
class Event:
    name: str
    kind: str = "oneshot"
    source: str = "webhook"
    priority: str | None = None
    look: dict | None = None
    duration_s: float | None = None
    data: dict = field(default_factory=dict)
    force: bool = False

    def __post_init__(self):
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            raise ValueError("name must be 1-64 characters: letters, digits, '.', '_', ':' or '-'")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if self.priority is not None and self.priority not in RULE_PRIORITIES:
            raise ValueError(f"priority must be one of {', '.join(RULE_PRIORITIES)}")
        if self.duration_s is not None:
            self.duration_s = min(MAX_DURATION_S, max(MIN_DURATION_S, float(self.duration_s)))
```

`led_notify/hub.py`:
```python
"""Tiny in-process pub/sub used to push live updates to SSE clients."""
from __future__ import annotations

import asyncio


class Hub:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self, maxsize: int = 100) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, type_: str, data=None) -> None:
        for q in list(self._subs):
            if q.full():
                q.get_nowait()  # a slow client loses its oldest message, never blocks us
            q.put_nowait((type_, data))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_core.py -q`
Expected: PASS (13 passed)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore led_notify tests
git commit -m "feat: project scaffold with clock, events and hub"
```

---

### Task 2: Pattern catalogue + vendored driver

**Files:**
- Create: `led_notify/driver/__init__.py` (empty), `led_notify/driver/banlanx_ctl.py` (copied), `led_notify/patterns.py`
- Test: `tests/test_patterns.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - Pattern types (frozen dataclasses): `Color(rgb)`, `Effect(fx, speed=None)`, `Sequence(steps: tuple, repeat=1)`; steps `Solid(rgb, ms)`, `FxStep(fx, ms)`, `Off(ms)`
  - `NAMED: dict[str, Pattern]`, `LOOK_KEYS = ("pattern","effect","color")`, `MAX_EFFECT`
  - `resolve(spec: dict) -> Pattern` (raises `PatternError(ValueError)`); spec has exactly one of `pattern`/`effect`/`color`, optional `speed` 1–10 with `effect`
  - `describe(spec: dict) -> str`, `catalog() -> {"named": [...], "effects": [{"fx", "name"}]}`, `sequence_ms(seq) -> int`

- [ ] **Step 1: Vendor the driver**

Run:
```bash
mkdir -p led_notify/driver && touch led_notify/driver/__init__.py
cp /Users/gustavogomez/led-strip/banlanx_ctl.py led_notify/driver/banlanx_ctl.py
cmp /Users/gustavogomez/led-strip/banlanx_ctl.py led_notify/driver/banlanx_ctl.py && echo identical
```
Expected: `identical`

- [ ] **Step 2: Write the failing tests**

`tests/test_patterns.py`:
```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_patterns.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.patterns'`

- [ ] **Step 4: Implement patterns**

`led_notify/patterns.py`:
```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_patterns.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add led_notify/driver led_notify/patterns.py tests/test_patterns.py
git commit -m "feat: pattern catalogue and vendored BanlanX driver"
```

---

### Task 3: Strip port, FakeStrip, Player

**Files:**
- Create: `led_notify/driver/port.py`, `led_notify/driver/fake.py`, `led_notify/engine/__init__.py` (empty), `led_notify/engine/player.py`
- Test: `tests/test_player.py`

**Interfaces:**
- Consumes: `patterns.Color/Effect/Sequence/Solid/FxStep/Off` (Task 2); `FakeClock` (Task 1).
- Produces:
  - `StripPort` protocol: `async power(on: bool)`, `async color(r, g, b, level)`, `async effect(fx)`, `async speed(v)`, `async brightness(level)`, `status() -> dict`
  - `FakeStrip()`: `.calls` list of tuples `("power", bool)`, `("color", (r,g,b), level)`, `("effect", fx)`, `("speed", v)`, `("brightness", level)`; `status() -> {"connected": True, "fake": True, "attempt": 0, "last_error": ""}`
  - `Player(strip, clock)`: `async show(pattern, level: int)` — level clamped 0–255; level 0 → power off; Effect/Color return immediately; Sequence awaits all steps (cancellable)

- [ ] **Step 1: Write the failing tests**

`tests/test_player.py`:
```python
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


async def test_effect_without_speed_skips_speed(player, strip):
    await player.show(Effect(0x01), 200)
    assert ("speed", 5) not in strip.calls and strip.calls[-1] == ("brightness", 200)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_player.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.driver.fake'`

- [ ] **Step 3: Implement port, fake, player**

`led_notify/driver/port.py`:
```python
"""The interface the engine uses to drive a strip (real BLE link or fake)."""
from __future__ import annotations

from typing import Protocol


class StripPort(Protocol):
    async def power(self, on: bool) -> None: ...
    async def color(self, r: int, g: int, b: int, level: int) -> None: ...
    async def effect(self, fx: int) -> None: ...
    async def speed(self, v: int) -> None: ...
    async def brightness(self, level: int) -> None: ...
    def status(self) -> dict: ...
```

`led_notify/driver/fake.py`:
```python
"""In-memory StripPort for tests and --fake-strip runs; records every command."""
from __future__ import annotations


class FakeStrip:
    def __init__(self):
        self.calls: list[tuple] = []

    async def power(self, on: bool) -> None:
        self.calls.append(("power", on))

    async def color(self, r: int, g: int, b: int, level: int) -> None:
        self.calls.append(("color", (r, g, b), level))

    async def effect(self, fx: int) -> None:
        self.calls.append(("effect", fx))

    async def speed(self, v: int) -> None:
        self.calls.append(("speed", v))

    async def brightness(self, level: int) -> None:
        self.calls.append(("brightness", level))

    def status(self) -> dict:
        return {"connected": True, "fake": True, "attempt": 0, "last_error": ""}
```

`led_notify/engine/__init__.py`: empty file.

`led_notify/engine/player.py`:
```python
"""Turns a Pattern into strip commands."""
from __future__ import annotations

from ..patterns import Color, Effect, FxStep, Off, Sequence, Solid


class Player:
    def __init__(self, strip, clock):
        self.strip = strip
        self.clock = clock
        self._on: bool | None = None

    async def _power(self, on: bool) -> None:
        if self._on != on:
            await self.strip.power(on)
            self._on = on

    async def show(self, pattern, level: int) -> None:
        """Effects and colours return once sent; sequences return when finished."""
        level = max(0, min(255, int(level)))
        if level == 0:
            await self._power(False)
            return
        await self._power(True)
        if isinstance(pattern, Color):
            await self.strip.color(*pattern.rgb, level)
        elif isinstance(pattern, Effect):
            await self.strip.effect(pattern.fx)
            if pattern.speed is not None:
                await self.strip.speed(pattern.speed)
            await self.strip.brightness(level)
        elif isinstance(pattern, Sequence):
            for _ in range(pattern.repeat):
                for step in pattern.steps:
                    await self._step(step, level)
        else:
            raise TypeError(f"not a pattern: {pattern!r}")

    async def _step(self, step, level: int) -> None:
        if isinstance(step, Solid):
            await self.strip.color(*step.rgb, level)
        elif isinstance(step, FxStep):
            await self.strip.effect(step.fx)
            await self.strip.brightness(level)
        elif isinstance(step, Off):
            await self.strip.color(0, 0, 0, level)
        await self.clock.sleep(step.ms / 1000)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_player.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add led_notify/driver/port.py led_notify/driver/fake.py led_notify/engine tests/test_player.py
git commit -m "feat: strip port, fake strip and pattern player"
```

---

### Task 4: JSON stores — settings, rules, devices

**Files:**
- Create: `led_notify/store/__init__.py` (empty), `led_notify/store/jsonfile.py`, `led_notify/store/settings.py`, `led_notify/store/devices.py`
- Modify: `tests/conftest.py` (add `settings` fixture)
- Test: `tests/test_stores.py`

**Interfaces:**
- Consumes: `events.RULE_PRIORITIES` (Task 1), `patterns.NAMED` (Task 2), `Hub` (Task 1).
- Produces:
  - `load_json(path, default) -> (data, warning: str | None)`; `save_json(path, data)` (atomic)
  - `DEFAULT_RULES`, `DEFAULT_SETTINGS`, `SettingsError(ValueError)`, `validate(data)`, `deep_merge(base, patch)`
  - `SettingsStore(path, hub=None)`: `.data` (dict, read-only by convention), `.warning`, `rule(name) -> dict | None`, `public() -> dict` (secrets → `{"set": bool}`), `update(patch: dict)` (validates whole result; raises `SettingsError`; saves; publishes `"settings"`)
  - Rule dict keys: `enabled, kind ("state"|"oneshot"), priority, pattern, duration_s, quiet_behavior ("dim"|"suppress"), clear_followup`
  - `DeviceStore(path, hub=None)`: `.warning`, `baseline_done`, `mark_baseline()`, `all() -> list[dict]` (newest first), `get(mac)`, `upsert(rec)`, `patch(mac, fields) -> dict` (KeyError/ValueError), `delete(mac) -> bool`, `save()` (publishes `"devices"`)
  - Device record keys: `mac, ip, vendor, name, known, announce_arrival, private_mac, first_seen, last_seen, last_seen_ts`
  - fixture `settings` (SettingsStore in tmp_path)

- [ ] **Step 1: Add the fixture and write the failing tests**

Append to `tests/conftest.py`:
```python
from led_notify.store.settings import SettingsStore


@pytest.fixture
def settings(tmp_path):
    return SettingsStore(tmp_path / "settings.json")
```

`tests/test_stores.py`:
```python
import copy
import json

import pytest

from led_notify.hub import Hub
from led_notify.store.devices import DeviceStore
from led_notify.store.jsonfile import load_json, save_json
from led_notify.store.settings import DEFAULT_SETTINGS, SettingsError, SettingsStore


# ---- jsonfile -------------------------------------------------------------
def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "x.json"
    save_json(p, {"a": 1})
    assert load_json(p, {}) == ({"a": 1}, None)
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_file_returns_copy_of_default(tmp_path):
    default = {"a": [1]}
    data, warning = load_json(tmp_path / "missing.json", default)
    data["a"].append(2)
    assert default == {"a": [1]} and warning is None


def test_corrupt_file_is_moved_aside(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{not json")
    data, warning = load_json(p, {"ok": True})
    assert data == {"ok": True} and "corrupt" in warning
    assert not p.exists() and len(list(tmp_path.glob("x.json.corrupt-*"))) == 1


# ---- settings -------------------------------------------------------------
def test_defaults_and_rule_lookup(settings):
    assert settings.data["max_brightness"] == 255
    assert settings.rule("internet.down")["clear_followup"] == "internet.up"
    assert settings.rule("device.new")["quiet_behavior"] == "suppress"
    assert settings.rule("dns.down")["quiet_behavior"] == "dim"
    assert settings.rule("nope") is None


def test_update_persists_and_publishes(tmp_path):
    hub = Hub()
    q = hub.subscribe()
    s = SettingsStore(tmp_path / "s.json", hub)
    s.update({"max_brightness": 128, "rules": {"dns.down": {"pattern": "red_breath"}}})
    assert q.get_nowait() == ("settings", None)
    again = SettingsStore(tmp_path / "s.json")
    assert again.data["max_brightness"] == 128
    assert again.rule("dns.down")["pattern"] == "red_breath"
    assert again.rule("dns.down")["priority"] == "high"


@pytest.mark.parametrize("patch", [
    {"max_brightness": 300},
    {"max_brightness": "high"},
    {"max_brightness": True},
    {"quiet_hours": {"start": "25:00"}},
    {"nope": 1},
    {"idle": {"color": [0, 0, 300]}},
    {"idle": {"color": [0, 0]}},
    {"internet": {"interval_s": 0}},
    {"internet": {"targets": []}},
    {"disk": {"clear_pct": 95}},
    {"thermal": {"clear_c": 90}},
    {"rules": {"internet.down": {"priority": "urgent"}}},
    {"rules": {"dns.down": {"pattern": "nope"}}},
    {"rules": {"dns.down": {"duration_s": 120}}},
    {"rules": {"brand.new": {"enabled": True}}},
    ["not", "a", "dict"],
])
def test_invalid_patches_are_rejected_and_nothing_changes(settings, patch):
    before = copy.deepcopy(settings.data)
    with pytest.raises(SettingsError):
        settings.update(patch)
    assert settings.data == before
    assert not settings.path.exists()


def test_new_complete_rule_can_be_added(settings):
    rule = {"enabled": True, "kind": "oneshot", "priority": "low", "pattern": "green_flash",
            "duration_s": None, "quiet_behavior": "suppress", "clear_followup": None}
    settings.update({"rules": {"backup.done": rule}})
    assert settings.rule("backup.done") == rule


def test_secrets_are_masked_and_placeholders_keep_them(settings):
    settings.update({"adguard": {"password": "pw"}, "webhook_token": "tok"})
    pub = settings.public()
    assert pub["adguard"]["password"] == {"set": True} and pub["webhook_token"] == {"set": True}
    settings.update({"adguard": {"password": {"set": True}, "username": "admin"}, "webhook_token": None})
    assert settings.data["adguard"]["password"] == "pw" and settings.data["webhook_token"] == "tok"
    settings.update({"webhook_token": ""})
    assert settings.public()["webhook_token"] == {"set": False}


def test_corrupt_or_invalid_file_falls_back_to_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"max_brightness": 999}))
    s = SettingsStore(p)
    assert s.data == DEFAULT_SETTINGS and "invalid" in s.warning


def test_stored_file_gains_new_default_keys(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"max_brightness": 100}))
    s = SettingsStore(p)
    assert s.data["max_brightness"] == 100 and s.data["idle"]["brightness"] == 12 and s.warning is None


# ---- devices --------------------------------------------------------------
def rec(mac, ts=1000.0, **kw):
    return {"mac": mac, "ip": "192.168.0.9", "vendor": "Acme", "name": "", "known": False,
            "announce_arrival": False, "private_mac": False, "first_seen": "2026-10-11T12:00:00-07:00",
            "last_seen": "2026-10-11T12:00:00-07:00", "last_seen_ts": ts, **kw}


def test_device_store_roundtrip_and_order(tmp_path):
    hub = Hub()
    q = hub.subscribe()
    d = DeviceStore(tmp_path / "d.json", hub)
    assert d.baseline_done is False
    d.upsert(rec("AA:00:00:00:00:01", 1000))
    d.upsert(rec("aa:00:00:00:00:02", 2000))
    d.mark_baseline()
    d.save()
    assert q.get_nowait() == ("devices", None)
    again = DeviceStore(tmp_path / "d.json")
    assert again.baseline_done is True
    assert [x["mac"] for x in again.all()] == ["aa:00:00:00:00:02", "AA:00:00:00:00:01"]
    assert again.get("AA:00:00:00:00:01") is not None and again.get("aa:00:00:00:00:01") is not None


def test_device_patch_validation_and_delete(tmp_path):
    d = DeviceStore(tmp_path / "d.json")
    d.upsert(rec("aa:00:00:00:00:01"))
    assert d.patch("AA:00:00:00:00:01", {"name": "Gus phone", "known": True})["name"] == "Gus phone"
    with pytest.raises(ValueError):
        d.patch("aa:00:00:00:00:01", {"ip": "1.2.3.4"})
    with pytest.raises(ValueError):
        d.patch("aa:00:00:00:00:01", {"known": "yes"})
    with pytest.raises(ValueError):
        d.patch("aa:00:00:00:00:01", {"name": "x" * 65})
    with pytest.raises(KeyError):
        d.patch("bb:00:00:00:00:01", {"name": "x"})
    assert d.delete("aa:00:00:00:00:01") is True and d.delete("aa:00:00:00:00:01") is False
    assert DeviceStore(tmp_path / "d.json").all() == []


def test_device_store_bad_shape_starts_empty(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps([1, 2]))
    d = DeviceStore(p)
    assert d.all() == [] and d.warning
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_stores.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.store'`

- [ ] **Step 3: Implement jsonfile**

`led_notify/store/__init__.py`: empty file.

`led_notify/store/jsonfile.py`:
```python
"""Atomic JSON persistence with corrupt-file recovery."""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path


def load_json(path: Path, default):
    """Return (data, warning). Missing file -> copy of default; corrupt file is moved aside."""
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default), None
    try:
        return json.loads(path.read_text()), None
    except (OSError, ValueError) as e:
        aside = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            path.rename(aside)
        except OSError:
            aside = path
        return copy.deepcopy(default), f"{path.name} was corrupt ({e}); saved as {aside.name}, using defaults"


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
```

- [ ] **Step 4: Implement settings**

`led_notify/store/settings.py`:
```python
"""Settings + rules persisted as JSON and validated against the default shape."""
from __future__ import annotations

import copy
import re
from pathlib import Path

from ..events import RULE_PRIORITIES
from ..patterns import NAMED
from .jsonfile import load_json, save_json


def _rule(priority, pattern, kind, duration_s=None, clear_followup=None):
    return {
        "enabled": True, "kind": kind, "priority": priority, "pattern": pattern, "duration_s": duration_s,
        "quiet_behavior": "dim" if priority in ("high", "critical") else "suppress",
        "clear_followup": clear_followup,
    }


DEFAULT_RULES = {
    "internet.down": _rule("critical", "red_breath", "state", clear_followup="internet.up"),
    "dns.down": _rule("high", "purple_breath", "state"),
    "disk.smart_error": _rule("high", "red_white_snake", "state"),
    "internet.slow": _rule("normal", "yellow_breath", "state"),
    "disk.full": _rule("normal", "yellow_stacking", "state"),
    "cpu.hot": _rule("normal", "fire_red_yellow", "state"),
    "device.new_late": _rule("high", "intruder", "oneshot", 8),
    "device.new": _rule("normal", "new_device", "oneshot"),
    "container.died": _rule("normal", "purple_flash_x3", "oneshot"),
    "internet.up": _rule("normal", "green_flash_x2", "oneshot"),
    "device.known_arrived": _rule("low", "welcome", "oneshot", 6),
    "adguard.block_spike": _rule("low", "yellow_white_stars", "oneshot", 5),
}

DEFAULT_SETTINGS = {
    "max_brightness": 255,
    "manual_timeout_min": 30,
    "webhook_token": "",
    "idle": {"color": [0, 179, 164], "brightness": 12},
    "quiet_hours": {"enabled": True, "start": "23:00", "end": "07:00", "brightness_pct": 20, "idle_off": True},
    "internet": {"targets": ["1.1.1.1", "8.8.8.8", "9.9.9.9"], "gateway": "192.168.0.1", "interval_s": 10,
                 "fail_rounds": 3, "ok_rounds": 2, "slow_rtt_ms": 150, "slow_loss_pct": 20, "slow_window_s": 300},
    "dns": {"server": "127.0.0.1", "query": "cloudflare.com", "interval_s": 30, "fail_rounds": 3, "ok_rounds": 2},
    "devices": {"interface": "eth0", "interval_s": 60, "late_start": "00:00", "late_end": "06:00",
                "arrival_absent_min": 30},
    "disk": {"paths": ["/host/DATA"], "full_pct": 90, "clear_pct": 85, "usage_interval_s": 300,
             "smart_devices": ["/dev/nvme0"], "smart_interval_s": 3600},
    "thermal": {"sensor_names": ["k10temp", "coretemp"], "hot_c": 85.0, "clear_c": 75.0, "sustain_s": 120,
                "interval_s": 30},
    "docker": {"socket": "/var/run/docker.sock", "intentional_window_s": 15},
    "adguard": {"url": "http://127.0.0.1:3001", "username": "", "password": "", "min_blocked": 50,
                "factor": 3.0, "interval_s": 60},
    "rules": DEFAULT_RULES,
}

SECRETS = (("adguard", "password"), ("webhook_token",))
RANGES = {
    "max_brightness": (0, 255), "manual_timeout_min": (1, 1440), "idle.brightness": (0, 255),
    "quiet_hours.brightness_pct": (1, 100), "internet.slow_loss_pct": (1, 100),
    "disk.full_pct": (1, 100), "disk.clear_pct": (1, 100),
    "adguard.min_blocked": (1, 100000), "adguard.factor": (1, 100),
}
POSITIVE_SUFFIXES = ("_s", "_min", "_rounds", "_ms")
TIME_KEYS = ("start", "end", "late_start", "late_end")
EMPTY_LIST_OK = ("disk.smart_devices",)
RULE_KEYS = {"enabled", "kind", "priority", "pattern", "duration_s", "quiet_behavior", "clear_followup"}
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsError(ValueError):
    pass


def deep_merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_shape(default, value, path: str) -> None:
    if isinstance(default, dict):
        if not isinstance(value, dict):
            raise SettingsError(f"{path or 'settings'}: expected an object")
        for k, v in value.items():
            p = f"{path}.{k}" if path else k
            if k not in default:
                raise SettingsError(f"{p}: unknown setting")
            if p != "rules":
                _check_shape(default[k], v, p)
        return
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(default, float):
        ok = _is_num(value)
    elif isinstance(default, str):
        ok = isinstance(value, str)
    elif isinstance(default, list):
        item = type(default[0])
        ok = isinstance(value, list) and all(isinstance(x, item) and not isinstance(x, bool) for x in value)
        if ok and not value and path not in EMPTY_LIST_OK:
            raise SettingsError(f"{path}: must not be empty")
    else:
        ok = True
    if not ok:
        raise SettingsError(f"{path}: expected {type(default).__name__}")
    if path in RANGES:
        lo, hi = RANGES[path]
        if not lo <= value <= hi:
            raise SettingsError(f"{path}: must be between {lo} and {hi}")
    if path.endswith(POSITIVE_SUFFIXES) and _is_num(value) and value <= 0:
        raise SettingsError(f"{path}: must be greater than 0")
    if path.split(".")[-1] in TIME_KEYS and not _HHMM.match(value):
        raise SettingsError(f"{path}: use HH:MM (24h)")
    if path == "idle.color" and (len(value) != 3 or not all(0 <= v <= 255 for v in value)):
        raise SettingsError("idle.color: must be [r, g, b] with values 0-255")


def _check_rules(rules) -> None:
    if not isinstance(rules, dict):
        raise SettingsError("rules: expected an object")
    for name, r in rules.items():
        p = f"rules.{name}"
        if not isinstance(r, dict) or set(r) != RULE_KEYS:
            raise SettingsError(f"{p}: needs exactly {', '.join(sorted(RULE_KEYS))}")
        if not isinstance(r["enabled"], bool):
            raise SettingsError(f"{p}.enabled: expected true/false")
        if r["kind"] not in ("state", "oneshot"):
            raise SettingsError(f"{p}.kind: must be state or oneshot")
        if r["priority"] not in RULE_PRIORITIES:
            raise SettingsError(f"{p}.priority: must be one of {', '.join(RULE_PRIORITIES)}")
        if r["pattern"] not in NAMED:
            raise SettingsError(f"{p}.pattern: unknown pattern {r['pattern']!r}")
        d = r["duration_s"]
        if d is not None and (not _is_num(d) or not 0.5 <= d <= 60):
            raise SettingsError(f"{p}.duration_s: must be empty or 0.5-60")
        if r["quiet_behavior"] not in ("dim", "suppress"):
            raise SettingsError(f"{p}.quiet_behavior: must be dim or suppress")
        if r["clear_followup"] is not None and not isinstance(r["clear_followup"], str):
            raise SettingsError(f"{p}.clear_followup: must be empty or an event name")


def validate(data) -> None:
    _check_shape(DEFAULT_SETTINGS, data, "")
    _check_rules(data["rules"])
    if data["disk"]["clear_pct"] >= data["disk"]["full_pct"]:
        raise SettingsError("disk.clear_pct must be below disk.full_pct")
    if data["thermal"]["clear_c"] >= data["thermal"]["hot_c"]:
        raise SettingsError("thermal.clear_c must be below thermal.hot_c")


def _drop_secret_placeholders(patch: dict) -> None:
    """A secret given as anything but a string (e.g. {"set": true} or null) means 'keep it'."""
    for path in SECRETS:
        parent = patch
        for key in path[:-1]:
            parent = parent.get(key) if isinstance(parent, dict) else None
        if isinstance(parent, dict) and path[-1] in parent and not isinstance(parent[path[-1]], str):
            del parent[path[-1]]


class SettingsStore:
    def __init__(self, path: Path, hub=None):
        self.path = Path(path)
        self.hub = hub
        stored, self.warning = load_json(self.path, {})
        data = deep_merge(copy.deepcopy(DEFAULT_SETTINGS), stored if isinstance(stored, dict) else {})
        try:
            validate(data)
        except SettingsError as e:
            self.warning = f"settings.json is invalid ({e}); using defaults"
            data = copy.deepcopy(DEFAULT_SETTINGS)
        self._data = data

    @property
    def data(self) -> dict:
        return self._data

    def rule(self, name: str) -> dict | None:
        return self._data["rules"].get(name)

    def public(self) -> dict:
        out = copy.deepcopy(self._data)
        for *head, last in SECRETS:
            parent = out
            for key in head:
                parent = parent[key]
            parent[last] = {"set": bool(parent[last])}
        return out

    def update(self, patch: dict) -> None:
        if not isinstance(patch, dict):
            raise SettingsError("expected an object")
        patch = copy.deepcopy(patch)
        _drop_secret_placeholders(patch)
        candidate = deep_merge(copy.deepcopy(self._data), patch)
        validate(candidate)
        self._data = candidate
        save_json(self.path, candidate)
        if self.hub:
            self.hub.publish("settings", None)
```

- [ ] **Step 5: Implement devices store**

`led_notify/store/devices.py`:
```python
"""Devices seen on the LAN, keyed by lower-case MAC."""
from __future__ import annotations

from pathlib import Path

from .jsonfile import load_json, save_json

PATCHABLE = {"name": str, "known": bool, "announce_arrival": bool}
EMPTY = {"baseline_done": False, "devices": {}}


class DeviceStore:
    def __init__(self, path: Path, hub=None):
        self.path = Path(path)
        self.hub = hub
        raw, self.warning = load_json(self.path, EMPTY)
        if not isinstance(raw, dict) or not isinstance(raw.get("devices"), dict):
            raw = {"baseline_done": False, "devices": {}}
            self.warning = self.warning or "devices.json had an unexpected shape; starting empty"
        self._raw = raw

    @property
    def baseline_done(self) -> bool:
        return bool(self._raw.get("baseline_done"))

    def mark_baseline(self) -> None:
        self._raw["baseline_done"] = True

    def all(self) -> list[dict]:
        return sorted(self._raw["devices"].values(), key=lambda d: d.get("last_seen_ts", 0), reverse=True)

    def get(self, mac: str) -> dict | None:
        return self._raw["devices"].get(mac.lower())

    def upsert(self, rec: dict) -> None:
        self._raw["devices"][rec["mac"].lower()] = rec

    def patch(self, mac: str, fields: dict) -> dict:
        rec = self.get(mac)
        if rec is None:
            raise KeyError(mac)
        if not isinstance(fields, dict):
            raise ValueError("expected an object")
        for k, v in fields.items():
            kind = PATCHABLE.get(k)
            if kind is None:
                raise ValueError(f"{k}: not editable")
            if not isinstance(v, kind) or (kind is str and len(v) > 64):
                raise ValueError(f"{k}: invalid value")
        rec.update(fields)
        self.save()
        return rec

    def delete(self, mac: str) -> bool:
        existed = self._raw["devices"].pop(mac.lower(), None) is not None
        if existed:
            self.save()
        return existed

    def save(self) -> None:
        save_json(self.path, self._raw)
        if self.hub:
            self.hub.publish("devices", None)
```

Note: `upsert` must store lower-case keys; the test upserts `"AA:..."` and reads it back via `get()` in either case — `rec["mac"]` keeps the caller's spelling while the key is lower-case. The device monitor always passes lower-case MACs.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_stores.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add led_notify/store tests/conftest.py tests/test_stores.py
git commit -m "feat: settings, rules and device stores with validation"
```

---

### Task 5: Quiet-hours window

**Files:**
- Create: `led_notify/engine/quiet.py`
- Test: `tests/test_quiet.py`

**Interfaces:**
- Produces: `parse_hhmm(s: str) -> datetime.time` (ValueError on bad input); `in_window(now: datetime, start: "HH:MM", end: "HH:MM") -> bool` (start inclusive, end exclusive, may span midnight, start == end → False)

- [ ] **Step 1: Write the failing tests**

`tests/test_quiet.py`:
```python
from datetime import datetime

import pytest

from led_notify.engine.quiet import in_window, parse_hhmm


def at(hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, 11, h, m)


@pytest.mark.parametrize("t,expected", [
    ("23:00", True), ("23:30", True), ("00:00", True), ("06:59", True),
    ("07:00", False), ("12:00", False), ("22:59", False),
])
def test_in_window_across_midnight(t, expected):
    assert in_window(at(t), "23:00", "07:00") is expected


@pytest.mark.parametrize("t,expected", [("09:00", True), ("12:00", True), ("08:59", False), ("17:00", False)])
def test_in_window_same_day(t, expected):
    assert in_window(at(t), "09:00", "17:00") is expected


def test_in_window_equal_start_end_is_never():
    assert in_window(at("10:00"), "10:00", "10:00") is False


@pytest.mark.parametrize("bad", ["25:00", "7", "07:60", "", "ab:cd"])
def test_parse_hhmm_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_hhmm(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_quiet.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.engine.quiet'`

- [ ] **Step 3: Implement**

`led_notify/engine/quiet.py`:
```python
"""Time-of-day windows (quiet hours, late-night device alerts)."""
from __future__ import annotations

import re
from datetime import datetime, time

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(s: str) -> time:
    m = _HHMM.match(s or "")
    if not m:
        raise ValueError(f"not a HH:MM time: {s!r}")
    return time(int(m.group(1)), int(m.group(2)))


def in_window(now: datetime, start: str, end: str) -> bool:
    s, e, t = parse_hhmm(start), parse_hhmm(end), now.time().replace(second=0, microsecond=0)
    if s == e:
        return False
    if s < e:
        return s <= t < e
    return t >= s or t < e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_quiet.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add led_notify/engine/quiet.py tests/test_quiet.py
git commit -m "feat: quiet-hours window helper"
```

---

### Task 6: Arbiter

**Files:**
- Create: `led_notify/engine/arbiter.py`
- Modify: `tests/fakes.py` (append `settle`)
- Test: `tests/test_arbiter.py`

**Interfaces:**
- Consumes: `Event`, `PRIORITIES` (Task 1); `NAMED`, `Color`, `Sequence`, `resolve`, `describe` (Task 2); `Player`, `FakeStrip` (Task 3); `SettingsStore` (Task 4); `in_window` (Task 5); `Hub` (Task 1).
- Produces:
  - `Arbiter(player, settings, clock, hub=None, log_size=200)`
  - `async submit(ev: Event) -> str` — outcomes: `active`, `cleared`, `queued`, `skipped:disabled|quiet|duplicate`, `ignored:already_active|not_active` (later log entries: `shown`, `skipped:priority`, `skipped:quiet`, `preempted`, `dropped:queue_full`)
  - `set_manual(spec: dict | None, brightness: int | None = None, power: bool = True)` (raises `PatternError`), `clear_manual()`
  - `snapshot() -> {"current", "active": [{name, priority, since, data}], "queue": [names], "playing", "manual": {desc, expires_in_s} | None, "quiet": bool}`
  - `async tick()`, `async run()`, `playing_task` property, `log: deque[dict]` with entries `{time, name, kind, source, outcome}`
  - Hub messages: `("log", entry)` and `("state", None)`
  - tests: `tests.fakes.settle(arbiter)`

- [ ] **Step 1: Add the settle helper**

Append to `tests/fakes.py`:
```python


async def settle(arbiter, rounds: int = 20) -> None:
    """Run arbiter ticks until queued one-shots have played (FakeClock makes them instant)."""
    for _ in range(rounds):
        await arbiter.tick()
        task = arbiter.playing_task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
```

- [ ] **Step 2: Write the failing tests**

`tests/test_arbiter.py`:
```python
import asyncio

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_arbiter.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.engine.arbiter'`

- [ ] **Step 4: Implement the arbiter**

`led_notify/engine/arbiter.py`:
```python
"""The single authority over what the strip shows."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass

from ..events import PRIORITIES, Event
from ..patterns import NAMED, Color, Sequence, describe, resolve
from .quiet import in_window

log = logging.getLogger(__name__)

DEDUP_S = 30.0
QUEUE_MAX = 5
DEFAULT_ONESHOT_S = 5.0
UNDEDUPED_SOURCES = ("test",)


@dataclass
class Look:
    key: tuple
    pattern: object
    level: int
    priority: int
    desc: str
    reason: str


@dataclass
class _State:
    event: Event
    rule: dict
    since: str
    seq: int


class Arbiter:
    def __init__(self, player, settings, clock, hub=None, log_size: int = 200):
        self.player = player
        self.settings = settings
        self.clock = clock
        self.hub = hub
        self.log: deque[dict] = deque(maxlen=log_size)
        self.current: dict | None = None
        self._states: dict[str, _State] = {}
        self._queue: deque[tuple[Event, dict]] = deque()
        self._dedup: dict[str, float] = {}
        self._manual: dict | None = None
        self._playing: tuple[Event, dict, asyncio.Task] | None = None
        self._rendered_key: tuple | None = None
        self._seq = 0
        self._wake = asyncio.Event()

    # ---- public API ------------------------------------------------------------
    @property
    def playing_task(self) -> asyncio.Task | None:
        return self._playing[2] if self._playing else None

    async def submit(self, ev: Event) -> str:
        rule = self._rule_for(ev)
        followup = None
        if ev.kind == "clear":
            outcome, followup = self._clear(ev)
        elif not rule["enabled"] and not ev.force:
            outcome = "skipped:disabled"
        elif ev.kind == "start":
            outcome = self._start(ev, rule)
        else:
            outcome = self._enqueue(ev, rule)
        self._log(ev, outcome)
        if followup is not None:
            await self.submit(followup)
        self._wake.set()
        return outcome

    def set_manual(self, spec: dict | None, brightness: int | None = None, power: bool = True) -> None:
        previous = self._manual_active()
        if spec is None:
            spec = previous["spec"] if previous else {"color": [255, 255, 255]}
        pattern = resolve(spec)  # raises PatternError before any state changes
        if not power:
            level = 0
        elif brightness is not None:
            level = max(0, min(255, int(brightness)))
        elif previous and previous["level"] > 0:
            level = previous["level"]
        else:
            level = self.settings.data["max_brightness"]
        self._seq += 1
        self._manual = {
            "spec": spec, "pattern": pattern, "level": level, "rev": self._seq,
            "desc": describe(spec) if power else "off",
            "expires": self.clock.monotonic() + self.settings.data["manual_timeout_min"] * 60,
        }
        self._wake.set()

    def clear_manual(self) -> None:
        self._manual = None
        self._wake.set()

    def snapshot(self) -> dict:
        manual = self._manual_active()
        active = sorted(self._states.values(), key=lambda s: (PRIORITIES[self._live_rule(s)["priority"]], s.seq),
                        reverse=True)
        return {
            "current": self.current,
            "active": [{"name": s.event.name, "priority": self._live_rule(s)["priority"], "since": s.since,
                        "data": s.event.data} for s in active],
            "queue": [ev.name for ev, _ in self._queue],
            "playing": self._playing[0].name if self._playing else None,
            "manual": None if manual is None else {
                "desc": manual["desc"],
                "expires_in_s": max(0, round(manual["expires"] - self.clock.monotonic())),
            },
            "quiet": self._quiet(),
        }

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("arbiter tick failed")
                self._rendered_key = None
            try:
                await asyncio.wait_for(self._wake.wait(), 1.0)
            except TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> None:
        if self._playing:
            return
        base = self._base()
        while self._queue:
            ev, rule = self._queue.popleft()
            if not ev.force and PRIORITIES[rule["priority"]] < base.priority:
                self._log(ev, "skipped:priority")
                continue
            level = self._level(rule, force=ev.force)
            if level is None:
                self._log(ev, "skipped:quiet")
                continue
            task = asyncio.create_task(self._play_oneshot(ev, rule, level))
            self._playing = (ev, rule, task)
            return
        if base.key != self._rendered_key:
            await self.player.show(base.pattern, base.level)
            self._rendered_key = base.key
            self._set_current(base.desc, base.reason)

    # ---- internals ---------------------------------------------------------------
    def _rule_for(self, ev: Event) -> dict:
        rule = self.settings.rule(ev.name)
        if rule is not None:
            return rule
        priority = ev.priority or "normal"
        return {
            "enabled": True, "kind": "oneshot" if ev.kind == "oneshot" else "state", "priority": priority,
            "look": ev.look or {"pattern": "white_flash"}, "duration_s": ev.duration_s,
            "quiet_behavior": "dim" if PRIORITIES[priority] >= PRIORITIES["high"] else "suppress",
            "clear_followup": None,
        }

    def _live_rule(self, st: _State) -> dict:
        return self.settings.rule(st.event.name) or st.rule

    @staticmethod
    def _pattern_of(rule: dict):
        return resolve(rule["look"]) if "look" in rule else NAMED[rule["pattern"]]

    @staticmethod
    def _desc_of(rule: dict) -> str:
        return describe(rule["look"]) if "look" in rule else rule["pattern"]

    def _quiet(self) -> bool:
        q = self.settings.data["quiet_hours"]
        return bool(q["enabled"]) and in_window(self.clock.now(), q["start"], q["end"])

    def _level(self, rule: dict, force: bool = False) -> int | None:
        """Brightness for a rule right now, or None if quiet hours suppress it."""
        mx = self.settings.data["max_brightness"]
        if force or not self._quiet():
            return mx
        if rule["quiet_behavior"] == "suppress":
            return None
        return max(1, round(mx * self.settings.data["quiet_hours"]["brightness_pct"] / 100))

    def _manual_active(self) -> dict | None:
        if self._manual and self.clock.monotonic() >= self._manual["expires"]:
            self._manual = None
        return self._manual

    def _start(self, ev: Event, rule: dict) -> str:
        if ev.name in self._states:
            return "ignored:already_active"
        self._seq += 1
        self._states[ev.name] = _State(ev, rule, self.clock.now().isoformat(timespec="seconds"), self._seq)
        if self._level(rule) is None:
            return "skipped:quiet"
        if self._playing and PRIORITIES[self._playing[1]["priority"]] < PRIORITIES[rule["priority"]]:
            self._playing[2].cancel()
        return "active"

    def _clear(self, ev: Event) -> tuple[str, Event | None]:
        st = self._states.pop(ev.name, None)
        if st is None:
            return "ignored:not_active", None
        name = self._live_rule(st).get("clear_followup")
        followup = Event(name, "oneshot", source=ev.source, data=dict(ev.data)) if name else None
        return "cleared", followup

    def _enqueue(self, ev: Event, rule: dict) -> str:
        if not ev.force:
            if self._level(rule) is None:
                return "skipped:quiet"
            if ev.source not in UNDEDUPED_SOURCES:
                now = self.clock.monotonic()
                key = f"{ev.name}|{ev.data.get('key', '')}"
                last = self._dedup.get(key)
                if last is not None and now - last < DEDUP_S:
                    return "skipped:duplicate"
                self._dedup = {k: t for k, t in self._dedup.items() if now - t < DEDUP_S}
                self._dedup[key] = now
        if len(self._queue) >= QUEUE_MAX:
            dropped, _ = self._queue.popleft()
            self._log(dropped, "dropped:queue_full")
        self._queue.append((ev, rule))
        return "queued"

    def _base(self) -> Look:
        best = None
        for st in self._states.values():
            rule = self._live_rule(st)
            if not rule["enabled"]:
                continue
            level = self._level(rule)
            if level is None:
                continue
            rank = (PRIORITIES[rule["priority"]], st.seq)
            if best is None or rank > best[0]:
                best = (rank, st, rule, level)
        manual = self._manual_active()
        if manual and (best is None or best[0][0] < PRIORITIES["manual"]):
            return Look(("manual", manual["rev"]), manual["pattern"], manual["level"], PRIORITIES["manual"],
                        manual["desc"], "manual")
        if best:
            (priority, seq), st, rule, level = best
            desc = self._desc_of(rule)
            return Look(("state", st.event.name, seq, desc, level), self._pattern_of(rule), level, priority, desc,
                        f"{st.event.name} since {st.since[11:16]}")
        idle = self.settings.data["idle"]
        night_off = self._quiet() and self.settings.data["quiet_hours"]["idle_off"]
        level = 0 if night_off else round(idle["brightness"] * self.settings.data["max_brightness"] / 255)
        rgb = tuple(idle["color"])
        return Look(("idle", rgb, level), Color(rgb), level, PRIORITIES["idle"], "idle glow", "idle")

    async def _play_oneshot(self, ev: Event, rule: dict, level: int) -> None:
        pattern = self._pattern_of(rule)
        self._log(ev, "shown")
        self._set_current(self._desc_of(rule), f"{ev.name} ({ev.source})")
        try:
            await self.player.show(pattern, level)
            if not isinstance(pattern, Sequence):
                await self.clock.sleep(rule.get("duration_s") or ev.duration_s or DEFAULT_ONESHOT_S)
        except asyncio.CancelledError:
            self._log(ev, "preempted")
        except Exception:
            log.exception("one-shot %s failed", ev.name)
        finally:
            self._playing = None
            self._rendered_key = None
            self._wake.set()

    def _log(self, ev: Event, outcome: str) -> None:
        entry = {"time": self.clock.now().isoformat(timespec="seconds"), "name": ev.name, "kind": ev.kind,
                 "source": ev.source, "outcome": outcome}
        self.log.append(entry)
        if self.hub:
            self.hub.publish("log", entry)

    def _set_current(self, desc: str, reason: str) -> None:
        self.current = {"pattern": desc, "reason": reason, "since": self.clock.now().isoformat(timespec="seconds")}
        if self.hub:
            self.hub.publish("state", None)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_arbiter.py -q`
Expected: PASS (all tests). If `test_higher_priority_state_preempts_playing_oneshot` fails, check that `_start` cancels the task and `_play_oneshot` catches `CancelledError` without re-raising.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add led_notify/engine/arbiter.py tests/fakes.py tests/test_arbiter.py
git commit -m "feat: arbiter with priorities, one-shot queue, manual override and quiet hours"
```

---

### Task 7: Monitor base + internet + DNS monitors

**Files:**
- Create: `led_notify/monitors/__init__.py` (empty), `led_notify/monitors/base.py`, `led_notify/monitors/internet.py`, `led_notify/monitors/dns.py`
- Test: `tests/test_monitors_net.py`

**Interfaces:**
- Consumes: `Event` (Task 1), `SettingsStore` fixture (Task 4), `FakeClock` (Task 1).
- Produces:
  - `Emit = Callable[[Event], Awaitable[object]]`; `MonitorDisabled(Exception)`
  - `Debouncer().feed(ok: bool, fail_n: int, ok_n: int) -> "start" | "clear" | None`, `.active`
  - `Hysteresis().feed(value: float, now: float, hi: float, lo: float, sustain_s: float = 0.0) -> "start" | "clear" | None`, `.active`
  - `PollingMonitor(settings, clock)`: class attrs `name`, `section`; `cfg` property; `async check() -> list[Event]`; `interval_s()`; `heartbeat` callable attribute (default no-op); `async run(emit)` loops check → heartbeat → emit → sleep
  - `internet.ping(host, timeout_s=1.0) -> float | None`, `parse_ping(text) -> float | None`, `InternetMonitor(settings, clock, ping=ping)` emitting `internet.down` start/clear (data `gateway_reachable`) and `internet.slow` start/clear
  - `dns.dns_ok(server, name, timeout_s=2.0) -> bool`, `DnsMonitor(settings, clock, probe=dns_ok)` emitting `dns.down` start/clear

- [ ] **Step 1: Write the failing tests**

`tests/test_monitors_net.py`:
```python
import pytest

from led_notify.events import Event
from led_notify.monitors.base import Debouncer, Hysteresis, PollingMonitor
from led_notify.monitors.dns import DnsMonitor
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
    for _ in range(30):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_monitors_net.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.monitors'`

- [ ] **Step 3: Implement base**

`led_notify/monitors/__init__.py`: empty file.

`led_notify/monitors/base.py`:
```python
"""Shared monitor plumbing: debouncers and the polling loop."""
from __future__ import annotations

from typing import Awaitable, Callable

from ..events import Event

Emit = Callable[[Event], Awaitable[object]]


class MonitorDisabled(Exception):
    """Raised by a monitor that isn't configured yet; the supervisor retries later."""


class Debouncer:
    """Consecutive-sample debounce for a boolean health signal."""

    def __init__(self):
        self.active = False
        self._fails = 0
        self._oks = 0

    def feed(self, ok: bool, fail_n: int, ok_n: int) -> str | None:
        if ok:
            self._oks += 1
            self._fails = 0
        else:
            self._fails += 1
            self._oks = 0
        if not self.active and self._fails >= fail_n:
            self.active = True
            return "start"
        if self.active and self._oks >= ok_n:
            self.active = False
            return "clear"
        return None


class Hysteresis:
    """Start when value >= hi for sustain_s; clear when value < lo."""

    def __init__(self):
        self.active = False
        self._above_since: float | None = None

    def feed(self, value: float, now: float, hi: float, lo: float, sustain_s: float = 0.0) -> str | None:
        if value >= hi:
            if self._above_since is None:
                self._above_since = now
        else:
            self._above_since = None
        if not self.active and self._above_since is not None and now - self._above_since >= sustain_s:
            self.active = True
            return "start"
        if self.active and value < lo:
            self.active = False
            return "clear"
        return None


class PollingMonitor:
    name = "monitor"
    section = ""

    def __init__(self, settings, clock):
        self.settings = settings
        self.clock = clock
        self.heartbeat: Callable[[], None] = lambda: None

    @property
    def cfg(self) -> dict:
        return self.settings.data[self.section]

    async def check(self) -> list[Event]:
        raise NotImplementedError

    def interval_s(self) -> float:
        return self.cfg["interval_s"]

    async def run(self, emit: Emit) -> None:
        while True:
            events = await self.check()
            self.heartbeat()
            for ev in events:
                await emit(ev)
            await self.clock.sleep(self.interval_s())
```

- [ ] **Step 4: Implement internet and dns**

`led_notify/monitors/internet.py`:
```python
"""Internet reachability and quality via ICMP ping."""
from __future__ import annotations

import asyncio
import re
import statistics
from collections import deque

from ..events import Event
from .base import Debouncer, PollingMonitor

_TIME = re.compile(r"time[=<]([\d.]+)\s*ms")


def parse_ping(text: str) -> float | None:
    m = _TIME.search(text)
    return float(m.group(1)) if m else None


async def ping(host: str, timeout_s: float = 1.0) -> float | None:
    """Round-trip time in ms, or None if the host didn't answer (Linux iputils ping)."""
    proc = await asyncio.create_subprocess_exec(
        "ping", "-n", "-c", "1", "-W", str(max(1, int(timeout_s))), host,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return parse_ping(out.decode(errors="replace")) if proc.returncode == 0 else None


class InternetMonitor(PollingMonitor):
    name = section = "internet"

    def __init__(self, settings, clock, ping=ping):
        super().__init__(settings, clock)
        self.ping = ping
        self.down = Debouncer()
        self.slow_active = False
        self._window: deque[tuple[float, list]] = deque()
        self._good_since: float | None = None

    async def check(self) -> list[Event]:
        cfg = self.cfg
        t = self.clock.monotonic()
        rtts = list(await asyncio.gather(*(self.ping(h) for h in cfg["targets"])))
        events: list[Event] = []
        change = self.down.feed(any(r is not None for r in rtts), cfg["fail_rounds"], cfg["ok_rounds"])
        if change == "start":
            gateway = await self.ping(cfg["gateway"])
            events.append(Event("internet.down", "start", source=self.name,
                                data={"gateway_reachable": gateway is not None}))
        elif change == "clear":
            events.append(Event("internet.down", "clear", source=self.name))
        events += self._check_slow(t, rtts, cfg)
        return events

    def _check_slow(self, t: float, rtts: list, cfg: dict) -> list[Event]:
        self._window.append((t, rtts))
        while t - self._window[0][0] > cfg["slow_window_s"]:
            self._window.popleft()
        if self.down.active:
            return self._end_slow()
        if not self.slow_active:
            samples = [r for _, rs in self._window for r in rs]
            good = [r for r in samples if r is not None]
            loss_pct = 100 * (len(samples) - len(good)) / len(samples)
            median = statistics.median(good) if good else 0.0
            covered = t - self._window[0][0] >= cfg["slow_window_s"] - cfg["interval_s"]
            if covered and (median > cfg["slow_rtt_ms"] or loss_pct > cfg["slow_loss_pct"]):
                self.slow_active = True
                self._good_since = None
                return [Event("internet.slow", "start", source=self.name,
                              data={"median_ms": round(median, 1), "loss_pct": round(loss_pct, 1)})]
            return []
        round_ok = all(r is not None for r in rtts) and statistics.median(rtts) <= cfg["slow_rtt_ms"]
        if not round_ok:
            self._good_since = None
            return []
        if self._good_since is None:
            self._good_since = t
        if t - self._good_since >= cfg["slow_window_s"]:
            return self._end_slow()
        return []

    def _end_slow(self) -> list[Event]:
        self._good_since = None
        if not self.slow_active:
            return []
        self.slow_active = False
        return [Event("internet.slow", "clear", source=self.name)]
```

`led_notify/monitors/dns.py`:
```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_monitors_net.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add led_notify/monitors tests/test_monitors_net.py
git commit -m "feat: monitor base, internet and dns monitors"
```

---

### Task 8: Device monitor

**Files:**
- Create: `led_notify/monitors/devices.py`
- Test: `tests/test_monitor_devices.py`

**Interfaces:**
- Consumes: `PollingMonitor` (Task 7), `DeviceStore` (Task 4), `in_window` (Task 5), `Event` (Task 1).
- Produces:
  - `parse_arp_scan(text) -> list[(ip, mac_lower, vendor)]` (deduped by MAC, `(DUP: n)` stripped)
  - `async arp_scan(interface) -> list[...]` (raises `RuntimeError` on non-zero exit)
  - `is_private_mac(mac) -> bool`
  - `DeviceTracker(store).observe(results, now_wall: datetime, cfg: dict) -> list[Event]`
  - `DeviceMonitor(settings, clock, store, scan=arp_scan)`; events `device.new` / `device.new_late` / `device.known_arrived` (oneshot, `data.key = mac`)

- [ ] **Step 1: Write the failing tests**

`tests/test_monitor_devices.py`:
```python
import pytest

from led_notify.monitors.devices import DeviceMonitor, DeviceTracker, is_private_mac, parse_arp_scan
from led_notify.store.devices import DeviceStore

PHONE = ("192.168.0.20", "da:11:22:33:44:55", "(Unknown: locally administered)")
ROUTER = ("192.168.0.1", "a4:2b:b0:00:00:01", "TP-LINK")
TV = ("192.168.0.30", "00:1c:c2:9f:4e:37", "Roku")


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / "devices.json")


def names(events):
    return [e.name for e in events]


def test_parse_arp_scan_with_header_and_dups():
    text = (
        "Interface: eth0, type: EN10MB, MAC: 00:11:22:33:44:55, IPv4: 192.168.0.61\n"
        "Starting arp-scan 1.10.0 with 256 hosts\n"
        "192.168.0.1\tA4:2B:B0:00:00:01\tTP-LINK TECHNOLOGIES CO.,LTD.\n"
        "192.168.0.20\tda:11:22:33:44:55\t(Unknown: locally administered)\n"
        "192.168.0.1\ta4:2b:b0:00:00:01\tTP-LINK TECHNOLOGIES CO.,LTD. (DUP: 2)\n"
        "\n3 packets received by filter, 0 packets dropped by kernel\n"
    )
    assert parse_arp_scan(text) == [
        ("192.168.0.1", "a4:2b:b0:00:00:01", "TP-LINK TECHNOLOGIES CO.,LTD."),
        ("192.168.0.20", "da:11:22:33:44:55", "(Unknown: locally administered)"),
    ]


def test_is_private_mac():
    assert is_private_mac("da:11:22:33:44:55") is True
    assert is_private_mac("a4:2b:b0:00:00:01") is False


def test_first_scan_is_a_silent_baseline(store, clock, settings):
    t = DeviceTracker(store)
    assert t.observe([ROUTER, PHONE], clock.now(), settings.data["devices"]) == []
    assert store.baseline_done and len(store.all()) == 2
    assert store.get(PHONE[1])["private_mac"] is True


def test_empty_first_scan_does_not_complete_baseline(store, clock, settings):
    t = DeviceTracker(store)
    assert t.observe([], clock.now(), settings.data["devices"]) == []
    assert store.baseline_done is False
    assert t.observe([ROUTER], clock.now(), settings.data["devices"]) == []


def test_new_device_alerts_once(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([ROUTER], clock.now(), cfg)
    evs = t.observe([ROUTER, TV], clock.now(), cfg)
    assert names(evs) == ["device.new"]
    assert evs[0].data == {"key": TV[1], "mac": TV[1], "ip": TV[0], "vendor": "Roku"}
    clock.advance(60)
    assert t.observe([ROUTER, TV], clock.now(), cfg) == []


def test_new_device_late_at_night(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([ROUTER], clock.now(), cfg)
    clock.set_time("01:00")
    assert names(t.observe([ROUTER, TV], clock.now(), cfg)) == ["device.new_late"]


def test_known_arrival_needs_30_minutes_away(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([ROUTER, PHONE], clock.now(), cfg)
    store.patch(PHONE[1], {"name": "Gus phone", "known": True, "announce_arrival": True})
    clock.advance(120)                      # missed one scan, back 2 min later
    assert t.observe([ROUTER, PHONE], clock.now(), cfg) == []
    clock.advance(31 * 60)                  # gone 31 min
    t.observe([ROUTER], clock.now(), cfg)
    evs = t.observe([ROUTER, PHONE], clock.now(), cfg)
    assert names(evs) == ["device.known_arrived"] and evs[0].data["name"] == "Gus phone"


def test_arrival_ignored_without_announce_flag(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([PHONE], clock.now(), cfg)
    clock.advance(3600)
    assert t.observe([PHONE], clock.now(), cfg) == []


def test_observe_updates_ip_and_last_seen(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([TV], clock.now(), cfg)
    clock.advance(60)
    t.observe([("192.168.0.31", TV[1], "")], clock.now(), cfg)
    rec = store.get(TV[1])
    assert rec["ip"] == "192.168.0.31" and rec["vendor"] == "Roku"
    assert rec["last_seen_ts"] == clock.now().timestamp()


async def test_device_monitor_uses_configured_interface(store, clock, settings):
    seen = []

    async def scan(interface):
        seen.append(interface)
        return [ROUTER]

    m = DeviceMonitor(settings, clock, store, scan=scan)
    assert await m.check() == []
    assert seen == ["eth0"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_monitor_devices.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.monitors.devices'`

- [ ] **Step 3: Implement**

`led_notify/monitors/devices.py`:
```python
"""LAN device discovery via arp-scan."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from ..engine.quiet import in_window
from ..events import Event
from .base import PollingMonitor

_LINE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})[ \t]*(.*)$", re.M)
_DUP = re.compile(r"\s*\(DUP: \d+\)$")


def parse_arp_scan(text: str) -> list[tuple[str, str, str]]:
    seen: dict[str, tuple[str, str, str]] = {}
    for ip, mac, vendor in _LINE.findall(text):
        mac = mac.lower()
        if mac not in seen:
            seen[mac] = (ip, mac, _DUP.sub("", vendor).strip())
    return list(seen.values())


async def arp_scan(interface: str) -> list[tuple[str, str, str]]:
    proc = await asyncio.create_subprocess_exec(
        "arp-scan", "--localnet", "--plain", "--retry=2", f"--interface={interface}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"arp-scan failed: {err.decode(errors='replace').strip()[:200]}")
    return parse_arp_scan(out.decode(errors="replace"))


def is_private_mac(mac: str) -> bool:
    """Locally-administered bit set: randomized 'private' Wi-Fi address."""
    return bool(int(mac[:2], 16) & 0x02)


class DeviceTracker:
    def __init__(self, store):
        self.store = store

    def observe(self, results, now_wall: datetime, cfg: dict) -> list[Event]:
        ts = now_wall.timestamp()
        iso = now_wall.isoformat(timespec="seconds")
        baseline = not self.store.baseline_done
        events: list[Event] = []
        for ip, mac, vendor in results:
            rec = self.store.get(mac)
            if rec is None:
                rec = {"mac": mac, "ip": ip, "vendor": vendor, "name": "", "known": False,
                       "announce_arrival": False, "private_mac": is_private_mac(mac),
                       "first_seen": iso, "last_seen": iso, "last_seen_ts": ts}
                if not baseline:
                    late = in_window(now_wall, cfg["late_start"], cfg["late_end"])
                    events.append(Event("device.new_late" if late else "device.new", "oneshot", source="devices",
                                        data={"key": mac, "mac": mac, "ip": ip, "vendor": vendor}))
            else:
                away_s = ts - rec.get("last_seen_ts", ts)
                if rec.get("announce_arrival") and away_s > cfg["arrival_absent_min"] * 60:
                    events.append(Event("device.known_arrived", "oneshot", source="devices",
                                        data={"key": mac, "mac": mac, "name": rec.get("name") or mac}))
                rec.update(ip=ip, last_seen=iso, last_seen_ts=ts)
                if vendor:
                    rec["vendor"] = vendor
            self.store.upsert(rec)
        if baseline and results:
            self.store.mark_baseline()
        if results:
            self.store.save()
        return events


class DeviceMonitor(PollingMonitor):
    name = section = "devices"

    def __init__(self, settings, clock, store, scan=arp_scan):
        super().__init__(settings, clock)
        self.scan = scan
        self.tracker = DeviceTracker(store)

    async def check(self) -> list[Event]:
        results = await self.scan(self.cfg["interface"])
        return self.tracker.observe(results, self.clock.now(), self.cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_monitor_devices.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add led_notify/monitors/devices.py tests/test_monitor_devices.py
git commit -m "feat: LAN device monitor with silent baseline and arrival alerts"
```

---

### Task 9: Disk and thermal monitors

**Files:**
- Create: `led_notify/monitors/disk.py`, `led_notify/monitors/thermal.py`
- Test: `tests/test_monitors_host.py`

**Interfaces:**
- Consumes: `PollingMonitor`, `Debouncer`, `Hysteresis` (Task 7).
- Produces:
  - `disk_usage_pct(path) -> float`, `parse_smart(text) -> bool | None`, `async smart_health(device) -> bool | None`
  - `DiskMonitor(settings, clock, usage=disk_usage_pct, smart=smart_health)`: `check()` = usage every cycle + SMART every `smart_interval_s`; events `disk.full` (data `path`, `pct`), `disk.smart_error`; `interval_s()` = `usage_interval_s`
  - `read_hwmon_temp(names, root="/sys/class/hwmon") -> float` (°C; RuntimeError if none)
  - `ThermalMonitor(settings, clock, read=read_hwmon_temp)` → `cpu.hot` (data `temp_c`)

- [ ] **Step 1: Write the failing tests**

`tests/test_monitors_host.py`:
```python
import pytest

from led_notify.monitors.disk import DiskMonitor, disk_usage_pct, parse_smart
from led_notify.monitors.thermal import ThermalMonitor, read_hwmon_temp


def names(events):
    return [(e.name, e.kind) for e in events]


# ---- disk -------------------------------------------------------------------------
def test_parse_smart():
    assert parse_smart("SMART overall-health self-assessment test result: PASSED") is True
    assert parse_smart("SMART overall-health self-assessment test result: FAILED!") is False
    assert parse_smart("SMART Health Status: OK") is True
    assert parse_smart("Smartctl open device: /dev/nvme0 failed: Permission denied") is None


def test_disk_usage_pct_is_a_percentage(tmp_path):
    assert 0.0 <= disk_usage_pct(str(tmp_path)) <= 100.0


def disk_monitor(settings, clock, usage_by_path, verdicts):
    async def smart(device):
        return verdicts.pop(0)
    return DiskMonitor(settings, clock, usage=lambda p: usage_by_path[p], smart=smart)


async def test_disk_full_hysteresis(settings, clock):
    usage = {"/host/DATA": 91.0}
    m = disk_monitor(settings, clock, usage, [True])
    evs = await m.check()
    assert names(evs) == [("disk.full", "start")] and evs[0].data == {"path": "/host/DATA", "pct": 91.0}
    usage["/host/DATA"] = 88.0
    assert m.check_usage() == []
    usage["/host/DATA"] = 84.0
    assert names(m.check_usage()) == [("disk.full", "clear")]


async def test_disk_uses_worst_path(settings, clock):
    settings.update({"disk": {"paths": ["/a", "/b"]}})
    m = disk_monitor(settings, clock, {"/a": 10.0, "/b": 95.0}, [True])
    assert (await m.check())[0].data["path"] == "/b"


async def test_smart_runs_hourly_and_reports_failures(settings, clock):
    m = disk_monitor(settings, clock, {"/host/DATA": 10.0}, [True, False, True])
    assert await m.check() == []
    clock.advance(300)
    assert await m.check() == []            # SMART not due yet
    clock.advance(3600)
    assert names(await m.check()) == [("disk.smart_error", "start")]
    clock.advance(3600)
    assert names(await m.check()) == [("disk.smart_error", "clear")]


async def test_smart_without_verdict_is_a_monitor_error(settings, clock):
    m = disk_monitor(settings, clock, {"/host/DATA": 10.0}, [None])
    with pytest.raises(RuntimeError, match="no health verdict"):
        await m.check()


async def test_disk_interval_is_usage_interval(settings, clock):
    assert disk_monitor(settings, clock, {}, []).interval_s() == 300


# ---- thermal ----------------------------------------------------------------------
def make_hwmon(root, idx, name, milli):
    d = root / f"hwmon{idx}"
    d.mkdir()
    (d / "name").write_text(name + "\n")
    (d / "temp1_input").write_text(f"{milli}\n")


def test_read_hwmon_temp_picks_cpu_sensor(tmp_path):
    make_hwmon(tmp_path, 0, "nvme", 44850)
    make_hwmon(tmp_path, 1, "k10temp", 43125)
    assert read_hwmon_temp(["k10temp", "coretemp"], root=str(tmp_path)) == 43.125


def test_read_hwmon_temp_without_sensor_raises(tmp_path):
    make_hwmon(tmp_path, 0, "nvme", 44850)
    with pytest.raises(RuntimeError, match="no CPU temperature sensor"):
        read_hwmon_temp(["k10temp"], root=str(tmp_path))


async def test_cpu_hot_after_two_minutes_and_clears_below_75(settings, clock):
    temps = [86.0]
    m = ThermalMonitor(settings, clock, read=lambda names: temps[0])
    assert await m.check() == []
    clock.advance(120)
    evs = await m.check()
    assert names(evs) == [("cpu.hot", "start")] and evs[0].data == {"temp_c": 86.0}
    temps[0] = 74.0
    assert names(await m.check()) == [("cpu.hot", "clear")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_monitors_host.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.monitors.disk'`

- [ ] **Step 3: Implement**

`led_notify/monitors/disk.py`:
```python
"""Disk usage and SMART health."""
from __future__ import annotations

import asyncio
import os
import re

from ..events import Event
from .base import Debouncer, Hysteresis, PollingMonitor

_ATA = re.compile(r"overall-health self-assessment test result:\s*(\w+)")
_SCSI = re.compile(r"SMART Health Status:\s*(\w+)")


def disk_usage_pct(path: str) -> float:
    """Used space as `df` reports it (reserved blocks count as unavailable)."""
    st = os.statvfs(path)
    used = st.f_blocks - st.f_bfree
    total = used + st.f_bavail
    return 100.0 * used / total if total else 0.0


def parse_smart(text: str) -> bool | None:
    m = _ATA.search(text)
    if m:
        return m.group(1).upper() == "PASSED"
    m = _SCSI.search(text)
    if m:
        return m.group(1).upper() == "OK"
    return None


async def smart_health(device: str) -> bool | None:
    proc = await asyncio.create_subprocess_exec(
        "smartctl", "-H", device, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return parse_smart(out.decode(errors="replace"))


class DiskMonitor(PollingMonitor):
    name = section = "disk"

    def __init__(self, settings, clock, usage=disk_usage_pct, smart=smart_health):
        super().__init__(settings, clock)
        self.usage = usage
        self.smart = smart
        self.full = Hysteresis()
        self.smart_bad = Debouncer()
        self._last_smart: float | None = None

    def interval_s(self) -> float:
        return self.cfg["usage_interval_s"]

    async def check(self) -> list[Event]:
        events = self.check_usage()
        now = self.clock.monotonic()
        if self._last_smart is None or now - self._last_smart >= self.cfg["smart_interval_s"]:
            self._last_smart = now
            events += await self.check_smart()
        return events

    def check_usage(self) -> list[Event]:
        cfg = self.cfg
        path, pct = max(((p, self.usage(p)) for p in cfg["paths"]), key=lambda kv: kv[1])
        change = self.full.feed(pct, self.clock.monotonic(), cfg["full_pct"], cfg["clear_pct"])
        return [Event("disk.full", change, source=self.name, data={"path": path, "pct": round(pct, 1)})] if change else []

    async def check_smart(self) -> list[Event]:
        devices = self.cfg["smart_devices"]
        if not devices:
            return []
        verdicts = [v for v in [await self.smart(d) for d in devices] if v is not None]
        if not verdicts:
            raise RuntimeError("smartctl returned no health verdict (check device access)")
        change = self.smart_bad.feed(all(verdicts), 1, 1)
        return [Event("disk.smart_error", change, source=self.name)] if change else []
```

`led_notify/monitors/thermal.py`:
```python
"""CPU temperature from hwmon."""
from __future__ import annotations

from pathlib import Path

from ..events import Event
from .base import Hysteresis, PollingMonitor


def read_hwmon_temp(names, root: str = "/sys/class/hwmon") -> float:
    for d in sorted(Path(root).iterdir()):
        try:
            if (d / "name").read_text().strip() in names:
                return int((d / "temp1_input").read_text().strip()) / 1000
        except (OSError, ValueError):
            continue
    raise RuntimeError(f"no CPU temperature sensor named {', '.join(names)} under {root}")


class ThermalMonitor(PollingMonitor):
    name = section = "thermal"

    def __init__(self, settings, clock, read=read_hwmon_temp):
        super().__init__(settings, clock)
        self.read = read
        self.hot = Hysteresis()

    async def check(self) -> list[Event]:
        cfg = self.cfg
        temp = self.read(cfg["sensor_names"])
        change = self.hot.feed(temp, self.clock.monotonic(), cfg["hot_c"], cfg["clear_c"], cfg["sustain_s"])
        return [Event("cpu.hot", change, source=self.name, data={"temp_c": round(temp, 1)})] if change else []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_monitors_host.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add led_notify/monitors/disk.py led_notify/monitors/thermal.py tests/test_monitors_host.py
git commit -m "feat: disk usage, SMART and CPU temperature monitors"
```

---

### Task 10: Docker and AdGuard monitors

**Files:**
- Create: `led_notify/monitors/docker.py`, `led_notify/monitors/adguard.py`
- Test: `tests/test_monitors_services.py`

**Interfaces:**
- Consumes: `PollingMonitor`, `MonitorDisabled` (Task 7), `Event` (Task 1).
- Produces:
  - `DockerDeathFilter().feed(evt: dict, now: float, window_s: float) -> Event | None`
  - `async docker_events(socket_path) -> AsyncIterator[dict]`
  - `DockerMonitor(settings, clock, stream=docker_events)`: `name = "docker"`, `heartbeat` attribute (called once when `run` starts), `async run(emit)` (raises `RuntimeError("docker event stream ended")` when the stream ends); events `container.died` (data `key`, `container`, `exit_code`)
  - `parse_querylog_times(payload) -> list[datetime]`, `async adguard_blocked_times(url, user, password, limit=500)`
  - `SpikeDetector().feed(count, min_blocked, factor) -> bool`
  - `AdguardMonitor(settings, clock, fetch=adguard_blocked_times)`: raises `MonitorDisabled` without credentials; event `adguard.block_spike` (data `blocked_last_min`)

- [ ] **Step 1: Write the failing tests**

`tests/test_monitors_services.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest

from led_notify.monitors.adguard import AdguardMonitor, SpikeDetector, parse_querylog_times
from led_notify.monitors.base import MonitorDisabled
from led_notify.monitors.docker import DockerDeathFilter, DockerMonitor


def dev(action, name="web", code=None, type_="container"):
    attrs = {"name": name}
    if code is not None:
        attrs["exitCode"] = str(code)
    return {"Type": type_, "Action": action, "Actor": {"ID": "abc", "Attributes": attrs}}


# ---- docker -----------------------------------------------------------------------
def test_crash_is_reported():
    ev = DockerDeathFilter().feed(dev("die", code=1), 0, 15)
    assert ev.name == "container.died" and ev.data == {"key": "web", "container": "web", "exit_code": 1}


def test_clean_exit_and_other_events_are_ignored():
    f = DockerDeathFilter()
    assert f.feed(dev("die", code=0), 0, 15) is None
    assert f.feed(dev("start"), 0, 15) is None
    assert f.feed(dev("exec_start: sh"), 0, 15) is None
    assert f.feed(dev("die", code=1, type_="network"), 0, 15) is None


def test_intentional_stop_is_ignored_but_old_stop_is_not():
    f = DockerDeathFilter()
    f.feed(dev("kill"), 100, 15)
    assert f.feed(dev("die", code=137), 105, 15) is None
    f.feed(dev("stop"), 200, 15)
    assert f.feed(dev("die", code=137), 230, 15) is not None


async def test_docker_monitor_emits_crashes_then_errors_when_stream_ends(settings, clock):
    async def stream(socket):
        assert socket == "/var/run/docker.sock"
        yield dev("start")
        yield dev("die", code=1)

    got = []

    async def emit(e):
        got.append(e)

    with pytest.raises(RuntimeError, match="ended"):
        await DockerMonitor(settings, clock, stream=stream).run(emit)
    assert [(e.name, e.data["container"]) for e in got] == [("container.died", "web")]


# ---- adguard ----------------------------------------------------------------------
def test_parse_querylog_times_handles_nanoseconds_and_z():
    times = parse_querylog_times({"data": [
        {"time": "2026-10-11T22:41:03.123456789-07:00"},
        {"time": "2026-10-12T05:41:00Z"},
    ]})
    assert times[0] == datetime(2026, 10, 11, 22, 41, 3, 123456, tzinfo=timezone(timedelta(hours=-7)))
    assert times[1] == datetime(2026, 10, 12, 5, 41, 0, tzinfo=timezone.utc)
    assert parse_querylog_times({}) == []


def test_spike_detector():
    s = SpikeDetector()
    assert s.feed(10, 50, 3) is False
    assert s.feed(200, 50, 3) is True       # 200 > 50 and > 3 x 10
    assert s.feed(60, 50, 3) is False       # 60 < 3 x avg(10, 200)


async def test_adguard_disabled_without_credentials(settings, clock):
    with pytest.raises(MonitorDisabled):
        await AdguardMonitor(settings, clock, fetch=None).check()


async def test_adguard_spike(settings, clock):
    settings.update({"adguard": {"username": "admin", "password": "pw"}})
    now = clock.now()
    recent = [now - timedelta(seconds=s) for s in range(60)] + [now - timedelta(minutes=5)]

    async def fetch(url, user, password):
        assert (url, user, password) == ("http://127.0.0.1:3001", "admin", "pw")
        return recent

    m = AdguardMonitor(settings, clock, fetch=fetch)
    evs = await m.check()
    assert [e.name for e in evs] == ["adguard.block_spike"] and evs[0].data == {"blocked_last_min": 60}
    assert await m.check() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_monitors_services.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.monitors.docker'`

- [ ] **Step 3: Implement docker**

`led_notify/monitors/docker.py`:
```python
"""Container crash detection from the Docker event stream."""
from __future__ import annotations

import json

import httpx

from ..events import Event

INTENTIONAL = ("stop", "kill", "restart")


class DockerDeathFilter:
    def __init__(self):
        self._intentional: dict[str, float] = {}

    def feed(self, evt: dict, now: float, window_s: float) -> Event | None:
        if evt.get("Type") != "container":
            return None
        action = evt.get("Action", "")
        attrs = evt.get("Actor", {}).get("Attributes", {})
        name = attrs.get("name", "?")
        if action in INTENTIONAL:
            self._intentional[name] = now
            return None
        if action != "die":
            return None
        stopped_at = self._intentional.pop(name, None)
        if stopped_at is not None and now - stopped_at <= window_s:
            return None
        code = attrs.get("exitCode", "0")
        if code == "0":
            return None
        return Event("container.died", "oneshot", source="docker",
                     data={"key": name, "container": name, "exit_code": int(code) if code.isdigit() else code})


async def docker_events(socket_path: str):
    transport = httpx.AsyncHTTPTransport(uds=socket_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=None) as client:
        params = {"filters": json.dumps({"type": ["container"]})}
        async with client.stream("GET", "/events", params=params) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.strip():
                    yield json.loads(line)


class DockerMonitor:
    name = "docker"

    def __init__(self, settings, clock, stream=docker_events):
        self.settings = settings
        self.clock = clock
        self.stream = stream
        self.filter = DockerDeathFilter()
        self.heartbeat = lambda: None

    async def run(self, emit) -> None:
        cfg = self.settings.data["docker"]
        self.heartbeat()  # an event stream has no polling cycle; a connect failure flips this to error at once
        async for evt in self.stream(cfg["socket"]):
            ev = self.filter.feed(evt, self.clock.monotonic(), cfg["intentional_window_s"])
            if ev is not None:
                await emit(ev)
        raise RuntimeError("docker event stream ended")
```

- [ ] **Step 4: Implement adguard**

`led_notify/monitors/adguard.py`:
```python
"""Spikes in blocked DNS queries from AdGuard Home's query log."""
from __future__ import annotations

import re
from collections import deque
from datetime import datetime

import httpx

from ..events import Event
from .base import MonitorDisabled, PollingMonitor

_EXTRA_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_querylog_times(payload: dict) -> list[datetime]:
    times = []
    for entry in payload.get("data") or []:
        stamp = _EXTRA_FRACTION.sub(r"\1", entry["time"]).replace("Z", "+00:00")
        times.append(datetime.fromisoformat(stamp))
    return times


async def adguard_blocked_times(url: str, user: str, password: str, limit: int = 500) -> list[datetime]:
    async with httpx.AsyncClient(timeout=10, auth=(user, password)) as client:
        resp = await client.get(f"{url.rstrip('/')}/control/querylog",
                                params={"response_status": "blocked", "limit": limit})
        resp.raise_for_status()
        return parse_querylog_times(resp.json())


class SpikeDetector:
    def __init__(self):
        self._history: deque[int] = deque(maxlen=60)

    def feed(self, count: int, min_blocked: int, factor: float) -> bool:
        avg = sum(self._history) / len(self._history) if self._history else 0.0
        self._history.append(count)
        return count > min_blocked and count > factor * avg


class AdguardMonitor(PollingMonitor):
    name = section = "adguard"

    def __init__(self, settings, clock, fetch=adguard_blocked_times):
        super().__init__(settings, clock)
        self.fetch = fetch
        self.detector = SpikeDetector()

    async def check(self) -> list[Event]:
        cfg = self.cfg
        if not (cfg["username"] and cfg["password"]):
            raise MonitorDisabled("set the AdGuard username and password under Rules → Settings")
        times = await self.fetch(cfg["url"], cfg["username"], cfg["password"])
        now = self.clock.now()
        count = sum(1 for t in times if 0 <= (now - t).total_seconds() < 60)
        if self.detector.feed(count, cfg["min_blocked"], cfg["factor"]):
            return [Event("adguard.block_spike", "oneshot", source=self.name, data={"blocked_last_min": count})]
        return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_monitors_services.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add led_notify/monitors/docker.py led_notify/monitors/adguard.py tests/test_monitors_services.py
git commit -m "feat: docker crash and adguard block-spike monitors"
```

---

### Task 11: Monitor supervisor

**Files:**
- Create: `led_notify/monitors/supervisor.py`
- Test: `tests/test_supervisor.py`

**Interfaces:**
- Consumes: `MonitorDisabled` (Task 7); monitors expose `name` and `async run(emit)`.
- Produces: `Supervisor(monitors, emit, clock, hub=None)`: `.health: dict[name, {"status": "starting"|"ok"|"error"|"disabled", "detail", "since"}]`, `async run()`, `async run_one(m)`. Assigns `m.heartbeat` on each monitor. Backoff 5 s doubling to 300 s, reset after a run lasting > 60 s; disabled monitors retried every 60 s. Publishes `("state", None)` on health changes.

- [ ] **Step 1: Write the failing tests**

`tests/test_supervisor.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_supervisor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.monitors.supervisor'`

- [ ] **Step 3: Implement**

`led_notify/monitors/supervisor.py`:
```python
"""Runs monitors, restarts the ones that crash, and tracks their health."""
from __future__ import annotations

import asyncio
import logging

from .base import MonitorDisabled

log = logging.getLogger(__name__)

BACKOFF_START_S = 5.0
BACKOFF_MAX_S = 300.0
DISABLED_RETRY_S = 60
HEALTHY_RUN_S = 60.0


class Supervisor:
    def __init__(self, monitors, emit, clock, hub=None):
        self.monitors = list(monitors)
        self.emit = emit
        self.clock = clock
        self.hub = hub
        self.health = {m.name: {"status": "starting", "detail": "", "since": ""} for m in self.monitors}
        for m in self.monitors:
            m.heartbeat = lambda name=m.name: self._set(name, "ok")

    def _set(self, name: str, status: str, detail: str = "") -> None:
        h = self.health[name]
        if (h["status"], h["detail"]) == (status, detail):
            return
        self.health[name] = {"status": status, "detail": detail,
                             "since": self.clock.now().isoformat(timespec="seconds")}
        if self.hub:
            self.hub.publish("state", None)

    async def run(self) -> None:
        await asyncio.gather(*(self.run_one(m) for m in self.monitors))

    async def run_one(self, m) -> None:
        backoff = BACKOFF_START_S
        while True:
            started = self.clock.monotonic()
            try:
                await m.run(self.emit)
                raise RuntimeError("monitor stopped unexpectedly")
            except asyncio.CancelledError:
                raise
            except MonitorDisabled as e:
                self._set(m.name, "disabled", str(e))
                await self.clock.sleep(DISABLED_RETRY_S)
            except Exception as e:
                log.warning("monitor %s failed: %s", m.name, e)
                if self.clock.monotonic() - started > HEALTHY_RUN_S:
                    backoff = BACKOFF_START_S
                self._set(m.name, "error", f"{type(e).__name__}: {e}")
                await self.clock.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_supervisor.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add led_notify/monitors/supervisor.py tests/test_supervisor.py
git commit -m "feat: monitor supervisor with restart backoff and health"
```

---

### Task 12: StripLink — persistent BLE connection

**Files:**
- Create: `led_notify/driver/link.py`
- Test: `tests/test_link.py`

**Interfaces:**
- Consumes: `banlanx_ctl.Strip` (Task 2) via the default factory; `FakeClock` (Task 1).
- Produces: `StripLink(address, clock, strip_factory=None, keepalive_s=30.0)` implementing `StripPort` (Task 3) plus `async run()` and `status() -> {"connected", "address", "attempt", "last_error"}`. Commands never raise; while offline they only update the desired state, which is replayed (power, then colour or effect → speed → brightness) on every (re)connect. Reconnect backoff 2 s doubling to 60 s. A factory returns an object with `async connect()`, `async disconnect()`, `client.is_connected`, `async on()/off()/color(r,g,b,level)/effect(fx)/speed(v)/brightness(level)/status(timeout=)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_link.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_link.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.driver.link'`

- [ ] **Step 3: Implement**

`led_notify/driver/link.py`:
```python
"""Owns the single BLE connection to the strip: connect, keep alive, reconnect, replay."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 60.0


def _default_factory(address: str):
    from .banlanx_ctl import Strip

    return Strip(address, family="6xx")


class StripLink:
    def __init__(self, address: str, clock, strip_factory=None, keepalive_s: float = 30.0):
        self.address = address
        self.clock = clock
        self.keepalive_s = keepalive_s
        self._factory = strip_factory or _default_factory
        self._strip = None
        self._lock = asyncio.Lock()
        self._desired: dict = {}
        self._broken = False
        self.connected = False
        self.attempt = 0
        self.last_error = ""

    def status(self) -> dict:
        return {"connected": self.connected, "address": self.address, "attempt": self.attempt,
                "last_error": self.last_error}

    # ---- StripPort ---------------------------------------------------------------
    async def power(self, on: bool) -> None:
        self._desired["power"] = on
        await self._do(lambda s: s.on() if on else s.off())

    async def color(self, r: int, g: int, b: int, level: int) -> None:
        self._desired["mode"] = ("color", (r, g, b, level))
        await self._do(lambda s: s.color(r, g, b, level))

    async def effect(self, fx: int) -> None:
        self._desired["mode"] = ("effect", fx)
        await self._do(lambda s: s.effect(fx))

    async def speed(self, v: int) -> None:
        self._desired["speed"] = v
        await self._do(lambda s: s.speed(v))

    async def brightness(self, level: int) -> None:
        self._desired["brightness"] = level
        await self._do(lambda s: s.brightness(level))

    # ---- connection lifecycle -------------------------------------------------------
    async def run(self) -> None:
        backoff = BACKOFF_START_S
        while True:
            try:
                self._strip = self._factory(self.address)
                self._broken = False
                await self._strip.connect()
                self.connected = True
                self.attempt = 0
                self.last_error = ""
                backoff = BACKOFF_START_S
                log.info("strip %s connected", self.address)
                await self._replay()
                await self._watch()
            except asyncio.CancelledError:
                self.connected = False
                await self._safe_disconnect()
                raise
            except (Exception, SystemExit) as e:  # banlanx_ctl raises SystemExit when the strip isn't found
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("strip link: %s", self.last_error)
            self.connected = False
            await self._safe_disconnect()
            self.attempt += 1
            await self.clock.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def _watch(self) -> None:
        last = self.clock.monotonic()
        while not self._broken and self._strip.client is not None and self._strip.client.is_connected:
            await self.clock.sleep(1.0)
            if self.clock.monotonic() - last >= self.keepalive_s:
                last = self.clock.monotonic()
                async with self._lock:
                    await self._strip.status(timeout=3.0)
        raise ConnectionError("strip disconnected")

    async def _replay(self) -> None:
        d = dict(self._desired)
        if "power" in d:
            on = d["power"]
            await self._do(lambda s: s.on() if on else s.off())
        mode = d.get("mode")
        if mode and mode[0] == "color":
            await self._do(lambda s: s.color(*mode[1]))
        elif mode:
            await self._do(lambda s: s.effect(mode[1]))
            if "speed" in d:
                await self._do(lambda s: s.speed(d["speed"]))
            if "brightness" in d:
                await self._do(lambda s: s.brightness(d["brightness"]))

    async def _do(self, fn) -> None:
        if not self.connected or self._strip is None:
            return
        try:
            async with self._lock:
                await fn(self._strip)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            self._broken = True
            self.connected = False

    async def _safe_disconnect(self) -> None:
        if self._strip is None:
            return
        try:
            await self._strip.disconnect()
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_link.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add led_notify/driver/link.py tests/test_link.py
git commit -m "feat: persistent BLE strip link with reconnect and state replay"
```

---

### Task 13: Web API

**Files:**
- Create: `led_notify/web/__init__.py` (empty), `led_notify/web/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Arbiter` (Task 6), `SettingsStore`/`SettingsError` (Task 4), `DeviceStore` (Task 4), `Hub` (Task 1), `Event` (Task 1), `resolve`/`catalog`/`PatternError`/`LOOK_KEYS` (Task 2), `FakeStrip` (Task 3), `Supervisor.health` (Task 11), any link with `status()`.
- Produces: `Services(arbiter, settings, devices, supervisor, link, hub, clock)` dataclass; `create_app(svc, lifespan=None) -> FastAPI` with the routes in spec §6.2. `GET /api/state` returns `arbiter.snapshot()` + `strip`, `monitors`, `warnings`.

- [ ] **Step 1: Write the failing tests**

`tests/test_api.py`:
```python
import pytest
from fastapi.testclient import TestClient

from led_notify.driver.fake import FakeStrip
from led_notify.engine.arbiter import Arbiter
from led_notify.engine.player import Player
from led_notify.hub import Hub
from led_notify.store.devices import DeviceStore
from led_notify.store.settings import SettingsStore
from led_notify.web.api import Services, create_app
from tests.fakes import FakeClock


@pytest.fixture
def svc(tmp_path):
    clock = FakeClock()
    hub = Hub()
    settings = SettingsStore(tmp_path / "settings.json", hub)
    devices = DeviceStore(tmp_path / "devices.json", hub)
    strip = FakeStrip()
    arbiter = Arbiter(Player(strip, clock), settings, clock, hub)
    return Services(arbiter=arbiter, settings=settings, devices=devices, supervisor=None, link=strip,
                    hub=hub, clock=clock)


@pytest.fixture
def client(svc):
    return TestClient(create_app(svc))


# ---- events webhook ----------------------------------------------------------------
def test_post_event(client, svc):
    r = client.post("/api/events", json={"name": "backup.done", "pattern": "green_flash"})
    assert r.status_code == 200 and r.json() == {"accepted": True, "outcome": "queued"}
    assert svc.arbiter.log[-1]["name"] == "backup.done" and svc.arbiter.log[-1]["source"] == "webhook"


def test_post_state_start_and_clear(client):
    assert client.post("/api/events", json={"name": "ci.red", "kind": "start", "priority": "high"}).json()["outcome"] == "active"
    assert client.post("/api/events", json={"name": "ci.red", "kind": "clear"}).json()["outcome"] == "cleared"


@pytest.mark.parametrize("body", [
    {},
    {"name": "has space"},
    {"name": "x", "kind": "bogus"},
    {"name": "x", "priority": "urgent"},
    {"name": "x", "pattern": "nope"},
    {"name": "x", "effect": 0},
    {"name": "x", "effect": 999},
    {"name": "x", "color": [300, 0, 0]},
    {"name": "x", "color": [1, 2]},
    {"name": "x", "pattern": "rainbow", "effect": 3},
    {"name": "x", "duration": "long"},
])
def test_bad_webhook_payloads_are_422(client, body):
    r = client.post("/api/events", json=body)
    assert r.status_code == 422, r.text


def test_webhook_token(client, svc):
    svc.settings.update({"webhook_token": "s3cret"})
    assert client.post("/api/events", json={"name": "a"}).status_code == 401
    assert client.post("/api/events", json={"name": "a"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/api/events", json={"name": "a"}, headers={"Authorization": "Bearer s3cret"}).status_code == 200


# ---- test + preview ------------------------------------------------------------------
def test_test_endpoint(client, svc):
    r = client.post("/api/test/dns.down")
    assert r.json()["outcome"] == "queued"
    assert svc.arbiter.log[-1]["source"] == "test"
    assert client.post("/api/test/nope").status_code == 404


def test_preview(client):
    assert client.post("/api/preview", json={"pattern": "rainbow"}).json()["outcome"] == "queued"
    assert client.post("/api/preview", json={"effect": 999}).status_code == 422
    assert client.post("/api/preview", json={}).status_code == 422


# ---- state + manual ------------------------------------------------------------------
def test_state(client):
    s = client.get("/api/state").json()
    assert {"current", "active", "queue", "manual", "quiet", "strip", "monitors", "warnings"} <= set(s)
    assert s["strip"]["fake"] is True and s["monitors"] == {}


def test_manual_control(client):
    s = client.post("/api/strip", json={"color": [255, 0, 0], "brightness": 80}).json()
    assert s["manual"]["desc"] == "color #ff0000"
    assert client.post("/api/strip", json={"effect": 999}).status_code == 422
    assert client.post("/api/strip", json={"brightness": 300}).status_code == 422
    assert client.post("/api/strip", json={"power": False}).json()["manual"]["desc"] == "off"
    assert client.delete("/api/strip/manual").json()["manual"] is None


# ---- rules + settings ----------------------------------------------------------------
def test_rules_get_and_put(client):
    rules = client.get("/api/rules").json()
    assert rules["dns.down"]["priority"] == "high"
    r = client.put("/api/rules", json={"dns.down": {"priority": "critical"}})
    assert r.status_code == 200 and r.json()["dns.down"]["priority"] == "critical"


def test_rules_put_invalid_is_422_and_unchanged(client):
    assert client.put("/api/rules", json={"dns.down": {"priority": "urgent"}}).status_code == 422
    assert client.get("/api/rules").json()["dns.down"]["priority"] == "high"


def test_settings_masks_secrets_and_validates(client):
    s = client.get("/api/settings").json()
    assert s["adguard"]["password"] == {"set": False} and "rules" not in s
    r = client.put("/api/settings", json={"max_brightness": 128, "adguard": {"password": "pw"}})
    assert r.status_code == 200
    assert r.json()["max_brightness"] == 128 and r.json()["adguard"]["password"] == {"set": True}
    assert client.put("/api/settings", json={"max_brightness": 999}).status_code == 422
    assert client.put("/api/settings", json={"rules": {}}).status_code == 422


# ---- devices ---------------------------------------------------------------------------
def seed(svc, mac="aa:bb:cc:dd:ee:ff", age_s=0):
    ts = svc.clock.now().timestamp() - age_s
    svc.devices.upsert({"mac": mac, "ip": "192.168.0.9", "vendor": "Acme", "name": "", "known": False,
                        "announce_arrival": False, "private_mac": False, "first_seen": "x", "last_seen": "x",
                        "last_seen_ts": ts})
    svc.devices.save()


def test_devices_list_patch_delete(client, svc):
    seed(svc)
    seed(svc, "11:22:33:44:55:66", age_s=3600)
    devs = {d["mac"]: d for d in client.get("/api/devices").json()}
    assert devs["aa:bb:cc:dd:ee:ff"]["online"] is True and devs["11:22:33:44:55:66"]["online"] is False
    r = client.patch("/api/devices/AA:BB:CC:DD:EE:FF", json={"name": "TV", "known": True})
    assert r.status_code == 200 and r.json()["name"] == "TV"
    assert client.patch("/api/devices/aa:bb:cc:dd:ee:ff", json={"ip": "1.2.3.4"}).status_code == 422
    assert client.patch("/api/devices/00:00:00:00:00:00", json={"name": "x"}).status_code == 404
    assert client.delete("/api/devices/aa:bb:cc:dd:ee:ff").status_code == 200
    assert client.delete("/api/devices/aa:bb:cc:dd:ee:ff").status_code == 404


# ---- catalogue + log --------------------------------------------------------------------
def test_patterns_and_log(client):
    p = client.get("/api/patterns").json()
    assert "red_breath" in p["named"] and len(p["effects"]) == 146
    client.post("/api/events", json={"name": "hello"})
    assert client.get("/api/log").json()[-1]["name"] == "hello"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.web'`

- [ ] **Step 3: Implement**

`led_notify/web/__init__.py`: empty file.

`led_notify/web/api.py`:
```python
"""HTTP API + live stream for the web UI and webhooks."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..events import Event
from ..patterns import LOOK_KEYS, PatternError, catalog, resolve
from ..store.settings import SettingsError


@dataclass
class Services:
    arbiter: Any
    settings: Any
    devices: Any
    supervisor: Any
    link: Any
    hub: Any
    clock: Any


class LookIn(BaseModel):
    pattern: str | None = None
    effect: int | None = None
    color: list[int] | None = None

    def spec(self) -> dict:
        spec = {k: getattr(self, k) for k in LOOK_KEYS if getattr(self, k) is not None}
        if spec:
            try:
                resolve(spec)
            except PatternError as e:
                raise HTTPException(422, str(e))
        return spec


class EventIn(LookIn):
    name: str
    kind: Literal["oneshot", "start", "clear"] = "oneshot"
    priority: Literal["low", "normal", "high", "critical"] | None = None
    duration: float | None = None
    data: dict = Field(default_factory=dict)


class PreviewIn(LookIn):
    duration: float = 8.0


class StripIn(LookIn):
    power: bool | None = None
    brightness: int | None = Field(default=None, ge=0, le=255)
    speed: int | None = Field(default=None, ge=1, le=10)


def create_app(svc: Services, lifespan=None) -> FastAPI:
    app = FastAPI(title="led-notify", lifespan=lifespan)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        token = svc.settings.data["webhook_token"]
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "missing or invalid bearer token")

    def state() -> dict:
        warnings = [w for w in (svc.settings.warning, svc.devices.warning) if w]
        return {**svc.arbiter.snapshot(), "strip": svc.link.status(),
                "monitors": svc.supervisor.health if svc.supervisor else {}, "warnings": warnings}

    def make_event(**kwargs) -> Event:
        try:
            return Event(**kwargs)
        except ValueError as e:
            raise HTTPException(422, str(e))

    async def submit(ev: Event) -> dict:
        return {"accepted": True, "outcome": await svc.arbiter.submit(ev)}

    @app.post("/api/events", dependencies=[Depends(require_token)])
    async def post_event(body: EventIn):
        spec = body.spec()
        return await submit(make_event(name=body.name, kind=body.kind, source="webhook", priority=body.priority,
                                       look=spec or None, duration_s=body.duration, data=body.data))

    @app.post("/api/test/{name}")
    async def test_event(name: str, force: bool = False):
        if svc.settings.rule(name) is None:
            raise HTTPException(404, f"no rule named {name!r}")
        return await submit(make_event(name=name, kind="oneshot", source="test", duration_s=8, force=force))

    @app.post("/api/preview")
    async def preview(body: PreviewIn):
        spec = body.spec()
        if not spec:
            raise HTTPException(422, "give one of pattern, effect, color")
        return await submit(make_event(name="preview", kind="oneshot", source="test", look=spec,
                                       duration_s=body.duration, force=True))

    @app.get("/api/state")
    def get_state():
        return state()

    @app.post("/api/strip")
    def manual(body: StripIn):
        spec = body.spec()
        if spec.get("effect") is not None and body.speed is not None:
            spec["speed"] = body.speed
        try:
            svc.arbiter.set_manual(spec or None, brightness=body.brightness, power=body.power is not False)
        except PatternError as e:
            raise HTTPException(422, str(e))
        return state()

    @app.delete("/api/strip/manual")
    def resume():
        svc.arbiter.clear_manual()
        return state()

    @app.get("/api/rules")
    def get_rules():
        return svc.settings.data["rules"]

    @app.put("/api/rules")
    def put_rules(body: dict = Body(...)):
        try:
            svc.settings.update({"rules": body})
        except SettingsError as e:
            raise HTTPException(422, str(e))
        return svc.settings.data["rules"]

    @app.get("/api/settings")
    def get_settings():
        return {k: v for k, v in svc.settings.public().items() if k != "rules"}

    @app.put("/api/settings")
    def put_settings(body: dict = Body(...)):
        if "rules" in body:
            raise HTTPException(422, "edit rules with PUT /api/rules")
        try:
            svc.settings.update(body)
        except SettingsError as e:
            raise HTTPException(422, str(e))
        return get_settings()

    @app.get("/api/devices")
    def get_devices():
        now = svc.clock.now().timestamp()
        window = 3 * svc.settings.data["devices"]["interval_s"]
        return [{**d, "online": now - d.get("last_seen_ts", 0) <= window} for d in svc.devices.all()]

    @app.patch("/api/devices/{mac}")
    def patch_device(mac: str, body: dict = Body(...)):
        try:
            return svc.devices.patch(mac, body)
        except KeyError:
            raise HTTPException(404, "unknown device")
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/devices/{mac}")
    def delete_device(mac: str):
        if not svc.devices.delete(mac):
            raise HTTPException(404, "unknown device")
        return {"deleted": mac.lower()}

    @app.get("/api/patterns")
    def patterns():
        return catalog()

    @app.get("/api/log")
    def get_log():
        return list(svc.arbiter.log)

    @app.get("/api/stream")
    async def stream(request: Request):
        q = svc.hub.subscribe()

        async def events():
            try:
                yield f"event: state\ndata: {json.dumps(state(), default=str)}\n\n"
                while not await request.is_disconnected():
                    try:
                        type_, data = await asyncio.wait_for(q.get(), 15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if type_ == "settings":
                        continue
                    payload = state() if type_ == "state" else data
                    yield f"event: {type_}\ndata: {json.dumps(payload, default=str)}\n\n"
            finally:
                svc.hub.unsubscribe(q)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add led_notify/web tests/test_api.py
git commit -m "feat: REST API, webhook and SSE stream"
```

---

### Task 14: Web UI

**Files:**
- Create: `led_notify/web/static/index.html`, `led_notify/web/static/app.js`, `led_notify/web/static/style.css`
- Modify: `led_notify/web/api.py` (mount static files at the end of `create_app`)
- Test: `tests/test_api.py` (append static test)

**Interfaces:**
- Consumes: every route from Task 13 (`/api/state`, `/api/stream` events `state`/`log`/`devices`, `/api/test/{name}?force=`, `/api/preview`, `/api/strip`, `/api/strip/manual`, `/api/devices`, `/api/rules`, `/api/settings`, `/api/patterns`, `/api/log`).
- Produces: the page at `/`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:
```python
def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>LED Notify</title>" in r.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api.py::test_ui_is_served -q`
Expected: FAIL — `assert 404 == 200`

- [ ] **Step 3: Mount static files**

In `led_notify/web/api.py`, add imports at the top:
```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
```
and replace the final `return app` of `create_app` with:
```python
    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
    return app
```

- [ ] **Step 4: Write the page**

`led_notify/web/static/index.html`:
```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LED Notify</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <div class="row">
    <h1>LED Notify</h1>
    <span id="strip" class="pill">…</span>
    <span id="quiet" class="pill quiet" hidden>quiet hours</span>
  </div>
  <div class="row"><span class="label">Now</span><span id="now">—</span></div>
  <div class="row"><span class="label">Active</span><span id="active"></span></div>
  <div class="row"><span class="label">Monitors</span><span id="monitors"></span></div>
  <div class="row"><span class="label">Mode</span><span id="manual-status">Automatic</span></div>
  <div id="warnings"></div>
</header>
<nav id="tabs">
  <button data-tab="test" class="active">Test</button>
  <button data-tab="manual">Manual</button>
  <button data-tab="devices">Devices</button>
  <button data-tab="rules">Rules</button>
  <button data-tab="log">Log</button>
</nav>
<main>
  <section id="tab-test">
    <h2>Events</h2>
    <label class="inline"><input type="checkbox" id="force"> Force (ignore priority &amp; quiet hours)</label>
    <div id="event-buttons" class="grid"></div>
    <h2>Pattern gallery</h2>
    <input id="gallery-search" type="search" placeholder="Search patterns and effects…">
    <div id="gallery" class="grid"></div>
  </section>
  <section id="tab-manual" hidden>
    <div class="card form">
      <label>Color <input type="color" id="m-color" value="#00b3a4"></label>
      <label>Brightness <input type="range" id="m-bright" min="1" max="255" value="128"><output id="m-bright-out">128</output></label>
      <button id="m-set-color">Set color</button>
    </div>
    <div class="card form">
      <label>Effect <select id="m-effect"></select></label>
      <label>Speed <input type="range" id="m-speed" min="1" max="10" value="5"><output id="m-speed-out">5</output></label>
      <button id="m-set-effect">Set effect</button>
    </div>
    <div class="row gap">
      <button id="m-off" class="secondary">Turn off</button>
      <button id="m-resume">Resume auto</button>
    </div>
  </section>
  <section id="tab-devices" hidden>
    <div class="row gap">
      <select id="dev-filter">
        <option value="all">All devices</option>
        <option value="unknown">Unknown only</option>
        <option value="online">Online only</option>
      </select>
      <span id="dev-count" class="muted"></span>
    </div>
    <div class="table-wrap">
      <table id="dev-table">
        <thead><tr><th>Name</th><th>MAC / vendor</th><th>IP</th><th>Seen</th><th>Known</th><th>Announce</th><th></th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>
  <section id="tab-rules" hidden>
    <h2>Rules</h2>
    <div class="table-wrap">
      <table id="rules-table">
        <thead><tr><th>Event</th><th>On</th><th>Priority</th><th>Pattern</th><th>Duration (s)</th><th>Quiet hours</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <button id="save-rules">Save rules</button>
    <h2>Settings</h2>
    <form id="settings-form" class="settings" onsubmit="return false"></form>
    <button id="save-settings">Save settings</button>
  </section>
  <section id="tab-log" hidden>
    <div class="table-wrap">
      <table id="log-table">
        <thead><tr><th>Time</th><th>Event</th><th>Source</th><th>Outcome</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>
</main>
<div id="toast" hidden></div>
<script src="app.js"></script>
</body>
</html>
```

`led_notify/web/static/app.js`:
```js
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hexToRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const rgbToHex = (rgb) => "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("");
const getPath = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

function setPath(obj, path, value) {
  const keys = path.split(".");
  let o = obj;
  keys.slice(0, -1).forEach((k) => { o = o[k] ??= {}; });
  o[keys.at(-1)] = value;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d
      : Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ")
      : `HTTP ${res.status}`);
  }
  return data;
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = bad ? "bad" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

async function act(fn, okMsg) {
  try {
    await fn();
    if (okMsg) toast(okMsg);
  } catch (e) {
    toast(e.message, true);
  }
}

const state = { catalog: { named: [], effects: [] }, rules: {}, settings: {}, devices: [], log: [] };

/* ---------- header ---------- */
function renderState(s) {
  const strip = s.strip || {};
  const el = $("#strip");
  el.textContent = strip.connected ? (strip.fake ? "Fake strip" : "Strip connected")
    : `Strip offline${strip.attempt ? ` · retry ${strip.attempt}` : ""}`;
  el.title = strip.last_error || "";
  el.className = "pill " + (strip.connected ? "ok" : "bad");
  const c = s.current;
  $("#now").textContent = c ? `${c.pattern} ← ${c.reason}` : "—";
  $("#active").innerHTML = (s.active || []).map((a) => `<span class="pill p-${esc(a.priority)}">${esc(a.name)}</span>`).join(" ")
    || '<span class="muted">none</span>';
  $("#monitors").innerHTML = Object.entries(s.monitors || {})
    .map(([n, h]) => `<span class="mon ${esc(h.status)}" title="${esc(h.status)} ${esc(h.detail)}">${esc(n)}</span>`).join(" ")
    || '<span class="muted">none</span>';
  $("#manual-status").textContent = s.manual ? `Manual: ${s.manual.desc} · ${Math.ceil(s.manual.expires_in_s / 60)} min left` : "Automatic";
  $("#quiet").hidden = !s.quiet;
  $("#warnings").innerHTML = (s.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("");
}
const loadState = () => api("GET", "/api/state").then(renderState).catch(() => {});

/* ---------- tabs ---------- */
function showTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$("main > section").forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  if (name === "devices") loadDevices();
  if (name === "rules") loadRulesAndSettings();
  if (name === "log") loadLog();
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ---------- test tab ---------- */
function renderEventButtons() {
  $("#event-buttons").innerHTML = Object.entries(state.rules).map(([name, r]) =>
    `<button class="event p-${esc(r.priority)}" data-name="${esc(name)}"><b>${esc(name)}</b><small>${esc(r.pattern)} · ${esc(r.priority)}</small></button>`).join("");
}
$("#event-buttons").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-name]");
  if (!b) return;
  const force = $("#force").checked;
  act(async () => {
    const r = await api("POST", `/api/test/${encodeURIComponent(b.dataset.name)}?force=${force}`);
    toast(`${b.dataset.name}: ${r.outcome}`);
  });
});

function renderGallery() {
  const q = $("#gallery-search").value.trim().toLowerCase();
  const items = [
    ...state.catalog.named.map((n) => ({ label: n, sub: "pattern", body: { pattern: n } })),
    ...state.catalog.effects.map((e) => ({ label: e.name, sub: `effect ${e.fx}`, body: { effect: e.fx } })),
  ].filter((i) => !q || i.label.toLowerCase().includes(q) || i.sub.includes(q));
  $("#gallery").innerHTML = items.map((i, idx) =>
    `<button class="swatch" data-idx="${idx}"><b>${esc(i.label)}</b><small>${esc(i.sub)}</small></button>`).join("");
  $("#gallery").onclick = (e) => {
    const b = e.target.closest("button[data-idx]");
    if (!b) return;
    const item = items[Number(b.dataset.idx)];
    act(() => api("POST", "/api/preview", { ...item.body, duration: 8 }), `Previewing ${item.label}`);
  };
}
$("#gallery-search").addEventListener("input", renderGallery);

/* ---------- manual tab ---------- */
$("#m-bright").addEventListener("input", (e) => { $("#m-bright-out").textContent = e.target.value; });
$("#m-speed").addEventListener("input", (e) => { $("#m-speed-out").textContent = e.target.value; });
$("#m-set-color").addEventListener("click", () => act(() =>
  api("POST", "/api/strip", { color: hexToRgb($("#m-color").value), brightness: Number($("#m-bright").value) }).then(renderState), "Color set"));
$("#m-set-effect").addEventListener("click", () => act(() =>
  api("POST", "/api/strip", { effect: Number($("#m-effect").value), speed: Number($("#m-speed").value), brightness: Number($("#m-bright").value) }).then(renderState), "Effect set"));
$("#m-off").addEventListener("click", () => act(() => api("POST", "/api/strip", { power: false }).then(renderState), "Strip off (manual)"));
$("#m-resume").addEventListener("click", () => act(() => api("DELETE", "/api/strip/manual").then(renderState), "Back to automatic"));
function renderEffectSelect() {
  $("#m-effect").innerHTML = state.catalog.effects.map((e) => `<option value="${e.fx}">${e.fx} · ${esc(e.name)}</option>`).join("");
}

/* ---------- devices tab ---------- */
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
async function loadDevices() {
  await act(async () => { state.devices = await api("GET", "/api/devices"); renderDevices(); });
}
function renderDevices() {
  const f = $("#dev-filter").value;
  const rows = state.devices.filter((d) => f === "all" || (f === "unknown" && !d.known) || (f === "online" && d.online));
  $("#dev-count").textContent = `${rows.length} of ${state.devices.length}`;
  $("#dev-table tbody").innerHTML = rows.map((d) => `
    <tr data-mac="${esc(d.mac)}">
      <td><input class="dev-name" value="${esc(d.name)}" placeholder="unnamed"></td>
      <td><code>${esc(d.mac)}</code>${d.private_mac ? ' <span class="pill tiny">private</span>' : ""}<br><small class="muted">${esc(d.vendor || "unknown vendor")}</small></td>
      <td>${esc(d.ip)}</td>
      <td><span class="mon ${d.online ? "ok" : "off"}">${d.online ? "online" : ago(d.last_seen_ts)}</span><br><small class="muted">first ${esc((d.first_seen || "").slice(0, 10))}</small></td>
      <td><input type="checkbox" class="dev-known" ${d.known ? "checked" : ""}></td>
      <td><input type="checkbox" class="dev-announce" ${d.announce_arrival ? "checked" : ""}></td>
      <td><button class="link dev-forget">Forget</button></td>
    </tr>`).join("");
}
$("#dev-filter").addEventListener("change", renderDevices);
$("#dev-table").addEventListener("change", (e) => {
  const tr = e.target.closest("tr[data-mac]");
  if (!tr) return;
  let patch;
  if (e.target.classList.contains("dev-name")) patch = { name: e.target.value.trim() };
  if (e.target.classList.contains("dev-known")) patch = { known: e.target.checked };
  if (e.target.classList.contains("dev-announce")) patch = { announce_arrival: e.target.checked };
  if (patch) act(() => api("PATCH", `/api/devices/${encodeURIComponent(tr.dataset.mac)}`, patch), "Saved");
});
$("#dev-table").addEventListener("click", (e) => {
  if (!e.target.classList.contains("dev-forget")) return;
  const mac = e.target.closest("tr").dataset.mac;
  if (!confirm(`Forget ${mac}? It will alert as a new device the next time it is seen.`)) return;
  act(async () => { await api("DELETE", `/api/devices/${encodeURIComponent(mac)}`); await loadDevices(); }, "Forgotten");
});

/* ---------- rules + settings tab ---------- */
const FIELDS = [
  ["Quiet hours", [["quiet_hours.enabled", "checkbox", "Enabled"], ["quiet_hours.start", "time", "Start"], ["quiet_hours.end", "time", "End"],
    ["quiet_hours.brightness_pct", "number", "Brightness %"], ["quiet_hours.idle_off", "checkbox", "Idle glow off at night"]]],
  ["Idle & brightness", [["idle.color", "color", "Idle color"], ["idle.brightness", "number", "Idle brightness (0-255)"],
    ["max_brightness", "number", "Max brightness (0-255)"], ["manual_timeout_min", "number", "Manual timeout (min)"]]],
  ["Internet", [["internet.slow_rtt_ms", "number", "Slow if median ping above (ms)"], ["internet.slow_loss_pct", "number", "Slow if packet loss above (%)"]]],
  ["Disk & CPU", [["disk.full_pct", "number", "Disk full at (%)"], ["disk.clear_pct", "number", "Clear below (%)"],
    ["thermal.hot_c", "number", "CPU hot at (°C)"], ["thermal.clear_c", "number", "Clear below (°C)"]]],
  ["AdGuard", [["adguard.url", "text", "URL"], ["adguard.username", "text", "Username"], ["adguard.password", "secret", "Password"]]],
  ["Webhook", [["webhook_token", "secret", "Bearer token (optional)"]]],
];
const opts = (values, selected) => values.map((v) => `<option ${v === selected ? "selected" : ""}>${esc(v)}</option>`).join("");

async function loadRulesAndSettings() {
  await act(async () => {
    [state.rules, state.settings] = await Promise.all([api("GET", "/api/rules"), api("GET", "/api/settings")]);
    renderRules();
    renderSettings();
    renderEventButtons();
  });
}
function renderRules() {
  $("#rules-table tbody").innerHTML = Object.entries(state.rules).map(([name, r]) => `
    <tr data-name="${esc(name)}">
      <td><b>${esc(name)}</b><br><small class="muted">${esc(r.kind)}</small></td>
      <td><input type="checkbox" data-k="enabled" ${r.enabled ? "checked" : ""}></td>
      <td><select data-k="priority">${opts(["low", "normal", "high", "critical"], r.priority)}</select></td>
      <td><select data-k="pattern">${opts(state.catalog.named, r.pattern)}</select></td>
      <td><input type="number" data-k="duration_s" min="0.5" max="60" step="0.5" value="${r.duration_s ?? ""}" placeholder="auto"></td>
      <td><select data-k="quiet_behavior">${opts(["dim", "suppress"], r.quiet_behavior)}</select></td>
    </tr>`).join("");
}
$("#save-rules").addEventListener("click", () => {
  const rules = structuredClone(state.rules);
  $$("#rules-table tbody tr").forEach((tr) => {
    const r = rules[tr.dataset.name];
    $$("[data-k]", tr).forEach((el) => {
      const k = el.dataset.k;
      r[k] = el.type === "checkbox" ? el.checked : k === "duration_s" ? (el.value === "" ? null : Number(el.value)) : el.value;
    });
  });
  act(async () => { state.rules = await api("PUT", "/api/rules", rules); renderEventButtons(); }, "Rules saved");
});
function renderSettings() {
  $("#settings-form").innerHTML = FIELDS.map(([title, fields]) => `<fieldset><legend>${esc(title)}</legend>${fields.map(([path, type, label]) => {
    const v = getPath(state.settings, path);
    if (type === "checkbox") return `<label class="inline"><input type="checkbox" data-path="${path}" ${v ? "checked" : ""}> ${esc(label)}</label>`;
    if (type === "color") return `<label>${esc(label)} <input type="color" data-path="${path}" data-type="color" value="${rgbToHex(v)}"></label>`;
    if (type === "secret") return `<label>${esc(label)} <input type="password" data-path="${path}" data-type="secret" autocomplete="new-password" placeholder="${v && v.set ? "set — leave blank to keep" : "not set"}"></label>`;
    return `<label>${esc(label)} <input type="${type}" data-path="${path}" data-type="${type}" value="${esc(v)}"></label>`;
  }).join("")}</fieldset>`).join("");
}
$("#save-settings").addEventListener("click", () => {
  const patch = {};
  $$("#settings-form [data-path]").forEach((el) => {
    const t = el.dataset.type;
    if (el.type === "checkbox") setPath(patch, el.dataset.path, el.checked);
    else if (t === "secret") { if (el.value) setPath(patch, el.dataset.path, el.value); }
    else if (t === "color") setPath(patch, el.dataset.path, hexToRgb(el.value));
    else if (t === "number") setPath(patch, el.dataset.path, Number(el.value));
    else setPath(patch, el.dataset.path, el.value);
  });
  act(async () => { state.settings = await api("PUT", "/api/settings", patch); renderSettings(); }, "Settings saved");
});

/* ---------- log tab ---------- */
async function loadLog() {
  await act(async () => { state.log = (await api("GET", "/api/log")).reverse(); renderLog(); });
}
function renderLog() {
  $("#log-table tbody").innerHTML = state.log.map((e) => `<tr>
    <td>${esc(e.time.slice(11, 19))}</td>
    <td>${esc(e.name)} <small class="muted">${esc(e.kind)}</small></td>
    <td>${esc(e.source)}</td>
    <td><span class="outcome ${esc(e.outcome.split(":")[0])}">${esc(e.outcome)}</span></td></tr>`).join("");
}
function addLog(entry) {
  state.log.unshift(entry);
  state.log.length = Math.min(state.log.length, 200);
  if (!$("#tab-log").hidden) renderLog();
}

/* ---------- live updates + boot ---------- */
function connectStream() {
  const es = new EventSource("/api/stream");
  es.addEventListener("state", (e) => renderState(JSON.parse(e.data)));
  es.addEventListener("log", (e) => addLog(JSON.parse(e.data)));
  es.addEventListener("devices", () => { if (!$("#tab-devices").hidden) loadDevices(); });
}

(async function init() {
  await act(async () => {
    [state.catalog, state.rules] = await Promise.all([api("GET", "/api/patterns"), api("GET", "/api/rules")]);
    renderEventButtons();
    renderGallery();
    renderEffectSelect();
  });
  loadState();
  connectStream();
  setInterval(loadState, 10000);
})();
```

`led_notify/web/static/style.css`:
```css
:root {
  --bg: #f6f7f9; --panel: #ffffff; --text: #1b1f24; --muted: #6a7380; --border: #dde1e6;
  --accent: #00a394; --ok: #1f9d55; --bad: #d64545; --warn: #c98a00;
  --crit: #d64545; --high: #e07b00; --normal: #2f6fde; --low: #7a8794;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #111418; --panel: #1a1e24; --text: #e6e9ee; --muted: #8a94a3; --border: #2b313a; --accent: #26c6b5; color-scheme: dark; }
}
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
header, nav, main { max-width: 1100px; margin: 0 auto; padding: 0 16px; }
header { padding-top: 16px; display: grid; gap: 6px; }
h1 { font-size: 20px; margin: 0; }
h2 { font-size: 16px; margin: 20px 0 8px; }
.row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.row.gap { gap: 12px; margin: 12px 0; }
.label { width: 76px; color: var(--muted); font-size: 13px; }
.muted { color: var(--muted); }
.pill { display: inline-block; padding: 2px 10px; border-radius: 999px; border: 1px solid var(--border); font-size: 13px; }
.pill.ok { border-color: var(--ok); color: var(--ok); }
.pill.bad { border-color: var(--bad); color: var(--bad); }
.pill.quiet { border-color: var(--muted); color: var(--muted); }
.pill.tiny { font-size: 11px; padding: 0 6px; }
.p-critical { border-color: var(--crit) !important; }
.p-high { border-color: var(--high) !important; }
.p-normal { border-color: var(--normal) !important; }
.p-low { border-color: var(--low) !important; }
.mon { font-size: 13px; padding: 1px 8px; border-radius: 6px; background: var(--panel); border: 1px solid var(--border); white-space: nowrap; }
.mon::before { content: "●"; margin-right: 4px; color: var(--muted); }
.mon.ok::before { color: var(--ok); }
.mon.error::before { color: var(--bad); }
.mon.starting::before { color: var(--warn); }
.warn { background: color-mix(in srgb, var(--warn) 15%, transparent); border: 1px solid var(--warn); padding: 6px 10px; border-radius: 8px; }
nav { display: flex; gap: 4px; margin-top: 14px; border-bottom: 1px solid var(--border); overflow-x: auto; }
nav button { background: none; border: none; border-bottom: 2px solid transparent; border-radius: 0; color: var(--muted); padding: 10px 12px; }
nav button.active { color: var(--text); border-bottom-color: var(--accent); }
main { padding-bottom: 48px; }
button { font: inherit; cursor: pointer; background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: 8px 14px; }
button.secondary { background: var(--panel); color: var(--text); border: 1px solid var(--border); }
button.link { background: none; color: var(--bad); padding: 0; }
input, select { font: inherit; color: var(--text); background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; }
input[type=checkbox] { width: 18px; height: 18px; }
input[type=color] { padding: 2px; width: 56px; height: 34px; }
input[type=search] { width: 100%; margin-bottom: 10px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
.grid button { text-align: left; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-left-width: 4px; display: grid; gap: 2px; }
.grid button small { color: var(--muted); }
.grid button:hover { border-color: var(--accent); }
.card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin: 12px 0; }
.form { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; }
.form label, .settings label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
label.inline { display: flex; align-items: center; gap: 8px; color: var(--text); font-size: 14px; margin: 6px 0; }
.settings { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-bottom: 12px; }
fieldset { border: 1px solid var(--border); border-radius: 10px; background: var(--panel); display: grid; gap: 10px; min-width: 0; }
legend { font-weight: 600; padding: 0 6px; }
.table-wrap { overflow-x: auto; background: var(--panel); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
td input.dev-name { width: 100%; min-width: 120px; }
code { font-size: 12px; }
.outcome.shown, .outcome.active, .outcome.queued, .outcome.cleared { color: var(--ok); }
.outcome.skipped, .outcome.dropped, .outcome.preempted, .outcome.ignored { color: var(--warn); }
#toast { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); background: var(--text); color: var(--bg); padding: 10px 16px; border-radius: 8px; max-width: calc(100% - 32px); }
#toast.bad { background: var(--bad); color: #fff; }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add led_notify/web tests/test_api.py
git commit -m "feat: single-page web UI"
```

---

### Task 15: App wiring, local run, smoke script

**Files:**
- Create: `led_notify/app.py`, `led_notify/smoke.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `build_monitors(settings, devices, clock) -> list` (7 monitors: internet, dns, devices, disk, thermal, docker, adguard)
  - `build(data_dir: Path, address: str, fake_strip: bool, monitors: bool) -> (FastAPI, Services)`; the app's lifespan starts `arbiter.run()`, `supervisor.run()` and (real strip only) `link.run()`
  - `python -m led_notify.app [--data-dir] [--port] [--address] [--fake-strip] [--no-monitors]` (env: `DATA_DIR`, `PORT`, `STRIP_ADDRESS`, `FAKE_STRIP=1`, `TZ`)
  - `smoke.run(client, sleep)` and `python -m led_notify.smoke --url http://192.168.0.61:8787`

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py`:
```python
import time

import httpx
from fastapi.testclient import TestClient

from led_notify import smoke
from led_notify.app import build, build_monitors
from led_notify.patterns import NAMED
from led_notify.store.devices import DeviceStore
from led_notify.store.settings import SettingsStore
from tests.fakes import FakeClock


def wait_for(fn, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_build_monitors_has_all_seven(tmp_path):
    clock = FakeClock()
    ms = build_monitors(SettingsStore(tmp_path / "s.json"), DeviceStore(tmp_path / "d.json"), clock)
    assert [m.name for m in ms] == ["internet", "dns", "devices", "disk", "thermal", "docker", "adguard"]


def test_app_runs_with_fake_strip_and_plays_events(tmp_path):
    app, svc = build(tmp_path / "data", "", fake_strip=True, monitors=False)
    with TestClient(app) as client:
        wait_for(lambda: client.get("/api/state").json()["current"])
        assert client.get("/api/state").json()["current"]["reason"] == "idle"
        assert client.post("/api/events", json={"name": "backup.done"}).json()["outcome"] == "queued"
        wait_for(lambda: any(e["outcome"] == "shown" for e in client.get("/api/log").json()))
    assert (tmp_path / "data").is_dir()


def test_smoke_previews_every_named_pattern():
    posted = []

    def handler(request):
        posted.append(request.read())
        return httpx.Response(200, json={"accepted": True, "outcome": "queued"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    slept = []
    smoke.run(client, sleep=slept.append)
    assert len(posted) == len(NAMED) and len(slept) == len(NAMED)
    assert b'"pattern":"red_breath"' in posted[0].replace(b" ", b"")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_app.py -q`
Expected: FAIL — `ImportError: cannot import name 'smoke' from 'led_notify'`

- [ ] **Step 3: Implement app.py**

`led_notify/app.py`:
```python
"""Entry point: wires stores, strip link, arbiter, monitors and the web app."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from .clock import SystemClock
from .driver.fake import FakeStrip
from .driver.link import StripLink
from .engine.arbiter import Arbiter
from .engine.player import Player
from .hub import Hub
from .monitors.adguard import AdguardMonitor
from .monitors.devices import DeviceMonitor
from .monitors.disk import DiskMonitor
from .monitors.dns import DnsMonitor
from .monitors.docker import DockerMonitor
from .monitors.internet import InternetMonitor
from .monitors.supervisor import Supervisor
from .monitors.thermal import ThermalMonitor
from .store.devices import DeviceStore
from .store.settings import SettingsStore
from .web.api import Services, create_app


def build_monitors(settings, devices, clock) -> list:
    return [
        InternetMonitor(settings, clock),
        DnsMonitor(settings, clock),
        DeviceMonitor(settings, clock, devices),
        DiskMonitor(settings, clock),
        ThermalMonitor(settings, clock),
        DockerMonitor(settings, clock),
        AdguardMonitor(settings, clock),
    ]


def build(data_dir: Path, address: str, fake_strip: bool, monitors: bool):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    clock = SystemClock(os.environ.get("TZ"))
    hub = Hub()
    settings = SettingsStore(data_dir / "settings.json", hub)
    devices = DeviceStore(data_dir / "devices.json", hub)
    link = FakeStrip() if fake_strip else StripLink(address, clock)
    arbiter = Arbiter(Player(link, clock), settings, clock, hub)
    supervisor = Supervisor(build_monitors(settings, devices, clock) if monitors else [], arbiter.submit, clock, hub)
    svc = Services(arbiter=arbiter, settings=settings, devices=devices, supervisor=supervisor, link=link,
                   hub=hub, clock=clock)

    @asynccontextmanager
    async def lifespan(_app):
        coros = [arbiter.run(), supervisor.run()]
        if isinstance(link, StripLink):
            coros.append(link.run())
        tasks = [asyncio.create_task(c) for c in coros]
        yield
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return create_app(svc, lifespan=lifespan), svc


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="led-notify: homelab notifications on a BLE LED strip")
    p.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "./data"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    p.add_argument("--address", default=os.environ.get("STRIP_ADDRESS", ""))
    p.add_argument("--fake-strip", action="store_true", default=os.environ.get("FAKE_STRIP") == "1")
    p.add_argument("--no-monitors", action="store_true")
    args = p.parse_args(argv)
    if not args.fake_strip and not args.address:
        p.error("set STRIP_ADDRESS / --address, or use --fake-strip")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app, _ = build(Path(args.data_dir), args.address, args.fake_strip, not args.no_monitors)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement smoke.py**

`led_notify/smoke.py`:
```python
"""Play every named pattern on the real strip through a running service.

    python -m led_notify.smoke --url http://192.168.0.61:8787
"""
from __future__ import annotations

import argparse
import time

import httpx

from .patterns import NAMED, Sequence, sequence_ms

HOLD_S = 4.0
GAP_S = 1.0


def run(client: httpx.Client, sleep=time.sleep) -> None:
    for name, pattern in NAMED.items():
        seconds = sequence_ms(pattern) / 1000 if isinstance(pattern, Sequence) else HOLD_S
        print(f"▶ {name:20s} ({seconds:.1f}s)", flush=True)
        client.post("/api/preview", json={"pattern": name, "duration": HOLD_S}).raise_for_status()
        sleep(seconds + GAP_S)
    print("Done — the strip returns to its normal state.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://192.168.0.61:8787")
    args = p.parse_args(argv)
    with httpx.Client(base_url=args.url, timeout=10) as client:
        run(client)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_app.py -q`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all tests)

- [ ] **Step 7: Check the UI locally with a fake strip**

Run (in the background): `.venv/bin/python -m led_notify.app --fake-strip --no-monitors --data-dir /tmp/led-notify-dev --port 8787`
Then: `curl -s localhost:8787/api/state | head -c 300` → JSON with `"reason": "idle"` and `"fake": true`.
Then: `curl -s -N --max-time 3 localhost:8787/api/stream | head -2` → first line `event: state`.
Open `http://localhost:8787` in a browser: header shows "Fake strip", the Test tab lists 12 event buttons, the gallery lists patterns + 146 effects; clicking `device.new` shows a toast `device.new: queued` and the Log tab shows `queued` then `shown`. Stop the server afterwards.

- [ ] **Step 8: Commit**

```bash
git add led_notify/app.py led_notify/smoke.py tests/test_app.py
git commit -m "feat: app wiring, CLI entry point and hardware smoke script"
```

---

### Task 16: Container, ZimaOS install, on-hardware verification

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `deploy.sh`, `README.md`

**Interfaces:**
- Consumes: `python -m led_notify.app` (Task 15), env `STRIP_ADDRESS`, `DATA_DIR`, `PORT`, `TZ`.
- Produces: image `led-notify:local` on the server; ZimaOS app `led-notify` (container `led-notify`) on port 8787.

- [ ] **Step 1: Write the container files**

`Dockerfile`:
```dockerfile
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends arp-scan smartmontools iputils-ping tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY led_notify ./led_notify
RUN pip install --no-cache-dir .

ENV DATA_DIR=/data PORT=8787 PYTHONUNBUFFERED=1
EXPOSE 8787
CMD ["python", "-m", "led_notify.app"]
```

`.dockerignore`:
```
.venv
.git
.pytest_cache
**/__pycache__
data
tests
docs
```

`docker-compose.yml`:
```yaml
name: led-notify
services:
  led-notify:
    container_name: led-notify
    image: led-notify:local
    pull_policy: never
    network_mode: host
    restart: unless-stopped
    cap_add: [NET_RAW, NET_ADMIN, SYS_RAWIO, SYS_ADMIN]
    devices:
      - /dev/nvme0:/dev/nvme0
    environment:
      STRIP_ADDRESS: "A6:15:50:62:0A:9D"
      TZ: America/Los_Angeles
      DATA_DIR: /data
      PORT: "8787"
    volumes:
      - type: bind
        source: /run/dbus/system_bus_socket
        target: /run/dbus/system_bus_socket
      - type: bind
        source: /var/run/docker.sock
        target: /var/run/docker.sock
        read_only: true
      - type: bind
        source: /DATA
        target: /host/DATA
        read_only: true
      - type: bind
        source: /DATA/AppData/led-notify/data
        target: /data
    labels:
      icon: https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons@main/png/wled.png
x-casaos:
  main: led-notify
  index: /
  port_map: "8787"
  scheme: http
  author: gustavogomez092
  category: Utilities
  architectures: [amd64]
  icon: https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons@main/png/wled.png
  title:
    en_us: LED Notify
  tagline:
    en_us: Homelab alerts on the Bluetooth LED strip
  description:
    en_us: Shows internet, DNS, device, disk, CPU and container events on the BanlanX SP548E strip. Webhook at /api/events.
```

`deploy.sh`:
```bash
#!/usr/bin/env bash
# Sync source to the ZimaOS server, build the image there, and (re)start the app.
set -euo pipefail
HOST="${HOST:-zima}"
APP=/DATA/AppData/led-notify
COMPOSE_INSTALLED=/var/lib/casaos/apps/led-notify/docker-compose.yml
cd "$(dirname "$0")"

ssh "$HOST" "sudo mkdir -p $APP/src $APP/data && sudo chown -R \$(id -un) $APP/src"
rsync -az --delete --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude data ./ "$HOST:$APP/src/"
ssh "$HOST" "cd $APP/src && sudo docker build -t led-notify:local ."

if ssh "$HOST" "sudo test -f $COMPOSE_INSTALLED"; then
  ssh "$HOST" "sudo docker compose -f $COMPOSE_INSTALLED up -d --force-recreate"
else
  ssh "$HOST" "sudo casaos-cli app-management install -f $APP/src/docker-compose.yml"
fi

ssh "$HOST" "curl -fsS --retry 15 --retry-connrefused --retry-delay 2 http://127.0.0.1:8787/api/state" | head -c 600
echo
```

`README.md`:
````markdown
# led-notify

Homelab notifications on a BanlanX SP548E Bluetooth LED strip, running as a ZimaOS app.

- UI: http://192.168.0.61:8787
- Webhook: `curl -X POST http://192.168.0.61:8787/api/events -H 'Content-Type: application/json' -d '{"name":"backup.done","pattern":"green_flash"}'`
  - optional: `"kind": "start" | "clear"` for persistent states, `"priority": "low|normal|high|critical"`,
    `"effect": 1-146` or `"color": [r,g,b]` instead of `"pattern"`, `"duration": seconds`
  - if a webhook token is set in the UI, add `-H 'Authorization: Bearer <token>'`

## Develop

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/python -m led_notify.app --fake-strip --no-monitors --data-dir /tmp/led-notify-dev
```

## Deploy

```bash
./deploy.sh                                              # sync, build on the server, (re)start
.venv/bin/python -m led_notify.smoke --url http://192.168.0.61:8787   # watch every pattern
```

The strip accepts one Bluetooth connection: while the service runs, the BanlanX phone app can't connect.
Design: `docs/superpowers/specs/2026-10-11-led-notify-design.md`.
````

- [ ] **Step 2: Commit the packaging files**

```bash
chmod +x deploy.sh
git add Dockerfile .dockerignore docker-compose.yml deploy.sh README.md
git commit -m "build: container image, ZimaOS compose app and deploy script"
```

- [ ] **Step 3: Deploy**

Ask the user to close the BanlanX phone app (or turn off phone Bluetooth) first.
Run: `./deploy.sh`
Expected: docker build finishes with `naming to docker.io/library/led-notify:local`, the install command succeeds, and the final line is `/api/state` JSON.
If `casaos-cli app-management install` fails (e.g. it tries to pull `led-notify:local`), fall back to:
`ssh zima "sudo docker compose -f /DATA/AppData/led-notify/src/docker-compose.yml up -d"` and note in the README that the app was started with compose directly.

- [ ] **Step 4: Verify the strip link and monitors**

Run: `ssh zima "sudo docker logs --tail 40 led-notify"`
Expected: `strip A6:15:50:62:0A:9D connected` within ~30 s and no repeating tracebacks.
Run: `ssh zima "curl -s http://127.0.0.1:8787/api/state" | python3 -m json.tool`
Expected: `strip.connected: true`; `monitors` shows `internet`, `dns`, `devices`, `disk`, `thermal`, `docker` as `ok` and `adguard` as `disabled`.
If BLE fails with a D-Bus permission error, confirm the container runs as root (`sudo docker exec led-notify id` → `uid=0`) and that `/run/dbus/system_bus_socket` is mounted; if a monitor shows `error`, read its `detail` and fix that probe (e.g. `smartctl` needing `SYS_ADMIN`, `arp-scan` interface name).

- [ ] **Step 5: Hardware smoke test (user watches the strip)**

Run: `.venv/bin/python -m led_notify.smoke --url http://192.168.0.61:8787`
Expected: each pattern name prints and the strip visibly shows it; afterwards it returns to the dim teal idle glow. Ask the user to confirm each pattern looks right; any that look wrong get their effect number adjusted in `NAMED` (and its test) and the app redeployed.

- [ ] **Step 6: End-to-end checks**

Run: `curl -s -X POST http://192.168.0.61:8787/api/events -H 'Content-Type: application/json' -d '{"name":"deploy.check","pattern":"blue_flash"}'`
Expected: `{"accepted":true,"outcome":"queued"}` and a blue flash on the strip.
Open `http://192.168.0.61:8787` and check the header, Devices tab (populated after ~60 s, baseline silent), and Log tab.
Ask the user to enter the AdGuard username/password under Rules → Settings; within a minute the `adguard` monitor turns `ok`.
Check the ZimaOS dashboard shows the "LED Notify" app tile.

- [ ] **Step 7: Commit any fixes made during verification**

```bash
git add -A
git commit -m "fix: adjustments from on-hardware verification"
```
(Skip if nothing changed.)
