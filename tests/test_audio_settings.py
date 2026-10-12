import copy
import json

import pytest

from led_notify.audio.refs import is_valid_ref
from led_notify.events import Event
from led_notify.store.settings import SettingsError, SettingsStore


def test_is_valid_ref():
    assert all(is_valid_ref(r) for r in ("default", "success", "error", "custom:my-chime_1"))
    assert not any(is_valid_ref(r) for r in (None, "", "nope", "custom:", "custom:Bad Name", "custom:" + "a" * 41, 3))
    assert is_valid_ref("custom:a\n") is False                  # no trailing newline sneaking past the regex


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
