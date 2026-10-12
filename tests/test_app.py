import time

import httpx
from fastapi.testclient import TestClient

from led_notify import smoke
from led_notify.app import build, build_monitors
from led_notify.patterns import NAMED
from led_notify.store.devices import DeviceStore
from led_notify.store.settings import SettingsStore
from led_notify.driver import link as link_mod
from tests.fakes import FakeClock
from tests.test_link import FakeBleStrip


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
    svc.settings.update({"quiet_hours": {"enabled": False}})  # the real wall clock may be in quiet hours
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


def test_shutdown_disconnects_the_strip(tmp_path, monkeypatch):
    made = []

    def factory(address):
        made.append(FakeBleStrip(fail=False))
        return made[-1]

    async def no_release(address):
        pass

    monkeypatch.setattr(link_mod, "_default_factory", factory)
    monkeypatch.setattr(link_mod, "bluez_release", no_release)
    app, svc = build(tmp_path / "data", "A6:15:50:62:0A:9D", fake_strip=False, monitors=False)
    with TestClient(app):
        wait_for(lambda: svc.link.status()["connected"])
    assert made and made[-1].client.is_connected is False  # lifespan shutdown -> Strip.disconnect()


def test_audio_device_follows_settings_without_restart(tmp_path):
    _, svc = build(tmp_path / "data", "", fake_strip=True, monitors=False)
    assert svc.audio.status()["device"] == "plughw:1,0" and svc.audio.status()["mode"] == "silent"
    svc.settings.update({"audio": {"device": "plughw:2,0"}})
    assert svc.audio.player.device == "plughw:2,0"
