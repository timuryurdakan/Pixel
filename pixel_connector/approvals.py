"""Local confirmation (V3.md §6.6) and the loopback control API (§5.3).

- `Approvals`: pending confirmations (task, agentCreate, runtime, peerRevoke) with a timeout; answered from the CLI
  (`pixel-connector approve|deny <id>`) or the page `http://127.0.0.1:<port>/onay`.
- `ToolApproval` (Phase 19, kind `toolApproval`): a Claude Code permission request or multiple-choice question sent by
  the local hook over /ctl. Besides the CLI and /onay it can be answered by a paired browser over E2E
  (`approval.answer`). Single-use random id, expiry, first answer wins; answers to questions are option *indices*
  checked against the request.
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
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import localhttp
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
        self.by: Optional[str] = None

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "preview": self.preview, "agent": self.agent,
                "expiresAt": int(self.expires * 1000), "createdAt": int(self.created * 1000)}


# --------------------------------------------------------------------------------------------------
# Phase 19: tool approvals (Claude Code PermissionRequest hook → connector → paired browsers)
# --------------------------------------------------------------------------------------------------

TOOL_KIND = "toolApproval"
TOOL_ID_RE = re.compile(r"^a[A-Za-z0-9_-]{16}\Z")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}\Z")
SESSION_RE = re.compile(r"^[0-9a-f]{20}\Z")
MATCH_RE = re.compile(r"^[0-9a-f]{16,64}\Z")
MAX_TOOL_PENDING = 16
SUMMARY_MAX = 200
AGENT_MAX = 40
PROJECT_MAX = 32
MAX_QUESTIONS = 4
MAX_OPTIONS = 6
QUESTION_MAX = 200
HEADER_MAX = 30
LABEL_MAX = 60
DESCRIPTION_MAX = 120
RAW_TEXT_MAX = 4096           # longer input strings are refused, not truncated (they are not from our hook)
# Control characters, zero-width and bidirectional formatting characters (Trojan Source) never reach a screen.
_INVISIBLE_RE = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")


class ToolLimit(Exception):
    """Too many tool approvals are waiting at once."""


def clean_text(value, limit: int) -> str:
    """Display text from the hook: invisible characters → space, whitespace collapsed, cut to `limit` with "…"."""
    s = " ".join(_INVISIBLE_RE.sub(" ", value).split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def _text(body: dict, key: str, limit: int, required: bool = False) -> Optional[str]:
    v = body.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or len(v) > RAW_TEXT_MAX:
        raise ValueError(key)
    out = clean_text(v, limit)
    if required and not out:
        raise ValueError(key)
    return out or None


def validate_tool_request(body) -> dict:
    """/ctl/approvals/tool body → normalized request. Raises ValueError(field) for anything outside the allow-list."""
    if not isinstance(body, dict):
        raise ValueError("body")
    ask = body.get("ask", "tool")
    if ask not in ("tool", "question"):
        raise ValueError("ask")
    tool = body.get("tool")
    if not isinstance(tool, str) or not TOOL_NAME_RE.match(tool):
        raise ValueError("tool")
    req = {"ask": ask, "tool": tool, "summary": _text(body, "summary", SUMMARY_MAX) or "",
           "agent": _text(body, "agent", AGENT_MAX) or "Claude Code", "project": _text(body, "project", PROJECT_MAX)}
    for key, rx in (("session", SESSION_RE), ("match", MATCH_RE)):
        v = body.get(key)
        if v is not None and (not isinstance(v, str) or not rx.match(v)):
            raise ValueError(key)
        req[key] = v
    if ask == "question":
        qs = body.get("questions")
        if not isinstance(qs, list) or not 1 <= len(qs) <= MAX_QUESTIONS:
            raise ValueError("questions")
        req["questions"] = [_question(q) for q in qs]
    elif body.get("questions") is not None:
        raise ValueError("questions")
    return req


def _question(q) -> dict:
    if not isinstance(q, dict):
        raise ValueError("questions")
    opts = q.get("options")
    multi = q.get("multiSelect", False)
    if not isinstance(multi, bool) or not isinstance(opts, list) or not 1 <= len(opts) <= MAX_OPTIONS:
        raise ValueError("questions")
    out_opts = []
    for o in opts:
        if not isinstance(o, dict):
            raise ValueError("questions")
        out_opts.append({"label": _text(o, "label", LABEL_MAX, required=True),
                         "description": _text(o, "description", DESCRIPTION_MAX)})
    return {"question": _text(q, "question", QUESTION_MAX, required=True), "header": _text(q, "header", HEADER_MAX),
            "multiSelect": multi, "options": out_opts}


def check_answers(questions: List[dict], answers) -> Optional[List[List[int]]]:
    """Answers are one list of option indices per question: exactly one for single choice, at least one (no duplicates)
    for multiSelect. → normalized (sorted) lists, or None when anything is off."""
    if not isinstance(answers, list) or len(answers) != len(questions):
        return None
    out = []
    for q, a in zip(questions, answers):
        if not isinstance(a, list) or not a or len(a) > len(q["options"]):
            return None
        if any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(q["options"]) for i in a):
            return None
        if len(set(a)) != len(a) or (not q["multiSelect"] and len(a) != 1):
            return None
        out.append(sorted(a))
    return out


def summary_hash(text: str) -> str:
    """Audit: a short hash instead of the command/summary text (never logged in full)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class ToolApproval(Pending):
    def __init__(self, pid: str, req: dict, on_done: Callable[[str], None], expires: float, created: float) -> None:
        if req["ask"] == "question":
            title = "Soru: %s" % (req["questions"][0]["header"] or req["questions"][0]["question"])
            preview = "\n".join(q["question"] + " — " + " / ".join(o["label"] for o in q["options"])
                                for q in req["questions"])
        else:
            title = "İzin: %s" % req["tool"]
            preview = req["summary"]
        super().__init__(pid, TOOL_KIND, title[:80], preview, req["agent"], on_done, expires, created)
        self.req = req
        self.answers: Optional[List[List[int]]] = None
        self.done = threading.Event()

    @property
    def is_question(self) -> bool:
        return self.req["ask"] == "question"

    def wire(self) -> dict:
        """The `approval` object of `approval.request` (V3.md §3.4)."""
        out = {"id": self.id, "ask": self.req["ask"], "agent": self.req["agent"], "tool": self.req["tool"],
               "summary": self.req["summary"], "project": self.req["project"],
               "expiresAt": int(self.expires * 1000), "createdAt": int(self.created * 1000)}
        if self.is_question:
            out["questions"] = self.req["questions"]
        return out

    def public(self) -> dict:
        d = super().public()
        d.update(self.wire())
        d["kind"] = TOOL_KIND
        return d

    def outcome(self) -> dict:
        """What the hook gets back from /ctl/approvals/tool."""
        decision = {"ok": "allow", "denied": "deny", "timeout": "timeout"}.get(self.result or "", "withdrawn")
        out = {"decision": decision, "id": self.id}
        if decision == "allow" and self.is_question:
            out["answers"] = self.answers
        return out


