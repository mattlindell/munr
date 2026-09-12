"""Exhaustive tests for the pure Fact policy (ADR 0002 trust/conflict rules).

No database is touched here — this is the deterministic core the store delegates to.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from munr.model import GLOBAL_SCOPE, Assertion, AssertedBy, Fact
from munr.rules import (
    ConfirmAction,
    RejectAction,
    WriteAction,
    resolve_confirm,
    resolve_reject,
    resolve_write,
)

CONFIRMED_AT = datetime(2026, 7, 19, 12, 0, 0)


def confirmed(value: str, by: AssertedBy = AssertedBy.USER) -> Fact:
    return Fact(GLOBAL_SCOPE, "location", value, by, confirmed_at=CONFIRMED_AT)


def pending(value: str, by: AssertedBy = AssertedBy.MODEL) -> Fact:
    return Fact(GLOBAL_SCOPE, "location", value, by, confirmed_at=None)


def assertion(value: str, by: AssertedBy) -> Assertion:
    return Assertion(GLOBAL_SCOPE, "location", value, by)


# --- user assertions: always win, overwrite in place, confirmed on write ---

def test_user_writes_live_when_no_existing_fact() -> None:
    d = resolve_write(None, assertion("Portland, OR metro", AssertedBy.USER))
    assert d.action is WriteAction.WRITE_LIVE
    assert d.wrote


def test_user_overwrites_a_confirmed_fact_in_place() -> None:
    d = resolve_write(confirmed("San Diego"), assertion("Portland, OR metro", AssertedBy.USER))
    assert d.action is WriteAction.WRITE_LIVE


def test_user_overwrites_a_pending_model_proposal() -> None:
    d = resolve_write(pending("Seattle"), assertion("Portland, OR metro", AssertedBy.USER))
    assert d.action is WriteAction.WRITE_LIVE


# --- model proposals: never overwrite a live fact ---

def test_model_lands_pending_when_no_existing_fact() -> None:
    d = resolve_write(None, assertion("Seattle", AssertedBy.MODEL))
    assert d.action is WriteAction.WRITE_PENDING
    assert d.wrote


def test_model_never_overwrites_a_confirmed_fact() -> None:
    # The San Diego -> Portland incident, structurally: batch inference cannot mutate a live fact.
    d = resolve_write(confirmed("Portland, OR metro"), assertion("Seattle", AssertedBy.MODEL))
    assert d.action is WriteAction.REFUSE_CONFLICT
    assert not d.wrote


def test_model_reproposing_the_confirmed_value_is_a_noop() -> None:
    d = resolve_write(confirmed("Portland, OR metro"), assertion("Portland, OR metro", AssertedBy.MODEL))
    assert d.action is WriteAction.NOOP
    assert not d.wrote


def test_model_updates_an_existing_pending_proposal() -> None:
    d = resolve_write(pending("Seattle"), assertion("Tacoma", AssertedBy.MODEL))
    assert d.action is WriteAction.WRITE_PENDING


# --- confirm ---

def test_confirm_promotes_a_pending_fact() -> None:
    assert resolve_confirm(pending("Seattle")) is ConfirmAction.CONFIRM


def test_confirm_is_idempotent_on_a_confirmed_fact() -> None:
    assert resolve_confirm(confirmed("Portland, OR metro")) is ConfirmAction.ALREADY


def test_confirm_missing_fact() -> None:
    assert resolve_confirm(None) is ConfirmAction.MISSING


# --- reject ---

def test_reject_discards_a_pending_fact() -> None:
    assert resolve_reject(pending("Seattle")) is RejectAction.REJECT


def test_reject_refuses_a_confirmed_fact() -> None:
    assert resolve_reject(confirmed("Portland, OR metro")) is RejectAction.REFUSE_LIVE


def test_reject_missing_fact() -> None:
    assert resolve_reject(None) is RejectAction.MISSING


@pytest.mark.parametrize("by", [AssertedBy.USER, AssertedBy.MODEL])
def test_asserted_by_round_trips_through_the_enum(by: AssertedBy) -> None:
    # Guards the DB text<->enum boundary the store relies on.
    assert AssertedBy(by.value) is by
