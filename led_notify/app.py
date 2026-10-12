"""Entry point: wires stores, strip link, arbiter, monitors and the web app."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from .audio.library import SoundLibrary
from .audio.player import AudioPlayer, init_mixer, run_aplay, silent_runner
from .audio.service import AudioService
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


def build(data_dir: Path, address: str, fake_strip: bool, monitors: bool, audio: bool = True):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    clock = SystemClock(os.environ.get("TZ"))
    hub = Hub()
    settings = SettingsStore(data_dir / "settings.json", hub)
    devices = DeviceStore(data_dir / "devices.json", hub)
    link = FakeStrip() if fake_strip else StripLink(address, clock)
    real_audio = audio and not fake_strip
    player = AudioPlayer(settings.data["audio"]["device"], runner=run_aplay if real_audio else silent_runner)
    audio_svc = AudioService(SoundLibrary(data_dir), player)
    arbiter = Arbiter(Player(link, clock), settings, clock, hub, chimes=audio_svc)
    supervisor = Supervisor(build_monitors(settings, devices, clock) if monitors else [], arbiter.submit, clock, hub)
    svc = Services(arbiter=arbiter, settings=settings, devices=devices, supervisor=supervisor, link=link,
                   hub=hub, clock=clock, audio=audio_svc)

    @asynccontextmanager
    async def lifespan(_app):
        # On SIGTERM uvicorn runs this shutdown path: stop the engine first (its last writes still
        # reach the strip), then the link, whose cancellation disconnects from the strip so BlueZ
        # does not keep a stale connection for the next container.
        if real_audio:
            await init_mixer(player.device)
        engine = [asyncio.create_task(arbiter.run()), asyncio.create_task(supervisor.run()),
                  asyncio.create_task(player.run())]
        link_task = asyncio.create_task(link.run()) if isinstance(link, StripLink) else None
        try:
            yield
        finally:
            for t in engine:
                t.cancel()
            await asyncio.gather(*engine, return_exceptions=True)
            if link_task is not None:
                link_task.cancel()
                await asyncio.gather(link_task, return_exceptions=True)

    return create_app(svc, lifespan=lifespan), svc


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="led-notify: homelab notifications on a BLE LED strip")
    p.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "./data"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    p.add_argument("--address", default=os.environ.get("STRIP_ADDRESS", ""))
    p.add_argument("--fake-strip", action="store_true", default=os.environ.get("FAKE_STRIP") == "1")
    p.add_argument("--no-monitors", action="store_true")
    p.add_argument("--no-audio", action="store_true", default=os.environ.get("NO_AUDIO") == "1")
    args = p.parse_args(argv)
    if not args.fake_strip and not args.address:
        p.error("set STRIP_ADDRESS / --address, or use --fake-strip")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app, _ = build(Path(args.data_dir), args.address, args.fake_strip, not args.no_monitors, audio=not args.no_audio)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
