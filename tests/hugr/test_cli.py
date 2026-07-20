"""CLI tests: drive ``hugr.cli.run`` end to end through an injected store.

Store-backed cases skip when the database is unreachable (via the `store` fixture). The
usage-error case needs no database.
"""

from __future__ import annotations

import pytest

from hugr.cli import EXIT_NOT_FOUND, EXIT_OK, EXIT_REFUSED, run
from hugr.store import FactStore

from .conftest import TEST_SCOPE

SCOPE_ARGS = ["--scope", TEST_SCOPE]


def test_no_verb_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        run(["fact"])
    assert exc.value.code == 2  # argparse usage error, no DB needed


def test_set_user_then_get(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["fact", "set", "location", "Portland, OR metro", *SCOPE_ARGS], store=store) == EXIT_OK
    out = capsys.readouterr().out
    assert "confirmed" in out

    assert run(["fact", "get", "location", *SCOPE_ARGS], store=store) == EXIT_OK
    out = capsys.readouterr().out
    assert "Portland, OR metro" in out
    assert "confirmed" in out


def test_get_missing_returns_not_found(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["fact", "get", "nope", *SCOPE_ARGS], store=store) == EXIT_NOT_FOUND
    assert "no such fact" in capsys.readouterr().err


def test_model_proposal_is_pending(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(["fact", "set", "editor", "neovim", "--as", "model", *SCOPE_ARGS], store=store)
    assert code == EXIT_OK
    assert "pending" in capsys.readouterr().out


def test_model_cannot_overwrite_confirmed(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    run(["fact", "set", "location", "Portland, OR metro", *SCOPE_ARGS], store=store)
    capsys.readouterr()
    code = run(["fact", "set", "location", "San Diego", "--as", "model", *SCOPE_ARGS], store=store)
    assert code == EXIT_REFUSED
    assert "refused" in capsys.readouterr().err
    # The confirmed value is untouched.
    run(["fact", "get", "location", *SCOPE_ARGS], store=store)
    assert "Portland, OR metro" in capsys.readouterr().out


def test_confirm_and_reject_flow(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    run(["fact", "set", "editor", "neovim", "--as", "model", *SCOPE_ARGS], store=store)
    capsys.readouterr()

    assert run(["fact", "confirm", "editor", *SCOPE_ARGS], store=store) == EXIT_OK
    assert "confirmed" in capsys.readouterr().out

    # A confirmed fact cannot be rejected.
    assert run(["fact", "reject", "editor", *SCOPE_ARGS], store=store) == EXIT_REFUSED
    assert "refused" in capsys.readouterr().err


def test_reject_discards_pending(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    run(["fact", "set", "editor", "vscode", "--as", "model", *SCOPE_ARGS], store=store)
    capsys.readouterr()
    assert run(["fact", "reject", "editor", *SCOPE_ARGS], store=store) == EXIT_OK
    assert "rejected" in capsys.readouterr().out
    assert run(["fact", "get", "editor", *SCOPE_ARGS], store=store) == EXIT_NOT_FOUND


def test_list_shows_facts(store: FactStore, capsys: pytest.CaptureFixture[str]) -> None:
    run(["fact", "set", "location", "Portland, OR metro", *SCOPE_ARGS], store=store)
    run(["fact", "set", "editor", "neovim", "--as", "model", *SCOPE_ARGS], store=store)
    capsys.readouterr()

    assert run(["fact", "list", *SCOPE_ARGS], store=store) == EXIT_OK
    out = capsys.readouterr().out
    assert "location" in out
    assert "editor" in out

    assert run(["fact", "list", *SCOPE_ARGS, "--confirmed"], store=store) == EXIT_OK
    out = capsys.readouterr().out
    assert "location" in out
    assert "editor" not in out
