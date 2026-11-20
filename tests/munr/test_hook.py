"""The Injection Hook, driven as Claude Code drives it: a subprocess fed a UserPromptSubmit
payload on stdin. Covers the injected output, fail-open, and the Cold-start Invariant.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from munr import hook, snapshot
from munr.model import GLOBAL_SCOPE, AssertedBy, Fact

PAYLOAD = json.dumps(
    {"hook_event_name": "UserPromptSubmit", "prompt": "where do I live?", "session_id": "t"}
)


def run_hook(snapshot_path: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "MUNR_SNAPSHOT_PATH": str(snapshot_path)}
    return subprocess.run(
        [sys.executable, "-m", "munr.hook"],
        input=PAYLOAD,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def seed(path: Path) -> None:
    confirmed = datetime(2026, 11, 20, tzinfo=timezone.utc)
    snapshot.write(
        path, [Fact(GLOBAL_SCOPE, "location", "Portland, OR metro", AssertedBy.USER, confirmed)]
    )


def test_injects_snapshot_as_additional_context(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.txt"
    seed(path)
    result = run_hook(path)
    assert result.returncode == 0
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    assert "location: Portland, OR metro" in out["additionalContext"]


def test_missing_snapshot_injects_nothing(tmp_path: Path) -> None:
    result = run_hook(tmp_path / "absent.txt")
    assert result.returncode == 0
    assert result.stdout == ""


def test_empty_snapshot_injects_nothing(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.txt"
    snapshot.write(path, [])
    result = run_hook(path)
    assert result.returncode == 0
    assert result.stdout == ""


def test_unreadable_snapshot_fails_open(tmp_path: Path) -> None:
    # A directory where the file should be: reading it raises, the hook must still exit 0.
    path = tmp_path / "snapshot.txt"
    path.mkdir()
    result = run_hook(path)
    assert result.returncode == 0
    assert result.stdout == ""


def test_undecodable_snapshot_fails_open(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.txt"
    path.write_bytes(b"\xff\xfe\xfa not utf-8")
    result = run_hook(path)
    assert result.returncode == 0
    assert result.stdout == ""


def test_slow_read_is_abandoned_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(_: Path) -> str:
        import time

        time.sleep(5)
        return "too late"

    monkeypatch.setattr(snapshot, "read", hang)
    assert hook.context(Path("ignored"), deadline=0.2) is None


def test_cold_start_invariant_hot_path_imports_no_heavy_stack() -> None:
    heavy = ["psycopg", "psycopg2", "torch", "sentence_transformers"]
    probe = (
        "import sys, munr.hook\n"
        f"print([m for m in {heavy!r} if m in sys.modules])"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=10, check=True
    )
    assert result.stdout.strip() == "[]"
