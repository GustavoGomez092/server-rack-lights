import pytest

from led_notify.monitors.devices import DeviceMonitor, DeviceTracker, is_private_mac, parse_arp_scan
from led_notify.hub import Hub
from led_notify.store.devices import DeviceStore

PHONE = ("192.168.0.20", "da:11:22:33:44:55", "(Unknown: locally administered)")
ROUTER = ("192.168.0.1", "a4:2b:b0:00:00:01", "TP-LINK")
TV = ("192.168.0.30", "00:1c:c2:9f:4e:37", "Roku")


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / "devices.json")


def names(events):
    return [e.name for e in events]


def test_parse_arp_scan_with_header_and_dups():
    text = (
        "Interface: eth0, type: EN10MB, MAC: 00:11:22:33:44:55, IPv4: 192.168.0.61\n"
        "Starting arp-scan 1.10.0 with 256 hosts\n"
        "192.168.0.1\tA4:2B:B0:00:00:01\tTP-LINK TECHNOLOGIES CO.,LTD.\n"
        "192.168.0.20\tda:11:22:33:44:55\t(Unknown: locally administered)\n"
        "192.168.0.1\ta4:2b:b0:00:00:01\tTP-LINK TECHNOLOGIES CO.,LTD. (DUP: 2)\n"
        "\n3 packets received by filter, 0 packets dropped by kernel\n"
    )
    assert parse_arp_scan(text) == [
        ("192.168.0.1", "a4:2b:b0:00:00:01", "TP-LINK TECHNOLOGIES CO.,LTD."),
        ("192.168.0.20", "da:11:22:33:44:55", "(Unknown: locally administered)"),
    ]


def test_is_private_mac():
    assert is_private_mac("da:11:22:33:44:55") is True
    assert is_private_mac("a4:2b:b0:00:00:01") is False


def test_first_scan_is_a_silent_baseline(store, clock, settings):
    t = DeviceTracker(store)
    assert t.observe([ROUTER, PHONE], clock.now(), settings.data["devices"]) == []
    assert store.baseline_done and len(store.all()) == 2
    assert store.get(PHONE[1])["private_mac"] is True


def test_empty_first_scan_does_not_complete_baseline(store, clock, settings):
    t = DeviceTracker(store)
    assert t.observe([], clock.now(), settings.data["devices"]) == []
    assert store.baseline_done is False
    assert t.observe([ROUTER], clock.now(), settings.data["devices"]) == []


def test_new_device_alerts_once(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([ROUTER], clock.now(), cfg)
    evs = t.observe([ROUTER, TV], clock.now(), cfg)
    assert names(evs) == ["device.new"]
    assert evs[0].data == {"key": TV[1], "mac": TV[1], "ip": TV[0], "vendor": "Roku"}
    clock.advance(60)
    assert t.observe([ROUTER, TV], clock.now(), cfg) == []


def test_new_device_late_at_night(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([ROUTER], clock.now(), cfg)
    clock.set_time("01:00")
    assert names(t.observe([ROUTER, TV], clock.now(), cfg)) == ["device.new_late"]


def test_known_arrival_needs_30_minutes_away(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([ROUTER, PHONE], clock.now(), cfg)
    store.patch(PHONE[1], {"name": "Gus phone", "known": True, "announce_arrival": True})
    clock.advance(120)                      # missed one scan, back 2 min later
    assert t.observe([ROUTER, PHONE], clock.now(), cfg) == []
    clock.advance(31 * 60)                  # gone 31 min
    t.observe([ROUTER], clock.now(), cfg)
    evs = t.observe([ROUTER, PHONE], clock.now(), cfg)
    assert names(evs) == ["device.known_arrived"] and evs[0].data["name"] == "Gus phone"


def test_arrival_ignored_without_announce_flag(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([PHONE], clock.now(), cfg)
    clock.advance(3600)
    assert t.observe([PHONE], clock.now(), cfg) == []


def test_observe_updates_ip_and_last_seen(store, clock, settings):
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([TV], clock.now(), cfg)
    clock.advance(60)
    t.observe([("192.168.0.31", TV[1], "")], clock.now(), cfg)
    rec = store.get(TV[1])
    assert rec["ip"] == "192.168.0.31" and rec["vendor"] == "Roku"
    assert rec["last_seen_ts"] == clock.now().timestamp()


async def test_device_monitor_uses_configured_interface(store, clock, settings):
    seen = []

    async def scan(interface):
        seen.append(interface)
        return [ROUTER]

    m = DeviceMonitor(settings, clock, store, scan=scan)
    assert await m.check() == []
    assert seen == ["eth0"]


# ---- final fixes: publish only on change (F5) ------------------------------------
def drain(q):
    kinds = []
    while not q.empty():
        kinds.append(q.get_nowait()[0])
    return kinds


def test_unchanged_scan_saves_last_seen_without_publishing(tmp_path, clock, settings):
    hub = Hub()
    q = hub.subscribe(maxsize=100)
    store = DeviceStore(tmp_path / "devices.json", hub)
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([ROUTER, TV], clock.now(), cfg)  # baseline
    assert drain(q) == ["devices"]
    clock.advance(60)
    t.observe([ROUTER, TV], clock.now(), cfg)
    assert drain(q) == []
    assert DeviceStore(tmp_path / "devices.json").get(TV[1])["last_seen_ts"] == clock.now().timestamp()


@pytest.mark.parametrize("scan", [
    [ROUTER, ("192.168.0.31", TV[1], "Roku")],  # ip changed
    [ROUTER, (TV[0], TV[1], "Roku Inc")],  # vendor changed
    [ROUTER, TV, PHONE],  # new device
])
def test_changed_scan_publishes(tmp_path, clock, settings, scan):
    hub = Hub()
    q = hub.subscribe(maxsize=100)
    store = DeviceStore(tmp_path / "devices.json", hub)
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([ROUTER, TV], clock.now(), cfg)
    drain(q)
    clock.advance(60)
    t.observe(scan, clock.now(), cfg)
    assert drain(q) == ["devices"]


def test_device_back_online_publishes(tmp_path, clock, settings):
    hub = Hub()
    q = hub.subscribe(maxsize=100)
    store = DeviceStore(tmp_path / "devices.json", hub)
    cfg = settings.data["devices"]
    t = DeviceTracker(store)
    t.observe([ROUTER, TV], clock.now(), cfg)
    clock.advance(600)
    t.observe([ROUTER], clock.now(), cfg)
    drain(q)
    clock.advance(60)
    t.observe([ROUTER, TV], clock.now(), cfg)  # TV was away 11 min > 3 scan intervals
    assert drain(q) == ["devices"]
