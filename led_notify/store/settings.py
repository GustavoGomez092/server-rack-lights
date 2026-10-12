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
