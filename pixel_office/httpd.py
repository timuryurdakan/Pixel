"""HTTP server: static files, JSON API and the SSE event stream (ARCHITECTURE.md §3.2–3.4, §7.6)."""

import getpass
import json
import logging
import mimetypes
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pixel_cloud import ids as cloud_ids
from pixel_cloud import settings as CS
from pixel_cloud.app import BodyTooLarge, HttpError, Request as CloudRequest, read_body
from pixel_cloud.validate import ValidationError, parse_local_rename, parse_status_body

from . import __version__
from .config import SSE_KEEPALIVE, SSE_RETRY_MS, WEB_ROOT
from .models import now_ms
from .store import AgentStore

log = logging.getLogger("pixel_office")

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}
SSE_POLL = 0.5   # s; how often an idle SSE handler checks for server shutdown
CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)


class PixelOfficeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], store: AgentStore, web_root: Path, *, reported=None,
                 names=None, write_key: str | None = None) -> None:
        self.store = store
        self.web_root = Path(web_root).resolve()
        self.shutting_down = threading.Event()
        self.reported = reported          # ReportedAgents → POST /api/status (CLOUD.md §16)
        self.names = names                # NamesStore → POST /api/agents/rename (CLOUD.md §15.4)
        self.write_key = write_key        # required as Bearer on the POST endpoints when set
        super().__init__(addr, RequestHandler)
        host, port = self.server_address[:2]
        self.allowed_origins = {f"http://{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]")}
        if host not in ("0.0.0.0", "::", ""):
            self.allowed_origins.add(f"http://{host}:{port}" if ":" not in host else f"http://[{host}]:{port}")

    @property
    def features(self) -> list[str]:
        return [f for f, on in (("rename", self.names is not None), ("status", self.reported is not None)) if on]

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001 (stdlib signature)
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return  # client went away; nothing to report
        log.exception("error while handling request from %s", client_address)


