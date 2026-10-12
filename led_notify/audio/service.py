"""Turns a sound ref into a queued chime."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class AudioService:
    def __init__(self, library, player):
        self.library = library
        self.player = player

    def chime(self, ref: str, volume_pct: int) -> bool:
        path = self.library.resolve(ref)
        if path is None:
            log.warning("sound %r not found; skipping chime", ref)
            return False
        self.player.enqueue(path, volume_pct)
        return True

    def status(self) -> dict:
        return self.player.status()
