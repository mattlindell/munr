from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import psycopg
import pytest

from munr import console, snapshot
from munr.model import GLOBAL_SCOPE, AssertedBy, Assertion, Fact
from munr.rules import ConfirmAction, RejectAction, resolve_confirm, resolve_reject

from .conftest import TEST_SCOPE

FIELDS = {"scope", "key", "value", "asserted_by", "confirmed_at"}


class SeedableStore(console.FactSource, Protocol):
    @property
    def snapshot_path(self) -> Path | None: ...

    def set(self, assertion: Assertion) -> object: ...


class MemoryStore:
    def __init__(self, snapshot_path: Path) -> None:
        self.snapshot_path = snapshot_path
        self.facts: dict[tuple[str, str], Fact] = {}

    def set(self, a: Assertion) -> None:
        confirmed = datetime.now(timezone.utc) if a.asserted_by is AssertedBy.USER else None
        self.facts[(a.scope, a.key)] = Fact(a.scope, a.key, a.value, a.asserted_by, confirmed)

    def get(self, scope: str, key: str) -> Fact | None:
        return self.facts.get((scope, key))

    def list(self, scope: str | None = None, status: str | None = None) -> list[Fact]:
        return [
            f
            for (s, _), f in sorted(self.facts.items())
            if (scope is None or s == scope)
            and (status is None or f.is_confirmed == (status == "confirmed"))
        ]

    def confirm(self, scope: str, key: str) -> ConfirmAction:
        action = resolve_confirm(self.get(scope, key))
        if action is ConfirmAction.CONFIRM:
            fact = self.facts[(scope, key)]
            self.facts[(scope, key)] = replace(fact, confirmed_at=datetime.now(timezone.utc))
            snapshot.write(self.snapshot_path, self.list(GLOBAL_SCOPE, "confirmed"))
        return action

    def reject(self, scope: str, key: str) -> RejectAction:
        action = resolve_reject(self.get(scope, key))
        if action is RejectAction.REJECT:
            del self.facts[(scope, key)]
            snapshot.write(self.snapshot_path, self.list(GLOBAL_SCOPE, "confirmed"))
        return action


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class Console:
    base: str
    token: str
    store: SeedableStore | None
    snapshot_path: Path

    def call(self, method: str, path: str, token: str | None = None) -> Response:
        req = urllib.request.Request(self.base + path, method=method)
        if token is not None:
            req.add_header(console.TOKEN_HEADER, token)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return Response(resp.status, dict(resp.headers), resp.read())
        except urllib.error.HTTPError as err:
            return Response(err.code, dict(err.headers), err.read())

    def get(self, path: str) -> Response:
        return self.call("GET", path)

    def post(self, path: str, token: str | None = None) -> Response:
        return self.call("POST", path, token)

    def act(self, scope: str, key: str, verb: str, token: str | None = None) -> Response:
        return self.post(f"/api/facts/{quote(scope, safe='')}/{quote(key, safe='')}/{verb}", token)

    def seed(self, scope: str, key: str, value: str, *, confirmed: bool) -> None:
        assert self.store is not None
        by = AssertedBy.USER if confirmed else AssertedBy.MODEL
        self.store.set(Assertion(scope, key, value, by))

    def facts(self, path: str, key: str) -> list[dict[str, Any]]:
        return [f for f in self.get(path).json() if f["key"] == key]

    def keys(self, path: str) -> set[tuple[str, str]]:
        return {(f["scope"], f["key"]) for f in self.get(path).json()}


@contextmanager
def serve(opener: console.StoreOpener, token: str) -> Iterator[str]:
    server = console.make_server(opener, token, port=0)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host!s}:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(params=["memory", "db"])
def app(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Console]:
    store: SeedableStore
    if request.param == "memory":
        store = MemoryStore(tmp_path / "snapshot.txt")
    else:
        store = request.getfixturevalue("snapshotting_store")
    snap = store.snapshot_path
    assert snap is not None

    def opener() -> AbstractContextManager[console.FactSource]:
        return nullcontext(store)

    token = console.new_token()
    with serve(opener, token) as base:
        yield Console(base, token, store, snap)


