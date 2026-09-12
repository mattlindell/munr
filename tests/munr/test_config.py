"""Tests for DSN construction. No database needed — we only parse the string back."""

from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict

from munr import config

DB_ENV = ["MUNR_DATABASE_URL", "MUNR_DB_HOST", "MUNR_DB_PORT", "MUNR_DB_NAME", "MUNR_DB_USER", "MUNR_DB_PASSWORD"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in DB_ENV:
        monkeypatch.delenv(name, raising=False)


def test_explicit_database_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUNR_DATABASE_URL", "postgresql://x@example/db")
    assert config.database_url() == "postgresql://x@example/db"


def test_defaults_match_the_desktop_instance() -> None:
    parsed = conninfo_to_dict(config.database_url())
    assert parsed["host"] == "127.0.0.1"
    assert parsed["dbname"] == "munr"
    assert parsed["user"] == "munr"
    assert "password" not in parsed  # never defaulted


def test_password_with_url_special_chars_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    nasty = "p@ss/w:o+rd #?"
    monkeypatch.setenv("MUNR_DB_PASSWORD", nasty)
    parsed = conninfo_to_dict(config.database_url())
    # The whole point of the fix: special characters survive intact, not corrupt the DSN.
    assert parsed["password"] == nasty
    assert parsed["host"] == "127.0.0.1"
