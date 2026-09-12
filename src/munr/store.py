"""The Fact store: a thin executor that reads current state, asks :mod:`munr.rules` what to
do, and runs the SQL the decision implies. All policy lives in :mod:`munr.rules`; this layer
only touches the database.

The mutating SQL also enforces the ADR 0002 invariants defensively (``ON CONFLICT ... WHERE
confirmed_at IS NULL``), so a model proposal can never overwrite a live fact even if the row
changes between the policy read and the write.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from . import config
from .model import Assertion, AssertedBy, Fact
from .rules import (
    ConfirmAction,
    RejectAction,
    WriteAction,
    WriteDecision,
    resolve_confirm,
    resolve_reject,
    resolve_write,
)

_COLUMNS = "scope, key, value, asserted_by, confirmed_at, created_at, updated_at"


@dataclass(frozen=True)
class WriteResult:
    """The outcome of a :meth:`FactStore.set` call: what was decided and the resulting row."""

    decision: WriteDecision
    fact: Fact | None  # the row after the write, or the untouched existing row on NOOP/REFUSE


def _row_to_fact(row: dict[str, object] | None) -> Fact | None:
    if row is None:
        return None
    return Fact(
        scope=row["scope"],  # type: ignore[arg-type]
        key=row["key"],  # type: ignore[arg-type]
        value=row["value"],  # type: ignore[arg-type]
        asserted_by=AssertedBy(row["asserted_by"]),
        confirmed_at=row["confirmed_at"],  # type: ignore[arg-type]
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
    )


class FactStore:
    """CRUD + confirm/reject over the ``facts`` table, gated by the ADR 0002 policy."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, dsn: str | None = None) -> FactStore:
        """Open an autocommit connection to the facts database."""
        conn = psycopg.connect(dsn or config.database_url(), autocommit=True)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> FactStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- reads ---

    def get(self, scope: str, key: str) -> Fact | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUMNS} FROM facts WHERE scope = %s AND key = %s",
                (scope, key),
            )
            return _row_to_fact(cur.fetchone())

    def list(self, scope: str | None = None, status: str | None = None) -> list[Fact]:
        """List facts, optionally filtered by scope and by ``status`` in {confirmed, pending}."""
        clauses: list[str] = []
        params: list[object] = []
        if scope is not None:
            clauses.append("scope = %s")
            params.append(scope)
        if status == "confirmed":
            clauses.append("confirmed_at IS NOT NULL")
        elif status == "pending":
            clauses.append("confirmed_at IS NULL")
        elif status is not None:
            raise ValueError(f"unknown status filter: {status!r}")

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM facts{where} ORDER BY scope, key", params)
            return [f for row in cur.fetchall() if (f := _row_to_fact(row)) is not None]

    # --- writes ---

    def set(self, assertion: Assertion) -> WriteResult:
        """Reconcile an assertion against the current row per ADR 0002 and apply the result."""
        existing = self.get(assertion.scope, assertion.key)
        decision = resolve_write(existing, assertion)

        if decision.action is WriteAction.WRITE_LIVE:
            fact = self._upsert_live(assertion)
        elif decision.action is WriteAction.WRITE_PENDING:
            fact = self._upsert_pending(assertion)
        else:  # NOOP or REFUSE_CONFLICT — the existing row stands untouched
            fact = existing

        return WriteResult(decision=decision, fact=fact)

    def _upsert_live(self, a: Assertion) -> Fact | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                INSERT INTO facts (scope, key, value, asserted_by, confirmed_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (scope, key) DO UPDATE
                    SET value = EXCLUDED.value,
                        asserted_by = EXCLUDED.asserted_by,
                        confirmed_at = NOW()
                RETURNING {_COLUMNS}
                """,
                (a.scope, a.key, a.value, AssertedBy.USER.value),
            )
            return _row_to_fact(cur.fetchone())

    def _upsert_pending(self, a: Assertion) -> Fact | None:
        # Defensive guard: only update when the existing row is itself Pending, so a live
        # fact is never overwritten by a model proposal even under a read/write race.
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                INSERT INTO facts (scope, key, value, asserted_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (scope, key) DO UPDATE
                    SET value = EXCLUDED.value,
                        asserted_by = EXCLUDED.asserted_by
                    WHERE facts.confirmed_at IS NULL
                RETURNING {_COLUMNS}
                """,
                (a.scope, a.key, a.value, AssertedBy.MODEL.value),
            )
            row = cur.fetchone()
        # If the guard suppressed the update (row became confirmed), fall back to a read.
        return _row_to_fact(row) if row is not None else self.get(a.scope, a.key)

    def confirm(self, scope: str, key: str) -> ConfirmAction:
        """Promote a Pending fact to Confirmed. Idempotent on an already-Confirmed fact."""
        action = resolve_confirm(self.get(scope, key))
        if action is ConfirmAction.CONFIRM:
            self._conn.execute(
                "UPDATE facts SET confirmed_at = NOW() "
                "WHERE scope = %s AND key = %s AND confirmed_at IS NULL",
                (scope, key),
            )
        return action

    def reject(self, scope: str, key: str) -> RejectAction:
        """Discard a Pending fact. Refuses to touch a Confirmed (live) fact."""
        action = resolve_reject(self.get(scope, key))
        if action is RejectAction.REJECT:
            self._conn.execute(
                "DELETE FROM facts WHERE scope = %s AND key = %s AND confirmed_at IS NULL",
                (scope, key),
            )
        return action