class RequestHandler(BaseHTTPRequestHandler):
    server: PixelOfficeServer
    server_version = "PixelOffice/1.0"

    # -- routing ----------------------------------------------------------------------------
    def do_GET(self) -> None:
        try:
            path = unquote(urlsplit(self.path).path)
        except ValueError:
            self._send_json(400, {"error": "bad request"})
            return
        try:
            if path == "/api/agents":
                self._send_json(200, self.server.store.snapshot())
            elif path == "/api/health":
                store = self.server.store
                health = {"ok": True, "mode": store.mode, "agents": len(store.ids())}
                if self.server.features:
                    health["features"] = self.server.features
                self._send_json(200, health)
            elif path == "/api/config" and self.server.features:
                # v2 page (CLOUD.md §9.1): anything but {"mode":"cloud"} means local. Answering here (instead of
                # the v1 404) keeps the browser console clean and carries the features for the ✎ button.
                self._send_json(200, {"mode": "local", "version": __version__, "features": self.server.features})
            elif path == "/events":
                self.handle_sse()
            else:
                self._serve_static(path)
        except CLIENT_GONE:
            self.close_connection = True

    def _method_not_allowed(self) -> None:
        self._send_json(405, {"error": "method not allowed"}, extra_headers={"Allow": "GET"})

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _method_not_allowed

    def do_POST(self) -> None:
        try:
            path = unquote(urlsplit(self.path).path)
        except ValueError:
            path = ""
        try:
            if path == "/api/status" and self.server.reported is not None:
                self._api_write(self._handle_status, CS.MAX_STATUS_BODY)
            elif path == "/api/agents/rename" and self.server.names is not None:
                self._api_write(self._handle_rename, CS.MAX_RENAME_BODY)
            else:
                self._method_not_allowed()
        except CLIENT_GONE:
            self.close_connection = True

    # -- local write API (CLOUD.md §15.4, §16) ---------------------------------------------------
    def _api_error(self, e: HttpError) -> None:
        headers = dict(e.headers)
        self._send_json(e.status, {"error": e.code, "message": e.message, **e.extra}, extra_headers=headers)

    def _guard(self) -> None:
        """CSRF guard + optional key. Origin (if sent) must be our own; JSON content type forces a
        CORS preflight for cross-site browsers, which this server never approves."""
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.allowed_origins:
            raise HttpError(403, "forbidden", "Cross-origin requests are not allowed")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise HttpError(400, "bad_request", "Content-Type must be application/json")
        if self.server.write_key is not None:
            if not cloud_ids.constant_time_eq(cloud_ids.bearer(self.headers), self.server.write_key):
                raise HttpError(401, "unauthorized", "Missing or invalid write key")

    def _api_write(self, handler, cap: int) -> None:
        req = CloudRequest("POST", self.path, self.headers, rfile=self.rfile,
                           client_ip=self.client_address[0] if self.client_address else "")
        try:
            self._guard()
            try:
                body = read_body(req, cap)
            except BodyTooLarge:
                raise HttpError(413, "payload_too_large", f"Body larger than {cap} bytes") from None
            handler(body)
        except HttpError as e:
            if not req.body_consumed:
                self.close_connection = True
            self._api_error(e)

    def _handle_status(self, body: bytes) -> None:
        from .reported import RoomFull
        try:
            updates = parse_status_body(body, default_owner=_os_user())
        except ValidationError as e:
            raise HttpError(400, "bad_request", str(e)) from None
        try:
            agent_ids = self.server.reported.apply(updates)
        except RoomFull:
            raise HttpError(429, "room_full", f"Already {CS.MAX_AGENTS_PER_ROOM} reported agents",
                            headers=[("Retry-After", "60")], extra={"retryAfter": 60}) from None
        self._send_json(200, {"ok": True, "ids": agent_ids, "serverTime": now_ms()})

    def _handle_rename(self, body: bytes) -> None:
        try:
            rr = parse_local_rename(body)
        except ValidationError as e:
            raise HttpError(400, "bad_request", str(e)) from None
        store = self.server.store
        agent = store.get(rr.target)
        if agent is None:
            raise HttpError(404, "not_found", "Unknown agent")
        if rr.scope == "project":
            if agent.is_subagent or not agent.project_path:
                raise HttpError(400, "bad_request", "This agent has no project path; use scope 'agent'")
            self.server.names.set("project", agent.project_path, rr.name)
        else:
            self.server.names.set("agent", agent.id, rr.name)
        store.refresh_names()
        current = store.get(rr.target) or agent
        self._send_json(200, {"ok": True, "agent": current.to_dict(names=True)})

    # -- responses --------------------------------------------------------------------------
    def _send_json(self, code: int, obj: dict, extra_headers=None) -> None:
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        items = extra_headers.items() if isinstance(extra_headers, dict) else (extra_headers or [])
        for k, v in items:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _not_found(self) -> None:
        self._send_json(404, {"error": "not found"})

    def _serve_static(self, url_path: str) -> None:
        if "\x00" in url_path or "\\" in url_path:
            self._not_found()
            return
        root = self.server.web_root
        rel = url_path.lstrip("/")
        if rel == "" or url_path.endswith("/"):
            rel = rel + "index.html"
        try:
            target = (root / rel).resolve()
        except (OSError, RuntimeError, ValueError):
            self._not_found()
            return
        if not target.is_relative_to(root) or not target.is_file():
            self._not_found()
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._not_found()
            return
        ctype = MIME_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] \
            or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- SSE --------------------------------------------------------------------------------
    @staticmethod
    def _frame(name: str, data: dict, seq: int) -> bytes:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return f"id: {seq}\nevent: {name}\ndata: {payload}\n\n".encode("utf-8")

    def handle_sse(self) -> None:
        store = self.server.store
        q, snap, seq = store.subscribe_with_snapshot()
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(f"retry: {SSE_RETRY_MS}\n\n".encode())
            self.wfile.write(self._frame("snapshot", snap, seq))
            self.wfile.flush()
            last_write = time.monotonic()
            while not self.server.shutting_down.is_set():
                try:
                    ev = q.get(timeout=SSE_POLL)
                except queue.Empty:
                    if time.monotonic() - last_write >= SSE_KEEPALIVE:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_write = time.monotonic()
                    continue
                if ev.seq < 0:   # overflow sentinel: client too slow → close, it will re-snapshot
                    break
                self.wfile.write(self._frame(ev.name, ev.data, ev.seq))
                self.wfile.flush()
                last_write = time.monotonic()
        except OSError:
            pass   # client disconnected (BrokenPipe, ConnectionReset, …)
        finally:
            store.unsubscribe(q)

    # -- logging ----------------------------------------------------------------------------
    def log_message(self, format: str, *args) -> None:  # noqa: A002 (stdlib signature)
        pass

    def log_error(self, format: str, *args) -> None:  # noqa: A002
        log.warning("http %s: %s", self.address_string(), format % args)


def _os_user() -> str:
    try:
        return getpass.getuser() or "local"
    except Exception:
        return "local"


def make_server(host: str, port: int, store: AgentStore, web_root: Path = WEB_ROOT, *, reported=None,
                names=None, write_key: str | None = None) -> PixelOfficeServer:
    """Without `reported`/`names` the server is exactly v1 (no write endpoints, v1 /api/health)."""
    return PixelOfficeServer((host, port), store, web_root, reported=reported, names=names, write_key=write_key)