class Approvals:
    def __init__(self, timeout_s: float = 120.0, notify_fn: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.timeout_s = timeout_s
        self.notify_fn = notify_fn or notify
        self.clock = clock
        self._lock = threading.Lock()
        self.pending: Dict[str, Pending] = {}

    def request(self, kind: str, title: str, preview: Optional[str], agent_name: Optional[str],
                on_done: Callable[[str], None], timeout_s: Optional[float] = None) -> Pending:
        """on_done(result) with result ∈ ok | denied | timeout, called exactly once (from a resolver thread).
        `timeout_s` overrides `confirm.timeoutS` (v4-C workflow checkpoints have their own)."""
        now = self.clock()
        pid = "p" + secrets.token_hex(4)
        p = Pending(pid, kind, title, preview, agent_name, on_done,
                    now + (self.timeout_s if timeout_s is None else timeout_s), now)
        with self._lock:
            self.pending[pid] = p
        try:
            self.notify_fn(agent_name or title)
        except Exception:
            pass
        return p

    def request_tool(self, req: dict, timeout_s: float, on_done: Callable[[ToolApproval, str], None]) -> ToolApproval:
        """Phase 19: register a validated tool approval (no desktop notification: Claude Code shows its own dialog)."""
        now = self.clock()
        holder: List[ToolApproval] = []
        p = ToolApproval("a" + secrets.token_urlsafe(12), req, lambda res: on_done(holder[0], res), now + timeout_s,
                         now)
        holder.append(p)
        with self._lock:
            if sum(1 for x in self.pending.values() if isinstance(x, ToolApproval)) >= MAX_TOOL_PENDING:
                raise ToolLimit()
            self.pending[p.id] = p
        return p

    def resolve(self, pid: str, ok: bool, by: str = "local") -> bool:
        return self.answer(pid, ok, None, by) == "ok"

    def answer(self, pid: str, ok: bool, answers=None, by: str = "local", tool_only: bool = False) -> str:
        """First answer wins. → "ok" | "unknown" (no such pending, already answered or expired; also any non-tool
        item when `tool_only`) | "bad_answer" (a question approved without valid answers, or answers where none
        belong). A question can only be approved with answers, so the CLI and /onay can only deny it."""
        with self._lock:
            p = self.pending.get(pid)
            if p is None or (tool_only and not isinstance(p, ToolApproval)):
                return "unknown"
            if isinstance(p, ToolApproval) and p.is_question and ok:
                norm = check_answers(p.req["questions"], answers)
                if norm is None:
                    return "bad_answer"
                p.answers = norm
            elif answers is not None:
                return "bad_answer"
            p.by = by
            del self.pending[pid]
        self._done(p, "ok" if ok else "denied")
        return "ok"

    def withdraw(self, session: Optional[str] = None, match: Optional[str] = None, pid: Optional[str] = None) -> List[str]:
        """The question was answered elsewhere (Claude Code's own dialog, the hook process went away): drop matching
        tool approvals with result "withdrawn". `session` + optional `match`, or one `pid`."""
        with self._lock:
            hit = [p for p in self.pending.values() if isinstance(p, ToolApproval)
                   and ((pid is not None and p.id == pid)
                        or (pid is None and session is not None and p.req.get("session") == session
                            and (match is None or p.req.get("match") == match)))]
            for p in hit:
                del self.pending[p.id]
        for p in hit:
            self._done(p, "withdrawn")
        return [p.id for p in hit]

    def _finish(self, pid: str, result: str) -> bool:
        with self._lock:
            p = self.pending.pop(pid, None)
        if p is None:
            return False
        self._done(p, result)
        return True

    @staticmethod
    def _done(p: Pending, result: str) -> None:
        p.result = result
        try:
            p.on_done(result)
        except Exception:
            pass
        if isinstance(p, ToolApproval):
            p.done.set()

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
    # SO_REUSEADDR lets a restarted service rebind the port while the old socket is in TIME_WAIT. On Windows the
    # same flag would let another local process bind the port too, so it stays off there.
    allow_reuse_address = os.name != "nt"

    def __init__(self, port: int, routes: Dict[Tuple[str, str], Route], approvals: Approvals,
                 token: Optional[str] = None) -> None:
        self.routes = routes
        self.approvals = approvals
        self.token = token or secrets.token_urlsafe(24)
        super().__init__(("127.0.0.1", port), _Handler)
        self.port = self.server_address[1]
        self.csrf = hmac.new(self.token.encode(), b"pixel-onay-csrf", hashlib.sha256).hexdigest()[:32]
        self.page_token = hmac.new(self.token.encode(), b"pixel-onay-page", hashlib.sha256).hexdigest()[:32]

    def handle_error(self, request, client_address) -> None:
        """A client that hangs up mid-answer (CLI timeout, closed browser tab, shutdown) is normal: no traceback in the
        journal for it (v2.md H28). Anything else gets one short line, never the request (it may carry previews)."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout)):
            return
        sys.stderr.write("ctl: istek işlenemedi (%s)\n" % type(exc).__name__)

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

    def _client_gone(self) -> bool:
        """Long-poll routes (Phase 19): True once the client has closed its end (the hook process ended or was
        killed). A readable socket with nothing to read means EOF."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

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
            self.close_connection = True            # the body was not read: never parse it as the next request
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
            if getattr(fn, "long_poll", False):
                status, obj = fn(body, query, self._client_gone)
            else:
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
            self.server.approvals.resolve(form.get("id", ""), form.get("karar") == "onayla", by="page")
            self._send(303, b"", "text/plain", [("Location", "/onay")])
            return
        if method != "GET" or path != "/onay":
            self._send(404, b"", "text/plain")
            return
        rows = []
        for p in self.server.approvals.list():
            question = p.get("ask") == "question"
            approve = ("<small>Seçenekleri telefondan ya da Claude Code penceresinden seçin.</small> " if question
                       else "<button name=karar value=onayla>Onayla</button> ")
            rows.append(
                "<li><b>%s</b> · %s<br><pre>%s</pre><form method=post action=/onay/karar>"
                "<input type=hidden name=csrf value=%s><input type=hidden name=id value=%s>"
                "%s<button name=karar value=reddet>Reddet</button>"
                "</form></li>" % (html.escape(p["agent"] or ""), html.escape(p["title"]),
                                  html.escape(p["preview"] or ""), self.server.csrf, html.escape(p["id"]), approve))
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
        with localhttp.urlopen(req, timeout=timeout) as r:      # never via a proxy (H8)
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None
