"""LAN device discovery via arp-scan."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from ..engine.quiet import in_window
from ..events import Event
from .base import PollingMonitor

_LINE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})[ \t]*(.*)$", re.M)
_DUP = re.compile(r"\s*\(DUP: \d+\)$")


def parse_arp_scan(text: str) -> list[tuple[str, str, str]]:
    seen: dict[str, tuple[str, str, str]] = {}
    for ip, mac, vendor in _LINE.findall(text):
        mac = mac.lower()
        if mac not in seen:
            seen[mac] = (ip, mac, _DUP.sub("", vendor).strip())
    return list(seen.values())


async def arp_scan(interface: str) -> list[tuple[str, str, str]]:
    proc = await asyncio.create_subprocess_exec(
        "arp-scan", "--localnet", "--plain", "--retry=2", f"--interface={interface}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"arp-scan failed: {err.decode(errors='replace').strip()[:200]}")
    return parse_arp_scan(out.decode(errors="replace"))


def is_private_mac(mac: str) -> bool:
    """Locally-administered bit set: randomized 'private' Wi-Fi address."""
    return bool(int(mac[:2], 16) & 0x02)


class DeviceTracker:
    def __init__(self, store):
        self.store = store

    def observe(self, results, now_wall: datetime, cfg: dict) -> list[Event]:
        ts = now_wall.timestamp()
        iso = now_wall.isoformat(timespec="seconds")
        baseline = not self.store.baseline_done
        events: list[Event] = []
        for ip, mac, vendor in results:
            rec = self.store.get(mac)
            if rec is None:
                rec = {"mac": mac, "ip": ip, "vendor": vendor, "name": "", "known": False,
                       "announce_arrival": False, "private_mac": is_private_mac(mac),
                       "first_seen": iso, "last_seen": iso, "last_seen_ts": ts}
                if not baseline:
                    late = in_window(now_wall, cfg["late_start"], cfg["late_end"])
                    events.append(Event("device.new_late" if late else "device.new", "oneshot", source="devices",
                                        data={"key": mac, "mac": mac, "ip": ip, "vendor": vendor}))
            else:
                away_s = ts - rec.get("last_seen_ts", ts)
                if rec.get("announce_arrival") and away_s > cfg["arrival_absent_min"] * 60:
                    events.append(Event("device.known_arrived", "oneshot", source="devices",
                                        data={"key": mac, "mac": mac, "name": rec.get("name") or mac}))
                rec.update(ip=ip, last_seen=iso, last_seen_ts=ts)
                if vendor:
                    rec["vendor"] = vendor
            self.store.upsert(rec)
        if baseline and results:
            self.store.mark_baseline()
        if results:
            self.store.save()
        return events


class DeviceMonitor(PollingMonitor):
    name = section = "devices"

    def __init__(self, settings, clock, store, scan=arp_scan):
        super().__init__(settings, clock)
        self.scan = scan
        self.tracker = DeviceTracker(store)

    async def check(self) -> list[Event]:
        results = await self.scan(self.cfg["interface"])
        return self.tracker.observe(results, self.clock.now(), self.cfg)
