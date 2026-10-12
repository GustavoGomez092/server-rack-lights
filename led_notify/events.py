"""Event model and priority scale shared by monitors, the arbiter and the API."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .audio.refs import is_valid_ref

PRIORITIES = {"idle": 0, "low": 10, "normal": 20, "manual": 25, "high": 30, "critical": 40}
RULE_PRIORITIES = ("low", "normal", "high", "critical")
KINDS = ("oneshot", "start", "clear")
MIN_DURATION_S = 0.5
MAX_DURATION_S = 60.0
_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


@dataclass
class Event:
    name: str
    kind: str = "oneshot"
    source: str = "webhook"
    priority: str | None = None
    look: dict | None = None
    duration_s: float | None = None
    data: dict = field(default_factory=dict)
    force: bool = False
    sound: str | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            raise ValueError("name must be 1-64 characters: letters, digits, '.', '_', ':' or '-'")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if self.priority is not None and self.priority not in RULE_PRIORITIES:
            raise ValueError(f"priority must be one of {', '.join(RULE_PRIORITIES)}")
        if self.duration_s is not None:
            self.duration_s = min(MAX_DURATION_S, max(MIN_DURATION_S, float(self.duration_s)))
        if self.sound not in (None, "") and not is_valid_ref(self.sound):
            raise ValueError("sound must be default, success, error or custom:<name>")
