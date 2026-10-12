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


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>LED Notify</title>" in r.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
