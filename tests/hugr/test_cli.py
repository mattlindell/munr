"""CLI tests: drive ``hugr.cli.run`` end to end through an injected store.

The CLI is read-and-propose-only: ``set`` always lands Pending, and there are no confirm/
reject verbs (those are Console actions, tested directly against the store). Store-backed
cases skip when the database is unreachable (via the `store` fixture); the usage-error and
verb-surface cases need no database.
"""

from __future__ import annotations

import pytest

from hugr.cli import EXIT_NOT_FOUND, EXIT_OK, EXIT_REFUSED, run
from hugr.model import Assertion, AssertedBy
from hugr.store import FactStore

from .conftest import TEST_SCOPE

SCOPE_ARGS = ["--scope", TEST_SCOPE]


def test_no_verb_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        run(["fact"])
    assert exc.value.code == 2  # argparse usage error, no DB needed


@pytest.mark.parametrize("verb", ["confirm", "reject"])
def test_review_verbs_are_not_on_the_cli(verb: str) -> None:
    # Approving/rejecting is a Console action, never an allowlisted CLI call.
    with pytest.raises(SystemExit) as exc:
        run(["fact", verb, "location", *SCOPE_ARGS])
    assert exc.value.code == 2


def test_set_lands_pending_then_get(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["fact", "set", "editor", "neovim", *SCOPE_ARGS], store=store) == EXIT_OK
    out = capsys.readouterr().out
    assert "pending" in out
    assert "confirmed" not in out  # the CLI never writes live

    assert run(["fact", "get", "editor", *SCOPE_ARGS], store=store) == EXIT_OK
    out = capsys.readouterr().out
    assert "neovim" in out
    assert "pending" in out


def test_get_missing_returns_not_found(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["fact", "get", "nope", *SCOPE_ARGS], store=store) == EXIT_NOT_FOUND
    assert "no such fact" in capsys.readouterr().err


def test_set_refuses_when_key_is_already_confirmed(
    store: FactStore, capsys: pytest.CaptureFixture[str]
) -> None:
    # Seed a Confirmed fact the way the Console would (a verified user assertion).
    store.set(Assertion(TEST_SCOPE, "location", "Portland, OR metro", AssertedBy.USER))

    code = run(["fact", "set", "location", "San Diego", *SCOPE_ARGS], store=store)
    assert code == EXIT_REFUSED
    assert "refused" in capsys.readouterr().err
    # The confirmed value is untouched.
    run(["fact", "get", "location", *SCOPE_ARGS], store=store)
    assert "Portland, OR metro" in capsys.readouterr().out


def test_set_reproposing_confirmed_value_is_noop(
    store: FactStore, capsys: pytest.CaptureFixture[str]
) -> None:
    store.set(Assertion(TEST_SCOPE, "location", "Portland, OR metro", AssertedBy.USER))
    capsys.readouterr()
    assert run(["fact", "set", "location", "Portland, OR metro", *SCOPE_ARGS], store=store) == EXIT_OK
    assert "no change" in capsys.readouterr().out


def test_list_shows_facts_and_filters(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    store.set(Assertion(TEST_SCOPE, "location", "Portland, OR metro", AssertedBy.USER))
    run(["fact", "set", "editor", "neovim", *SCOPE_ARGS], store=store)  # pending proposal
    capsys.readouterr()

    assert run(["fact", "list", *SCOPE_ARGS], store=store) == EXIT_OK
    out = capsys.readouterr().out
    assert "location" in out
    assert "editor" in out

    assert run(["fact", "list", *SCOPE_ARGS, "--pending"], store=store) == EXIT_OK
    out = capsys.readouterr().out
    assert "editor" in out
    assert "location" not in out
