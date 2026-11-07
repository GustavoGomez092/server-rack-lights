"""Plain words for alert names on the rack screen's banner."""
from __future__ import annotations

LABELS = {
    "internet.down": "Internet down", "dns.down": "DNS down", "disk.smart_error": "Drive health warning",
    "internet.slow": "Internet slow", "disk.full": "Disk almost full", "cpu.hot": "CPU hot",
    "device.new_late": "Unknown device joined", "device.new": "New device joined",
    "container.died": "An app stopped", "internet.up": "Internet back", "device.known_arrived": "Someone arrived",
    "adguard.block_spike": "Blocking spike",
}


def label(name: str) -> str:
    if name in LABELS:
        return LABELS[name]
    words = name.replace(".", " ").replace("_", " ").replace("-", " ").split()
    return " ".join(words).capitalize() if words else name
