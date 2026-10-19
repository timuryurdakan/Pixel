"""Local confirmation (V3.md §6.6) and the loopback control API (§5.3).

- `Approvals`: pending confirmations (task, agentCreate, runtime, peerRevoke) with a timeout; answered from the CLI
  (`pixel-connector approve|deny <id>`) or the page `http://127.0.0.1:<port>/onay`.
- `notify(name)`: OS notification with a fixed text that never includes the prompt.
- `ControlServer`: `http://127.0.0.1:<port>/ctl/*` JSON API for the CLI, protected by the token in control.json
  (`X-Pixel-Ctl` header, constant-time compare) and a strict Host check (DNS rebinding). The /onay page uses a
  separate *page token* derived from it (HMAC, Phase 15) in an HttpOnly SameSite=Strict cookie (set once through
  `/onay?t=<page token>`, which the CLI prints) plus a CSRF token on every form. The page token only answers
  approvals: browsers send cookies for 127.0.0.1 to every local port, so it must never unlock the /ctl API (pairing).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from .config import CONTROL_FILE, read_json, write_json

MAX_BODY = 16 * 1024


class Pending:
    def __init__(self, pid: str, kind: str, title: str, preview: Optional[str], agent: Optional[str],
                 on_done: Callable[[str], None], expires: float, created: float) -> None:
        self.id = pid
        self.kind = kind
        self.title = title
        self.preview = preview
        self.agent = agent
        self.on_done = on_done
        self.expires = expires
        self.created = created
        self.result: Optional[str] = None

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "preview": self.preview, "agent": self.agent,
                "expiresAt": int(self.expires * 1000), "createdAt": int(self.created * 1000)}


class Approvals:
    def __init__(self, timeout_s: float = 120.0, notify_fn: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.timeout_s = timeout_s
        self.notify_fn = notify_fn or notify
        self.clock = clock
        self._lock = threading.Lock()
        self.pending: Dict[str, Pending] = {}

    def request(self, kind: str, title: str, preview: Optional[str], agent_name: Optional[str],
                on_done: Callable[[str], None]) -> Pending:
        """on_done(result) with result ∈ ok | denied | timeout, called exactly once (from a resolver thread)."""
        now = self.clock()
        pid = "p" + secrets.token_hex(4)
        p = Pending(pid, kind, title, preview, agent_name, on_done, now + self.timeout_s, now)
        with self._lock:
            self.pending[pid] = p
        try:
            self.notify_fn(agent_name or title)
        except Exception:
            pass
        return p

    def resolve(self, pid: str, ok: bool) -> bool:
        return self._finish(pid, "ok" if ok else "denied")

    def _finish(self, pid: str, result: str) -> bool:
        with self._lock:
            p = self.pending.pop(pid, None)
        if p is None:
            return False
        p.result = result
        try:
            p.on_done(result)
        except Exception:
            pass
        return True

    def list(self) -> List[dict]:
        with self._lock:
            return [p.public() for p in self.pending.values()]

    def tick(self) -> None:
        now = self.clock()
        with self._lock:
            expired = [pid for pid, p in self.pending.items() if now >= p.expires]
        for pid in expired:
            self._finish(pid, "timeout")

    def cancel(self, pid: str) -> None:
        """Withdraw without calling on_done (e.g. the task was cancelled meanwhile)."""
        with self._lock:
            self.pending.pop(pid, None)


def _safe_label(name: str) -> str:
    return re.sub(r"[^\w .·-]", "", str(name or ""), flags=re.UNICODE)[:32] or "ajan"


def notify(agent_name: str, run=None) -> bool:
    """Best-effort local notification. Fixed text, no prompt; the name is reduced to safe characters."""
    if run is None:
        def run(argv, **kw):
            proc = subprocess.Popen(argv, **kw)
            threading.Thread(target=proc.wait, daemon=True).start()     # reap it
            return proc
    text = "Pixel Office: %s için onay bekleniyor" % _safe_label(agent_name)
    try:
        if sys.platform == "darwin":
            run(["osascript", "-e", 'display notification "%s" with title "Pixel Office"' % text.replace('"', "")],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sys.platform == "win32":
            ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]"
                  " > $null; $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1); "
                  "$t.GetElementsByTagName('text')[0].AppendChild($t.CreateTextNode('%s')) > $null; "
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Pixel Office')"
                  ".Show([Windows.UI.Notifications.ToastNotification]::new($t))" % text.replace("'", ""))
            run(["powershell", "-NoProfile", "-Command", ps], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            return True
        exe = shutil.which("notify-send")
        if exe:
            run([exe, "Pixel Office", text], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            return True
    except OSError:
        pass
    return False


# --------------------------------------------------------------------------------------------------
# Loopback control server
# --------------------------------------------------------------------------------------------------

Route = Callable[[dict, dict], Tuple[int, object]]


def load_control(home: str) -> Optional[dict]:
    d = read_json(os.path.join(home, CONTROL_FILE))
    if isinstance(d, dict) and isinstance(d.get("port"), int) and isinstance(d.get("token"), str):
        return d
    return None


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port: int, routes: Dict[Tuple[str, str], Route], approvals: Approvals,
                 token: Optional[str] = None) -> None:
        self.routes = routes
        self.approvals = approvals
        self.token = token or secrets.token_urlsafe(24)
        super().__init__(("127.0.0.1", port), _Handler)
        self.port = self.server_address[1]
        self.csrf = hmac.new(self.token.encode(), b"pixel-onay-csrf", hashlib.sha256).hexdigest()[:32]
        self.page_token = hmac.new(self.token.encode(), b"pixel-onay-page", hashlib.sha256).hexdigest()[:32]

    def page_link(self) -> str:
        return "http://127.0.0.1:%d/onay?t=%s" % (self.port, self.page_token)

    def write_control_file(self, home: str) -> None:
        write_json(os.path.join(home, CONTROL_FILE), {"port": self.port, "token": self.token})

    def start(self) -> threading.Thread:
        th = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True, name="ctl")
        th.start()
        return th


PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"


class _Handler(BaseHTTPRequestHandler):
    server: ControlServer
    server_version = "pixel-connector"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):          # never log requests (they may carry prompt previews)
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in ("127.0.0.1:%d" % self.server.port, "localhost:%d" % self.server.port)

    def _send(self, status: int, body: bytes, ctype: str, extra=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra or []:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> Optional[bytes]:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n < 0 or n > MAX_BODY:
            return None
        return self.rfile.read(n) if n else b""

    def _token_ok(self, given: Optional[str]) -> bool:
        return isinstance(given, str) and hmac.compare_digest(given.encode(), self.server.token.encode())

    def _page_token_ok(self, given: Optional[str]) -> bool:
        return isinstance(given, str) and hmac.compare_digest(given.encode(), self.server.page_token.encode())

    def _cookie_token(self) -> Optional[str]:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "pixel_ctl":
                return v
        return None

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._json(403, {"error": "bad_host"})
            return
        u = urlsplit(self.path)
        path, query = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
        if path == "/onay" or path.startswith("/onay/"):
            self._onay(method, path, query)
            return
        if not path.startswith("/ctl/"):
            self._json(404, {"error": "not_found"})
            return
        if not self._token_ok(self.headers.get("X-Pixel-Ctl")):
            self._json(401, {"error": "unauthorized"})
            return
        body = {}
        if method == "POST":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                self._json(415, {"error": "json_only"})
                return
            raw = self._body()
            try:
                body = json.loads(raw or b"{}") if raw is not None else None
            except ValueError:
                body = None
            if not isinstance(body, dict):
                self._json(400, {"error": "bad_request"})
                return
        fn = self.server.routes.get((method, path))
        if fn is None:
            self._json(404, {"error": "not_found"})
            return
        try:
            status, obj = fn(body, query)
        except Exception as e:
            status, obj = 500, {"error": "internal", "message": type(e).__name__}
        self._json(status, obj)

    # -- /onay page --------------------------------------------------------------------------------
    def _onay(self, method: str, path: str, query: dict) -> None:
        if method == "GET" and path == "/onay" and "t" in query:
            if not self._page_token_ok(query.get("t")):
                self._send(401, b"Yetkisiz", "text/plain; charset=utf-8")
                return
            self._send(303, b"", "text/plain", [("Location", "/onay"),
                                                ("Set-Cookie", "pixel_ctl=%s; HttpOnly; SameSite=Strict; Path=/onay"
                                                 % self.server.page_token)])
            return
        if not self._page_token_ok(self._cookie_token()):
            body = ("<!doctype html><meta charset=utf-8><title>Pixel Office onay</title><p>Bu sayfayı açmak için "
                    "terminalde <code>pixel-connector approve --link</code> komutunun verdiği bağlantıyı kullanın.")
            self._send(401, body.encode(), "text/html; charset=utf-8", [("Content-Security-Policy", PAGE_CSP)])
            return
        if method == "POST" and path == "/onay/karar":
            raw = self._body() or b""
            form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
            if not hmac.compare_digest(form.get("csrf", "").encode(), self.server.csrf.encode()):
                self._send(403, "CSRF".encode(), "text/plain; charset=utf-8")
                return
            self.server.approvals.resolve(form.get("id", ""), form.get("karar") == "onayla")
            self._send(303, b"", "text/plain", [("Location", "/onay")])
            return
        if method != "GET" or path != "/onay":
            self._send(404, b"", "text/plain")
            return
        rows = []
        for p in self.server.approvals.list():
            rows.append(
                "<li><b>%s</b> · %s<br><pre>%s</pre><form method=post action=/onay/karar>"
                "<input type=hidden name=csrf value=%s><input type=hidden name=id value=%s>"
                "<button name=karar value=onayla>Onayla</button> <button name=karar value=reddet>Reddet</button>"
                "</form></li>" % (html.escape(p["agent"] or ""), html.escape(p["title"]),
                                  html.escape(p["preview"] or ""), self.server.csrf, html.escape(p["id"])))
        page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                "<meta http-equiv=refresh content=5><title>Pixel Office onay</title>"
                "<style>body{font:15px system-ui;margin:2em;max-width:50em}pre{white-space:pre-wrap;background:#eee;"
                "padding:.5em}li{margin-bottom:1em}</style><h1>Onay bekleyenler</h1>%s"
                % ("<ul>%s</ul>" % "".join(rows) if rows else "<p>Onay bekleyen bir şey yok."))
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", [("Content-Security-Policy", PAGE_CSP)])


def ctl_request(home: str, method: str, path: str, body: Optional[dict] = None, timeout: float = 10.0):
    """CLI side: call the running service. → (status, obj) or raises OSError when no service answers."""
    import urllib.error
    import urllib.request
    c = load_control(home)
    if c is None:
        raise ConnectionRefusedError("no control.json")
    url = "http://127.0.0.1:%d%s" % (c["port"], path)
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"X-Pixel-Ctl": c["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None
