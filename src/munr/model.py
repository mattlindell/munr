"""Core Fact types shared by the policy layer, the store, and the CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

#: The global Fact Scope. 0.5 injects only global facts; project scope is carried in the
#: schema but not injected until Scope Identity is resolved (CONTEXT.md).
GLOBAL_SCOPE = "*"


class AssertedBy(str, Enum):
    """Provenance: who asserted a Fact. Gates promotion into the injected tier."""

    USER = "user"
    MODEL = "model"


@dataclass(frozen=True)
class Fact:
    """A single ``key -> value`` datum for a scope, as it lives in the ``facts`` table.

    ``confirmed_at IS NOT NULL`` is the sole injection gate: a Confirmed fact is injectable,
    a Pending one is not (ADR 0002).
    """

    scope: str
    key: str
    value: str
    asserted_by: AssertedBy
    confirmed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


@dataclass(frozen=True)
class Assertion:
    """An incoming write request, before it is reconciled against existing state."""

    scope: str
    key: str
    value: str
    asserted_by: AssertedBy
