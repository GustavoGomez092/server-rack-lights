"""Container crash detection from the Docker event stream."""
from __future__ import annotations

import json

import httpx

from ..events import Event

INTENTIONAL = ("stop", "kill", "restart")


class DockerDeathFilter:
    def __init__(self):
        self._intentional: dict[str, float] = {}

    def feed(self, evt: dict, now: float, window_s: float) -> Event | None:
        if evt.get("Type") != "container":
            return None
        action = evt.get("Action", "")
        attrs = evt.get("Actor", {}).get("Attributes", {})
        name = attrs.get("name", "?")
        if action in INTENTIONAL:
            self._intentional[name] = now
            return None
        if action != "die":
            return None
        stopped_at = self._intentional.pop(name, None)
        if stopped_at is not None and now - stopped_at <= window_s:
            return None
        code = attrs.get("exitCode", "0")
        if code == "0":
            return None
        return Event("container.died", "oneshot", source="docker",
                     data={"key": name, "container": name, "exit_code": int(code) if code.isdigit() else code})


async def docker_events(socket_path: str):
    transport = httpx.AsyncHTTPTransport(uds=socket_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=None) as client:
        params = {"filters": json.dumps({"type": ["container"]})}
        async with client.stream("GET", "/events", params=params) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.strip():
                    yield json.loads(line)


class DockerMonitor:
    name = "docker"

    def __init__(self, settings, clock, stream=docker_events):
        self.settings = settings
        self.clock = clock
        self.stream = stream
        self.filter = DockerDeathFilter()
        self.heartbeat = lambda: None

    async def run(self, emit) -> None:
        cfg = self.settings.data["docker"]
        self.heartbeat()  # an event stream has no polling cycle; a connect failure flips this to error at once
        async for evt in self.stream(cfg["socket"]):
            ev = self.filter.feed(evt, self.clock.monotonic(), cfg["intentional_window_s"])
            if ev is not None:
                await emit(ev)
        raise RuntimeError("docker event stream ended")
