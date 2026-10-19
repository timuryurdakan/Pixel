#!/usr/bin/env python3
"""Pixel Office v2 — local cloud dev server (CLOUD.md §13.1).

    python3 dev_server.py                 # http://127.0.0.1:8787 — web/ + /api/* (MemoryStorage, random PIXEL_SECRET)
    python3 dev_server.py --demo          # also creates room "Demo Oda" with 3 fake friends posting
    python3 dev_server.py --relay         # v3: also starts tools/dev_relay.py on ws://127.0.0.1:8788 (V3.md §4.4)
    UPSTASH_REDIS_REST_URL=… UPSTASH_REDIS_REST_TOKEN=… PIXEL_SECRET=… python3 dev_server.py

Mounts the same `pixel_cloud.app.dispatch` as the Vercel functions. Does not emulate the CDN cache; the
`X-Pixel-Cache-Hint` header shows the s-maxage Vercel would apply.
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import secrets
import re
import signal
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pixel_cloud import __version__
from pixel_cloud.app import App, Request, Response
from pixel_cloud.vercel import ApiHandler

WEB_ROOT = Path(__file__).resolve().parent / "web"
DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8787

DEFAULT_RELAY_PORT = 8788


def security_headers(connect_extra: str = "") -> list[tuple[str, str]]:
    """Same headers as vercel.json; `connect_extra` adds the dev relay origin to connect-src (--relay)."""
    connect = "'self'" + (f" {connect_extra}" if connect_extra else "")
    return [
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        ("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                    f"script-src 'self'; connect-src {connect}; frame-ancestors 'none'; base-uri 'none'"),
    ]


SECURITY_HEADERS = security_headers()
MIME_TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8", ".webmanifest": "application/manifest+json",
}
_SMAXAGE = re.compile(r"s-maxage=(\d+)")


class DevServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, app: App, web_root: Path, headers: list[tuple[str, str]] | None = None) -> None:
        self.app = app
        self.web_root = Path(web_root).resolve()
        self.security_headers = headers or SECURITY_HEADERS
        super().__init__(addr, DevHandler)


class DevHandler(ApiHandler):
    server: DevServer
    server_version = f"PixelCloudDev/{__version__}"

    @property
    def extra_headers(self) -> list[tuple[str, str]]:   # type: ignore[override]
        return self.server.security_headers

    def dispatch_api(self, req: Request) -> Response:
        resp = self.server.app.dispatch(req)
        cc = resp.header("Cache-Control") or ""
        m = _SMAXAGE.search(cc)
        if m:
            resp.headers.append(("X-Pixel-Cache-Hint", f"s-maxage={m.group(1)}"))
        return resp

    def handle_any(self) -> None:
        try:
            path = urlsplit(self.path).path
        except ValueError:
            path = ""
        if path == "/api" or path.startswith("/api/"):
            self.handle_api()
        elif self.command in ("GET", "HEAD"):
            self.serve_static(path)
        else:
            self.write_response(Response(405, [("Content-Type", "application/json; charset=utf-8"),
                                               ("Allow", "GET, HEAD"), ("Cache-Control", "no-store")],
                                         b'{"error":"method_not_allowed","message":"Method not allowed"}'),
                                close=True)
            self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = handle_any

    def _not_found(self) -> None:
        self.write_response(Response(404, [("Content-Type", "application/json; charset=utf-8"),
                                           ("Cache-Control", "no-store")],
                                     b'{"error":"not_found","message":"Not found"}'))

    def serve_static(self, url_path: str) -> None:
        """Same path-traversal guard as v1 httpd.py."""
        try:
            url_path = unquote(url_path, errors="strict")
        except UnicodeDecodeError:
            self._not_found()
            return
        if "\x00" in url_path or "\\" in url_path:
            self._not_found()
            return
        root = self.server.web_root
        rel = url_path.lstrip("/")
        if rel == "" or url_path.endswith("/"):
            rel += "index.html"
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
        rel_parts = target.relative_to(root).parts
        if rel_parts and rel_parts[0] == "hook":
            ctype, cache = "text/plain; charset=utf-8", "no-cache"
        elif rel_parts and rel_parts[0] == "connector":      # same as vercel.json (V3.md §13.5)
            ctype = "application/zip" if target.suffix == ".pyz" else "text/plain; charset=utf-8"
            cache = "no-cache"
        else:
            ctype = MIME_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] \
                or "application/octet-stream"
            cache = "no-cache" if target.name == "sw.js" else "no-store"
        self.write_response(Response(200, [("Content-Type", ctype), ("Cache-Control", cache)], body))


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="dev_server.py", description="Pixel Office cloud dev server (web/ + /api/*).")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    p.add_argument("--demo", action="store_true", help='create room "Demo Oda" with 3 fake friends posting')
    p.add_argument("--relay", action="store_true",
                   help=f"v3: also run the local relay (tools/dev_relay.py) on 127.0.0.1:{DEFAULT_RELAY_PORT}")
    p.add_argument("--relay-port", type=int, default=DEFAULT_RELAY_PORT, help="port for --relay (0 = any free port)")
    p.add_argument("--version", action="version", version=f"Pixel Office cloud {__version__}")
    return p.parse_args(argv)


def _sigterm(signum, frame) -> None:  # noqa: ANN001
    raise KeyboardInterrupt


def start_relay(port: int, web_port: int):
    """Start tools/dev_relay.py in-process for --relay; sets RELAY_SECRET / PIXEL_RELAY_URL for this process so the
    Vercel-style app issues tickets for it. → (server, ws_url, secret)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
    import dev_relay
    secret = os.environ.get("RELAY_SECRET") or ""
    if len(secret) < 32:
        secret = secrets.token_urlsafe(32)
    relay = dev_relay.Relay(secret, dev_relay.default_origins(web_port))
    srv, url = dev_relay.serve_in_thread(relay, "127.0.0.1", port)
    srv.relay = relay
    os.environ["RELAY_SECRET"] = secret
    os.environ["PIXEL_RELAY_URL"] = url
    return srv, url, secret


