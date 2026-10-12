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
