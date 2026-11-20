"""Snapshot rendering, atomic write, and the TTL age-check (ADR 0003). No database needed."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from munr import snapshot
from munr.model import GLOBAL_SCOPE, AssertedBy, Fact

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)


def fact(key: str, value: str, *, scope: str = GLOBAL_SCOPE, confirmed: bool = True) -> Fact:
    return Fact(scope, key, value, AssertedBy.USER, confirmed_at=NOW if confirmed else None)


def test_renders_confirmed_global_facts_as_plain_statements() -> None:
    text = snapshot.render([fact("location", "Portland, OR metro"), fact("employer", "Caffelli")])
    assert "location: Portland, OR metro" in text
    assert "employer: Caffelli" in text
    for imperative in ("always", "remember", "must", "never"):  # statements, not instructions
        assert imperative not in text.lower()


def test_render_excludes_pending_and_project_scoped_facts() -> None:
    text = snapshot.render(
        [
            fact("location", "Portland, OR metro"),
            fact("location2", "San Diego", confirmed=False),
            fact("language", "python", scope="repo:munr"),
        ]
    )
    assert "Portland" in text
    assert "San Diego" not in text
    assert "python" not in text


def test_render_of_nothing_is_empty() -> None:
    assert snapshot.render([]) == ""
    assert snapshot.render([fact("x", "y", confirmed=False)]) == ""


def test_multiline_value_stays_one_statement() -> None:
    text = snapshot.render([fact("name", "Matt\nLindell")])
    assert "name: Matt Lindell" in text


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "snapshot.txt"
    snapshot.write(path, [fact("location", "Portland, OR metro")])
    assert "location: Portland, OR metro" in snapshot.read(path)
    assert list(path.parent.iterdir()) == [path]  # no temp file left behind


def test_write_replaces_previous_contents(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.txt"
    snapshot.write(path, [fact("location", "San Diego")])
    snapshot.write(path, [fact("location", "Portland, OR metro")])
    text = snapshot.read(path)
    assert "Portland" in text and "San Diego" not in text


def test_missing_snapshot_is_stale(tmp_path: Path) -> None:
    assert snapshot.is_stale(tmp_path / "absent.txt", ttl=3600)


def test_fresh_snapshot_is_not_stale_until_ttl(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.txt"
    snapshot.write(path, [])
    assert not snapshot.is_stale(path, ttl=3600)
    old = time.time() - 7200
    os.utime(path, (old, old))
    assert snapshot.is_stale(path, ttl=3600)


def test_path_and_ttl_come_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUNR_SNAPSHOT_PATH", str(tmp_path / "s.txt"))
    monkeypatch.setenv("MUNR_SNAPSHOT_TTL_SECONDS", "60")
    assert snapshot.default_path() == tmp_path / "s.txt"
    assert snapshot.ttl_seconds() == 60


def test_defaults_without_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUNR_SNAPSHOT_PATH", raising=False)
    monkeypatch.delenv("MUNR_SNAPSHOT_TTL_SECONDS", raising=False)
    assert snapshot.default_path() == Path.home() / ".munr" / "snapshot.txt"
    assert snapshot.ttl_seconds() == snapshot.DEFAULT_TTL_SECONDS
