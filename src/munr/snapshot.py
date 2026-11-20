"""The Snapshot: a pre-rendered file of Confirmed global Facts on the desktop (ADR 0003).

Write-rarely, read-every-turn. The store regenerates it on every Fact mutation; core actions
run a TTL age-check as a backstop for out-of-band changes. The Injection Hook only reads it.

**Stdlib only.** The hook imports this module on its hot path, so it must never pull in the
database driver or anything heavy (the Cold-start Invariant). Callers hand in ``Fact``
objects; this module never queries for them.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

from .model import GLOBAL_SCOPE, Fact

DEFAULT_TTL_SECONDS = 3600
HEADER = "Facts about the user, recorded in munr:"

_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SECONDS = 0.05


def default_path() -> Path:
    override = os.environ.get("MUNR_SNAPSHOT_PATH")
    return Path(override) if override else Path.home() / ".munr" / "snapshot.txt"


def ttl_seconds() -> int:
    return int(os.environ.get("MUNR_SNAPSHOT_TTL_SECONDS", DEFAULT_TTL_SECONDS))


def render(facts: Iterable[Fact]) -> str:
    """Render Confirmed global Facts as plain statements; empty when there are none."""
    lines = [
        f"- {f.key}: {' '.join(f.value.split())}"
        for f in sorted(facts, key=lambda f: f.key)
        if f.is_confirmed and f.scope == GLOBAL_SCOPE
    ]
    return "\n".join([HEADER, *lines]) + "\n" if lines else ""


def write(path: Path, facts: Iterable[Fact]) -> None:
    """Atomically replace the Snapshot, so a concurrent hook read sees old or new, never half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(facts))
        _replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _replace(src: str, dst: Path) -> None:
    # On Windows, os.replace fails while a reader holds dst open. The hook's read takes
    # microseconds, so a short retry rides it out.
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def is_stale(path: Path, ttl: int) -> bool:
    """True when the Snapshot is missing or older than ``ttl`` seconds."""
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return True
    return age >= ttl