def test_facts_lists_only_confirmed_and_pending_lists_only_pending(app: Console) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-live", "yes", confirmed=True)
    app.seed(GLOBAL_SCOPE, "pv4-proposed", "maybe", confirmed=False)

    confirmed = app.keys("/api/facts")
    pending = app.keys("/api/facts/pending")

    assert (GLOBAL_SCOPE, "pv4-live") in confirmed
    assert (GLOBAL_SCOPE, "pv4-proposed") not in confirmed
    assert (GLOBAL_SCOPE, "pv4-proposed") in pending
    assert (GLOBAL_SCOPE, "pv4-live") not in pending


def test_fact_items_carry_the_five_fields(app: Console) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-live", "yes", confirmed=True)
    app.seed(GLOBAL_SCOPE, "pv4-proposed", "maybe", confirmed=False)

    [live] = app.facts("/api/facts", "pv4-live")
    [proposed] = app.facts("/api/facts/pending", "pv4-proposed")

    assert set(live) == FIELDS
    assert live["asserted_by"] == "user"
    datetime.fromisoformat(live["confirmed_at"])
    assert proposed == {
        "scope": GLOBAL_SCOPE,
        "key": "pv4-proposed",
        "value": "maybe",
        "asserted_by": "model",
        "confirmed_at": None,
    }


def test_facts_filters_by_scope(app: Console) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-live", "g", confirmed=True)
    app.seed(TEST_SCOPE, "pv4-live", "p", confirmed=True)

    scoped = app.keys(f"/api/facts?scope={quote(TEST_SCOPE)}")

    assert (TEST_SCOPE, "pv4-live") in scoped
    assert all(scope == TEST_SCOPE for scope, _ in scoped)


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
@pytest.mark.parametrize("verb", ["confirm", "reject"])
def test_mutation_without_a_valid_token_is_forbidden(
    app: Console, verb: str, token: str | None
) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-proposed", "maybe", confirmed=False)

    resp = app.act(GLOBAL_SCOPE, "pv4-proposed", verb, token)

    assert resp.status == 403
    assert (GLOBAL_SCOPE, "pv4-proposed") in app.keys("/api/facts/pending")


def test_token_never_appears_in_unauthenticated_responses(app: Console) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-proposed", "maybe", confirmed=False)

    for path in ["/ui", "/", "/health", "/api/facts", "/api/facts/pending", "/nope"]:
        assert app.token.encode() not in app.get(path).body, path


def test_ui_serves_the_console_page(app: Console) -> None:
    resp = app.get("/ui")

    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("text/html")
    assert b"Pending Proposals" in resp.body


def test_confirming_a_pending_global_fact_promotes_it_and_updates_the_snapshot(
    app: Console,
) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-proposed", "Portland, OR metro", confirmed=False)

    resp = app.act(GLOBAL_SCOPE, "pv4-proposed", "confirm", app.token)

    assert resp.status == 200
    assert (GLOBAL_SCOPE, "pv4-proposed") in app.keys("/api/facts")
    assert (GLOBAL_SCOPE, "pv4-proposed") not in app.keys("/api/facts/pending")
    assert "- pv4-proposed: Portland, OR metro" in snapshot.read(app.snapshot_path)


def test_confirming_an_already_confirmed_fact_is_idempotent(app: Console) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-live", "yes", confirmed=True)
    before = app.facts("/api/facts", "pv4-live")

    resp = app.act(GLOBAL_SCOPE, "pv4-live", "confirm", app.token)

    assert resp.status == 200
    assert app.facts("/api/facts", "pv4-live") == before


@pytest.mark.parametrize("verb", ["confirm", "reject"])
def test_acting_on_a_missing_fact_is_not_found(app: Console, verb: str) -> None:
    assert app.act(GLOBAL_SCOPE, "pv4-absent", verb, app.token).status == 404


