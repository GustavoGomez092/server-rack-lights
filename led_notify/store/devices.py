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
