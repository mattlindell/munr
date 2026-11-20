"""Event-triggered Snapshot regeneration and the TTL backstop, against the real facts table.

Uses the global scope inside a rolled-back transaction: the Snapshot only ever carries
Confirmed global Facts, so that is the scope that has to move. Nothing persists.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from munr import snapshot
from munr.cli import EXIT_OK, run
from munr.model import GLOBAL_SCOPE, Assertion, AssertedBy
from munr.store import FactStore

KEY = "test:pv-3:location"


def snap(store: FactStore) -> str:
    assert store.snapshot_path is not None
    return snapshot.read(store.snapshot_path)


def test_user_assertion_is_in_the_snapshot_immediately(snapshotting_store: FactStore) -> None:
    snapshotting_store.set(Assertion(GLOBAL_SCOPE, KEY, "Portland, OR metro", AssertedBy.USER))
    assert f"{KEY}: Portland, OR metro" in snap(snapshotting_store)


def test_pending_proposal_never_reaches_the_snapshot(snapshotting_store: FactStore) -> None:
    snapshotting_store.set(Assertion(GLOBAL_SCOPE, KEY, "San Diego", AssertedBy.MODEL))
    assert "San Diego" not in snap(snapshotting_store)


def test_confirm_regenerates(snapshotting_store: FactStore) -> None:
    snapshotting_store.set(Assertion(GLOBAL_SCOPE, KEY, "Portland, OR metro", AssertedBy.MODEL))
    assert "Portland" not in snap(snapshotting_store)
    snapshotting_store.confirm(GLOBAL_SCOPE, KEY)
    assert f"{KEY}: Portland, OR metro" in snap(snapshotting_store)


def test_user_overwrite_replaces_the_injected_value(snapshotting_store: FactStore) -> None:
    snapshotting_store.set(Assertion(GLOBAL_SCOPE, KEY, "San Diego", AssertedBy.USER))
    snapshotting_store.set(Assertion(GLOBAL_SCOPE, KEY, "Portland, OR metro", AssertedBy.USER))
    text = snap(snapshotting_store)
    assert "Portland" in text and "San Diego" not in text


def test_project_scoped_fact_is_not_injected(snapshotting_store: FactStore) -> None:
    snapshotting_store.set(Assertion("test:pv-3", KEY, "python", AssertedBy.USER))
    assert "python" not in snap(snapshotting_store)


def test_ttl_backstop_regenerates_a_stale_snapshot(snapshotting_store: FactStore) -> None:
    path = snapshotting_store.snapshot_path
    assert path is not None
    assert snapshotting_store.refresh_snapshot_if_stale(ttl=3600)  # missing -> regenerated
    assert path.exists()
    assert not snapshotting_store.refresh_snapshot_if_stale(ttl=3600)  # fresh -> left alone

    old = time.time() - 7200
    os.utime(path, (old, old))
    assert snapshotting_store.refresh_snapshot_if_stale(ttl=3600)


def test_store_without_a_snapshot_path_never_writes_one(store: FactStore, tmp_path: Path) -> None:
    store.set(Assertion(GLOBAL_SCOPE, KEY, "Portland, OR metro", AssertedBy.USER))
    assert not store.refresh_snapshot_if_stale(ttl=0)
    assert list(tmp_path.iterdir()) == []


def test_cli_read_runs_the_ttl_backstop(
    snapshotting_store: FactStore, capsys: pytest.CaptureFixture[str]
) -> None:
    path = snapshotting_store.snapshot_path
    assert path is not None and not path.exists()
    assert run(["fact", "list", "--scope", "test:pv-3"], store=snapshotting_store) == EXIT_OK
    assert path.exists()