def main(argv=None) -> int:
    args = parse_args(argv)
    relay_srv = None
    headers = SECURITY_HEADERS
    if args.relay:
        try:
            relay_srv, relay_url, relay_secret = start_relay(args.relay_port, args.port)
        except OSError as e:
            print(f"Cannot start the relay on 127.0.0.1:{args.relay_port}: {e}", file=sys.stderr)
            return 1
        headers = security_headers(relay_url)
    app = App()
    try:
        srv = DevServer((args.host, args.port), app, WEB_ROOT, headers)
    except OSError as e:
        print(f"Cannot listen on {args.host}:{args.port}: {e}", file=sys.stderr)
        if relay_srv:
            relay_srv.shutdown()
            relay_srv.server_close()
        return 1
    port = srv.server_address[1]
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::", "") else args.host
    base = f"http://{shown}:{port}"
    print(f"Pixel Office cloud dev server at {base}  (storage: {app.storage.name})", flush=True)
    if relay_srv:
        from pixel_cloud.relay import parse_origins
        relay_srv.relay.allowed = parse_origins(f"http://127.0.0.1:{port},http://localhost:{port},{base}")
        print(f"Relay (dev_relay) at {relay_url}  — PIXEL_RELAY_URL={relay_url}", flush=True)
        print(f"RELAY_SECRET={relay_secret}  (this run only)", flush=True)
    if app.settings.secret_generated:
        print("PIXEL_SECRET not set: using a random secret for this run (write keys change on restart).", flush=True)
    demo = None
    if args.demo:
        from pixel_cloud.demo import DemoWriter
        demo = DemoWriter(app, base_url=base)
        demo.create_room()
        demo.start()
        print(f"Demo room: {demo.join_url}", flush=True)
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, _sigterm)
    except ValueError:
        pass
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nShutting down…", flush=True)
    finally:
        if demo:
            demo.stop()
        srv.server_close()
        if relay_srv:
            relay_srv.shutdown()
            relay_srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
