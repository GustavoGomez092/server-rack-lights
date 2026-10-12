"""CPU temperature from hwmon."""
from __future__ import annotations

from pathlib import Path

from ..events import Event
from .base import Hysteresis, PollingMonitor


def read_hwmon_temp(names, root: str = "/sys/class/hwmon") -> float:
    for d in sorted(Path(root).iterdir()):
        try:
            if (d / "name").read_text().strip() in names:
                return int((d / "temp1_input").read_text().strip()) / 1000
        except (OSError, ValueError):
            continue
    raise RuntimeError(f"no CPU temperature sensor named {', '.join(names)} under {root}")


class ThermalMonitor(PollingMonitor):
    name = section = "thermal"

    def __init__(self, settings, clock, read=read_hwmon_temp):
        super().__init__(settings, clock)
        self.read = read
        self.hot = Hysteresis()

    async def check(self) -> list[Event]:
        cfg = self.cfg
        temp = self.read(cfg["sensor_names"])
        change = self.hot.feed(temp, self.clock.monotonic(), cfg["hot_c"], cfg["clear_c"], cfg["sustain_s"])
        return [Event("cpu.hot", change, source=self.name, data={"temp_c": round(temp, 1)})] if change else []
