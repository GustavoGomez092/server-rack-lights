"""Entry point: wires stores, strip link, arbiter, monitors and the web app."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from .clock import SystemClock
from .driver.fake import FakeStrip
from .driver.link import StripLink
from .engine.arbiter import Arbiter
from .engine.player import Player
from .hub import Hub
from .monitors.adguard import AdguardMonitor
from .monitors.devices import DeviceMonitor
from .monitors.disk import DiskMonitor
from .monitors.dns import DnsMonitor
from .monitors.docker import DockerMonitor
from .monitors.internet import InternetMonitor
from .monitors.supervisor import Supervisor
from .monitors.thermal import ThermalMonitor
from .store.devices import DeviceStore
from .store.settings import SettingsStore
from .web.api import Services, create_app


def build_monitors(settings, devices, clock) -> list:
    return [
        InternetMonitor(settings, clock),
        DnsMonitor(settings, clock),
        DeviceMonitor(settings, clock, devices),
        DiskMonitor(settings, clock),
        ThermalMonitor(settings, clock),
        DockerMonitor(settings, clock),
        AdguardMonitor(settings, clock),
    ]


def build(data_dir: Path, address: str, fake_strip: bool, monitors: bool):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    clock = SystemClock(os.environ.get("TZ"))
    hub = Hub()
    settings = SettingsStore(data_dir / "settings.json", hub)
    devices = DeviceStore(data_dir / "devices.json", hub)
    link = FakeStrip() if fake_strip else StripLink(address, clock)
    arbiter = Arbiter(Player(link, clock), settings, clock, hub)
    supervisor = Supervisor(build_monitors(settings, devices, clock) if monitors else [], arbiter.submit, clock, hub)
    svc = Services(arbiter=arbiter, settings=settings, devices=devices, supervisor=supervisor, link=link,
                   hub=hub, clock=clock)

    @asynccontextmanager
    async def lifespan(_app):
        coros = [arbiter.run(), supervisor.run()]
        if isinstance(link, StripLink):
            coros.append(link.run())
        tasks = [asyncio.create_task(c) for c in coros]
        yield
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return create_app(svc, lifespan=lifespan), svc


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="led-notify: homelab notifications on a BLE LED strip")
    p.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "./data"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    p.add_argument("--address", default=os.environ.get("STRIP_ADDRESS", ""))
    p.add_argument("--fake-strip", action="store_true", default=os.environ.get("FAKE_STRIP") == "1")
    p.add_argument("--no-monitors", action="store_true")
    args = p.parse_args(argv)
    if not args.fake_strip and not args.address:
        p.error("set STRIP_ADDRESS / --address, or use --fake-strip")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app, _ = build(Path(args.data_dir), args.address, args.fake_strip, not args.no_monitors)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