def test_rejecting_a_pending_fact_deletes_it(app: Console) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-proposed", "maybe", confirmed=False)

    resp = app.act(GLOBAL_SCOPE, "pv4-proposed", "reject", app.token)

    assert resp.status == 200
    assert (GLOBAL_SCOPE, "pv4-proposed") not in app.keys("/api/facts/pending")
    assert (GLOBAL_SCOPE, "pv4-proposed") not in app.keys("/api/facts")


def test_rejecting_a_confirmed_fact_conflicts_and_leaves_it(app: Console) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-live", "yes", confirmed=True)

    resp = app.act(GLOBAL_SCOPE, "pv4-live", "reject", app.token)

    assert resp.status == 409
    assert (GLOBAL_SCOPE, "pv4-live") in app.keys("/api/facts")


def test_same_key_in_two_scopes_is_acted_on_independently(app: Console) -> None:
    app.seed(GLOBAL_SCOPE, "pv4-shared", "g", confirmed=False)
    app.seed(TEST_SCOPE, "pv4-shared", "p", confirmed=False)

    assert app.act(TEST_SCOPE, "pv4-shared", "confirm", app.token).status == 200
    assert (TEST_SCOPE, "pv4-shared") in app.keys("/api/facts")
    assert (GLOBAL_SCOPE, "pv4-shared") in app.keys("/api/facts/pending")

    assert app.act(GLOBAL_SCOPE, "pv4-shared", "reject", app.token).status == 200
    assert (GLOBAL_SCOPE, "pv4-shared") not in app.keys("/api/facts/pending")
    assert (TEST_SCOPE, "pv4-shared") in app.keys("/api/facts")


def test_keys_with_reserved_characters_round_trip(app: Console) -> None:
    app.seed(GLOBAL_SCOPE, "pv4/odd key?", "v", confirmed=False)

    assert app.act(GLOBAL_SCOPE, "pv4/odd key?", "confirm", app.token).status == 200
    assert (GLOBAL_SCOPE, "pv4/odd key?") in app.keys("/api/facts")


def test_unknown_routes_are_not_found(app: Console) -> None:
    assert app.get("/api/facts/%2A/x/confirm").status == 405
    assert app.post("/api/facts/%2A/x/delete", app.token).status == 404
    assert app.get("/api/nope").status == 404


@pytest.fixture
def down() -> Iterator[Console]:
    def unreachable() -> AbstractContextManager[console.FactSource]:
        raise psycopg.OperationalError("connection refused")

    token = console.new_token()
    with serve(unreachable, token) as base:
        yield Console(base, token, None, Path())


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/facts"),
        ("GET", "/api/facts/pending"),
        ("POST", "/api/facts/%2A/location/confirm"),
        ("POST", "/api/facts/%2A/location/reject"),
    ],
)
def test_unreachable_database_is_a_json_503(down: Console, method: str, path: str) -> None:
    resp = down.call(method, path, down.token)

    assert resp.status == 503
    body = resp.json()
    assert isinstance(body, dict) and "error" in body


def test_unreachable_database_still_serves_the_page(down: Console) -> None:
    assert down.get("/ui").status == 200


def test_server_binds_loopback_only() -> None:
    def opener() -> AbstractContextManager[console.FactSource]:
        raise AssertionError("binding must not open the store")

    server = console.make_server(opener, console.new_token(), port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def _launch_url() -> str:
    proc = subprocess.Popen(
        [sys.executable, "-m", "munr.console", "--port", "0", "--no-browser"],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if "token=" in line:
                return str(line[line.index("http://") :]).strip()
        raise AssertionError("console printed no launch URL")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_each_launch_prints_a_loopback_url_with_a_fresh_token() -> None:
    first, second = _launch_url(), _launch_url()

    for url in (first, second):
        assert url.startswith("http://127.0.0.1:")
        assert "/ui?token=" in url
    assert first.split("token=")[1] != second.split("token=")[1]


def test_console_imports_no_heavy_stack() -> None:
    heavy = ["psycopg2", "torch", "sentence_transformers"]
    probe = f"import sys, munr.console\nprint([m for m in {heavy!r} if m in sys.modules])"
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30, check=True
    )
    assert result.stdout.strip() == "[]"
