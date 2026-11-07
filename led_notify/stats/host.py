"""Whole-machine numbers from /proc and statvfs (not namespaced, so the container sees the host's)."""
from __future__ import annotations

import os


def cpu_times(path: str = "/proc/stat") -> tuple[int, int]:
    """(busy, total) jiffies from the aggregate "cpu" line; guest time is already inside user/nice."""
    with open(path) as f:
        fields = [int(x) for x in f.readline().split()[1:9]]
    total = sum(fields)
    idle = fields[3] + fields[4]
    return total - idle, total


def cpu_pct(prev: tuple[int, int], cur: tuple[int, int]) -> float:
    d_total = cur[1] - prev[1]
    return 100.0 * (cur[0] - prev[0]) / d_total if d_total > 0 else 0.0


def memory(path: str = "/proc/meminfo") -> dict:
    """Used memory as `free` reports it: MemTotal - MemAvailable."""
    kb = {}
    with open(path) as f:
        for line in f:
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                kb[key] = int(rest.split()[0])
    return {"total_bytes": kb["MemTotal"] * 1024, "used_bytes": (kb["MemTotal"] - kb["MemAvailable"]) * 1024}


def uptime_s(path: str = "/proc/uptime") -> float:
    with open(path) as f:
        return float(f.read().split()[0])


def disk(label: str, path: str) -> dict:
    """Size and use like `df`; a drive that is not mounted (e.g. the USB backup unplugged) has no numbers."""
    if not os.path.ismount(path):
        return {"label": label, "mounted": False, "used_bytes": None, "total_bytes": None, "pct": None}
    st = os.statvfs(path)
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    usable = used + st.f_bavail * st.f_frsize  # reserved blocks count as unavailable, as in df's Use%
    return {"label": label, "mounted": True, "used_bytes": used, "total_bytes": st.f_blocks * st.f_frsize,
            "pct": round(100.0 * used / usable, 1) if usable else 0.0}
