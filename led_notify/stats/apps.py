"""Per-app CPU and memory from the host's cgroup v2 files, named the way the owner knows the apps."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

LIST_TTL_S = 30.0
NAMES = {
    "paperclip": "Paperclip", "jellyfin": "Jellyfin", "qbittorrent": "qBittorrent", "adguard-home": "AdGuard",
    "hermes": "Hermes", "browser": "Brand browser", "browser-mcp": "Browser MCP", "flaresolverr": "FlareSolverr",
    "nzbget": "NZBGet", "calibre-web-automated": "Calibre-Web", "audiobookshelf": "Audiobookshelf",
    "led-notify": "Command Center", "led-notify-display": "Rack screen", "gluetun": "VPN",
    "caddy": "Home Lab Names", "postgres": "Postgres", "seerr": "Seerr", "jslab-tunnel": "JSLab tunnel",
    "deunhealth": "Deunhealth",
}
PREFIXES = (("jslab-prod-", "JSLab site"), ("jslab-staging-", "JSLab staging"))


def friendly_name(container: str) -> str:
    if container in NAMES:
        return NAMES[container]
    for prefix, name in PREFIXES:
        if container.startswith(prefix):
            return name
    return " ".join(w.capitalize() for w in container.replace("_", "-").split("-") if w)


async def docker_list(socket_path: str) -> list[dict]:
    transport = httpx.AsyncHTTPTransport(uds=socket_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=5) as client:
        resp = await client.get("/containers/json")
        resp.raise_for_status()
        return [{"id": c["Id"], "name": c["Names"][0].lstrip("/")} for c in resp.json()]


def _read_kv(path: Path, key: str) -> int:
    for line in path.read_text().splitlines():
        k, _, v = line.partition(" ")
        if k == key:
            return int(v)
    raise KeyError(key)


class AppMeter:
    def __init__(self, cgroup_root: str, list_containers, clock, ncpu: int | None = None):
        self.root = Path(cgroup_root) / "system.slice"
        self.list_containers = list_containers
        self.clock = clock
        self.ncpu = ncpu or os.cpu_count() or 1
        self._containers: list[dict] = []
        self._listed_at: float | None = None
        self._prev: dict[str, tuple[float, int]] = {}  # id -> (monotonic, usage_usec)

    async def _refresh(self) -> None:
        now = self.clock.monotonic()
        if self._listed_at is not None and now - self._listed_at < LIST_TTL_S:
            return
        self._listed_at = now  # a failing socket is retried after the TTL, not every sample
        try:
            self._containers = await self.list_containers()
        except Exception as e:
            log.warning("listing containers failed: %s", e)

    async def sample(self) -> list[dict]:
        """Every running container, busiest first; CPU is % of the whole machine."""
        await self._refresh()
        now = self.clock.monotonic()
        out, prev = [], {}
        for c in self._containers:
            d = self.root / f"docker-{c['id']}.scope"
            try:
                usage = _read_kv(d / "cpu.stat", "usage_usec")
                mem = int((d / "memory.current").read_text()) - _read_kv(d / "memory.stat", "inactive_file")
            except (OSError, KeyError, ValueError):
                continue  # stopped or removed since the list was taken
            prev[c["id"]] = (now, usage)
            cpu = 0.0
            if c["id"] in self._prev:
                t0, u0 = self._prev[c["id"]]
                if now > t0:
                    cpu = max(0.0, 100.0 * (usage - u0) / ((now - t0) * 1e6 * self.ncpu))
            out.append({"name": friendly_name(c["name"]), "cpu_pct": round(cpu, 1), "mem_bytes": max(0, mem)})
        self._prev = prev
        out.sort(key=lambda a: (a["cpu_pct"], a["mem_bytes"]), reverse=True)
        return out
