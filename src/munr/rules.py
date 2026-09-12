"""Pure conflict/trust policy for the Fact write path (ADR 0002).

All decisions about *whether and how* a write, confirm, or reject may proceed live here,
with no database dependency, so the trust order — **user > existing confirmed > model
proposal** — is exhaustively unit-testable. The store is a thin executor that reads the
current row, asks this module what to do, and runs the SQL the decision implies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .model import Assertion, AssertedBy, Fact


class WriteAction(Enum):
    """What a ``set`` should do given the existing row and the incoming assertion."""

    WRITE_LIVE = auto()       # write/overwrite as Confirmed (a user assertion always wins)
    WRITE_PENDING = auto()    # write as Pending (model proposal; no live fact blocks it)
    NOOP = auto()             # model re-proposes the value already Confirmed — nothing to do
    REFUSE_CONFLICT = auto()  # model proposal would differ from a Confirmed fact — refused


@dataclass(frozen=True)
class WriteDecision:
    action: WriteAction
    reason: str

    @property
    def wrote(self) -> bool:
        return self.action in (WriteAction.WRITE_LIVE, WriteAction.WRITE_PENDING)


def resolve_write(existing: Fact | None, incoming: Assertion) -> WriteDecision:
    """Decide how an incoming assertion reconciles with the existing fact (if any).

    Trust order (ADR 0002):
      * A **user** assertion always wins and overwrites in place, Confirmed.
      * A **model** proposal never overwrites a live (Confirmed) fact; it may only land
        Pending when nothing Confirmed stands in its way.
    """
    if incoming.asserted_by is AssertedBy.USER:
        return WriteDecision(
            WriteAction.WRITE_LIVE,
            "user assertion overwrites in place and is confirmed on write",
        )

    # Model proposal from here down.
    if existing is None:
        return WriteDecision(
            WriteAction.WRITE_PENDING,
            "model proposal lands pending (no existing fact)",
        )
    if existing.is_confirmed:
        if existing.value == incoming.value:
            return WriteDecision(
                WriteAction.NOOP,
                "model proposal matches the confirmed value; nothing to do",
            )
        return WriteDecision(
            WriteAction.REFUSE_CONFLICT,
            "model proposal cannot overwrite a confirmed fact",
        )
    return WriteDecision(
        WriteAction.WRITE_PENDING,
        "model proposal updates the existing pending fact",
    )


class ConfirmAction(Enum):
    CONFIRM = auto()   # a Pending fact is promoted to Confirmed
    ALREADY = auto()   # already Confirmed — idempotent no-op
    MISSING = auto()   # no such fact


def resolve_confirm(existing: Fact | None) -> ConfirmAction:
    if existing is None:
        return ConfirmAction.MISSING
    if existing.is_confirmed:
        return ConfirmAction.ALREADY
    return ConfirmAction.CONFIRM


class RejectAction(Enum):
    REJECT = auto()       # a Pending fact is discarded
    REFUSE_LIVE = auto()  # Confirmed — rejecting a live fact is a delete, out of scope for reject
    MISSING = auto()      # no such fact


def resolve_reject(existing: Fact | None) -> RejectAction:
    if existing is None:
        return RejectAction.MISSING
    if existing.is_confirmed:
        return RejectAction.REFUSE_LIVE
    return RejectAction.REJECT
