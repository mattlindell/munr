"""Fixtures for munr's store integration tests.

Each test runs inside a transaction that is rolled back, so nothing persists to the real
facts database. If the database is unreachable (no Docker / no creds), the suite skips —
keeping it green without infrastructure, per the donor's "no Docker required" promise. The
reachability check is session-scoped so a down database costs one probe, not one per test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from munr import config
from munr.store import FactStore

#: A dedicated scope so tests never read or clobber real global ('*') facts.
TEST_SCOPE = "test:pv-2"


@pytest.fixture(scope="session")
def dsn() -> str:
    resolved = config.database_url()
    try:
        psycopg.connect(resolved, connect_timeout=2).close()
    except psycopg.OperationalError as exc:
        pytest.skip(f"facts database unreachable: {exc}")
    return resolved


@pytest.fixture
def store(dsn: str) -> Iterator[FactStore]:
    conn = psycopg.connect(dsn, autocommit=False, connect_timeout=2)
    try:
        yield FactStore(conn)
    finally:
        conn.rollback()  # discard everything this test wrote
        conn.close()


@pytest.fixture
def snapshotting_store(dsn: str, tmp_path: Path) -> Iterator[FactStore]:
    """A rolled-back store that regenerates its Snapshot into a temp file, never the real one."""
    conn = psycopg.connect(dsn, autocommit=False, connect_timeout=2)
    try:
        yield FactStore(conn, snapshot_path=tmp_path / "snapshot.txt")
    finally:
        conn.rollback()
        conn.close()
