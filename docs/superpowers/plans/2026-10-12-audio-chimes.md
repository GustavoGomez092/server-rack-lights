# Audio Chimes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play short chimes through the server's internal speaker for led-notify events, chosen per rule and governed by the same arbitration as the lights.

**Architecture:** A `SoundLibrary` resolves sound refs (`default`/`success`/`error` built-ins or `custom:<name>` uploads) to WAV files. An `AudioPlayer` plays WAVs one at a time through `aplay` from a small queue, scaling volume in Python. An `AudioService` joins the two and is handed to the `Arbiter`, which decides *when* to chime (one-shot shown, a state's first display) and at what volume (day, night-critical-only, forced). The API and UI expose per-rule sounds, uploads, and previews.

**Tech Stack:** Python 3.12 stdlib (`wave`, `array`, `asyncio` subprocess), ALSA `aplay`/`amixer`, `mpg123` for MP3 uploads, FastAPI, vanilla JS. No new pip dependencies.

**Spec:** `docs/superpowers/specs/2026-10-12-audio-chimes-design.md`

## Global Constraints

- Built-in sound refs are exactly `default`, `success`, `error`; uploads are `custom:<name>` with `<name>` matching `^[a-z0-9_-]{1,40}$`.
- Uploads: WAV (16-bit PCM) or MP3, ≤ 2 MB raw, ≤ 10 s; stored as `/data/sounds/<name>.wav`.
- `audio` settings defaults: `{"enabled": true, "volume_pct": 70, "night_volume_pct": 30, "device": "plughw:1,0"}`; percentages 0–100.
- Player queue max **3** (drop oldest), per-play timeout **15 s**; mixer: `amixer -c <card> sset Master/Speaker 100% unmute` at startup.
- Night (quiet hours): only rules with priority `critical` chime, at `night_volume_pct`; forced (test/preview) plays use `volume_pct`.
- Audio must never raise into, block, or delay the light path.
- Built-in WAVs come from `/Volumes/Content/projects/HomeLab/chimes/{default,success,error}.wav` (already converted; `default` boosted +9 dB) — copy, don't re-encode.
- Python ≥ 3.12; run tests with `.venv/bin/pytest` from the repo root `/Volumes/Content/projects/HomeLab/led-notify`.

## Review Focus

1. **Existing `settings.json` on the server** (rules without `sound`, no `audio` section, plus the user's AdGuard credentials) must load with no warning and keep every saved value → Task 1 `test_old_settings_file_migrates_without_losing_data`.
2. **`aplay` missing or failing** (no `/dev/snd`, busy device) must leave the lights untouched and surface `last_error` in status → Task 2 `test_runner_failure_is_isolated_and_reported`, Task 4 `test_chime_exception_never_breaks_lights`.
3. **Burst of events** (a scan finds 10 new devices) must not build a minutes-long chime backlog → Task 2 `test_queue_overflow_keeps_newest_three`.
4. **Deleting an uploaded sound that rules use** must reset those rules to `default` (no dangling refs) → Task 5 `test_delete_sound_resets_rules_using_it`.
5. **Messy upload filenames** (`My Chime (1).MP3`) slug to a valid name; built-in collisions are refused → Task 3 `test_upload_name_is_slugged` / `test_upload_rejects_builtin_name`.

---

## File Structure

```
led_notify/
├── sounds/default.wav success.wav error.wav    # bundled built-ins (Task 3)
├── audio/
│   ├── __init__.py
│   ├── refs.py        # BUILTINS, CUSTOM_PREFIX, NAME_RE, is_valid_ref()        (Task 1)
│   ├── library.py     # SoundLibrary: list/resolve/import/delete               (Task 3)
│   ├── player.py      # scale_wav, run_aplay, silent_runner, init_mixer, AudioPlayer (Task 2)
│   ├── service.py     # AudioService(library, player): chime(), status()       (Task 2)
│   └── fake.py        # FakeChimes for tests                                    (Task 2)
├── events.py          # Event.sound                                             (Task 1)
├── store/settings.py  # rule "sound", "audio" section, migration                (Task 1)
├── engine/arbiter.py  # when/how loud to chime                                   (Task 4)
├── web/api.py         # sounds endpoints, sound on events, audio status          (Task 5)
├── web/static/*       # Sound column, Audio settings, Sounds manager             (Task 6)
└── app.py             # wiring                                                    (Task 5)
Dockerfile, docker-compose.yml, pyproject.toml                                     (Task 5)
```

**Waves (for parallel execution):** W1 = Tasks 1, 2 · W2 = Tasks 3, 4 · W3 = Task 5 · W4 = Task 6 · W5 = Task 7.

---

### Task 1: Sound refs, `Event.sound`, settings (rule sounds, `audio`, migration)

**Files:**
- Create: `led_notify/audio/__init__.py` (empty), `led_notify/audio/refs.py`
- Modify: `led_notify/events.py`, `led_notify/store/settings.py`
- Modify: `tests/test_stores.py` (one existing test), Test: `tests/test_audio_settings.py`

**Interfaces:**
- Produces: `refs.BUILTINS = ("default", "success", "error")`, `refs.CUSTOM_PREFIX = "custom:"`, `refs.NAME_RE`, `refs.is_valid_ref(ref) -> bool` (syntax only).
- Produces: `Event(..., sound: str | None = None)` — `None` = unspecified, `""` = silent, else a valid ref (ValueError otherwise).
- Produces: every rule dict has key `"sound"` (`str | None`); `settings.data["audio"]` = `{"enabled", "volume_pct", "night_volume_pct", "device"}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_audio_settings.py`:
```python
import copy
import json

import pytest

from led_notify.audio.refs import is_valid_ref
from led_notify.events import Event
from led_notify.store.settings import SettingsError, SettingsStore


def test_is_valid_ref():
    assert all(is_valid_ref(r) for r in ("default", "success", "error", "custom:my-chime_1"))
    assert not any(is_valid_ref(r) for r in (None, "", "nope", "custom:", "custom:Bad Name", "custom:" + "a" * 41, 3))


def test_event_sound_validation():
    assert Event("x").sound is None
    assert Event("x", sound="").sound == ""
    assert Event("x", sound="custom:door").sound == "custom:door"
    with pytest.raises(ValueError):
        Event("x", sound="nope")


def test_default_rule_sounds_and_audio_section(settings):
    expect = {
        "internet.down": "error", "dns.down": "error", "disk.smart_error": "error", "device.new_late": "error",
        "container.died": "error", "internet.up": "success", "device.known_arrived": "success",
        "device.new": "default", "internet.slow": "default", "disk.full": "default", "cpu.hot": "default",
        "adguard.block_spike": None,
    }
    assert {name: r["sound"] for name, r in settings.data["rules"].items()} == expect
    assert settings.data["audio"] == {"enabled": True, "volume_pct": 70, "night_volume_pct": 30,
                                      "device": "plughw:1,0"}


def test_audio_settings_update(settings):
    settings.update({"audio": {"volume_pct": 100, "night_volume_pct": 0, "enabled": False}})
    assert settings.data["audio"]["volume_pct"] == 100 and settings.data["audio"]["enabled"] is False


@pytest.mark.parametrize("patch", [
    {"audio": {"volume_pct": 101}},
    {"audio": {"night_volume_pct": -1}},
    {"audio": {"enabled": "yes"}},
    {"rules": {"dns.down": {"sound": "nope"}}},
    {"rules": {"dns.down": {"sound": "custom:Bad Name"}}},
])
def test_invalid_audio_patches_rejected(settings, patch):
    before = copy.deepcopy(settings.data)
    with pytest.raises(SettingsError):
        settings.update(patch)
    assert settings.data == before


def test_rule_sound_can_be_silenced_or_custom(settings):
    settings.update({"rules": {"dns.down": {"sound": None}, "cpu.hot": {"sound": "custom:beep"}}})
    assert settings.rule("dns.down")["sound"] is None and settings.rule("cpu.hot")["sound"] == "custom:beep"


def test_old_settings_file_migrates_without_losing_data(tmp_path):
    old_rule = {"enabled": True, "kind": "oneshot", "priority": "low", "pattern": "green_flash",
                "duration_s": None, "quiet_behavior": "suppress", "clear_followup": None}
    old_dns = {"enabled": True, "kind": "state", "priority": "high", "pattern": "red_breath",
               "duration_s": None, "quiet_behavior": "dim", "clear_followup": None}
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"adguard": {"username": "admin", "password": "pw"}, "max_brightness": 200,
                             "rules": {"backup.done": old_rule, "dns.down": old_dns}}))
    s = SettingsStore(p)
    assert s.warning is None
    assert s.data["adguard"]["password"] == "pw" and s.data["max_brightness"] == 200
    assert s.rule("backup.done")["sound"] is None           # custom rule: silent
    assert s.rule("dns.down")["sound"] == "error"           # default rule keeps its default sound
    assert s.rule("dns.down")["pattern"] == "red_breath"    # user's edit kept
    assert s.data["audio"]["volume_pct"] == 70
```

In `tests/test_stores.py`, replace the body of `test_new_complete_rule_can_be_added` so the rule includes the new key:
```python
def test_new_complete_rule_can_be_added(settings):
    rule = {"enabled": True, "kind": "oneshot", "priority": "low", "pattern": "green_flash",
            "duration_s": None, "quiet_behavior": "suppress", "clear_followup": None, "sound": None}
    settings.update({"rules": {"backup.done": rule}})
    assert settings.rule("backup.done") == rule
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_audio_settings.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.audio'`

- [ ] **Step 3: Create `refs.py`**

`led_notify/audio/__init__.py`: empty file.

`led_notify/audio/refs.py`:
```python
"""Names that identify a chime: built-ins or custom uploads (syntax only)."""
from __future__ import annotations

import re

BUILTINS = ("default", "success", "error")
CUSTOM_PREFIX = "custom:"
NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def is_valid_ref(ref) -> bool:
    if not isinstance(ref, str):
        return False
    if ref in BUILTINS:
        return True
    return ref.startswith(CUSTOM_PREFIX) and bool(NAME_RE.match(ref[len(CUSTOM_PREFIX):]))
```

- [ ] **Step 4: Add `Event.sound`**

In `led_notify/events.py` add the import `from .audio.refs import is_valid_ref` below the existing imports, add the field after `force`:
```python
    force: bool = False
    sound: str | None = None
```
and at the end of `__post_init__`:
```python
        if self.sound not in (None, "") and not is_valid_ref(self.sound):
            raise ValueError("sound must be default, success, error or custom:<name>")
```

- [ ] **Step 5: Settings — rule sounds, audio section, validation, migration**

In `led_notify/store/settings.py`:

Add the import:
```python
from ..audio.refs import is_valid_ref
```

Replace `_rule` and `DEFAULT_RULES` with:
```python
def _rule(priority, pattern, kind, duration_s=None, clear_followup=None, sound="default"):
    return {
        "enabled": True, "kind": kind, "priority": priority, "pattern": pattern, "duration_s": duration_s,
        "quiet_behavior": "dim" if priority in ("high", "critical") else "suppress",
        "clear_followup": clear_followup, "sound": sound,
    }


DEFAULT_RULES = {
    "internet.down": _rule("critical", "red_breath", "state", clear_followup="internet.up", sound="error"),
    "dns.down": _rule("high", "purple_breath", "state", sound="error"),
    "disk.smart_error": _rule("high", "red_white_snake", "state", sound="error"),
    "internet.slow": _rule("normal", "yellow_breath", "state"),
    "disk.full": _rule("normal", "yellow_stacking", "state"),
    "cpu.hot": _rule("normal", "fire_red_yellow", "state"),
    "device.new_late": _rule("high", "intruder", "oneshot", 8, sound="error"),
    "device.new": _rule("normal", "new_device", "oneshot"),
    "container.died": _rule("normal", "purple_flash_x3", "oneshot", sound="error"),
    "internet.up": _rule("normal", "green_flash_x2", "oneshot", sound="success"),
    "device.known_arrived": _rule("low", "welcome", "oneshot", 6, sound="success"),
    "adguard.block_spike": _rule("low", "yellow_white_stars", "oneshot", 5, sound=None),
}
```

In `DEFAULT_SETTINGS`, add after `"adguard": {...},`:
```python
    "audio": {"enabled": True, "volume_pct": 70, "night_volume_pct": 30, "device": "plughw:1,0"},
```

In `RANGES` add:
```python
    "audio.volume_pct": (0, 100), "audio.night_volume_pct": (0, 100),
```

Change `RULE_KEYS` to:
```python
RULE_KEYS = {"enabled", "kind", "priority", "pattern", "duration_s", "quiet_behavior", "clear_followup", "sound"}
```

At the end of the per-rule loop in `_check_rules` (after the `clear_followup` check) add:
```python
        if r["sound"] is not None and not is_valid_ref(r["sound"]):
            raise SettingsError(f"{p}.sound: must be empty, default, success, error or custom:<name>")
```

Add this function above `class SettingsStore`:
```python
def _migrate(data: dict) -> None:
    """Older files have rules without "sound"; custom rules default to silent."""
    for rule in (data.get("rules") or {}).values():
        if isinstance(rule, dict):
            rule.setdefault("sound", None)
```

In `SettingsStore.__init__`, call it right after the merge:
```python
        data = deep_merge(copy.deepcopy(DEFAULT_SETTINGS), stored)
        _migrate(data)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_audio_settings.py tests/test_stores.py tests/test_core.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add led_notify/audio/__init__.py led_notify/audio/refs.py led_notify/events.py led_notify/store/settings.py tests/test_audio_settings.py tests/test_stores.py
git commit -m "feat: sound refs, Event.sound, per-rule sounds and audio settings with migration" -- led_notify/audio/__init__.py led_notify/audio/refs.py led_notify/events.py led_notify/store/settings.py tests/test_audio_settings.py tests/test_stores.py
```

---

### Task 2: AudioPlayer, AudioService, FakeChimes

**Files:**
- Create: `led_notify/audio/player.py`, `led_notify/audio/service.py`, `led_notify/audio/fake.py`
- Create if missing: `led_notify/audio/__init__.py` (empty — Task 1 may create it first; leave it if present)
- Test: `tests/test_audio_player.py`

**Interfaces:**
- Produces: `scale_wav(data: bytes, volume_pct: int) -> bytes`; `async run_aplay(device, wav, timeout)`; `async silent_runner(device, wav, timeout)`; `async init_mixer(device)`.
- Produces: `AudioPlayer(device: str, runner=run_aplay)`: `enqueue(path, volume_pct)`, `async play_now(path, volume_pct)`, `async run()`, `status() -> {"device","played","last_error","queued"}`.
- Produces: `AudioService(library, player)`: `.library`, `.player`, `chime(ref, volume_pct) -> bool`, `status() -> dict`. `library` needs only `resolve(ref) -> Path | None`.
- Produces: `FakeChimes(library=None)`: `.library`, `.chimes: list[tuple[str, int]]`, `chime(ref, volume_pct) -> True`, `status() -> {"fake": True}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_audio_player.py`:
```python
import array
import asyncio
import io
import wave

import pytest

from led_notify.audio.fake import FakeChimes
from led_notify.audio.player import AudioPlayer, scale_wav
from led_notify.audio.service import AudioService


def make_wav(samples, width=2, rate=8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            w.writeframes(array.array("h", samples).tobytes())
        else:
            w.writeframes(bytes(samples))
    return buf.getvalue()


def samples_of(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        return list(a)


def test_scale_wav_volume():
    src = make_wav([1000, -2000, 32767, -32768])
    assert samples_of(scale_wav(src, 100)) == [1000, -2000, 32767, -32768]
    assert samples_of(scale_wav(src, 50)) == [500, -1000, 16383, -16384]
    assert samples_of(scale_wav(src, 0)) == [0, 0, 0, 0]
    assert samples_of(scale_wav(src, 150)) == [1000, -2000, 32767, -32768]   # clamped to 100 %


def test_scale_wav_rejects_8_bit():
    with pytest.raises(ValueError):
        scale_wav(make_wav([1, 2, 3], width=1), 50)


class Recorder:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def __call__(self, device, wav, timeout):
        self.calls.append((device, samples_of(wav), timeout))
        if self.fail:
            raise self.fail


async def test_play_now_scales_and_uses_device(tmp_path):
    p = tmp_path / "s.wav"
    p.write_bytes(make_wav([1000, -1000]))
    rec = Recorder()
    player = AudioPlayer("plughw:1,0", runner=rec)
    await player.play_now(p, 50)
    assert rec.calls == [("plughw:1,0", [500, -500], 15.0)]
    assert player.status()["played"] == 1 and player.status()["last_error"] == ""


async def test_runner_failure_is_isolated_and_reported(tmp_path):
    p = tmp_path / "s.wav"
    p.write_bytes(make_wav([1]))
    player = AudioPlayer("plughw:1,0", runner=Recorder(fail=RuntimeError("aplay failed: no such device")))
    await player.play_now(p, 70)                     # must not raise
    assert "no such device" in player.status()["last_error"]
    await player.play_now(tmp_path / "missing.wav", 70)
    assert "FileNotFoundError" in player.status()["last_error"]


async def test_queue_overflow_keeps_newest_three(tmp_path):
    player = AudioPlayer("d", runner=Recorder())
    for i in range(5):
        player.enqueue(tmp_path / f"{i}.wav", 70)
    assert player.status()["queued"] == 3
    names = [player._queue.get_nowait()[0].name for _ in range(3)]
    assert names == ["2.wav", "3.wav", "4.wav"]


async def test_run_plays_queued_sounds_in_order(tmp_path):
    for i in range(2):
        (tmp_path / f"{i}.wav").write_bytes(make_wav([i * 100 + 100]))
    rec = Recorder()
    player = AudioPlayer("d", runner=rec)
    player.enqueue(tmp_path / "0.wav", 100)
    player.enqueue(tmp_path / "1.wav", 100)
    task = asyncio.create_task(player.run())
    for _ in range(50):
        await asyncio.sleep(0)
        if len(rec.calls) == 2:
            break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert [c[1] for c in rec.calls] == [[100], [200]]


class Lib:
    def __init__(self, path):
        self.path = path

    def resolve(self, ref):
        return self.path if ref == "default" else None


async def test_service_chime_resolves_and_enqueues(tmp_path):
    player = AudioPlayer("d", runner=Recorder())
    svc = AudioService(Lib(tmp_path / "d.wav"), player)
    assert svc.chime("default", 70) is True and player.status()["queued"] == 1
    assert svc.chime("custom:gone", 70) is False and player.status()["queued"] == 1
    assert svc.status()["device"] == "d"


def test_fake_chimes_records():
    f = FakeChimes()
    assert f.chime("error", 30) is True and f.chimes == [("error", 30)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_audio_player.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.audio.fake'` (or `led_notify.audio` if Task 1 hasn't landed)

- [ ] **Step 3: Implement `player.py`**

`led_notify/audio/player.py`:
```python
"""Plays WAV chimes through ALSA, one at a time, never blocking or failing callers."""
from __future__ import annotations

import array
import asyncio
import io
import logging
import re
import sys
import wave
from pathlib import Path

log = logging.getLogger(__name__)

QUEUE_MAX = 3
PLAY_TIMEOUT_S = 15.0


def scale_wav(data: bytes, volume_pct: int) -> bytes:
    """Return the WAV with its 16-bit samples scaled to volume_pct (clamped to 0-100)."""
    factor = max(0, min(100, int(volume_pct))) / 100
    with wave.open(io.BytesIO(data)) as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    if params.sampwidth != 2:
        raise ValueError("only 16-bit PCM WAV is supported")
    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    if factor != 1:
        for i, s in enumerate(samples):
            samples[i] = int(s * factor)
    if sys.byteorder == "big":
        samples.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(samples.tobytes())
    return out.getvalue()


async def run_aplay(device: str, wav: bytes, timeout: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        "aplay", "-q", "-D", device, "-",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(wav), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"aplay timed out after {timeout:.0f} s")
    if proc.returncode != 0:
        raise RuntimeError(f"aplay failed: {err.decode(errors='replace').strip()[:200]}")


async def silent_runner(device: str, wav: bytes, timeout: float) -> None:
    """Used with --fake-strip / --no-audio: accept the sound and play nothing."""
    return None


async def init_mixer(device: str) -> None:
    """Unmute the internal speaker and set it to full; volume is applied in software per chime."""
    m = re.search(r":(\d+)", device)
    card = m.group(1) if m else "0"
    for control in ("Master", "Speaker"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-q", "-c", card, "sset", control, "100%", "unmute",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except FileNotFoundError:
            log.warning("amixer not found; leaving mixer as is")
            return


class AudioPlayer:
    def __init__(self, device: str, runner=run_aplay):
        self.device = device
        self.runner = runner
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.played = 0
        self.last_error = ""

    def enqueue(self, path, volume_pct: int) -> None:
        if self._queue.full():
            self._queue.get_nowait()  # a burst keeps only the newest chimes
        self._queue.put_nowait((Path(path), int(volume_pct)))

    async def play_now(self, path, volume_pct: int) -> None:
        try:
            wav = scale_wav(Path(path).read_bytes(), volume_pct)
            await self.runner(self.device, wav, PLAY_TIMEOUT_S)
            self.played += 1
            self.last_error = ""
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("chime failed: %s", self.last_error)

    async def run(self) -> None:
        while True:
            path, volume = await self._queue.get()
            await self.play_now(path, volume)

    def status(self) -> dict:
        return {"device": self.device, "played": self.played, "last_error": self.last_error,
                "queued": self._queue.qsize()}
```

- [ ] **Step 4: Implement `service.py` and `fake.py`**

`led_notify/audio/service.py`:
```python
"""Turns a sound ref into a queued chime."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class AudioService:
    def __init__(self, library, player):
        self.library = library
        self.player = player

    def chime(self, ref: str, volume_pct: int) -> bool:
        path = self.library.resolve(ref)
        if path is None:
            log.warning("sound %r not found; skipping chime", ref)
            return False
        self.player.enqueue(path, volume_pct)
        return True

    def status(self) -> dict:
        return self.player.status()
```

`led_notify/audio/fake.py`:
```python
"""Records chimes instead of playing them (tests)."""
from __future__ import annotations


class FakeChimes:
    def __init__(self, library=None):
        self.library = library
        self.chimes: list[tuple[str, int]] = []

    def chime(self, ref: str, volume_pct: int) -> bool:
        self.chimes.append((ref, volume_pct))
        return True

    def status(self) -> dict:
        return {"fake": True}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_audio_player.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add led_notify/audio/player.py led_notify/audio/service.py led_notify/audio/fake.py tests/test_audio_player.py
git commit -m "feat: audio player with bounded queue, software volume and aplay runner" -- led_notify/audio/player.py led_notify/audio/service.py led_notify/audio/fake.py tests/test_audio_player.py
```
(If you created `led_notify/audio/__init__.py`, add it to both lists.)

---

### Task 3: Built-in sounds + SoundLibrary

**Files:**
- Create: `led_notify/sounds/default.wav`, `success.wav`, `error.wav` (copied), `led_notify/audio/library.py`
- Modify: `pyproject.toml` (package data)
- Test: `tests/test_audio_library.py`

**Interfaces:**
- Consumes: `led_notify.audio.refs` (`BUILTINS`, `CUSTOM_PREFIX`, `NAME_RE`, `is_valid_ref`) from Task 1.
- Produces: `SoundLibrary(data_dir, decode_mp3=mpg123_decode, builtin_dir=BUILTIN_DIR)`: `resolve(ref) -> Path | None`, `exists(ref) -> bool`, `list() -> [{"ref","name","builtin","duration_s"}]`, `import_upload(filename, data, content_type) -> item`, `delete(name) -> bool`; `SoundError(ValueError)`; `slugify(filename) -> str`.

- [ ] **Step 1: Copy the built-in WAVs and add package data**

```bash
mkdir -p led_notify/sounds
cp /Volumes/Content/projects/HomeLab/chimes/default.wav /Volumes/Content/projects/HomeLab/chimes/success.wav /Volumes/Content/projects/HomeLab/chimes/error.wav led_notify/sounds/
```
In `pyproject.toml` change the package-data line to:
```toml
led_notify = ["web/static/*", "sounds/*.wav"]
```

- [ ] **Step 2: Write the failing tests**

`tests/test_audio_library.py`:
```python
import array
import io
import wave

import pytest

from led_notify.audio.library import SoundError, SoundLibrary, slugify


def make_wav(seconds=1.0, width=2, rate=8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        n = int(seconds * rate)
        w.writeframes(array.array("h", [0] * n).tobytes() if width == 2 else bytes(n))
    return buf.getvalue()


@pytest.fixture
def lib(tmp_path):
    return SoundLibrary(tmp_path, decode_mp3=lambda data: make_wav(1.5))


def test_builtins_are_bundled_and_listed(lib):
    items = {i["ref"]: i for i in lib.list()}
    assert set(items) == {"default", "success", "error"}
    assert all(i["builtin"] for i in items.values())
    assert 2.0 < items["default"]["duration_s"] < 2.3
    assert 0.9 < items["error"]["duration_s"] < 1.3
    assert lib.exists("success") and lib.resolve("success").name == "success.wav"


def test_resolve_unknown_and_invalid(lib):
    assert lib.resolve("custom:nothing") is None and lib.resolve("nope") is None and lib.resolve(None) is None


def test_upload_wav_then_list_resolve_delete(lib):
    item = lib.import_upload("door.wav", make_wav(1.0), "audio/wav")
    assert item == {"ref": "custom:door", "name": "door", "builtin": False, "duration_s": 1.0}
    assert lib.exists("custom:door")
    assert "custom:door" in [i["ref"] for i in lib.list()]
    assert lib.delete("door") is True and lib.delete("door") is False
    assert not lib.exists("custom:door")


def test_upload_mp3_is_decoded(lib):
    item = lib.import_upload("ring.mp3", b"ID3fake", "audio/mpeg")
    assert item["ref"] == "custom:ring" and item["duration_s"] == 1.5


def test_upload_name_is_slugged(lib):
    assert slugify("My Chime (1).MP3") == "my-chime-1"
    assert lib.import_upload("My Chime (1).wav", make_wav(0.5), "audio/x-wav")["ref"] == "custom:my-chime-1"


def test_upload_rejects_builtin_name(lib):
    with pytest.raises(SoundError, match="built-in"):
        lib.import_upload("default.wav", make_wav(1.0), "audio/wav")


@pytest.mark.parametrize("name,data,ctype,msg", [
    ("big.wav", b"x" * (2 * 1024 * 1024 + 1), "audio/wav", "2 MB"),
    ("long.wav", make_wav(11.0), "audio/wav", "10 seconds"),
    ("eight.wav", make_wav(1.0, width=1), "audio/wav", "16-bit"),
    ("junk.wav", b"not a wav", "audio/wav", "valid WAV"),
    ("pic.png", make_wav(1.0), "image/png", "WAV or MP3"),
    ("!!!.wav", make_wav(1.0), "audio/wav", "name"),
])
def test_upload_rejections(lib, name, data, ctype, msg):
    with pytest.raises(SoundError, match=msg):
        lib.import_upload(name, data, ctype)


def test_mp3_decoder_failure_is_a_sound_error(tmp_path):
    def broken(data):
        raise SoundError("could not decode the MP3 file")
    with pytest.raises(SoundError, match="decode"):
        SoundLibrary(tmp_path, decode_mp3=broken).import_upload("x.mp3", b"x", "audio/mpeg")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_audio_library.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'led_notify.audio.library'`

- [ ] **Step 4: Implement `library.py`**

`led_notify/audio/library.py`:
```python
"""Built-in chimes plus user uploads, all stored as 16-bit WAV."""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
import wave
from pathlib import Path

from .refs import BUILTINS, CUSTOM_PREFIX, NAME_RE, is_valid_ref

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "sounds"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_DURATION_S = 10.0
WAV_TYPES = ("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave")
MP3_TYPES = ("audio/mpeg", "audio/mp3")


class SoundError(ValueError):
    pass


def slugify(filename: str) -> str:
    stem = Path(filename).stem.lower()
    return re.sub(r"[^a-z0-9_-]+", "-", stem).strip("-")[:40]


def _wav_info(data: bytes) -> tuple[float, int]:
    """(duration seconds, sample width bytes)."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / w.getframerate(), w.getsampwidth()
    except (wave.Error, EOFError, ZeroDivisionError) as e:
        raise SoundError(f"not a valid WAV file ({e})")


def mpg123_decode(data: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d, "in.mp3"), Path(d, "out.wav")
        src.write_bytes(data)
        try:
            subprocess.run(["mpg123", "-q", "-w", str(dst), str(src)], check=True, timeout=20,
                           capture_output=True)
        except FileNotFoundError:
            raise SoundError("MP3 support needs mpg123, which is not installed")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise SoundError("could not decode the MP3 file")
        return dst.read_bytes()


class SoundLibrary:
    def __init__(self, data_dir, decode_mp3=mpg123_decode, builtin_dir: Path = BUILTIN_DIR):
        self.dir = Path(data_dir) / "sounds"
        self.builtin_dir = Path(builtin_dir)
        self.decode_mp3 = decode_mp3

    def resolve(self, ref) -> Path | None:
        if not is_valid_ref(ref):
            return None
        if ref in BUILTINS:
            path = self.builtin_dir / f"{ref}.wav"
        else:
            path = self.dir / f"{ref[len(CUSTOM_PREFIX):]}.wav"
        return path if path.is_file() else None

    def exists(self, ref) -> bool:
        return self.resolve(ref) is not None

    def list(self) -> list[dict]:
        items = [self._item(name, name, True, p) for name in BUILTINS if (p := self.resolve(name))]
        if self.dir.is_dir():
            for p in sorted(self.dir.glob("*.wav")):
                if NAME_RE.match(p.stem):
                    items.append(self._item(CUSTOM_PREFIX + p.stem, p.stem, False, p))
        return items

    @staticmethod
    def _item(ref: str, name: str, builtin: bool, path: Path) -> dict:
        try:
            duration = round(_wav_info(path.read_bytes())[0], 2)
        except (SoundError, OSError):
            duration = None
        return {"ref": ref, "name": name, "builtin": builtin, "duration_s": duration}

    def import_upload(self, filename: str, data: bytes, content_type: str) -> dict:
        name = slugify(filename or "")
        if not NAME_RE.match(name):
            raise SoundError("name must be 1-40 characters: a-z, 0-9, '-' or '_'")
        if name in BUILTINS:
            raise SoundError(f"{name!r} is a built-in sound name")
        if len(data) > MAX_UPLOAD_BYTES:
            raise SoundError("file is larger than 2 MB")
        ctype = (content_type or "").split(";")[0].strip().lower()
        if ctype in MP3_TYPES:
            data = self.decode_mp3(data)
        elif ctype not in WAV_TYPES:
            raise SoundError("upload a WAV or MP3 file")
        duration, width = _wav_info(data)
        if width != 2:
            raise SoundError("WAV must be 16-bit PCM")
        if duration > MAX_DURATION_S:
            raise SoundError("sound is longer than 10 seconds")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{name}.tmp"
        tmp.write_bytes(data)
        tmp.replace(self.dir / f"{name}.wav")
        return self._item(CUSTOM_PREFIX + name, name, False, self.dir / f"{name}.wav")

    def delete(self, name: str) -> bool:
        if not NAME_RE.match(name or ""):
            return False
        path = self.dir / f"{name}.wav"
        if not path.is_file():
            return False
        path.unlink()
        return True
```

Note: the size check runs before MP3 decoding; `big.wav` in the test fails on size before parsing.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_audio_library.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add led_notify/sounds/default.wav led_notify/sounds/success.wav led_notify/sounds/error.wav led_notify/audio/library.py pyproject.toml tests/test_audio_library.py
git commit -m "feat: bundled chimes and sound library with validated WAV/MP3 uploads" -- led_notify/sounds/default.wav led_notify/sounds/success.wav led_notify/sounds/error.wav led_notify/audio/library.py pyproject.toml tests/test_audio_library.py
```

---

### Task 4: Arbiter decides when and how loud to chime

**Files:**
- Modify: `led_notify/engine/arbiter.py`
- Test: `tests/test_arbiter_chimes.py`

**Interfaces:**
- Consumes: rule `"sound"` and `settings.data["audio"]` (Task 1), `Event.sound` (Task 1), `FakeChimes` (Task 2), `tests.fakes.settle`, `FakeClock`.
- Produces: `Arbiter(player, settings, clock, hub=None, log_size=200, chimes=None)`; `chimes` needs `chime(ref: str, volume_pct: int)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_arbiter_chimes.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_arbiter_chimes.py -q`
Expected: FAIL — `TypeError: Arbiter.__init__() got an unexpected keyword argument 'chimes'`

- [ ] **Step 3: Implement in `arbiter.py`**

Change the constructor signature and add two attributes:
```python
    def __init__(self, player, settings, clock, hub=None, log_size: int = 200, chimes=None):
```
and in its body (after `self._seq = 0`):
```python
        self.chimes = chimes
        self._chimed_states: set[int] = set()  # state seqs that already chimed on first display
```

In `_rule_for`, add `"sound"` to the ad-hoc rule dict:
```python
            "clear_followup": None,
            "sound": ev.sound if ev.sound is not None else "default",
```

In `tick()`, at the end of the render branch (after `self._set_current(base.desc, base.reason)`), add:
```python
            if base.key[0] == "state":
                self._chime_state(base.key)
```

In `_clear`, right after `st = self._states.pop(ev.name, None)` and its `None` check, add:
```python
        self._chimed_states.discard(st.seq)
```

In `_play_oneshot`, after `self._log(ev, "shown")`:
```python
            self._chime(rule, force=ev.force)
```

Add these methods (next to `_level`):
```python
    def _chime(self, rule: dict, force: bool = False) -> None:
        """Queue the rule's sound: day volume, or night volume for critical rules; never raises."""
        if self.chimes is None:
            return
        ref = rule.get("sound")
        audio = self.settings.data["audio"]
        if not ref or not audio["enabled"]:
            return
        if force or not self._quiet():
            volume = audio["volume_pct"]
        elif rule["priority"] == "critical":
            volume = audio["night_volume_pct"]
        else:
            return
        try:
            self.chimes.chime(ref, volume)
        except Exception:
            log.exception("chime %s failed", ref)

    def _chime_state(self, key: tuple) -> None:
        """A state chimes the first time it becomes the displayed look, never again while active."""
        _, name, seq, *_ = key
        if seq in self._chimed_states:
            return
        st = self._states.get(name)
        if st is None or st.seq != seq:
            return
        self._chimed_states.add(seq)
        self._chime(self._live_rule(st))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_arbiter_chimes.py tests/test_arbiter.py -q`
Expected: PASS (existing arbiter tests unaffected — they pass no `chimes`)

- [ ] **Step 5: Commit**

```bash
git add led_notify/engine/arbiter.py tests/test_arbiter_chimes.py
git commit -m "feat: arbiter chimes on shown one-shots and first display of states" -- led_notify/engine/arbiter.py tests/test_arbiter_chimes.py
```

---

### Task 5: API, app wiring, container

**Files:**
- Modify: `led_notify/web/api.py`, `led_notify/app.py`, `Dockerfile`, `docker-compose.yml`
- Modify: `tests/test_api.py` (fixture + new tests), `tests/test_app.py` (none expected — verify)

**Interfaces:**
- Consumes: `SoundLibrary`, `SoundError` (Task 3), `AudioPlayer`, `AudioService`, `silent_runner`, `init_mixer`, `FakeChimes` (Task 2), `Arbiter(..., chimes=)` (Task 4), `Event.sound` (Task 1).
- Produces: `Services(..., audio=None)`; endpoints `GET /api/sounds`, `PUT /api/sounds/{name}`, `DELETE /api/sounds/{name}`, `POST /api/sounds/play`; `sound` on `/api/events` and `/api/preview`; `"audio"` in `/api/state`; `build(data_dir, address, fake_strip, monitors, audio=True)`.

- [ ] **Step 1: Update the test fixture and write the failing tests**

In `tests/test_api.py`, add imports:
```python
import array
import io
import wave

from led_notify.audio.fake import FakeChimes
from led_notify.audio.library import SoundLibrary
```
Replace the `svc` fixture with:
```python
@pytest.fixture
def svc(tmp_path):
    clock = FakeClock()
    hub = Hub()
    settings = SettingsStore(tmp_path / "settings.json", hub)
    devices = DeviceStore(tmp_path / "devices.json", hub)
    strip = FakeStrip()
    audio = FakeChimes(SoundLibrary(tmp_path))
    arbiter = Arbiter(Player(strip, clock), settings, clock, hub, chimes=audio)
    return Services(arbiter=arbiter, settings=settings, devices=devices, supervisor=None, link=strip,
                    hub=hub, clock=clock, audio=audio)
```
Append the tests:
```python
def wav_bytes(seconds=0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(array.array("h", [0] * int(8000 * seconds)).tobytes())
    return buf.getvalue()


def test_sounds_list_has_builtins(client):
    refs = [s["ref"] for s in client.get("/api/sounds").json()]
    assert refs[:3] == ["default", "success", "error"]


def test_upload_play_and_delete_sound(client, svc):
    r = client.put("/api/sounds/Door Bell.wav", content=wav_bytes(), headers={"Content-Type": "audio/wav"})
    assert r.status_code == 200 and r.json()["ref"] == "custom:door-bell"
    assert client.post("/api/sounds/play", json={"sound": "custom:door-bell"}).status_code == 200
    assert svc.audio.chimes[-1] == ("custom:door-bell", 70)
    assert client.post("/api/sounds/play", json={"sound": "custom:nope"}).status_code == 404
    assert client.delete("/api/sounds/door-bell").status_code == 200
    assert client.delete("/api/sounds/door-bell").status_code == 404


def test_upload_rejections_are_422(client):
    assert client.put("/api/sounds/x.wav", content=b"junk", headers={"Content-Type": "audio/wav"}).status_code == 422
    assert client.put("/api/sounds/default.wav", content=wav_bytes(),
                      headers={"Content-Type": "audio/wav"}).status_code == 422
    assert client.put("/api/sounds/x.png", content=wav_bytes(),
                      headers={"Content-Type": "image/png"}).status_code == 422


def test_delete_sound_resets_rules_using_it(client, svc):
    client.put("/api/sounds/beep.wav", content=wav_bytes(), headers={"Content-Type": "audio/wav"})
    assert client.put("/api/rules", json={"cpu.hot": {"sound": "custom:beep"}}).status_code == 200
    r = client.delete("/api/sounds/beep")
    assert r.json()["rules_reset"] == ["cpu.hot"]
    assert svc.settings.rule("cpu.hot")["sound"] == "default"


def test_rules_reject_unknown_custom_sound(client):
    assert client.put("/api/rules", json={"cpu.hot": {"sound": "custom:missing"}}).status_code == 422
    assert client.put("/api/rules", json={"cpu.hot": {"sound": None}}).status_code == 200


def test_webhook_sound_validation(client):
    assert client.post("/api/events", json={"name": "a", "sound": "success"}).status_code == 200
    assert client.post("/api/events", json={"name": "b", "sound": ""}).status_code == 200
    assert client.post("/api/events", json={"name": "c", "sound": "custom:missing"}).status_code == 422
    assert client.post("/api/events", json={"name": "d", "sound": "nope"}).status_code == 422


def test_state_includes_audio_status(client):
    assert client.get("/api/state").json()["audio"] == {"fake": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -q`
Expected: FAIL — `TypeError: Services.__init__() got an unexpected keyword argument 'audio'`

- [ ] **Step 3: Implement the API changes in `api.py`**

Add imports:
```python
from ..audio.library import SoundError
from ..audio.refs import CUSTOM_PREFIX
```
Add a field to `Services` (last, with a default):
```python
    clock: Any
    audio: Any = None
```
Add `sound` to the request models:
```python
class EventIn(LookIn):
    name: str
    kind: Literal["oneshot", "start", "clear"] = "oneshot"
    priority: Literal["low", "normal", "high", "critical"] | None = None
    duration: float | None = None
    data: dict = Field(default_factory=dict)
    sound: str | None = None


class PreviewIn(LookIn):
    duration: float = 8.0
    sound: str | None = None


class SoundPlayIn(BaseModel):
    sound: str
```
Inside `create_app`, add helpers after `submit`:
```python
    def check_sound(ref: str | None) -> None:
        if ref in (None, ""):
            return
        if svc.audio is None or not svc.audio.library.exists(ref):
            raise HTTPException(422, f"unknown sound {ref!r}")
```
In `state()`, add the audio status:
```python
        return {**svc.arbiter.snapshot(), "strip": svc.link.status(),
                "monitors": svc.supervisor.health if svc.supervisor else {}, "warnings": warnings,
                "audio": svc.audio.status() if svc.audio else None}
```
Replace `post_event` and `preview` with:
```python
    @app.post("/api/events", dependencies=[Depends(require_token)])
    async def post_event(body: EventIn):
        spec = body.spec()
        check_sound(body.sound)
        return await submit(make_event(name=body.name, kind=body.kind, source="webhook", priority=body.priority,
                                       look=spec or None, duration_s=body.duration, data=body.data,
                                       sound=body.sound))

    @app.post("/api/preview")
    async def preview(body: PreviewIn):
        spec = body.spec()
        if not spec:
            raise HTTPException(422, "give one of pattern, effect, color")
        check_sound(body.sound)
        return await submit(make_event(name="preview", kind="oneshot", source="test", look=spec,
                                       duration_s=body.duration, force=True, sound=body.sound or ""))
```
In `put_rules`, before `svc.settings.update(...)`:
```python
        for rule in body.values() if isinstance(body, dict) else []:
            if isinstance(rule, dict) and isinstance(rule.get("sound"), str) and rule["sound"].startswith(CUSTOM_PREFIX):
                check_sound(rule["sound"])
```
Add the sounds endpoints (before the `/api/stream` route):
```python
    @app.get("/api/sounds")
    async def list_sounds():
        return svc.audio.library.list() if svc.audio else []

    @app.put("/api/sounds/{name}")
    async def upload_sound(name: str, request: Request):
        if svc.audio is None:
            raise HTTPException(404, "audio is not available")
        data = await request.body()
        try:
            return svc.audio.library.import_upload(name, data, request.headers.get("content-type", ""))
        except SoundError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/sounds/{name}")
    async def delete_sound(name: str):
        if svc.audio is None or not svc.audio.library.delete(name):
            raise HTTPException(404, "unknown sound")
        ref = CUSTOM_PREFIX + name
        reset = sorted(n for n, r in svc.settings.data["rules"].items() if r.get("sound") == ref)
        if reset:
            svc.settings.update({"rules": {n: {"sound": "default"} for n in reset}})
        return {"deleted": name, "rules_reset": reset}

    @app.post("/api/sounds/play")
    async def play_sound(body: SoundPlayIn):
        if svc.audio is None or not svc.audio.library.exists(body.sound):
            raise HTTPException(404, "unknown sound")
        svc.audio.chime(body.sound, svc.settings.data["audio"]["volume_pct"])
        return {"playing": body.sound}
```

- [ ] **Step 4: Wire it up in `app.py`**

Add imports:
```python
from .audio.library import SoundLibrary
from .audio.player import AudioPlayer, init_mixer, run_aplay, silent_runner
from .audio.service import AudioService
```
Change `build` to:
```python
def build(data_dir: Path, address: str, fake_strip: bool, monitors: bool, audio: bool = True):
```
After `link = ...`, create the audio service and pass it on:
```python
    real_audio = audio and not fake_strip
    player = AudioPlayer(settings.data["audio"]["device"], runner=run_aplay if real_audio else silent_runner)
    audio_svc = AudioService(SoundLibrary(data_dir), player)
    arbiter = Arbiter(Player(link, clock), settings, clock, hub, chimes=audio_svc)
```
(replace the existing `arbiter = ...` line) and add `audio=audio_svc` to the `Services(...)` call.

In `lifespan`, start the player with the engine tasks:
```python
        if real_audio:
            await init_mixer(player.device)
        engine = [asyncio.create_task(arbiter.run()), asyncio.create_task(supervisor.run()),
                  asyncio.create_task(player.run())]
```
In `main`, add the flag and pass it:
```python
    p.add_argument("--no-audio", action="store_true", default=os.environ.get("NO_AUDIO") == "1")
    ...
    app, _ = build(Path(args.data_dir), args.address, args.fake_strip, not args.no_monitors, audio=not args.no_audio)
```

- [ ] **Step 5: Container**

`Dockerfile` — extend the apt line:
```dockerfile
 && apt-get install -y --no-install-recommends arp-scan smartmontools iputils-ping tzdata alsa-utils mpg123 \
```
`docker-compose.yml` — add the sound devices under `devices:`:
```yaml
    devices:
      - /dev/nvme0:/dev/nvme0
      - /dev/snd:/dev/snd
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (only the pre-existing Starlette TestClient deprecation warning)

- [ ] **Step 7: Commit**

```bash
git add led_notify/web/api.py led_notify/app.py Dockerfile docker-compose.yml tests/test_api.py
git commit -m "feat: sounds API, sound on events, audio wiring and container sound access" -- led_notify/web/api.py led_notify/app.py Dockerfile docker-compose.yml tests/test_api.py
```

---

### Task 6: UI — Sound column, Audio settings, Sounds manager

**Files:**
- Modify: `led_notify/web/static/index.html`, `led_notify/web/static/app.js`, `led_notify/web/static/style.css`

**Interfaces:**
- Consumes: `GET /api/sounds` → `[{ref,name,builtin,duration_s}]`, `PUT /api/sounds/{name}` (raw body + Content-Type), `DELETE /api/sounds/{name}` → `{rules_reset}`, `POST /api/sounds/play {sound}`, rule `sound` (`null` = silent), settings `audio.*`.

- [ ] **Step 1: `index.html`**

Change the rules table header to:
```html
        <thead><tr><th>Event</th><th>On</th><th>Priority</th><th>Pattern</th><th>Sound</th><th>Duration (s)</th><th>Quiet hours</th></tr></thead>
```
After `<button id="save-settings">Save settings</button>` add:
```html
    <h2>Sounds</h2>
    <div class="card">
      <div class="row gap">
        <input type="file" id="sound-file" accept=".wav,.mp3,audio/wav,audio/mpeg">
        <button id="sound-upload">Upload</button>
        <span class="muted">WAV or MP3, up to 10 s and 2 MB</span>
      </div>
      <div class="table-wrap">
        <table id="sounds-table">
          <thead><tr><th>Sound</th><th>Length</th><th></th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
```

- [ ] **Step 2: `app.js`**

Add `sounds: []` to the `state` object:
```js
const state = { catalog: { named: [], effects: [] }, rules: {}, settings: {}, devices: [], log: [], sounds: [] };
```
Add to `FIELDS` (after the "Idle & brightness" entry):
```js
  ["Audio", [["audio.enabled", "checkbox", "Chimes enabled"], ["audio.volume_pct", "number", "Volume % (day)"],
    ["audio.night_volume_pct", "number", "Night volume % (critical only)"]]],
```
Replace `loadRulesAndSettings` with:
```js
async function loadRulesAndSettings() {
  await act(async () => {
    [state.rules, state.settings, state.sounds] = await Promise.all([
      api("GET", "/api/rules"), api("GET", "/api/settings"), api("GET", "/api/sounds")]);
    renderRules();
    renderSettings();
    renderSounds();
    renderEventButtons();
  });
}
const soundOptions = (selected) => `<option value="" ${selected ? "" : "selected"}>(silent)</option>` +
  state.sounds.map((s) => `<option value="${esc(s.ref)}" ${s.ref === selected ? "selected" : ""}>${esc(s.ref)}</option>`).join("");
```
In `renderRules`, add the Sound cell after the pattern cell:
```js
      <td><select data-k="pattern">${opts(state.catalog.named, r.pattern)}</select></td>
      <td class="nowrap"><select data-k="sound">${soundOptions(r.sound)}</select>
        <button type="button" class="link play-sound" title="Preview sound">▶</button></td>
```
In the save-rules handler, replace the value mapping line with:
```js
      r[k] = el.type === "checkbox" ? el.checked
        : k === "duration_s" ? (el.value === "" ? null : Number(el.value))
        : k === "sound" ? (el.value || null)
        : el.value;
```
Add preview, upload, list and delete handlers:
```js
async function playSound(ref) {
  if (!ref) { toast("Silent: nothing to play"); return; }
  await act(() => api("POST", "/api/sounds/play", { sound: ref }), `Playing ${ref}`);
}
$("#rules-table").addEventListener("click", (e) => {
  const b = e.target.closest(".play-sound");
  if (b) playSound($("select[data-k=sound]", b.closest("tr")).value);
});
function renderSounds() {
  $("#sounds-table tbody").innerHTML = state.sounds.map((s) => `
    <tr data-ref="${esc(s.ref)}" data-name="${esc(s.name)}">
      <td><code>${esc(s.ref)}</code>${s.builtin ? ' <span class="pill tiny">built-in</span>' : ""}</td>
      <td>${s.duration_s == null ? "?" : esc(s.duration_s) + " s"}</td>
      <td class="nowrap"><button type="button" class="link play-sound-row">▶ Play</button>
        ${s.builtin ? "" : '<button type="button" class="link sound-delete">Delete</button>'}</td>
    </tr>`).join("");
}
$("#sounds-table").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-ref]");
  if (!tr) return;
  if (e.target.classList.contains("play-sound-row")) playSound(tr.dataset.ref);
  if (e.target.classList.contains("sound-delete")) {
    if (!confirm(`Delete ${tr.dataset.ref}? Rules using it switch to "default".`)) return;
    act(async () => {
      const r = await api("DELETE", `/api/sounds/${encodeURIComponent(tr.dataset.name)}`);
      await loadRulesAndSettings();
      toast(r.rules_reset.length ? `Deleted; reset ${r.rules_reset.join(", ")} to default` : "Deleted");
    });
  }
});
$("#sound-upload").addEventListener("click", () => {
  const file = $("#sound-file").files[0];
  if (!file) { toast("Choose a WAV or MP3 file first", true); return; }
  const type = file.type || (file.name.toLowerCase().endsWith(".mp3") ? "audio/mpeg" : "audio/wav");
  act(async () => {
    const res = await fetch(`/api/sounds/${encodeURIComponent(file.name)}`, { method: "PUT", headers: { "Content-Type": type }, body: file });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
    $("#sound-file").value = "";
    await loadRulesAndSettings();
    toast(`Uploaded ${data.ref}`);
  });
});
```

- [ ] **Step 3: `style.css`**

Append:
```css
.nowrap { white-space: nowrap; }
button.link.play-sound, button.link.play-sound-row { color: var(--accent); margin-left: 6px; }
```

- [ ] **Step 4: Verify**

Run: `node --check led_notify/web/static/app.js && .venv/bin/pytest tests/test_api.py -q`
Expected: `node` prints nothing; tests PASS.
Then run locally: `.venv/bin/python -m led_notify.app --fake-strip --no-monitors --data-dir /tmp/ln-audio-dev --port 8799` (background) and check in a browser that the Rules tab shows a Sound column with `(silent)/default/success/error`, the Settings form shows an Audio box, and the Sounds section lists the three built-ins; upload a small WAV and see it appear in the list and in the Sound selects. Stop the server.

- [ ] **Step 5: Commit**

```bash
git add led_notify/web/static/index.html led_notify/web/static/app.js led_notify/web/static/style.css
git commit -m "feat: sound column, audio settings and sounds manager in the UI" -- led_notify/web/static/index.html led_notify/web/static/app.js led_notify/web/static/style.css
```

---

### Task 7: Deploy and verify on the hardware

**Files:** none (operational). Use the `operating-zima-home-server` skill for server access rules.

- [ ] **Step 1: Deploy**

Run: `./deploy.sh` from the repo root. Expected: build succeeds (the apt layer now installs `alsa-utils mpg123`), container recreated, health check JSON printed.

- [ ] **Step 2: Check migration, audio status and the strip**

```bash
curl -s http://192.168.4.61:8787/api/state | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["strip"]["connected"], s["audio"], s["warnings"])'
curl -s http://192.168.4.61:8787/api/settings | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["audio"], s["adguard"]["password"])'
curl -s http://192.168.4.61:8787/api/rules | python3 -c 'import json,sys; print({k: v["sound"] for k, v in json.load(sys.stdin).items()})'
```
Expected: strip `True`; audio status has `"last_error": ""`; `warnings` is `[]`; audio settings show the defaults; the AdGuard password still shows `{"set": true}` (settings survived); rules show the default sound table.

- [ ] **Step 3: Audible test — ask the user first**

Ask the user to confirm it's an OK time for sound (not during quiet hours or while people sleep). Then:
Play one sound per command, giving the user a few seconds to listen between them:
```bash
curl -s -X POST http://192.168.4.61:8787/api/sounds/play -H 'Content-Type: application/json' -d '{"sound":"default"}'
curl -s -X POST http://192.168.4.61:8787/api/sounds/play -H 'Content-Type: application/json' -d '{"sound":"success"}'
curl -s -X POST http://192.168.4.61:8787/api/sounds/play -H 'Content-Type: application/json' -d '{"sound":"error"}'
curl -s http://192.168.4.61:8787/api/state | python3 -c 'import json,sys; print(json.load(sys.stdin)["audio"])'
```
Expected: the user hears default (loudest), success, error from the server; `played` ≥ 3 and `last_error` empty. If `last_error` mentions the device, run `ssh zima 'sudo docker exec led-notify aplay -l'` and adjust `audio.device` via `PUT /api/settings`.

- [ ] **Step 4: Update the server skill and push**

Add a line to `~/.claude/skills/operating-zima-home-server/SKILL.md` under "Where things are": `| Chimes | internal speaker card 1 (ALC233) via /dev/snd; led-notify audio device plughw:1,0; built-ins in led_notify/sounds/, uploads in /DATA/AppData/led-notify/data/sounds; backup of finals in HomeLab/chimes/ |`. Then `git push`.
