#!/usr/bin/env python3
"""Pixel Office local relay: a stdlib Python implementation of the Cloudflare relay protocol (V3.md §3, §4.4).

    RELAY_SECRET=… python3 tools/dev_relay.py [--host 127.0.0.1] [--port 8788] [--origins http://127.0.0.1:8787]
    python3 dev_server.py --relay           # starts this on 127.0.0.1:8788 with a random RELAY_SECRET

Same URL shape (`ws://127.0.0.1:8788/v1/rooms/<rid>/ws`, `GET /health`), the same ticket verification
(`pixel_cloud.relay.verify_ticket`, env `RELAY_SECRET`), the same frames, limits, close codes, error codes and the
same `{"t":"ping"}` → `{"t":"pong"}` auto-response as relay/src/{lib,core}.js. `tests/relay_conformance.py` must pass
unchanged against both. Nothing is logged except start-up; `b` is never parsed, logged or stored.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pixel_cloud.relay import (  # noqa: E402
    PEER_ID_RE, ROLES, SUBPROTOCOL, TicketError, origin_allowed, parse_origins, ticket_from_protocols, verify_ticket,
)
from pixel_connector import ws as W  # noqa: E402

__all__ = ["Relay", "DevRelayServer", "serve_in_thread", "validate_frame", "verify_ticket", "main"]

DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8788
PROTOCOL_VERSION = 1
MAX_FRAME = 98304
MAX_B_BROWSER = 49152
MAX_B_DEVICE = 61440
MAX_DEVICES = 8
MAX_BROWSERS = 24
MAX_SLOTS = 4
SLOT_TTL_MS = 600_000
RATE = {"device": (20.0, 100), "browser": (5.0, 30), "pair": (3 / 60, 3)}
BAD_FRAME_LIMIT = 5
BAD_FRAME_WINDOW_MS = 60_000
RATE_STRIKE_LIMIT = 20
CLOSE_POLICY, CLOSE_TOO_BIG, CLOSE_REPLACED, CLOSE_RATE = 1008, 1009, 4009, 4029
PING_FRAME, PONG_FRAME = '{"t":"ping"}', '{"t":"pong"}'

ROOM_PATH_RE = re.compile(r"^/v1/rooms/([a-z2-7]{26})/ws\Z")
CONN_RE = re.compile(r"^c:[A-Za-z0-9_-]{12}\Z")
SLOT_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}\Z")
CH_RE = re.compile(r"^[A-Za-z0-9_-]{16}\Z")
B64URL_RE = re.compile(r"^[A-Za-z0-9_-]*\Z")
APP_RE = re.compile(r"^[\x20-\x7e]{1,40}\Z")
CAP_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}\Z")

ERR_TEXT = {
    "bad_frame": "Malformed frame", "too_large": "Payload too large", "rate_limited": "Too many messages",
    "device_offline": "Device is not connected", "conn_gone": "Browser connection is gone",
    "slot_unknown": "Unknown or expired pairing slot", "slot_taken": "Pairing slot is in use",
    "slot_limit": "Too many pairing slots", "forbidden": "Not allowed for this role",
    "version": "Unsupported protocol version",
}
HTTP_REASONS = {200: "OK", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
                426: "Upgrade Required", 429: "Too Many Requests", 503: "Service Unavailable"}


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def err_frame(code: str, ref=None) -> str:
    return _dumps({"t": "err", "code": code, "ref": ref, "message": ERR_TEXT.get(code, code)})


def _safe_int(v):
    """JS Number.isSafeInteger semantics (1.0 counts, booleans don't). → int or None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if -(2 ** 53) < v < 2 ** 53 else None
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return None


def _reject_constant(_):
    raise ValueError("non-standard JSON constant")


def validate_frame(text: str, role: str) -> dict:
    """Python twin of `validateFrame` in relay/src/lib.js. {"ok": True, "frame": {...}} | {"ok": False, "code", "ref"}."""
    def fail(code, ref=None):
        return {"ok": False, "code": code, "ref": ref}
    try:
        f = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return fail("bad_frame")
    if not isinstance(f, dict) or not isinstance(f.get("t"), str):
        return fail("bad_frame")
    seq = _safe_int(f.get("seq"))
    ref = seq if seq is not None and seq >= 0 else None
    t = f["t"]
    if t == "hello":
        if _safe_int(f.get("v")) != PROTOCOL_VERSION:
            return fail("version")
        app = f.get("app")
        if app is not None and (not isinstance(app, str) or not APP_RE.match(app)):
            return fail("bad_frame")
        caps = f.get("caps")
        if caps is not None:
            if not isinstance(caps, list) or len(caps) > 16 or \
                    not all(isinstance(c, str) and CAP_RE.match(c) for c in caps):
                return fail("bad_frame")
        return {"ok": True, "frame": {"t": "hello", "v": 1, "app": app, "caps": caps or []}}
    if t == "send":
        to, k, b, ch = f.get("to"), f.get("k"), f.get("b"), f.get("ch")
        if not isinstance(to, str) or ref is None or k not in ("hs", "e") or not isinstance(b, str) \
                or not (ch is None or (isinstance(ch, str) and CH_RE.match(ch))):
            return fail("bad_frame", ref)
        if PEER_ID_RE.match(to):
            target = ("device", to)
        elif CONN_RE.match(to):
            target = ("conn", to)
        elif to.startswith("pair:") and SLOT_RE.match(to[5:]):
            target = ("pair", to[5:])
        else:
            return fail("bad_frame", ref)
        if not b or not B64URL_RE.match(b) or len(b) % 4 == 1:
            return fail("bad_frame", ref)
        if len(b) * 3 // 4 > (MAX_B_DEVICE if role == "device" else MAX_B_BROWSER):
            return fail("too_large", ref)
        if role == "browser" and target[0] == "conn":
            return fail("forbidden", ref)
        if role == "device" and target[0] != "conn":
            return fail("forbidden", ref)
        return {"ok": True, "frame": {"t": "send", "to": to, "target": target, "ch": ch, "seq": ref, "k": k, "b": b}}
    if t in ("pair.open", "pair.close"):
        slot = f.get("slot")
        if not isinstance(slot, str) or not SLOT_RE.match(slot):
            return fail("bad_frame")
        if role != "device":
            return fail("forbidden")
        return {"ok": True, "frame": {"t": t, "slot": slot}}
    if t == "presence.get":
        if role != "browser":
            return fail("forbidden")
        return {"ok": True, "frame": {"t": t}}
    if t == "ping":
        return {"ok": True, "frame": {"t": "ping"}}
    return fail("bad_frame")


class TokenBucket:
    def __init__(self, rate: float, burst: int, now_ms: int) -> None:
        self.rate, self.burst, self.tokens, self.t = rate, burst, float(burst), now_ms

    def take(self, now_ms: int) -> bool:
        dt = max(0, now_ms - self.t) / 1000
        self.t = max(self.t, now_ms)
        self.tokens = min(self.burst, self.tokens + dt * self.rate)
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class Peer:
    def __init__(self, ws: W.WebSocket, role: str, pid: str, conn, since: int) -> None:
        self.ws, self.role, self.id, self.conn, self.since = ws, role, pid, conn, since
        self.app = None
        self.slot, self.slot_exp = None, 0
        self.replaced = False
        self.gone = False
        rate, burst = RATE[role]
        self.bucket = TokenBucket(rate, burst, since)
        self.pair_bucket = None
        self.strikes = 0
        self.bad: list[int] = []

    def send(self, text: str) -> bool:
        if self.gone:
            return False
        try:
            self.ws.send_text(text)
            return True
        except (OSError, W.WebSocketError):
            return False

    def close(self, code: int, reason: str) -> None:
        try:
            self.ws.close(code, reason)
        except (OSError, W.WebSocketError):
            pass


class Room:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.peers: list[Peer] = []

    def live(self, role: str | None = None, exclude: Peer | None = None) -> list[Peer]:
        return [p for p in self.peers if not p.replaced and not p.gone and p is not exclude
                and (role is None or p.role == role)]


class Relay:
    def __init__(self, secret: bytes | str | None, allowed_origins, clock: Callable[[], float] = time.time) -> None:
        self.secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        self.allowed = parse_origins(allowed_origins) if isinstance(allowed_origins, str) else list(allowed_origins or [])
        self.clock = clock
        self.rooms: dict[str, Room] = {}
        self.lock = threading.Lock()

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def room(self, rid: str) -> Room:
        with self.lock:
            r = self.rooms.get(rid)
            if r is None:
                r = self.rooms[rid] = Room()
            return r

    # -- HTTP / upgrade (Worker part) --
    def handle(self, sock: socket.socket) -> None:
        buf = bytearray()
        sock.settimeout(10)
        try:
            req = W.read_http_request(sock, buf)
        except (OSError, W.WebSocketError):
            return
        try:
            path = urlsplit(req.target).path
        except ValueError:
            path = ""

        def reply(status: int, code: str | None = None, body: dict | None = None, extra=None) -> None:
            data = b"" if req.method == "HEAD" else _dumps(body if body is not None else {"error": code}).encode()
            try:
                sock.sendall(W.http_response(status, HTTP_REASONS.get(status, ""), data, extra=extra))
            except OSError:
                pass

        if path == "/health":
            if req.method not in ("GET", "HEAD"):
                return reply(405, "method_not_allowed", extra=[("Allow", "GET, HEAD")])
            return reply(200, body={"ok": True, "v": PROTOCOL_VERSION})
        m = ROOM_PATH_RE.match(path)
        if not m:
            return reply(404, "not_found")
        rid = m.group(1)
        if req.method != "GET":
            return reply(405, "method_not_allowed", extra=[("Allow", "GET")])
        if (req.header("upgrade") or "").lower() != "websocket" or not W.is_upgrade_request(req):
            return reply(426, "upgrade_required")
        if not self.secret:
            return reply(503, "relay_not_configured")
        ticket = ticket_from_protocols(req.header("sec-websocket-protocol"))
        if not ticket:
            return reply(401, "bad_ticket")
        try:
            claims = verify_ticket(self.secret, ticket, int(self.clock()), rid)
        except TicketError:
            return reply(401, "bad_ticket")
        if not origin_allowed(claims["ro"], req.header("origin"), self.allowed):
            return reply(403, "bad_origin")
        self.serve_socket(sock, buf, req, rid, claims, reply)

    # -- Durable Object part --
    def serve_socket(self, sock, buf, req, rid: str, claims: dict, reply) -> None:
        role, pid = claims["ro"], claims["id"]
        if role not in ROLES or not PEER_ID_RE.match(pid):
            return reply(400, "bad_request")
        room = self.room(rid)
        with room.lock:
            now = self.now_ms()
            replaced: list[Peer] = []
            if role == "device":
                replaced = [p for p in room.live("device") if p.id == pid]
                if len(room.live("device")) - len(replaced) >= MAX_DEVICES:
                    return reply(429, "room_full")
            elif len(room.live("browser")) >= MAX_BROWSERS:
                return reply(429, "room_full")
            try:
                sock.sendall(W.accept_response(req.header("sec-websocket-key"), SUBPROTOCOL))
            except OSError:
                return
            sock.settimeout(None)
            ws = W.WebSocket(sock, client=False, max_message=MAX_FRAME, subprotocol=SUBPROTOCOL, buffered=bytes(buf))
            conn = "c:" + _rand12() if role == "browser" else None
            peer = Peer(ws, role, pid, conn, now)
            room.peers.append(peer)
            for old in replaced:
                old.replaced = True
                old.close(CLOSE_REPLACED, "replaced")
            peer.send(_dumps({"t": "welcome", "v": PROTOCOL_VERSION, "conn": conn, "role": role, "id": pid,
                              "serverTime": now, "limits": {"maxFrame": MAX_FRAME, "msgPerSec": int(RATE[role][0])}}))
            if role == "browser":
                peer.send(self.presence_frame(room))
            else:
                self.broadcast_presence(room)
        try:
            self.loop(room, peer)
        finally:
            self.gone(rid, room, peer)

    def loop(self, room: Room, peer: Peer) -> None:
        while True:
            try:
                data = peer.ws.recv()
            except W.ConnectionClosed:
                return
            except OSError:
                return
            if peer.replaced or peer.gone:
                continue
            if data == PING_FRAME:                    # auto-response: no rate limit, no "wake-up"
                peer.send(PONG_FRAME)
                continue
            with room.lock:
                closed = self.on_message(room, peer, data)
            if closed:                                # close frame sent; wait for the client's reply
                peer.ws.close(wait=2.0)
                return

    def on_message(self, room: Room, peer: Peer, data) -> bool:
        """→ True when the socket was closed by the relay."""
        now = self.now_ms()
        if not isinstance(data, str):
            return self.bad_frame(peer, now, "bad_frame", None)
        v = validate_frame(data, peer.role)
        if not peer.bucket.take(now):
            return self.rate_limited(peer, v["frame"].get("seq") if v["ok"] else v["ref"])
        peer.strikes = 0
        if not v["ok"]:
            return self.bad_frame(peer, now, v["code"], v["ref"])
        f = v["frame"]
        t = f["t"]
        if t == "hello":
            peer.app = f["app"]
            if peer.role == "device":
                self.broadcast_presence(room)
        elif t == "ping":
            peer.send(PONG_FRAME)
        elif t == "presence.get":
            peer.send(self.presence_frame(room))
        elif t == "pair.open":
            self.pair_open(room, peer, f["slot"], now)
        elif t == "pair.close":
            if peer.slot == f["slot"] and peer.slot_exp > now:
                peer.slot, peer.slot_exp = None, 0
            else:
                peer.send(err_frame("slot_unknown"))
        elif t == "send":
            return self.route(room, peer, f, now)
        return False

    def rate_limited(self, peer: Peer, ref) -> bool:
        peer.strikes += 1
        if peer.strikes >= RATE_STRIKE_LIMIT:
            peer.close(CLOSE_RATE, "rate limited")
            return True
        peer.send(err_frame("rate_limited", ref))
        return False

    def bad_frame(self, peer: Peer, now: int, code: str, ref) -> bool:
        peer.bad = [t for t in peer.bad if now - t < BAD_FRAME_WINDOW_MS] + [now]
        if len(peer.bad) >= BAD_FRAME_LIMIT:
            peer.close(CLOSE_POLICY, "bad frames")
            return True
        peer.send(err_frame(code, ref))
        return False

    def pair_open(self, room: Room, peer: Peer, slot: str, now: int) -> None:
        active = 0
        for other in room.live("device", exclude=peer):
            if other.slot and other.slot_exp > now:
                if other.slot == slot:
                    peer.send(err_frame("slot_taken"))
                    return
                active += 1
        if active >= MAX_SLOTS:
            peer.send(err_frame("slot_limit"))
            return
        peer.slot, peer.slot_exp = slot, now + SLOT_TTL_MS
        peer.send(_dumps({"t": "pair.opened", "slot": slot, "exp": peer.slot_exp}))

    def route(self, room: Room, peer: Peer, f: dict, now: int) -> bool:
        kind, tid = f["target"]
        target = None
        missing = "device_offline"
        if kind == "device":
            cands = [p for p in room.live("device") if p.id == tid]
            target = cands[-1] if cands else None
        elif kind == "pair":
            if peer.pair_bucket is None:
                peer.pair_bucket = TokenBucket(RATE["pair"][0], RATE["pair"][1], now)
            if not peer.pair_bucket.take(now):
                return self.rate_limited(peer, f["seq"])
            cands = [p for p in room.live("device") if p.slot == tid and p.slot_exp > now]
            target = cands[0] if cands else None
            missing = "slot_unknown"
        else:
            cands = [p for p in room.live("browser") if p.conn == tid]
            target = cands[0] if cands else None
            missing = "conn_gone"
        msg = target and _dumps({"t": "msg", "from": peer.id if peer.role == "device" else peer.conn,
                                 "bid": peer.id if peer.role == "browser" else None,
                                 "ch": f["ch"], "seq": f["seq"], "k": f["k"], "b": f["b"]})
        if not target or not target.send(msg):
            peer.send(err_frame(missing, f["seq"]))
        return False

    def presence_frame(self, room: Room, exclude: Peer | None = None) -> str:
        by_id: dict[str, dict] = {}
        for p in room.live("device", exclude=exclude):
            prev = by_id.get(p.id)
            if prev is None or p.since >= prev["since"]:
                by_id[p.id] = {"id": p.id, "since": p.since, "app": p.app}
        devices = sorted(by_id.values(), key=lambda d: d["since"])
        return _dumps({"t": "presence", "devices": devices})

    def broadcast_presence(self, room: Room, exclude: Peer | None = None) -> None:
        frame = self.presence_frame(room, exclude)
        for p in room.live("browser", exclude=exclude):
            p.send(frame)

    def gone(self, rid: str, room: Room, peer: Peer) -> None:
        with room.lock:
            was_replaced = peer.replaced
            peer.gone = True
            if peer in room.peers:
                room.peers.remove(peer)
            if not was_replaced:
                if peer.role == "device":
                    self.broadcast_presence(room)
                else:
                    frame = _dumps({"t": "left", "conn": peer.conn})
                    for d in room.live("device"):
                        d.send(frame)
            empty = not room.peers
        peer.close(1000, "")
        peer.ws._gone()
        if empty:
            with self.lock:
                if self.rooms.get(rid) is room and not room.peers:
                    del self.rooms[rid]


def _rand12() -> str:
    import base64
    return base64.urlsafe_b64encode(os.urandom(9)).decode("ascii")


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.server.relay.handle(self.request)   # type: ignore[attr-defined]


class DevRelayServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, relay: Relay) -> None:
        self.relay = relay
        super().__init__(addr, _Handler)


