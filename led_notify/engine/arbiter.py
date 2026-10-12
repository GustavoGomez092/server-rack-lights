"""The single authority over what the strip shows."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass

from ..events import PRIORITIES, Event
from ..patterns import BREATH_FX, NAMED, Color, Effect, PatternError, Sequence, describe, resolve
from .quiet import in_window

log = logging.getLogger(__name__)

DEDUP_S = 30.0
QUEUE_MAX = 5
DEFAULT_ONESHOT_S = 5.0
UNDEDUPED_SOURCES = ("test",)
CHIME_COOLDOWN_S = 600.0  # one chime per rule + data.key per 10 min; forced events bypass it


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
    def __init__(self, player, settings, clock, hub=None, log_size: int = 200, chimes=None):
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
        self._steady_task: asyncio.Task | None = None  # loops a Sequence used as the base look
        self._rendered_key: tuple | None = None
        self._seq = 0
        self.chimes = chimes
        self._chimed_states: set[int] = set()  # state seqs whose first-display chime is handled
        self._chimed_at: dict[str, float] = {}   # "name|key" -> monotonic time of its last chime
        self._wake = asyncio.Event()

    # ---- public API ------------------------------------------------------------
    @property
    def playing_task(self) -> asyncio.Task | None:
        return self._playing[2] if self._playing else None

    async def submit(self, ev: Event) -> str:
        if ev.kind != "clear" and ev.look is not None:
            try:
                resolve(ev.look)
            except PatternError:
                self._log(ev, "rejected:invalid_look")
                return "rejected:invalid_look"
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
        level = min(level, self.settings.data["max_brightness"])
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
        try:
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
        finally:
            await self.stop_steady()

    async def stop_steady(self) -> None:
        """Cancel the looping base sequence (if any) and wait until it has stopped sending."""
        task, self._steady_task = self._steady_task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task])

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
            await self.stop_steady()
            task = asyncio.create_task(self._play_oneshot(ev, rule, level))
            self._playing = (ev, rule, task)
            task.add_done_callback(lambda t, ev=ev: self._oneshot_done(t, ev))
            return
        if base.key != self._rendered_key:
            await self.stop_steady()
            if isinstance(base.pattern, Sequence) and base.level > 0:
                # A sequence would end dark after one pass; as a steady look it loops until replaced.
                self._steady_task = asyncio.create_task(self._loop_steady(base.pattern, base.level))
            else:
                await self.player.show(base.pattern, base.level)
            self._rendered_key = base.key
            self._set_current(base.desc, base.reason)
            if base.key[0] == "state":
                self._chime_state(base.key)

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
            "sound": ev.sound if ev.sound is not None else "default",
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

    def _chime(self, rule: dict, ev: Event, force: bool = False) -> bool:
        """Queue the rule's sound: day volume, or night volume for critical rules; never raises.

        Returns False only when quiet hours deferred the chime (so a state can chime once they end);
        True once the chime is handled: queued, skipped by the cooldown, or deliberately silent.
        """
        if self.chimes is None:
            return True
        ref = rule.get("sound")
        audio = self.settings.data["audio"]
        if not ref or not audio["enabled"]:
            return True
        if force or not self._quiet():
            volume = audio["volume_pct"]
        elif rule["priority"] == "critical":
            volume = audio["night_volume_pct"]
        else:
            return False
        if not force:
            now = self.clock.monotonic()
            key = f"{ev.name}|{ev.data.get('key', '')}"
            last = self._chimed_at.get(key)
            if last is not None and now - last < CHIME_COOLDOWN_S:
                return True
            self._chimed_at = {k: t for k, t in self._chimed_at.items() if now - t < CHIME_COOLDOWN_S}
            self._chimed_at[key] = now
        try:
            self.chimes.chime(ref, volume)
        except Exception:
            log.exception("chime %s failed", ref)
        return True

    def _chime_state(self, key: tuple) -> None:
        """A state chimes the first time it is displayed outside quiet silence, never again while active."""
        _, name, seq, *_ = key
        if seq in self._chimed_states:
            return
        st = self._states.get(name)
        if st is None or st.seq != seq:
            return
        if self._chime(self._live_rule(st), st.event):
            self._chimed_states.add(seq)

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
        self._chimed_states.discard(st.seq)
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
        if idle["mode"] == "breath":
            fx, speed = BREATH_FX[idle["breath_color"]], idle["breath_speed"]
            return Look(("idle", "breath", fx, speed, level), Effect(fx, speed), level, PRIORITIES["idle"],
                        "idle breath", "idle")
        rgb = tuple(idle["color"])
        return Look(("idle", "glow", rgb, level), Color(rgb), level, PRIORITIES["idle"], "idle glow", "idle")

    async def _loop_steady(self, pattern: Sequence, level: int) -> None:
        while True:
            try:
                await self.player.show(pattern, level)
            except Exception:
                log.exception("steady sequence failed")
                await self.clock.sleep(1.0)

    async def _play_oneshot(self, ev: Event, rule: dict, level: int) -> None:
        try:
            pattern = self._pattern_of(rule)
            self._log(ev, "shown")
            # UI Test clicks always chime at day volume and skip the cooldown (spec §5), forced or not.
            self._chime(rule, ev, force=ev.force or ev.source == "test")
            self._set_current(self._desc_of(rule), f"{ev.name} ({ev.source})")
            await self.player.show(pattern, level)
            if not isinstance(pattern, Sequence):
                await self.clock.sleep(rule.get("duration_s") or ev.duration_s or DEFAULT_ONESHOT_S)
        except Exception:
            log.exception("one-shot %s failed", ev.name)

    def _oneshot_done(self, task: asyncio.Task, ev: Event) -> None:
        """Runs even if the task was cancelled before it ever started."""
        if task.cancelled():
            self._log(ev, "preempted")
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
