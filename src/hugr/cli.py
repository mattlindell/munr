"""The ``hugr`` CLI — the primary agent- and human-facing surface (ADR 0001).

Verbs: ``hugr fact set|get|list|confirm|reject``. Output is stable and greppable so an agent
reading it over Bash/PowerShell can parse the result. Exit codes:

* ``0`` — did what was asked (wrote, confirmed, rejected, listed, got) or a benign no-op
* ``2`` — usage error (argparse)
* ``3`` — refused by policy (a model proposal vs a confirmed fact; rejecting a live fact)
* ``4`` — no such fact
* ``5`` — the facts database could not be reached

Confirming and rejecting are deliberate human operations — the Console is the authoritative
surface for them (CONTEXT.md). They live here so the Console and a human at the terminal
share one code path; an agent should not self-confirm its own proposals.
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from .model import GLOBAL_SCOPE, Assertion, AssertedBy, Fact
from .rules import ConfirmAction, RejectAction, WriteAction
from .store import FactStore

EXIT_OK = 0
EXIT_REFUSED = 3
EXIT_NOT_FOUND = 4
EXIT_NO_DB = 5


def _scope_label(scope: str) -> str:
    return "global" if scope == GLOBAL_SCOPE else scope


def _fmt(fact: Fact) -> str:
    status = "confirmed" if fact.is_confirmed else "pending"
    return (
        f"{fact.key} = {fact.value}  "
        f"[{status}, scope={_scope_label(fact.scope)}, by={fact.asserted_by.value}]"
    )


def _missing(key: str, scope: str) -> int:
    print(f"no such fact: {key} (scope {_scope_label(scope)})", file=sys.stderr)
    return EXIT_NOT_FOUND


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hugr", description="hugr fact store")
    top = parser.add_subparsers(dest="group", required=True)

    fact = top.add_parser("fact", help="manage stable facts").add_subparsers(
        dest="verb", required=True
    )

    def add_scope(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--scope",
            default=GLOBAL_SCOPE,
            help="fact scope ('*' = global, the default; else a project key)",
        )

    p_set = fact.add_parser("set", help="assert or propose a fact value")
    p_set.add_argument("key")
    p_set.add_argument("value")
    add_scope(p_set)
    p_set.add_argument(
        "--as",
        dest="asserted_by",
        choices=[a.value for a in AssertedBy],
        default=AssertedBy.USER.value,
        help="provenance: 'user' writes live/confirmed (default); 'model' lands pending",
    )

    p_get = fact.add_parser("get", help="read a fact by key")
    p_get.add_argument("key")
    add_scope(p_get)

    p_list = fact.add_parser("list", help="list facts")
    p_list.add_argument(
        "--scope",
        default=None,  # list spans all scopes unless restricted
        help="restrict to a scope ('*' = global)",
    )
    status = p_list.add_mutually_exclusive_group()
    status.add_argument("--pending", action="store_const", const="pending", dest="status")
    status.add_argument("--confirmed", action="store_const", const="confirmed", dest="status")

    p_confirm = fact.add_parser("confirm", help="promote a pending fact to confirmed")
    p_confirm.add_argument("key")
    add_scope(p_confirm)

    p_reject = fact.add_parser("reject", help="discard a pending fact")
    p_reject.add_argument("key")
    add_scope(p_reject)

    return parser


def _do_set(store: FactStore, args: argparse.Namespace) -> int:
    assertion = Assertion(args.scope, args.key, args.value, AssertedBy(args.asserted_by))
    result = store.set(assertion)
    action = result.decision.action

    if action is WriteAction.WRITE_LIVE:
        print(f"set (confirmed): {_fmt(result.fact)}" if result.fact else "set (confirmed)")
        return EXIT_OK
    if action is WriteAction.WRITE_PENDING:
        line = _fmt(result.fact) if result.fact else f"{args.key} = {args.value}"
        print(f"proposed (pending): {line} - confirm in the Console")
        return EXIT_OK
    if action is WriteAction.NOOP:
        print(f"no change: {_fmt(result.fact)}" if result.fact else "no change")
        return EXIT_OK
    # REFUSE_CONFLICT
    current = result.fact.value if result.fact else "?"
    print(
        f"refused: '{args.key}' is a confirmed fact ({current}); "
        "a model proposal cannot overwrite it",
        file=sys.stderr,
    )
    return EXIT_REFUSED


def _do_get(store: FactStore, args: argparse.Namespace) -> int:
    fact = store.get(args.scope, args.key)
    if fact is None:
        return _missing(args.key, args.scope)
    print(_fmt(fact))
    return EXIT_OK


def _do_list(store: FactStore, args: argparse.Namespace) -> int:
    facts = store.list(scope=args.scope, status=args.status)
    if not facts:
        print("(no facts)")
        return EXIT_OK
    for fact in facts:
        print(_fmt(fact))
    return EXIT_OK


def _do_confirm(store: FactStore, args: argparse.Namespace) -> int:
    action = store.confirm(args.scope, args.key)
    if action is ConfirmAction.MISSING:
        return _missing(args.key, args.scope)
    fact = store.get(args.scope, args.key)
    verb = "confirmed" if action is ConfirmAction.CONFIRM else "already confirmed"
    print(f"{verb}: {_fmt(fact)}" if fact else verb)
    return EXIT_OK


def _do_reject(store: FactStore, args: argparse.Namespace) -> int:
    action = store.reject(args.scope, args.key)
    if action is RejectAction.MISSING:
        return _missing(args.key, args.scope)
    if action is RejectAction.REFUSE_LIVE:
        print(
            f"refused: '{args.key}' is confirmed; reject only discards pending facts",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    print(f"rejected (discarded pending): {args.key}")
    return EXIT_OK


_DISPATCH = {
    "set": _do_set,
    "get": _do_get,
    "list": _do_list,
    "confirm": _do_confirm,
    "reject": _do_reject,
}


def run(argv: list[str], store: FactStore | None = None) -> int:
    """Parse ``argv`` and execute. Pass ``store`` to inject one (tests); otherwise connect."""
    args = build_parser().parse_args(argv)

    owns_store = store is None
    if owns_store:
        try:
            store = FactStore.connect()
        except psycopg.OperationalError:
            # Don't echo the raw driver error: the resolved DSN can carry the password.
            print(
                "cannot reach the facts database "
                "(check it is running and HUGR_DATABASE_URL / HUGR_DB_* are set)",
                file=sys.stderr,
            )
            return EXIT_NO_DB
    assert store is not None
    try:
        return _DISPATCH[args.verb](store, args)
    finally:
        if owns_store:
            store.close()


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
