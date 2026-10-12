"""Atomic JSON persistence with corrupt-file recovery."""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path


def load_json(path: Path, default):
    """Return (data, warning). Missing file -> copy of default; corrupt file is moved aside."""
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default), None
    try:
        return json.loads(path.read_text()), None
    except (OSError, ValueError) as e:
        aside = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            path.rename(aside)
        except OSError:
            aside = path
        return copy.deepcopy(default), f"{path.name} was corrupt ({e}); saved as {aside.name}, using defaults"


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
