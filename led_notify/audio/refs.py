"""Names that identify a chime: built-ins or custom uploads (syntax only)."""
from __future__ import annotations

import re

BUILTINS = ("default", "success", "error")
CUSTOM_PREFIX = "custom:"
NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def is_valid_ref(ref) -> bool:
    if not isinstance(ref, str):
        return False
    if ref in BUILTINS:
        return True
    return ref.startswith(CUSTOM_PREFIX) and bool(NAME_RE.match(ref[len(CUSTOM_PREFIX):]))
