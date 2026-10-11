"""BaseHTTPRequestHandler adapter around `app.dispatch` (Vercel Python runtime and dev_server.py)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from . import __version__
from .app import Request, Response, dispatch


def client_ip(headers, client_address) -> str:
    xff = headers.get("X-Forwarded-For")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first[:64]
    try:
        return str(client_address[0])
    except (TypeError, IndexError):
        return ""


class ApiHandler(BaseHTTPRequestHandler):
    """Forwards every method to `dispatch`. Subclasses may override `dispatch_api` (tests, dev server)."""
    server_version = f"PixelCloud/{__version__}"
    protocol_version = "HTTP/1.1"
    extra_headers: list[tuple[str, str]] = []

    def dispatch_api(self, req: Request) -> Response:
        return dispatch(req)

    def handle_api(self) -> None:
        scheme = "https" if getattr(self.server, "is_https", False) else "http"
        req = Request(method=self.command, path=self.path, headers=self.headers,
                      client_ip=client_ip(self.headers, self.client_address), rfile=self.rfile, scheme=scheme)
        resp = self.dispatch_api(req)
        if not req.body_consumed:
            self.close_connection = True      # unread request body: the connection can't be reused
        self.write_response(resp, close=not req.body_consumed)

    def write_response(self, resp: Response, close: bool = False) -> None:
        self.send_response(resp.status)
        for k, v in resp.headers:
            self.send_header(k, v)
        for k, v in self.extra_headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp.body)))
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD" and resp.body:
            self.wfile.write(resp.body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = handle_api

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — never log paths/headers/keys
        pass


def make_handler() -> type[BaseHTTPRequestHandler]:
    """A fresh `ApiHandler` subclass (tests). The api/*.py files must spell out `class handler(ApiHandler)`
    instead: Vercel only detects a literal top-level class named `handler`, not an assignment."""
    class handler(ApiHandler):
        pass
    return handler
