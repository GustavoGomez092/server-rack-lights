from datetime import datetime, timedelta, timezone

import pytest

from led_notify.monitors.adguard import COOLDOWN_POLLS, AdguardMonitor, SpikeDetector, parse_querylog_times
from led_notify.monitors.base import MonitorDisabled
from led_notify.monitors.docker import DockerDeathFilter, DockerMonitor


def dev(action, name="web", code=None, type_="container"):
    attrs = {"name": name}
    if code is not None:
        attrs["exitCode"] = str(code)
    return {"Type": type_, "Action": action, "Actor": {"ID": "abc", "Attributes": attrs}}


# ---- docker -----------------------------------------------------------------------
def test_crash_is_reported():
    ev = DockerDeathFilter().feed(dev("die", code=1), 0, 15)
    assert ev.name == "container.died" and ev.data == {"key": "web", "container": "web", "exit_code": 1}


def test_clean_exit_and_other_events_are_ignored():
    f = DockerDeathFilter()
    assert f.feed(dev("die", code=0), 0, 15) is None
    assert f.feed(dev("start"), 0, 15) is None
    assert f.feed(dev("exec_start: sh"), 0, 15) is None
    assert f.feed(dev("die", code=1, type_="network"), 0, 15) is None


def test_intentional_stop_is_ignored_but_old_stop_is_not():
    f = DockerDeathFilter()
    f.feed(dev("kill"), 100, 15)
    assert f.feed(dev("die", code=137), 105, 15) is None
    f.feed(dev("stop"), 200, 15)
    assert f.feed(dev("die", code=137), 230, 15) is not None


async def test_docker_monitor_emits_crashes_then_errors_when_stream_ends(settings, clock):
    async def stream(socket):
        assert socket == "/var/run/docker.sock"
        yield dev("start")
        yield dev("die", code=1)

    got = []

    async def emit(e):
        got.append(e)

    with pytest.raises(RuntimeError, match="ended"):
        await DockerMonitor(settings, clock, stream=stream).run(emit)
    assert [(e.name, e.data["container"]) for e in got] == [("container.died", "web")]


# ---- adguard ----------------------------------------------------------------------
def test_parse_querylog_times_handles_nanoseconds_and_z():
    times = parse_querylog_times({"data": [
        {"time": "2026-10-11T22:41:03.123456789-07:00"},
        {"time": "2026-10-12T05:41:00Z"},
    ]})
    assert times[0] == datetime(2026, 10, 11, 22, 41, 3, 123456, tzinfo=timezone(timedelta(hours=-7)))
    assert times[1] == datetime(2026, 10, 12, 5, 41, 0, tzinfo=timezone.utc)
    assert parse_querylog_times({}) == []


def test_spike_detector():
    s = SpikeDetector()
    assert [s.feed(10, 50, 3) for _ in range(5)] == [False] * 5  # warm-up: baseline of 10
    assert s.feed(200, 50, 3) is True       # 200 > 50 and > 3 x 10
    assert s.feed(40, 50, 3) is False       # below min_blocked (and in cooldown)


def test_sustained_elevated_rate_fires_once_then_quiets():
    s = SpikeDetector()
    for _ in range(5):
        s.feed(10, 50, 3)
    fired = [s.feed(200, 50, 3) for _ in range(60)]  # the rate jumps and stays there for an hour
    assert fired[0] is True and fired.count(True) == 1  # cooldown samples made 200 the new baseline


def test_distinct_spike_after_cooldown_fires_again():
    s = SpikeDetector()
    for _ in range(5):
        s.feed(10, 50, 3)
    assert s.feed(200, 50, 3) is True
    assert [s.feed(10, 50, 3) for _ in range(COOLDOWN_POLLS - 1)] == [False] * (COOLDOWN_POLLS - 1)
    assert s.feed(200, 50, 3) is False      # still inside the cooldown
    assert s.feed(10, 50, 3) is False       # cooldown over; back to normal
    assert s.feed(200, 50, 3) is True       # a new, separate spike alerts again
    assert s.feed(200, 50, 3) is False      # ...and starts a new cooldown


def test_spike_detector_never_fires_before_five_samples():
    s = SpikeDetector()
    assert [s.feed(1000, 50, 3) for _ in range(4)] == [False] * 4  # e.g. first polls after a restart
    assert s.feed(10, 50, 3) is False
    assert s.feed(5000, 50, 3) is True      # 5 samples now: 5000 > 3 x avg(1000 x 4, 10)


async def test_adguard_disabled_without_credentials(settings, clock):
    with pytest.raises(MonitorDisabled):
        await AdguardMonitor(settings, clock, fetch=None).check()


async def test_adguard_spike(settings, clock):
    settings.update({"adguard": {"username": "admin", "password": "pw"}})
    now = clock.now()
    burst = [now - timedelta(seconds=s) for s in range(60)] + [now - timedelta(minutes=5)]
    served = {"times": burst[-1:]}  # baseline: nothing blocked in the last minute

    async def fetch(url, user, password):
        assert (url, user, password) == ("http://127.0.0.1:3001", "admin", "pw")
        return served["times"]

    m = AdguardMonitor(settings, clock, fetch=fetch)
    for _ in range(5):
        assert await m.check() == []
    served["times"] = burst
    evs = await m.check()
    assert [e.name for e in evs] == ["adguard.block_spike"] and evs[0].data == {"blocked_last_min": 60}


async def test_adguard_first_poll_after_restart_does_not_alert(settings, clock):
    settings.update({"adguard": {"username": "admin", "password": "pw"}})
    now = clock.now()

    async def fetch(url, user, password):
        return [now - timedelta(seconds=s) for s in range(60)]

    assert await AdguardMonitor(settings, clock, fetch=fetch).check() == []
