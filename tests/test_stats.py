import pytest

from led_notify.stats.apps import AppMeter, friendly_name
from led_notify.stats.host import cpu_pct, cpu_times, disk, memory
from led_notify.stats.sampler import StatsSampler

GB = 1024 ** 3


def test_cpu_pct(tmp_path):
    stat = tmp_path / "stat"
    stat.write_text("cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 2 3\n")
    a = cpu_times(str(stat))
    assert a == (200, 1000)
    stat.write_text("cpu  250 0 150 1000 200 0 0 0 0 0\n")
    b = cpu_times(str(stat))
    assert cpu_pct(a, b) == pytest.approx(100 * 200 / 600)
    assert cpu_pct(b, b) == 0.0


def test_memory_used(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal:       15278080 kB\nMemFree:         1961984 kB\nMemAvailable:    5076992 kB\n")
    m = memory(str(p))
    assert m == {"total_bytes": 15278080 * 1024, "used_bytes": (15278080 - 5076992) * 1024}


def test_disk_mounted(tmp_path, monkeypatch):
    monkeypatch.setattr("led_notify.stats.host.os.path.ismount", lambda p: True)
    d = disk("Main", str(tmp_path))
    assert d["label"] == "Main" and d["mounted"] is True
    assert 0 < d["used_bytes"] <= d["total_bytes"] and 0 <= d["pct"] <= 100


def test_disk_not_mounted(tmp_path):
    d = disk("Backup", str(tmp_path / "missing"))
    assert d == {"label": "Backup", "mounted": False, "used_bytes": None, "total_bytes": None, "pct": None}
    d = disk("Backup", str(tmp_path))  # exists but nothing is mounted there
    assert d["mounted"] is False and d["pct"] is None


def test_friendly_names():
    assert friendly_name("paperclip") == "Paperclip"
    assert friendly_name("qbittorrent") == "qBittorrent"
    assert friendly_name("adguard-home") == "AdGuard"
    assert friendly_name("jslab-prod-66b8385") == "JSLab site"
    assert friendly_name("jslab-staging-f8c3ad9") == "JSLab staging"
    assert friendly_name("calibre-web-automated") == "Calibre-Web"
    assert friendly_name("sonarr") == "Sonarr"
    assert friendly_name("some_new-thing") == "Some New Thing"


def _scope(root, cid, usage_usec, current, inactive):
    d = root / "system.slice" / f"docker-{cid}.scope"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cpu.stat").write_text(f"usage_usec {usage_usec}\nuser_usec 1\nsystem_usec 1\n")
    (d / "memory.current").write_text(f"{current}\n")
    (d / "memory.stat").write_text(f"anon 5\nfile 7\ninactive_file {inactive}\nactive_file 3\n")


async def test_apps_top_by_cpu(tmp_path, clock):
    containers = [{"id": "aaa", "name": "jellyfin"}, {"id": "bbb", "name": "paperclip"}]

    async def lister():
        return containers

    _scope(tmp_path, "aaa", 1_000_000, 2 * GB, GB // 2)
    _scope(tmp_path, "bbb", 1_000_000, GB, 0)
    meter = AppMeter(str(tmp_path), lister, clock, ncpu=8)
    first = await meter.sample()
    assert {a["name"] for a in first} == {"Jellyfin", "Paperclip"}
    assert all(a["cpu_pct"] == 0.0 for a in first)
    clock.advance(2)
    _scope(tmp_path, "aaa", 1_000_000 + 400_000, 2 * GB, GB // 2)   # 0.2 core over 2 s
    _scope(tmp_path, "bbb", 1_000_000 + 8_000_000, GB, 0)          # 4 cores over 2 s
    apps = await meter.sample()
    assert [a["name"] for a in apps] == ["Paperclip", "Jellyfin"]
    assert apps[0]["cpu_pct"] == pytest.approx(50.0)
    assert apps[1]["cpu_pct"] == pytest.approx(2.5)
    assert apps[1]["mem_bytes"] == 2 * GB - GB // 2


async def test_apps_skip_vanished_container(tmp_path, clock):
    async def lister():
        return [{"id": "aaa", "name": "hermes"}, {"id": "gone", "name": "browser"}]

    _scope(tmp_path, "aaa", 10, GB, 0)
    meter = AppMeter(str(tmp_path), lister, clock, ncpu=8)
    assert [a["name"] for a in await meter.sample()] == ["Hermes"]


async def test_apps_list_cached_30s(tmp_path, clock):
    calls = []

    async def lister():
        calls.append(1)
        return []

    meter = AppMeter(str(tmp_path), lister, clock, ncpu=8)
    await meter.sample()
    clock.advance(10)
    await meter.sample()
    assert len(calls) == 1
    clock.advance(25)
    await meter.sample()
    assert len(calls) == 2


async def test_apps_lister_failure_keeps_last_list(tmp_path, clock):
    state = {"fail": False}

    async def lister():
        if state["fail"]:
            raise OSError("docker socket gone")
        return [{"id": "aaa", "name": "hermes"}]

    _scope(tmp_path, "aaa", 10, GB, 0)
    meter = AppMeter(str(tmp_path), lister, clock, ncpu=8)
    await meter.sample()
    state["fail"] = True
    clock.advance(31)
    assert [a["name"] for a in await meter.sample()] == ["Hermes"]


async def test_sampler_survives_part_failure(clock):
    class Apps:
        async def sample(self):
            return [{"name": f"App{i}", "cpu_pct": float(10 - i), "mem_bytes": i} for i in range(7)]

    def broken():
        raise OSError("no sensor")

    s = StatsSampler(clock, Apps(), disks=[], read_cpu=lambda: (0, 0), read_mem=lambda: {"used_bytes": 1, "total_bytes": 2},
                     read_cpu_temp=broken, read_drive_temp=lambda: 47.85, read_uptime=lambda: 100.0)
    await s.sample_once()
    latest = s.latest
    assert latest["temps"] == {"cpu_c": None, "drive_c": 47.9}
    assert latest["memory"] == {"used_bytes": 1, "total_bytes": 2}
    assert [a["name"] for a in latest["apps"]] == ["App0", "App1", "App2", "App3", "App4"]
    assert latest["uptime_s"] == 100.0 and latest["cpu_pct"] == 0.0
