"""The Injection Hook: a ``UserPromptSubmit`` hook that injects the Snapshot every turn (ADR 0003).

Fail-open: any error, a missing or empty Snapshot, or a read that outlasts the deadline
injects nothing and exits 0, so the turn always proceeds. The hot path is stdlib only and
never touches the database (the Cold-start Invariant); keep every import here that way.

Register it in Claude Code ``settings.json``::

    "hooks": {"UserPromptSubmit": [{"hooks": [
        {"type": "command", "command": "munr-hook", "timeout": 2}
    ]}]}
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from . import snapshot

DEADLINE_SECONDS = 1.5  # under the ~2s budget, leaving room for interpreter startup


def context(path: Path, deadline: float = DEADLINE_SECONDS) -> str | None:
    """The Snapshot text to inject, or None if it is unavailable, empty, or too slow."""
    found: list[str] = []

    def load() -> None:
        try:
            found.append(snapshot.read(path))
        except Exception:
            pass

    reader = threading.Thread(target=load, daemon=True)
    reader.start()
    reader.join(deadline)
    text = found[0].strip() if found else ""
    return text or None


def main() -> None:
    try:
        text = context(snapshot.default_path())
        if text:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": text,
                }
            }
            sys.stdout.write(json.dumps(output))
    except Exception:
        pass
    raise SystemExit(0)


if __name__ == "__main__":
    main()
