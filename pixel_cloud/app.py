"""`dispatch(Request) -> Response`: routing, auth, limits, caching headers and errors (CLOUD.md §5, §8, §15.3).

Transport-agnostic: the Vercel adapter (`vercel.py`), `dev_server.py`, the demo writer and the tests all
call `App.dispatch`. Nothing here logs request bodies, keys or the Authorization header.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from . import derive, ids, relay, residents
from . import settings as S
from .ratelimit import RateLimiter, retry_after_seconds
from .storage import StorageError, StorageNotConfigured, storage_from_env
from .validate import Update, ValidationError, parse_cloud_rename, parse_json, parse_status_body, parse_update, sanitize

log = logging.getLogger("pixel_cloud")

HOOK_CLIENT = Path(__file__).resolve().parent.parent / "web" / "hook" / "pixel_hook.py"
JSON_CT = "application/json; charset=utf-8"
_HOST_RE = re.compile(r"^(?:[A-Za-z0-9.-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])(?::\d{1,5})?\Z")


# -- request / response ------------------------------------------------------------------------------

class Headers:
    """Case-insensitive read-only view over a dict / http.client.HTTPMessage."""

    def __init__(self, src: Mapping[str, str] | None = None) -> None:
        self._d: dict[str, str] = {}
        if src is not None:
            items = src.items()
            for k, v in items:
                self._d.setdefault(str(k).lower(), str(v))

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._d.get(name.lower(), default)

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._d


class BodyTooLarge(Exception):
    pass


@dataclass
class Request:
    method: str
    path: str                               # path + query, as received
    headers: Mapping[str, str] | Headers = field(default_factory=dict)
    body: bytes | None = None               # already-read body (tests, demo) …
    client_ip: str = ""
    rfile: BinaryIO | None = None           # … or a stream to read lazily (adapters), after size checks
    scheme: str = "http"
    body_consumed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.headers, Headers):
            self.headers = Headers(self.headers)
        self.method = self.method.upper()
        if self.rfile is not None and self.body is None:
            self.body_consumed = False


@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: bytes = b""

    def header(self, name: str) -> str | None:
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return None

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8")) if self.body else None


class HttpError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: list[tuple[str, str]] | None = None,
                 extra: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.headers = headers or []
        self.extra = extra or {}


def _json_response(status: int, obj: object, cache: str = "no-store",
                   headers: list[tuple[str, str]] | None = None) -> Response:
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    h = [("Content-Type", JSON_CT), ("Cache-Control", cache), ("Access-Control-Allow-Origin", "*")]
    return Response(status, h + (headers or []), body)


def _error_response(e: HttpError) -> Response:
    obj = {"error": e.code, "message": e.message, **e.extra}
    return _json_response(e.status, obj, headers=e.headers)


def _rate_limited(wait: float, code: str = "rate_limited", message: str = "Too many requests") -> HttpError:
    secs = retry_after_seconds(wait)
    return HttpError(429, code, message, headers=[("Retry-After", str(secs))], extra={"retryAfter": secs})


# -- body reading ------------------------------------------------------------------------------------

def _read_chunked(rfile: BinaryIO, cap: int) -> bytes:
    out = bytearray()
    while True:
        line = rfile.readline(1026)
        if not line:
            break
        try:
            size = int(line.split(b";", 1)[0].strip() or b"0", 16)
        except ValueError:
            raise HttpError(400, "bad_request", "Malformed chunked body") from None
        if size == 0:
            while rfile.readline(1026) not in (b"\r\n", b"\n", b""):   # trailers
                pass
            break
        if len(out) + size > cap:
            raise BodyTooLarge
        out += rfile.read(size)
        rfile.readline(3)
    return bytes(out)


def read_body(req: Request, cap: int) -> bytes:
    """Body ≤ cap bytes. Checks Content-Length before reading; BodyTooLarge leaves the stream unread."""
    if req.body is not None:
        if len(req.body) > cap:
            raise BodyTooLarge
        return req.body
    if req.rfile is None:
        return b""
    cl = req.headers.get("Content-Length")
    if cl is not None:
        try:
            n = int(cl.strip())
        except ValueError:
            raise HttpError(400, "bad_request", "Invalid Content-Length") from None
        if n < 0:
            raise HttpError(400, "bad_request", "Invalid Content-Length")
        if n > cap:
            raise BodyTooLarge
        data = req.rfile.read(n) if n else b""
        req.body, req.body_consumed = data, True
        return data
    if "chunked" in (req.headers.get("Transfer-Encoding") or "").lower():
        data = _read_chunked(req.rfile, cap)
        req.body, req.body_consumed = data, True
        return data
    req.body, req.body_consumed = b"", True
    return b""


def drain_body(req: Request, limit: int = 8 * 1024 * 1024) -> None:
    """Read and discard a declared body (keeps the connection usable); give up above `limit`."""
    if req.rfile is None or req.body_consumed:
        return
    try:
        n = int((req.headers.get("Content-Length") or "").strip())
    except ValueError:
        return
    if n > limit:
        return
    while n > 0:
        chunk = req.rfile.read(min(n, 65536))
        if not chunk:
            break
        n -= len(chunk)
    req.body_consumed = True


# -- hook mapper (cc_hook) -----------------------------------------------------------------------------

_mapper_lock = threading.Lock()
_mapper_cache: dict[str, object] = {}


def load_hook_mapper(path: Path = HOOK_CLIENT):
    """`map_event` from web/hook/pixel_hook.py (Phase 8). None when the file is absent or broken."""
    with _mapper_lock:
        if "fn" in _mapper_cache:
            return _mapper_cache["fn"]
        fn = None
        try:
            if path.is_file():
                spec = importlib.util.spec_from_file_location("pixel_hook_server_copy", path)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    cand = getattr(mod, "map_event", None)
                    fn = cand if callable(cand) else None
        except Exception as e:   # a broken client file must not break the other endpoints
            log.warning("cannot load hook mapper: %s", type(e).__name__)
            fn = None
        _mapper_cache["fn"] = fn
        return fn


def _update_to_dict(u: object) -> dict | None:
    if isinstance(u, dict):
        return dict(u)
    for attr in ("as_payload", "to_dict", "_asdict"):
        f = getattr(u, attr, None)
        if callable(f):
            d = f()
            return dict(d) if isinstance(d, dict) else None
    try:
        import dataclasses
        if dataclasses.is_dataclass(u):
            return dataclasses.asdict(u)
    except Exception:  # pragma: no cover
        pass
    return None


# -- the app -----------------------------------------------------------------------------------------

class App:
    def __init__(self, storage=None, settings: S.Settings | None = None, clock: Callable[[], float] = time.time,
                 env: Mapping[str, str] | None = None, mapper: Callable | None | str = "auto") -> None:
        self.clock = clock
        self.settings = settings or S.Settings.from_env(env)
        self.storage = storage if storage is not None else storage_from_env(env, clock)
        self._mapper = mapper
        self.limit_room_write = RateLimiter(*S.RATE_ROOM_WRITE, clock=clock)
        self.limit_ip_write = RateLimiter(*S.RATE_IP_WRITE, clock=clock)
        self.limit_ip_create = RateLimiter(*S.RATE_IP_CREATE, clock=clock)
        self.limit_room_rename = RateLimiter(*S.RATE_ROOM_RENAME, clock=clock)
        self.limit_room_ticket = RateLimiter(*S.RATE_RELAY_TICKET, clock=clock)
        self.limit_ip_ticket = RateLimiter(*S.RATE_RELAY_TICKET, clock=clock)
        self.limit_ip_state = RateLimiter(*S.RATE_IP_STATE, clock=clock)
        self._ping: tuple[float, bool] | None = None                       # (checked_at, ok) for /api/health
        self._lock = threading.Lock()
        self._ttl_refreshed: OrderedDict[str, float] = OrderedDict()   # room → last EXPIRE (this instance)
        self._prev: OrderedDict[str, tuple[str, int, int]] = OrderedDict()  # agent → (st, ss, sa)
        self._micro: dict[str, tuple[float, dict[str, str]]] = {}       # room → (fetched_at, HGETALL)
        self.routes: dict[str, dict[str, Callable[[Request, dict], Response]]] = {
            "/api/config": {"GET": self.h_config},
            "/api/health": {"GET": self.h_health},
            "/api/rooms": {"POST": self.h_rooms},
            "/api/state": {"GET": self.h_state},
            "/api/status": {"POST": self.h_status},
            "/api/cc_hook": {"POST": self.h_cc_hook},
            "/api/rename": {"POST": self.h_rename},
            "/api/residents": {"POST": self.h_residents},
            "/api/relay_ticket": {"POST": self.h_relay_ticket},
        }

    # -- helpers ----------------------------------------------------------------------------------
    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    @property
    def mapper(self):
        if self._mapper == "auto":
            return load_hook_mapper()
        return self._mapper

    def _need_secret(self) -> bytes:
        if not self.settings.secret:
            raise HttpError(503, "storage_not_configured", "Server secret (PIXEL_SECRET) is not configured")
        return self.settings.secret

    def _room(self, q: dict) -> str:
        room = (q.get("room") or [""])[0]
        if not ids.valid_room_id(room):
            raise HttpError(404, "not_found", "Unknown room")
        return room

    def _auth(self, req: Request, room: str) -> None:
        secret = self._need_secret()
        if not ids.check_write_key(secret, room, ids.bearer(req.headers)):
            raise HttpError(401, "unauthorized", "Missing or invalid write key")

    def _body(self, req: Request, cap: int) -> bytes:
        try:
            return read_body(req, cap)
        except BodyTooLarge:
            raise HttpError(413, "payload_too_large", f"Body larger than {cap} bytes") from None

    def _rate(self, *checks: tuple[RateLimiter, str]) -> None:
        taken: list[tuple[RateLimiter, str]] = []
        for lim, key in checks:
            wait = lim.check(key)
            if wait > 0:
                for l2, k2 in taken:
                    l2.refund(k2)
                raise _rate_limited(wait)
            taken.append((lim, key))

    def _refresh_ttl(self, room: str) -> bool:
        now = self.clock()
        with self._lock:
            last = self._ttl_refreshed.get(room)
            if last is not None and now - last < S.TTL_REFRESH_EVERY:
                return False
            self._ttl_refreshed[room] = now
            self._ttl_refreshed.move_to_end(room)
            while len(self._ttl_refreshed) > 10_000:
                self._ttl_refreshed.popitem(last=False)
            return True

    def _invalidate(self, room: str) -> None:
        with self._lock:
            self._micro.pop(room, None)

    def _read_fields(self, room: str) -> dict[str, str]:
        now = self.clock()
        with self._lock:
            hit = self._micro.get(room)
            if hit is not None and now - hit[0] < S.MICRO_CACHE_S:
                return hit[1]
        fields = self.storage.read_room(room)
        with self._lock:
            self._micro[room] = (now, fields)
            if len(self._micro) > 1000:
                for k in [k for k, (t, _) in self._micro.items() if now - t >= S.MICRO_CACHE_S]:
                    del self._micro[k]
        return fields

    def _base_url(self, req: Request) -> str:
        if self.settings.public_url:
            return self.settings.public_url
        host = (req.headers.get("X-Forwarded-Host") or req.headers.get("Host") or "").split(",")[0].strip()
        proto = (req.headers.get("X-Forwarded-Proto") or req.scheme or "https").split(",")[0].strip().lower()
        if proto not in ("http", "https"):
            proto = "https"
        if host and _HOST_RE.match(host):
            return f"{proto}://{host}"
        return ""

    # -- dispatch ---------------------------------------------------------------------------------
    def dispatch(self, req: Request) -> Response:
        try:
            parts = urlsplit(req.path)
            path = unquote(parts.path)
            q = parse_qs(parts.query, keep_blank_values=True)
        except ValueError:
            return _error_response(HttpError(400, "bad_request", "Malformed request target"))
        route = self.routes.get(path)
        try:
            if route is None:
                raise HttpError(404, "not_found", "No such endpoint")
            method = "GET" if req.method == "HEAD" and "GET" in route else req.method
            if method == "OPTIONS" and "POST" in route:
                return Response(204, [
                    ("Access-Control-Allow-Origin", "*"),
                    ("Access-Control-Allow-Methods", "POST, OPTIONS"),
                    ("Access-Control-Allow-Headers", "Authorization, Content-Type"),
                    ("Access-Control-Max-Age", "86400"),
                    ("Cache-Control", "no-store"),
                ])
            handler = route.get(method)
            if handler is None:
                allow = ", ".join(sorted(set(route) | ({"HEAD"} if "GET" in route else {"OPTIONS"})))
                raise HttpError(405, "method_not_allowed", "Method not allowed", headers=[("Allow", allow)])
            return handler(req, q)
        except HttpError as e:
            return _error_response(e)
        except ValidationError as e:
            return _error_response(HttpError(400, "bad_request", str(e)))
        except StorageNotConfigured:
            return _error_response(HttpError(503, "storage_not_configured", "Storage is not configured"))
        except StorageError as e:
            log.warning("storage error: %s", e)
            return _error_response(HttpError(503, "storage_unavailable", "Storage is unavailable, retry later",
                                             headers=[("Retry-After", "5")]))
        except Exception as e:   # never leak a traceback (or a request body) to the client or the log
            log.error("internal error in %s: %s", path, type(e).__name__)
            return _error_response(HttpError(500, "internal_error", "Internal server error"))

    # -- GET /api/config, /api/health -------------------------------------------------------------
    def h_config(self, req: Request, q: dict) -> Response:
        st = self.settings
        return _json_response(200, {
            "mode": "cloud", "version": __version__,
            "createRequiresToken": bool(st.create_token),
            "storage": self.storage.name,
            "poll": {"activeMs": st.poll_active_ms, "calmMs": st.poll_calm_ms, "emptyMs": st.poll_empty_ms},
            "limits": {"maxAgentsPerRoom": S.MAX_AGENTS_PER_ROOM, "maxBatch": S.MAX_BATCH},
            "relay": {"url": st.relay_url, "v": 1} if st.relay_enabled else None,
            "features": ["control"] if st.relay_enabled else [],
        }, cache=S.CONFIG_CACHE_CONTROL)

    def h_health(self, req: Request, q: dict) -> Response:
        now = self.clock()
        cached = self._ping
        if cached is not None and 0 <= now - cached[0] < S.HEALTH_PING_TTL:
            ok = cached[1]
        else:
            try:
                ok = bool(self.storage.ping())
            except Exception:
                ok = False
            self._ping = (now, ok)
        return _json_response(200, {"ok": True, "version": __version__, "storage": self.storage.name,
                                    "storageOk": ok})

    # -- POST /api/rooms --------------------------------------------------------------------------
    def h_rooms(self, req: Request, q: dict) -> Response:
        secret = self._need_secret()
        body = self._body(req, S.MAX_CREATE_BODY)
        data = parse_json(body) if body.strip() else {}
        if not isinstance(data, dict):
            raise HttpError(400, "bad_request", "body must be a JSON object")
        wait = self.limit_ip_create.check(req.client_ip or "?")   # before the token check: no brute force
        if wait > 0:
            raise _rate_limited(wait)
        token = self.settings.create_token
        if token:
            given = data.get("createToken")
            if not isinstance(given, str) or not ids.constant_time_eq(given, token):
                raise HttpError(403, "forbidden", "A valid create token is required")
        try:
            name = derive_room_name(data.get("name"))
        except ValidationError as e:
            self.limit_ip_create.refund(req.client_ip or "?")
            raise HttpError(400, "bad_request", str(e)) from None
        room = ids.new_room_id()
        created = self.now_ms()
        meta = json.dumps({"name": name, "createdAt": created, "v": 1}, ensure_ascii=False, separators=(",", ":"))
        self.storage.create_room(room, meta)
        with self._lock:
            self._ttl_refreshed[room] = self.clock()
        key = ids.write_key(secret, room)
        base = self._base_url(req)
        view = f"{base}/?room={room}"
        return _json_response(201, {"roomId": room, "name": name, "writeKey": key, "viewUrl": view,
                                    "joinUrl": f"{view}#key={key}", "createdAt": created})

    # -- GET /api/state ---------------------------------------------------------------------------
    def h_state(self, req: Request, q: dict) -> Response:
        room = self._room(q)
        self._rate((self.limit_ip_state, req.client_ip or "?"))     # before any storage command (S16-03)
        fields = self._read_fields(room)
        snap = derive.snapshot(room, fields, self.now_ms(), self.settings)
        if snap.purge:
            try:
                self.storage.delete(room, snap.purge)
                self._invalidate(room)
            except StorageError as e:   # purge is best effort; the snapshot is still correct
                log.warning("purge failed: %s", e)
        return _json_response(200, snap.payload, cache=derive.cache_control(snap.activity))

    # -- POST /api/status -------------------------------------------------------------------------
    def h_status(self, req: Request, q: dict) -> Response:
        room = self._room(q)
        self._auth(req, room)
        body = self._body(req, S.MAX_STATUS_BODY)
        self._rate((self.limit_ip_write, req.client_ip or "?"), (self.limit_room_write, room))
        try:
            updates = parse_status_body(body)
        except ValidationError as e:
            raise HttpError(400, "bad_request", str(e)) from None
        aids = self.write_updates(room, updates)
        return _json_response(200, {"ok": True, "ids": aids, "serverTime": self.now_ms()})

    def write_updates(self, room: str, updates: list[Update]) -> list[str]:
        now = self.now_ms()
        puts: dict[str, str] = {}
        dels: list[str] = []
        out: list[str] = []
        prev_new: dict[str, tuple[str, int, int]] = {}
        for u in updates:
            oid = u.owner_id or ids.default_owner_id(u.owner)
            aid = ids.agent_id(room, oid, u.session, u.sub)
            out.append(aid)
            if u.state == "offline":
                dels.append(aid)
                puts.pop(aid, None)
                prev_new.pop(aid, None)
                continue
            if aid in dels:
                dels.remove(aid)
            parent = ids.agent_id(room, oid, u.session, None) if u.sub else None
            with self._lock:
                prev = prev_new.get(aid) or self._prev.get(aid)
            rec = derive.make_record(u, owner_id=oid, parent_id=parent, now_ms=now, prev=prev)
            puts[aid] = derive.dump_record(rec)
            prev_new[aid] = (rec["st"], rec["ss"], rec["sa"])
        if dels:
            self.storage.delete(room, dels)
        if puts:
            created = self.storage.put_agents(room, puts, self._refresh_ttl(room))
            new = [f for f, c in created.items() if c]
            if new:
                n_agents = sum(1 for f in self.storage.keys(room) if ids.AGENT_ID_RE.match(f))
                excess = n_agents - S.MAX_AGENTS_PER_ROOM
                if excess > 0:
                    drop = new[-excess:]
                    self.storage.delete(room, drop)
                    for f in drop:
                        prev_new.pop(f, None)
                    self._invalidate(room)
                    raise HttpError(429, "room_full",
                                    f"Room already has {S.MAX_AGENTS_PER_ROOM} agents",
                                    headers=[("Retry-After", "60")], extra={"retryAfter": 60})
        with self._lock:
            for aid in dels:
                self._prev.pop(aid, None)
            for aid, v in prev_new.items():
                self._prev[aid] = v
                self._prev.move_to_end(aid)
            while len(self._prev) > 4096:
                self._prev.popitem(last=False)
        self._invalidate(room)
        return out

    # -- POST /api/rename -------------------------------------------------------------------------
    def h_rename(self, req: Request, q: dict) -> Response:
        room = self._room(q)
        self._auth(req, room)
        body = self._body(req, S.MAX_RENAME_BODY)
        self._rate((self.limit_ip_write, req.client_ip or "?"), (self.limit_room_rename, room))
        try:
            rr = parse_cloud_rename(body)
        except ValidationError as e:
            raise HttpError(400, "bad_request", str(e)) from None
        field_name = ("n:" if rr.scope == "agent" else "no:") + rr.target
        now = self.now_ms()
        if rr.name is None:
            self.storage.delete(room, [field_name])
        else:
            value = json.dumps({"name": rr.name, "ts": now}, ensure_ascii=False, separators=(",", ":"))
            created = self.storage.put_agents(room, {field_name: value}, self._refresh_ttl(room))
            if created.get(field_name):
                self._enforce_name_limit(room, field_name, now)
        self._invalidate(room)
        return _json_response(200, {"ok": True, "name": rr.name, "scope": rr.scope, "serverTime": now})

    # -- POST /api/residents ----------------------------------------------------------------------
    def h_residents(self, req: Request, q: dict) -> Response:
        """Replace the room's always-present resident agents (pixel_cloud/residents.py); [] clears."""
        room = self._room(q)
        self._auth(req, room)
        body = self._body(req, S.MAX_RENAME_BODY)
        self._rate((self.limit_ip_write, req.client_ip or "?"), (self.limit_room_rename, room))
        try:
            items = residents.parse_residents(body)
        except ValidationError as e:
            raise HttpError(400, "bad_request", str(e)) from None
        if items:
            self.storage.put_agents(room, {residents.FIELD: residents.dump_residents(items)}, self._refresh_ttl(room))
        else:
            self.storage.delete(room, [residents.FIELD])
        self._invalidate(room)
        now = self.now_ms()
        ids = [residents.resident_id(room, i, r.name) for i, r in enumerate(items)]
        return _json_response(200, {"ok": True, "ids": ids, "serverTime": now})

    # -- POST /api/relay_ticket (v3, V3.md §6.2, §13.4) ----------------------------------------------
    def h_relay_ticket(self, req: Request, q: dict) -> Response:
        """Short-lived relay ticket for the room's write-key holder. 0 storage commands; nothing is logged."""
        room = self._room(q)
        st = self.settings
        if not st.relay_enabled:
            raise HttpError(503, "relay_not_configured", "The realtime relay is not configured on this server")
        self._auth(req, room)
        body = self._body(req, S.MAX_TICKET_BODY)
        data = parse_json(body)
        if not isinstance(data, dict):
            raise HttpError(400, "bad_request", "body must be a JSON object")
        role, pid = data.get("role"), data.get("id")
        if role not in relay.ROLES:
            raise HttpError(400, "bad_request", "'role' must be 'browser' or 'device'")
        if not isinstance(pid, str) or not relay.PEER_ID_RE.match(pid):
            raise HttpError(400, "bad_request", "'id' must be 16 characters [a-z2-7]")
        self._rate((self.limit_ip_ticket, req.client_ip or "?"), (self.limit_room_ticket, room))
        ticket, exp = relay.mint_ticket(st.relay_secret, room, role, pid, int(self.clock()))
        return _json_response(200, {"url": f"{st.relay_url}/v1/rooms/{room}/ws", "ticket": ticket, "exp": exp})

    def _enforce_name_limit(self, room: str, new_field: str, now: int) -> None:
        keys = self.storage.keys(room)
        names = [k for k in keys if k.startswith(("n:", "no:"))]
        if len(names) <= S.MAX_NAME_FIELDS:
            return
        fields = self.storage.read_room(room)
        stale = []
        for k in names:
            if k == new_field or not k.startswith("n:") or k[2:] in fields:
                continue
            obj = derive.load_json_obj(fields.get(k)) or {}
            ts = obj.get("ts") if isinstance(obj.get("ts"), int) else 0
            if now - ts > S.PURGE_AFTER * 1000:
                stale.append(k)
        if len(names) - len(stale) > S.MAX_NAME_FIELDS:
            self.storage.delete(room, [new_field])
            raise HttpError(429, "room_full", f"Room already has {S.MAX_NAME_FIELDS} names",
                            headers=[("Retry-After", "60")], extra={"retryAfter": 60})
        self.storage.delete(room, stale)

    # -- POST /api/cc_hook (opt-in raw hook fallback) ---------------------------------------------
    def h_cc_hook(self, req: Request, q: dict) -> Response:
        room = self._room(q)
        self._auth(req, room)
        try:
            body = read_body(req, S.MAX_CC_HOOK_BODY)
        except BodyTooLarge:
            drain_body(req)
            return Response(204, [("Cache-Control", "no-store"), ("Access-Control-Allow-Origin", "*")])
        self._rate((self.limit_ip_write, req.client_ip or "?"), (self.limit_room_write, room))
        owner_raw = req.headers.get("X-Pixel-Owner") or ""
        owner = sanitize(unquote(owner_raw), S.OWNER_MAX)
        if not owner:
            raise HttpError(400, "bad_request", "X-Pixel-Owner header is required")
        oid = req.headers.get("X-Pixel-Owner-Id") or None
        if oid is not None and not ids.OWNER_ID_RE.match(oid):
            raise HttpError(400, "bad_request", "X-Pixel-Owner-Id has an invalid format")
        oid = oid or ids.default_owner_id(owner)
        try:
            event = parse_json(body)
        except ValidationError as e:
            raise HttpError(400, "bad_request", str(e)) from None
        del body
        req.body = None                          # drop the raw hook JSON as early as possible
        if not isinstance(event, dict):
            raise HttpError(400, "bad_request", "body must be a JSON object")
        mapper = self.mapper
        if mapper is None:
            raise HttpError(503, "hook_mapper_unavailable", "Server-side hook mapping is not installed")
        secret = self._need_secret()
        import hashlib
        import hmac as _hmac
        salt = _hmac.new(secret, b"pixel-cc-salt:v1:" + room.encode(), hashlib.sha256).hexdigest()[:32]
        cfg = {"server": "", "room": room, "name": owner, "owner": owner, "ownerId": oid, "salt": salt,
               "send_mcp_names": False, "hide_projects": False, "project_aliases": {}, "enabled": True}
        try:
            raw_updates = mapper(event, cfg, None) or []
        except Exception as e:
            log.warning("hook mapper failed: %s", type(e).__name__)
            raw_updates = []
        finally:
            del event
        updates: list[Update] = []
        for item in list(raw_updates)[: S.MAX_BATCH]:
            d = _update_to_dict(item)
            if d is None:
                continue
            d.update(owner=owner, ownerId=oid, source="claude-code")
            d.pop("name", None)                  # the hook path never names agents (§15.2)
            try:
                updates.append(parse_update(d))
            except ValidationError:
                continue
        if updates:
            self.write_updates(room, updates)
        return _json_response(200, {})


def derive_room_name(value: object) -> str:
    from .validate import clean_room_name
    return clean_room_name(value)


_default: App | None = None
_default_lock = threading.Lock()


def get_app() -> App:
    global _default
    with _default_lock:
        if _default is None:
            from .storage import get_storage
            _default = App(storage=get_storage())
        return _default


def dispatch(req: Request) -> Response:
    """Module-level entry point used by api/*.py (through vercel.py) and dev_server.py."""
    return get_app().dispatch(req)
