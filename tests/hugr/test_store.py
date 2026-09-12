"""Integration tests for FactStore against the real facts table (ADR 0002 end to end).

Skips when the database is unreachable (see conftest). Every test is rolled back.
"""

from __future__ import annotations

from hugr.model import Assertion, AssertedBy
from hugr.rules import ConfirmAction, RejectAction, WriteAction
from hugr.store import FactStore

from .conftest import TEST_SCOPE


def user(key: str, value: str) -> Assertion:
    return Assertion(TEST_SCOPE, key, value, AssertedBy.USER)


def model(key: str, value: str) -> Assertion:
    return Assertion(TEST_SCOPE, key, value, AssertedBy.MODEL)


# --- write path ---

def test_user_assertion_writes_confirmed(store: FactStore) -> None:
    result = store.set(user("location", "Portland, OR metro"))
    assert result.decision.action is WriteAction.WRITE_LIVE
    fact = store.get(TEST_SCOPE, "location")
    assert fact is not None
    assert fact.value == "Portland, OR metro"
    assert fact.is_confirmed
    assert fact.asserted_by is AssertedBy.USER


def test_model_proposal_lands_pending(store: FactStore) -> None:
    result = store.set(model("editor", "neovim"))
    assert result.decision.action is WriteAction.WRITE_PENDING
    fact = store.get(TEST_SCOPE, "editor")
    assert fact is not None
    assert not fact.is_confirmed
    assert fact.asserted_by is AssertedBy.MODEL


def test_model_never_overwrites_a_confirmed_fact(store: FactStore) -> None:
    store.set(user("location", "Portland, OR metro"))
    result = store.set(model("location", "Seattle"))
    assert result.decision.action is WriteAction.REFUSE_CONFLICT
    fact = store.get(TEST_SCOPE, "location")
    assert fact is not None
    assert fact.value == "Portland, OR metro"  # unchanged
    assert fact.asserted_by is AssertedBy.USER


def test_model_reproposing_confirmed_value_is_noop(store: FactStore) -> None:
    store.set(user("location", "Portland, OR metro"))
    result = store.set(model("location", "Portland, OR metro"))
    assert result.decision.action is WriteAction.NOOP


def test_user_overwrites_a_pending_proposal(store: FactStore) -> None:
    store.set(model("location", "Seattle"))
    result = store.set(user("location", "Portland, OR metro"))
    assert result.decision.action is WriteAction.WRITE_LIVE
    fact = store.get(TEST_SCOPE, "location")
    assert fact is not None
    assert fact.value == "Portland, OR metro"
    assert fact.is_confirmed


def test_model_updates_existing_pending(store: FactStore) -> None:
    store.set(model("location", "Seattle"))
    result = store.set(model("location", "Tacoma"))
    assert result.decision.action is WriteAction.WRITE_PENDING
    fact = store.get(TEST_SCOPE, "location")
    assert fact is not None
    assert fact.value == "Tacoma"
    assert not fact.is_confirmed


# --- confirm / reject ---

def test_confirm_promotes_pending(store: FactStore) -> None:
    store.set(model("location", "Portland, OR metro"))
    assert store.confirm(TEST_SCOPE, "location") is ConfirmAction.CONFIRM
    fact = store.get(TEST_SCOPE, "location")
    assert fact is not None and fact.is_confirmed


def test_confirm_already_confirmed(store: FactStore) -> None:
    store.set(user("location", "Portland, OR metro"))
    assert store.confirm(TEST_SCOPE, "location") is ConfirmAction.ALREADY


def test_confirm_missing(store: FactStore) -> None:
    assert store.confirm(TEST_SCOPE, "nope") is ConfirmAction.MISSING


def test_reject_discards_pending(store: FactStore) -> None:
    store.set(model("location", "Seattle"))
    assert store.reject(TEST_SCOPE, "location") is RejectAction.REJECT
    assert store.get(TEST_SCOPE, "location") is None


def test_reject_refuses_confirmed(store: FactStore) -> None:
    store.set(user("location", "Portland, OR metro"))
    assert store.reject(TEST_SCOPE, "location") is RejectAction.REFUSE_LIVE
    assert store.get(TEST_SCOPE, "location") is not None


def test_reject_missing(store: FactStore) -> None:
    assert store.reject(TEST_SCOPE, "nope") is RejectAction.MISSING


# --- list ---

def test_list_filters_by_scope_and_status(store: FactStore) -> None:
    store.set(user("location", "Portland, OR metro"))
    store.set(model("editor", "neovim"))

    scoped = store.list(scope=TEST_SCOPE)
    assert {f.key for f in scoped} == {"location", "editor"}

    confirmed = store.list(scope=TEST_SCOPE, status="confirmed")
    assert {f.key for f in confirmed} == {"location"}

    pending = store.list(scope=TEST_SCOPE, status="pending")
    assert {f.key for f in pending} == {"editor"}


# --- the original-failure smoke test (PV-6 in miniature) ---

def test_original_failure_location_stays_portland(store: FactStore) -> None:
    store.set(user("location", "Portland, OR metro"))
    # Batch inference "decides" the user moved — it must not win.
    store.set(model("location", "San Diego"))
    fact = store.get(TEST_SCOPE, "location")
    assert fact is not None
    assert fact.value == "Portland, OR metro"
