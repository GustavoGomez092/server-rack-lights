"""Devices seen on the LAN, keyed by lower-case MAC."""
from __future__ import annotations

from pathlib import Path

from .jsonfile import load_json, move_aside, save_json

PATCHABLE = {"name": str, "known": bool, "announce_arrival": bool}
EMPTY = {"baseline_done": False, "devices": {}}


class DeviceStore:
    def __init__(self, path: Path, hub=None):
        self.path = Path(path)
        self.hub = hub
        raw, self.warning = load_json(self.path, EMPTY)
        if self.warning is None and not (isinstance(raw, dict) and isinstance(raw.get("devices"), dict)):
            backup = move_aside(self.path)
            self.warning = f"devices.json had an unexpected shape; saved as {backup}, starting empty"
            raw = {"baseline_done": False, "devices": {}}
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
        updated = {**rec, **fields}
        self._commit({**self._raw, "devices": {**self._raw["devices"], mac.lower(): updated}})
        return updated

    def delete(self, mac: str) -> bool:
        key = mac.lower()
        if key not in self._raw["devices"]:
            return False
        devices = {k: v for k, v in self._raw["devices"].items() if k != key}
        self._commit({**self._raw, "devices": devices})
        return True

    def save(self, publish: bool = True) -> None:
        save_json(self.path, self._raw)
        if publish:
            self._publish()

    def _commit(self, raw: dict) -> None:
        """Persist the new state first; only then replace the in-memory state."""
        save_json(self.path, raw)
        self._raw = raw
        self._publish()

    def _publish(self) -> None:
        if self.hub:
            self.hub.publish("devices", None)
