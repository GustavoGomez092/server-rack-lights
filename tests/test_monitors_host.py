import pytest

from led_notify.monitors.disk import DiskMonitor, disk_usage_pct, parse_smart
from led_notify.monitors.thermal import ThermalMonitor, read_hwmon_temp


def names(events):
    return [(e.name, e.kind) for e in events]


# ---- disk -------------------------------------------------------------------------
def test_parse_smart():
    assert parse_smart("SMART overall-health self-assessment test result: PASSED") is True
    assert parse_smart("SMART overall-health self-assessment test result: FAILED!") is False
    assert parse_smart("SMART Health Status: OK") is True
    assert parse_smart("Smartctl open device: /dev/nvme0 failed: Permission denied") is None


def test_disk_usage_pct_is_a_percentage(tmp_path):
    assert 0.0 <= disk_usage_pct(str(tmp_path)) <= 100.0


def disk_monitor(settings, clock, usage_by_path, verdicts):
    async def smart(device):
        return verdicts.pop(0)
    return DiskMonitor(settings, clock, usage=lambda p: usage_by_path[p], smart=smart)


async def test_disk_full_hysteresis(settings, clock):
    usage = {"/host/DATA": 91.0}
    m = disk_monitor(settings, clock, usage, [True])
    evs = await m.check()
    assert names(evs) == [("disk.full", "start")] and evs[0].data == {"path": "/host/DATA", "pct": 91.0}
    usage["/host/DATA"] = 88.0
    assert m.check_usage() == []
    usage["/host/DATA"] = 84.0
    assert names(m.check_usage()) == [("disk.full", "clear")]


async def test_disk_uses_worst_path(settings, clock):
    settings.update({"disk": {"paths": ["/a", "/b"]}})
    m = disk_monitor(settings, clock, {"/a": 10.0, "/b": 95.0}, [True])
    assert (await m.check())[0].data["path"] == "/b"


async def test_smart_runs_hourly_and_reports_failures(settings, clock):
    m = disk_monitor(settings, clock, {"/host/DATA": 10.0}, [True, False, True])
    assert await m.check() == []
    clock.advance(300)
    assert await m.check() == []            # SMART not due yet
    clock.advance(3600)
    assert names(await m.check()) == [("disk.smart_error", "start")]
    clock.advance(3600)
    assert names(await m.check()) == [("disk.smart_error", "clear")]


async def test_smart_without_verdict_is_a_monitor_error(settings, clock):
    m = disk_monitor(settings, clock, {"/host/DATA": 10.0}, [None])
    with pytest.raises(RuntimeError, match="no health verdict"):
        await m.check()


async def test_disk_interval_is_usage_interval(settings, clock):
    assert disk_monitor(settings, clock, {}, []).interval_s() == 300


# ---- thermal ----------------------------------------------------------------------
def make_hwmon(root, idx, name, milli):
    d = root / f"hwmon{idx}"
    d.mkdir()
    (d / "name").write_text(name + "\n")
    (d / "temp1_input").write_text(f"{milli}\n")


def test_read_hwmon_temp_picks_cpu_sensor(tmp_path):
    make_hwmon(tmp_path, 0, "nvme", 44850)
    make_hwmon(tmp_path, 1, "k10temp", 43125)
    assert read_hwmon_temp(["k10temp", "coretemp"], root=str(tmp_path)) == 43.125


def test_read_hwmon_temp_without_sensor_raises(tmp_path):
    make_hwmon(tmp_path, 0, "nvme", 44850)
    with pytest.raises(RuntimeError, match="no CPU temperature sensor"):
        read_hwmon_temp(["k10temp"], root=str(tmp_path))


async def test_cpu_hot_after_two_minutes_and_clears_below_75(settings, clock):
    temps = [86.0]
    m = ThermalMonitor(settings, clock, read=lambda names: temps[0])
    assert await m.check() == []
    clock.advance(120)
    evs = await m.check()
    assert names(evs) == [("cpu.hot", "start")] and evs[0].data == {"temp_c": 86.0}
    temps[0] = 74.0
    assert names(await m.check()) == [("cpu.hot", "clear")]