def serve_in_thread(relay: Relay, host: str = DEFAULT_HOST, port: int = 0):
    """Start a relay server on a background thread. → (server, base_url). Stop with server.shutdown(); server_close()."""
    srv = DevRelayServer((host, port), relay)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True, name="dev-relay")
    th.start()
    return srv, f"ws://{host}:{srv.server_address[1]}"


def default_origins(port: int = 8787) -> str:
    return f"http://127.0.0.1:{port},http://localhost:{port}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dev_relay.py", description="Pixel Office local relay (same protocol as the Worker).")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--origins", default=os.environ.get("ALLOWED_ORIGINS") or default_origins(),
                   help="comma-separated browser origins (env ALLOWED_ORIGINS)")
    args = p.parse_args(argv)
    secret = os.environ.get("RELAY_SECRET", "")
    if len(secret) < 32:
        print("RELAY_SECRET (≥ 32 chars) must be set in the environment.", file=sys.stderr)
        return 2
    try:
        srv = DevRelayServer((args.host, args.port), Relay(secret, args.origins))
    except OSError as e:
        print(f"Cannot listen on {args.host}:{args.port}: {e}", file=sys.stderr)
        return 1
    print(f"Pixel Office dev relay at ws://{args.host}:{srv.server_address[1]}  (origins: {args.origins})", flush=True)

    def _stop(signum, frame):  # noqa: ANN001
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _stop)
    except ValueError:
        pass
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
