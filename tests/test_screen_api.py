import pytest
from fastapi.testclient import TestClient

from led_notify.app import build
from led_notify.driver.fake import FakeStrip
from led_notify.engine.arbiter import Arbiter
from led_notify.engine.player import Player
from led_notify.events import Event
from led_notify.hub import Hub
from led_notify.screen.labels import label
from led_notify.screen.schedule import ScreenSchedule
from led_notify.store.devices import DeviceStore
from led_notify.store.settings import SettingsStore
from led_notify.web.api import Services, create_app
from tests.fakes import FakeClock, settle


class Stats:
    latest = None


@pytest.fixture
def svc(tmp_path):
    clock = FakeClock()
    hub = Hub()
    settings = SettingsStore(tmp_path / "settings.json", hub)
    screen = ScreenSchedule(settings, clock)
    arbiter = Arbiter(Player(FakeStrip(), clock), settings, clock, hub, on_shown=screen.note_alert)
    return Services(arbiter=arbiter, settings=settings, devices=DeviceStore(tmp_path / "devices.json", hub),
                    supervisor=None, link=FakeStrip(), hub=hub, clock=clock, screen=screen, stats=Stats(),
                    hostname="GomezPalacios")


@pytest.fixture
def client(svc):
    return TestClient(create_app(svc))


def test_labels():
    assert label("internet.down") == "Internet down"
    assert label("device.new_late") == "Unknown device joined"
    assert label("backup.done") == "Backup done"


def test_screen_endpoint_power(client, svc):
    svc.clock.set_time("23:30")
    assert client.get("/api/screen").json()["power"] == "off"
    svc.clock.set_time("12:00")
    body = client.get("/api/screen").json()
    assert (body["power"], body["theme"]) == ("on", "light")


def test_screen_data_shape(client):
    body = client.get("/api/screen/data").json()
    assert set(body) == {"screen", "stats", "banner", "host", "now"}
    assert body["stats"] is None and body["banner"] == [] and body["host"] == {"name": "GomezPalacios"}


async def test_banner_from_active_state_and_recent_alert(client, svc):
    await svc.arbiter.submit(Event("internet.slow", "start"))
    await settle(svc.arbiter)
    await svc.arbiter.submit(Event("device.new_late", data={"key": "x"}))
    await settle(svc.arbiter)
    banner = client.get("/api/screen/data").json()["banner"]
    assert [b["text"] for b in banner] == ["Internet slow", "Unknown device joined"]
    svc.clock.advance(6 * 60)  # recent alert older than wake_min drops off; the active state stays
    assert [b["text"] for b in client.get("/api/screen/data").json()["banner"]] == ["Internet slow"]


async def test_night_alert_wakes_screen(client, svc):
    svc.clock.set_time("02:00")
    await svc.arbiter.submit(Event("internet.down", "start"))
    await settle(svc.arbiter)
    body = client.get("/api/screen").json()
    assert (body["power"], body["theme"]) == ("on", "dark")


def test_screen_page_removed(client):
    # The rack screen is a native program now (display/screen); the browser page is gone.
    assert client.get("/screen/").status_code == 404


def test_build_wires_screen(tmp_path):
    app, svc = build(tmp_path / "data", "", fake_strip=True, monitors=False)
    assert svc.arbiter.on_shown == svc.screen.note_alert
    with TestClient(app) as c:
        assert c.get("/api/screen").status_code == 200


async def test_banner_priority_for_webhook_alert_without_rule(client, svc):
    await svc.arbiter.submit(Event("backup.failed", priority="high", look={"pattern": "white_flash"}))
    await settle(svc.arbiter)
    banner = client.get("/api/screen/data").json()["banner"]
    assert banner == [{"text": "Backup failed", "since": banner[0]["since"], "priority": "high"}]


def test_brightness_slider_previews_its_look(client, svc):
    svc.clock.set_time("12:00")
    client.put("/api/settings", json={"screen": {"brightness_night": 40}})
    body = client.get("/api/screen").json()
    assert (body["theme"], body["brightness"]) == ("dark", 40)
    svc.clock.advance(11)
    assert client.get("/api/screen").json()["theme"] == "light"


def test_unchanged_or_other_settings_do_not_preview(client, svc):
    svc.clock.set_time("12:00")
    settings = client.get("/api/settings").json()
    client.put("/api/settings", json={"screen": settings["screen"]})  # the Save button resends every field
    client.put("/api/settings", json={"screen": {"wake_min": 7}})
    assert client.get("/api/screen").json()["theme"] == "light"


def test_brightness_change_without_screen_still_saves(svc):
    svc.screen = None
    r = TestClient(create_app(svc)).put("/api/settings", json={"screen": {"brightness_day": 50}})
    assert r.status_code == 200 and r.json()["screen"]["brightness_day"] == 50
