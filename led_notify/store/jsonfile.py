"""Atomic JSON persistence with corrupt-file recovery."""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path


def move_aside(path: Path) -> str:
    """Rename a bad file to <name>.corrupt-<ts> (never overwriting an earlier backup).

    Returns the new file name. If the rename fails, the original is left in place and its name is returned.
    """
    path = Path(path)
    stamp = int(time.time())
    aside = path.with_name(f"{path.name}.corrupt-{stamp}")
    n = 2
    while aside.exists():
        aside = path.with_name(f"{path.name}.corrupt-{stamp}-{n}")
        n += 1
    try:
        path.rename(aside)
    except OSError:
        return path.name
    return aside.name


def load_json(path: Path, default):
    """Return (data, warning). Missing file -> copy of default; unparseable file is moved aside."""
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default), None
    try:
        return json.loads(path.read_text()), None
    except (OSError, ValueError) as e:
        backup = move_aside(path)
        return copy.deepcopy(default), f"{path.name} was corrupt ({e}); saved as {backup}, using defaults"


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
