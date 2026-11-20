"""HTTP contract of the Console.

Reads are open. Every Fact mutation must carry the Console Token in the ``X-Munr-Token``
header; without it the request is refused with ``403`` before the store is touched. Nothing
served without the token ever contains it.

Routes address a Fact by its full identity ``(scope, key)``; each segment is percent-encoded,
so the global scope ``*`` travels as ``%2A``.
"""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
import webbrowser
from collections.abc import Callable
from contextlib import AbstractContextManager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg

from .model import Fact
from .rules import ConfirmAction, RejectAction
from .store import FactStore

HOST = "127.0.0.1"
DEFAULT_PORT = 3334
TOKEN_HEADER = "X-Munr-Token"


class FactSource(Protocol):
    def list(self, scope: str | None = None, status: str | None = None) -> list[Fact]: ...
    def confirm(self, scope: str, key: str) -> ConfirmAction: ...
    def reject(self, scope: str, key: str) -> RejectAction: ...


StoreOpener = Callable[[], AbstractContextManager[FactSource]]

_CONFIRM_STATUS = {
    ConfirmAction.CONFIRM: HTTPStatus.OK,
    ConfirmAction.ALREADY: HTTPStatus.OK,
    ConfirmAction.MISSING: HTTPStatus.NOT_FOUND,
}
_REJECT_STATUS = {
    RejectAction.REJECT: HTTPStatus.OK,
    RejectAction.REFUSE_LIVE: HTTPStatus.CONFLICT,
    RejectAction.MISSING: HTTPStatus.NOT_FOUND,
}


def new_token() -> str:
    return secrets.token_urlsafe(32)


def launch_url(port: int, token: str) -> str:
    return f"http://{HOST}:{port}/ui?token={token}"


def _ui_html() -> bytes:
    packaged = resources.files("munr").joinpath("ui.html")
    if packaged.is_file():
        return packaged.read_bytes()
    return (Path(__file__).resolve().parents[2] / "mcp-server" / "ui.html").read_bytes()


def _to_json(fact: Fact) -> dict[str, str | None]:
    return {
        "scope": fact.scope,
        "key": fact.key,
        "value": fact.value,
        "asserted_by": fact.asserted_by.value,
        "confirmed_at": fact.confirmed_at.isoformat() if fact.confirmed_at else None,
    }


def make_server(open_store: StoreOpener, token: str, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """A loopback-only server whose handlers open a store per request via ``open_store``."""
    ui = _ui_html()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # the default access log would print the launch request line, token included

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: object) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._json(status, {"error": message})

        def _with_store(self, act: Callable[[FactSource], tuple[int, object]]) -> None:
            try:
                with open_store() as store:
                    status, payload = act(store)
            except psycopg.OperationalError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "facts database unreachable")
                return
            self._json(status, payload)

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path in ("/", "/ui"):
                self._send(HTTPStatus.OK, ui, "text/html; charset=utf-8")
            elif url.path == "/health":
                self._json(HTTPStatus.OK, {"status": "ok"})
            elif url.path == "/api/facts":
                scope = parse_qs(url.query).get("scope", [None])[0]
                self._with_store(
                    lambda s: (HTTPStatus.OK, [_to_json(f) for f in s.list(scope, "confirmed")])
                )
            elif url.path == "/api/facts/pending":
                self._with_store(
                    lambda s: (HTTPStatus.OK, [_to_json(f) for f in s.list(None, "pending")])
                )
            elif _fact_action(url.path) is not None:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "use POST")
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:
            target = _fact_action(urlsplit(self.path).path)
            if target is None:
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            presented = self.headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(presented.encode(), token.encode()):
                self._error(HTTPStatus.FORBIDDEN, "missing or invalid Console Token")
                return
            scope, key, verb = target
            self._with_store(lambda s: _apply(s, scope, key, verb))

    return ThreadingHTTPServer((HOST, port), Handler)


def _fact_action(path: str) -> tuple[str, str, str] | None:
    parts = path.split("/")
    if len(parts) != 6 or parts[:3] != ["", "api", "facts"] or parts[5] not in ("confirm", "reject"):
        return None
    scope, key = unquote(parts[3]), unquote(parts[4])
    if not scope or not key:
        return None
    return scope, key, parts[5]


def _apply(store: FactSource, scope: str, key: str, verb: str) -> tuple[int, object]:
    action: ConfirmAction | RejectAction
    if verb == "confirm":
        action = store.confirm(scope, key)
        status = _CONFIRM_STATUS[action]
    else:
        action = store.reject(scope, key)
        status = _REJECT_STATUS[action]
    return status, {"scope": scope, "key": key, "result": action.name.lower()}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="munr-console", description="munr fact review Console")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="print the URL; don't open it")
    args = parser.parse_args(argv)

    token = new_token()
    server = make_server(FactStore.connect, token, args.port)
    url = launch_url(server.server_address[1], token)
    print(f"munr Console: {url}", flush=True)
    print("Open this URL to review Pending Facts. Ctrl+C stops the Console.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
