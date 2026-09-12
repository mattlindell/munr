"""Connection configuration for the hugr fact store.

Resolution order for the DSN:

1. ``HUGR_DATABASE_URL`` — a full libpq connection string, if set.
2. Otherwise assembled from ``HUGR_DB_{HOST,PORT,NAME,USER,PASSWORD}`` with defaults that
   match the dedicated desktop pgvector instance (ADR 0005).

The password is never defaulted in code — the donor's hard-coded credential is exactly the
kind of thing we rotate (see CLAUDE.md security defaults). Supply it via the environment.
"""

from __future__ import annotations

import os

from psycopg.conninfo import make_conninfo

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "5432"
DEFAULT_NAME = "hugr"
DEFAULT_USER = "hugr"


def database_url() -> str:
    """Return the libpq DSN for the facts database.

    Built with ``make_conninfo`` rather than f-string interpolation so a rotated password
    containing URL-special characters (``@``, ``/``, ``:``, spaces, ...) is escaped correctly
    instead of corrupting the connection string.
    """
    url = os.environ.get("HUGR_DATABASE_URL")
    if url:
        return url

    params: dict[str, str] = {
        "host": os.environ.get("HUGR_DB_HOST", DEFAULT_HOST),
        "port": os.environ.get("HUGR_DB_PORT", DEFAULT_PORT),
        "dbname": os.environ.get("HUGR_DB_NAME", DEFAULT_NAME),
        "user": os.environ.get("HUGR_DB_USER", DEFAULT_USER),
    }
    password = os.environ.get("HUGR_DB_PASSWORD")
    if password:
        params["password"] = password
    return make_conninfo(**params)
