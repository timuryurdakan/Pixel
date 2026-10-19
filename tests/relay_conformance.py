#!/usr/bin/env python3
"""Relay protocol conformance suite (V3.md §3, §4.4, §17.1). Stdlib only.

Runs unchanged against `tools/dev_relay.py` (in-process, via tests/test_dev_relay.py) and against the deployed
Cloudflare Worker:

    # tickets minted locally with the shared secret (all tests):
    RELAY_SECRET=… python3 tests/relay_conformance.py --url wss://pixel-relay.<sub>.workers.dev
    # tickets fetched from Vercel with a room write key (no secret on this machine; time-forging tests skip):
    python3 tests/relay_conformance.py --url wss://pixel-relay.<sub>.workers.dev \
        --server https://pixel-rouge-phi.vercel.app --room <rid> --key <writeKey>

Also reads PIXEL_RELAY_CONFORMANCE_URL (+ RELAY_SECRET / PIXEL_RELAY_CONFORMANCE_{SERVER,ROOM,KEY,ORIGIN}) when
run from `python3 -m unittest discover -s tests` through test_dev_relay.py; without it only dev_relay is tested.
Against a deployed Worker the suite uses a few hundred Durable Object requests (≈ 0.3% of the free daily quota).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import ssl
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pixel_cloud import relay as R  # noqa: E402
from pixel_connector import ws as W  # noqa: E402

DEFAULT_ORIGIN = "https://pixel-rouge-phi.vercel.app"
RECV_TIMEOUT = 5.0


def rand_b32(n: int) -> str:
    return base64.b32encode(secrets.token_bytes(n)).decode("ascii").rstrip("=").lower()


def rand_room() -> str:
    return rand_b32(16)[:26]


def rand_peer() -> str:
    return rand_b32(10)[:16]


def b64(data: bytes) -> str:
    return R.b64url_encode(data)


# -- targets ------------------------------------------------------------------------------------------------

class Target:
    """Where the relay is and how tickets are obtained. Subclasses: DevTarget (test_dev_relay.py), RemoteTarget."""
    name = "relay"
    url = ""                    # ws(s)://host[:port]
    origin = DEFAULT_ORIGIN     # an allowed browser Origin
    secret: bytes | None = None
    fixed_room: str | None = None

    def new_room(self) -> str:
        return self.fixed_room or rand_room()

    def ticket(self, room: str, role: str, pid: str, iat_offset: int = 0) -> str:
        if self.secret is None:
            raise unittest.SkipTest("needs RELAY_SECRET")
        return R.mint_ticket(self.secret, room, role, pid, int(time.time()) + iat_offset)[0]

    def can_advance(self) -> bool:
        return False

    def advance(self, seconds: float) -> None:
        raise unittest.SkipTest("clock control only in-process")

    def ssl_context(self):
        return None


class RemoteTarget(Target):
    def __init__(self, url: str, secret: str | None = None, server: str | None = None, room: str | None = None,
                 key: str | None = None, origin: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.name = self.url
        self.secret = secret.encode() if secret else None
        self.server = server.rstrip("/") if server else None
        self.fixed_room = room if (server and room) else None
        self.key = key
        self.origin = origin or DEFAULT_ORIGIN
        if not self.secret and not (self.server and room and key):
            raise SystemExit("need RELAY_SECRET, or --server + --room + --key")

    def ticket(self, room: str, role: str, pid: str, iat_offset: int = 0) -> str:
        if self.secret is not None:
            return super().ticket(room, role, pid, iat_offset)
        if iat_offset:
            raise unittest.SkipTest("forging ticket times needs RELAY_SECRET")
        body = json.dumps({"role": role, "id": pid}).encode()
        for _ in range(20):
            req = urllib.request.Request(f"{self.server}/api/relay_ticket?room={room}", data=body, method="POST",
                                         headers={"Authorization": f"Bearer {self.key}",
                                                  "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read())["ticket"]
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    raise
                time.sleep(min(30, int(e.headers.get("Retry-After") or "2")))
        raise RuntimeError("ticket endpoint kept rate limiting")


TARGET: Target | None = None


def target_from_env() -> Target | None:
    url = os.environ.get("PIXEL_RELAY_CONFORMANCE_URL")
    if not url:
        return None
    return RemoteTarget(url, os.environ.get("RELAY_SECRET"), os.environ.get("PIXEL_RELAY_CONFORMANCE_SERVER"),
                        os.environ.get("PIXEL_RELAY_CONFORMANCE_ROOM"), os.environ.get("PIXEL_RELAY_CONFORMANCE_KEY"),
                        os.environ.get("PIXEL_RELAY_CONFORMANCE_ORIGIN"))


# -- client helper ------------------------------------------------------------------------------------------

class Client:
    def __init__(self, ws: W.WebSocket, role: str, pid: str) -> None:
        self.ws, self.role, self.id = ws, role, pid
        self.backlog: list[dict] = []
        self.welcome = self.next("welcome")
        self.conn = self.welcome.get("conn")

    def send(self, obj) -> None:
        self.ws.send_text(obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":")))

    def recv(self, timeout: float = RECV_TIMEOUT) -> dict:
        if self.backlog:
            return self.backlog.pop(0)
        return json.loads(self.ws.recv(timeout=timeout))

    def next(self, t: str, timeout: float = RECV_TIMEOUT, **match) -> dict:
        """Next frame of type `t` (matching `match`); earlier frames of other types stay in the backlog."""
        for i, f in enumerate(self.backlog):
            if f.get("t") == t and all(f.get(k) == v for k, v in match.items()):
                return self.backlog.pop(i)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError(f"no {t!r} frame within {timeout}s (backlog: {self.backlog[-3:]})")
            f = json.loads(self.ws.recv(timeout=left))
            if f.get("t") == t and all(f.get(k) == v for k, v in match.items()):
                return f
            self.backlog.append(f)

    def closed_with(self, timeout: float = RECV_TIMEOUT) -> int:
        """Read until the relay closes the socket; → close code."""
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                self.ws.recv(timeout=max(0.05, deadline - time.monotonic()))
        except W.ConnectionClosed as e:
            return e.code
        except socket.timeout:
            pass
        raise AssertionError("socket was not closed")

    def close(self) -> None:
        try:
            self.ws.close(1000, "", wait=0.5)
        except Exception:
            pass


class ConformanceTests(unittest.TestCase):
    target: Target | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if cls.target is None:
            cls.target = TARGET
        if cls.target is None:
            raise unittest.SkipTest("no relay target configured")

    def setUp(self) -> None:
        self.t = self.target
        self.room = self.t.new_room()
        self.clients: list[Client] = []

    def tearDown(self) -> None:
        for c in self.clients:
            c.close()
        if self.t.fixed_room:
            time.sleep(0.3)      # let the DO notice the closes before the next test reuses the room

    # -- helpers --
    def ws_url(self, room: str | None = None) -> str:
        return f"{self.t.url}/v1/rooms/{room or self.room}/ws"

    def http_url(self, path: str) -> str:
        u = self.t.url
        return ("https" + u[3:] if u.startswith("wss") else "http" + u[2:]) + path

    def raw(self, role: str, pid: str, ticket: str | None = "auto", origin: str | None = "auto", room: str | None = None,
            protocols=None, url: str | None = None) -> W.WebSocket:
        room = room or self.room
        if ticket == "auto":
            ticket = self.t.ticket(room, role, pid)
        if origin == "auto":
            origin = self.t.origin if role == "browser" else None
        if protocols is None:
            protocols = [R.SUBPROTOCOL] + (["tk." + ticket] if ticket else [])
        return W.connect(url or self.ws_url(room), protocols=protocols, origin=origin, timeout=10,
                         ssl_context=self.t.ssl_context(), max_message=1 << 20)

    def client(self, role: str, pid: str | None = None, **kw) -> Client:
        pid = pid or rand_peer()
        ws = self.raw(role, pid, **kw)
        self.assertEqual(ws.subprotocol, R.SUBPROTOCOL)
        c = Client(ws, role, pid)
        self.clients.append(c)
        return c

    def assert_http(self, fn, status: int, code: str | None = None) -> None:
        with self.assertRaises(W.HandshakeError) as cm:
            ws = fn()
            ws.close()
        self.assertEqual(cm.exception.status, status, cm.exception.body[:200])
        if code:
            self.assertEqual(json.loads(cm.exception.body or b"{}").get("error"), code)

    def send_frame(self, to: str, seq: int, b: str | None = None, ch: str | None = None, k: str = "e") -> dict:
        return {"t": "send", "to": to, "ch": ch, "seq": seq, "k": k, "b": b if b is not None else b64(b"x" * 24)}

    # -- HTTP surface --
    def test_health(self):
        ctx = self.t.ssl_context()
        with urllib.request.urlopen(self.http_url("/health"), timeout=10, context=ctx) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(json.loads(r.read()), {"ok": True, "v": 1})
            self.assertIn("no-store", r.headers.get("Cache-Control", ""))

    def test_unknown_path_and_plain_get(self):
        for path in ("/", "/v1/rooms/short/ws", f"/v1/rooms/{self.room}/wss", f"/v2/rooms/{self.room}/ws"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(self.http_url(path), timeout=10, context=self.t.ssl_context())
                cm.exception.close()
                self.assertEqual(cm.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.http_url(f"/v1/rooms/{self.room}/ws"), timeout=10, context=self.t.ssl_context())
        cm.exception.close()
        self.assertEqual(cm.exception.code, 426)

    # -- tickets (§6.2) --
    def test_rejects_missing_forged_and_misplaced_tickets(self):
        pid = rand_peer()
        good = self.t.ticket(self.room, "device", pid)
        self.assert_http(lambda: self.raw("device", pid, ticket=None), 401, "bad_ticket")
        self.assert_http(lambda: self.raw("device", pid, protocols=["tk." + good]), 401, "bad_ticket")
        forged = good[:-2] + ("AA" if good[-2:] != "AA" else "BA")
        self.assert_http(lambda: self.raw("device", pid, ticket=forged), 401, "bad_ticket")
        self.assert_http(lambda: self.raw("device", pid, ticket="v1.e30.AAAA"), 401, "bad_ticket")
        other_room = rand_room()
        self.assert_http(lambda: self.raw("device", pid, ticket=good, room=other_room), 401, "bad_ticket")
        # never in the URL: a ticket in the query string is not accepted
        self.assert_http(lambda: self.raw("device", pid, ticket=None, url=self.ws_url() + "?ticket=" + good),
                         401, "bad_ticket")

    def test_rejects_expired_and_future_tickets(self):
        pid = rand_peer()
        expired = self.t.ticket(self.room, "device", pid, iat_offset=-121)
        self.assert_http(lambda: self.raw("device", pid, ticket=expired), 401, "bad_ticket")
        future = self.t.ticket(self.room, "device", pid, iat_offset=90)
        self.assert_http(lambda: self.raw("device", pid, ticket=future), 401, "bad_ticket")

    def test_origin_rules(self):
        self.assert_http(lambda: self.raw("browser", rand_peer(), origin=None), 403, "bad_origin")
        self.assert_http(lambda: self.raw("browser", rand_peer(), origin="https://evil.example"), 403, "bad_origin")
        self.assert_http(lambda: self.raw("device", rand_peer(), origin=self.t.origin), 403, "bad_origin")

    # -- welcome / presence --
    def test_welcome_and_presence(self):
        br = self.client("browser")
        w = br.welcome
        self.assertEqual((w["v"], w["role"], w["id"]), (1, "browser", br.id))
        self.assertRegex(w["conn"], r"^c:[A-Za-z0-9_-]{12}$")
        self.assertIsInstance(w["serverTime"], int)
        self.assertGreaterEqual(w["limits"]["maxFrame"], 65536)
        p = br.next("presence")
        self.assertIsInstance(p["devices"], list)
        dev = self.client("device")
        self.assertIsNone(dev.welcome["conn"])
        self.assertEqual(dev.welcome["role"], "device")
        p = br.next("presence")
        self.assertIn(dev.id, [d["id"] for d in p["devices"]])
        dev.send({"t": "hello", "v": 1, "app": "connector/3.0.0", "caps": ["e2e1"]})
        p = br.next("presence")
        self.assertIn({"id": dev.id, "since": dev.welcome["serverTime"], "app": "connector/3.0.0"}, p["devices"])
        br.send({"t": "presence.get"})
        self.assertIn(dev.id, [d["id"] for d in br.next("presence")["devices"]])
        dev.close()
        self.clients.remove(dev)
        p = br.next("presence")
        self.assertNotIn(dev.id, [d["id"] for d in p["devices"]])

    def test_hello_version_gate(self):
        br = self.client("browser")
        br.send({"t": "hello", "v": 2, "app": "web/9"})
        self.assertEqual(br.next("err")["code"], "version")

    def test_device_replaced(self):
        br = self.client("browser")
        pid = rand_peer()
        d1 = self.client("device", pid)
        d2 = self.client("device", pid)
        self.assertEqual(d1.closed_with(), 4009)
        br.send({"t": "presence.get"})
        ids = None
        for _ in range(5):
            ids = [d["id"] for d in br.next("presence")["devices"]]
            if ids.count(pid) == 1:
                break
        self.assertEqual(ids.count(pid), 1)
        br.send(self.send_frame(pid, 1))
        self.assertEqual(d2.next("msg")["seq"], 1)

    # -- routing --
    def test_routing_both_ways(self):
        dev = self.client("device")
        br = self.client("browser")
        payload = b64(os.urandom(300))
        ch = "AbCdEfGhIjKlMnOp"
        br.send({"t": "send", "to": dev.id, "ch": None, "seq": 0, "k": "hs", "b": payload})
        m = dev.next("msg")
        self.assertEqual(m, {"t": "msg", "from": br.conn, "bid": br.id, "ch": None, "seq": 0, "k": "hs", "b": payload})
        reply = b64(b"\x00\xff" * 100)
        dev.send({"t": "send", "to": br.conn, "ch": ch, "seq": 7, "k": "e", "b": reply})
        m = br.next("msg")
        self.assertEqual(m, {"t": "msg", "from": dev.id, "bid": None, "ch": ch, "seq": 7, "k": "e", "b": reply})
        # no fan-out: a second browser receives nothing addressed to the first
        br2 = self.client("browser")
        dev.send({"t": "send", "to": br.conn, "ch": ch, "seq": 8, "k": "e", "b": reply})
        self.assertEqual(br.next("msg")["seq"], 8)
        br2.send({"t": "ping"})
        self.assertEqual(br2.next("pong"), {"t": "pong"})
        self.assertFalse([f for f in br2.backlog if f.get("t") == "msg"])

    def test_b_is_opaque(self):
        """The relay never interprets `b`: JSON-looking or random base64url arrives byte-identical."""
        dev = self.client("device")
        br = self.client("browser")
        for i, raw in enumerate((b'{"t":"send","to":"x"}', b"\x00" * 33, os.urandom(4096), "çğıöşü".encode())):
            b = b64(raw)
            br.send(self.send_frame(dev.id, i, b=b))
            self.assertEqual(dev.next("msg", seq=i)["b"], b)

    def test_offline_targets(self):
        br = self.client("browser")
        dev = self.client("device")
        br.send(self.send_frame(rand_peer(), 41))
        e = br.next("err")
        self.assertEqual((e["code"], e["ref"]), ("device_offline", 41))
        dev.send(self.send_frame("c:AAAAAAAAAAAA", 42))
        e = dev.next("err")
        self.assertEqual((e["code"], e["ref"]), ("conn_gone", 42))

    def test_forbidden_directions(self):
        br = self.client("browser")
        dev = self.client("device")
        br.send(self.send_frame(br.conn, 1))
        self.assertEqual(br.next("err")["code"], "forbidden")
        dev.send(self.send_frame(dev.id, 2))
        self.assertEqual(dev.next("err")["code"], "forbidden")
        dev.send({"t": "presence.get"})
        self.assertEqual(dev.next("err")["code"], "forbidden")
        br.send({"t": "pair.open", "slot": "K7Q2"})
        self.assertEqual(br.next("err")["code"], "forbidden")

    def test_rooms_are_isolated(self):
        """Phase 15: a browser in another room cannot reach this room's device, its pairing slot or its presence."""
        if self.t.fixed_room:
            self.skipTest("needs tickets for a second room (RELAY_SECRET)")
        dev = self.client("device")
        dev.send({"t": "pair.open", "slot": "QX7Z"})
        dev.next("pair.opened")
        other = rand_room()
        br = self.client("browser", room=other)
        self.assertEqual(br.next("presence")["devices"], [])
        br.send(self.send_frame(dev.id, 1))
        e = br.next("err")
        self.assertEqual((e["code"], e["ref"]), ("device_offline", 1))
        br.send(self.send_frame("pair:QX7Z", 2, k="hs"))
        e = br.next("err")
        self.assertEqual((e["code"], e["ref"]), ("slot_unknown", 2))
        dev.send({"t": "ping"})
        dev.next("pong")
        self.assertFalse([f for f in dev.backlog if f.get("t") == "msg"])

    def test_left_notification(self):
        dev = self.client("device")
        br = self.client("browser")
        conn = br.conn
        br.close()
        self.clients.remove(br)
        self.assertEqual(dev.next("left"), {"t": "left", "conn": conn})

    # -- pairing slots --
    def test_pairing_slots(self):
        dev = self.client("device")
        slot = "K7Q2"
        dev.send({"t": "pair.open", "slot": slot})
        po = dev.next("pair.opened")
        self.assertEqual(po["slot"], slot)
        self.assertAlmostEqual(po["exp"], dev.welcome["serverTime"] + 600_000, delta=60_000)
        br = self.client("browser")
        br.send(self.send_frame("pair:" + slot, 0, k="hs"))
        m = dev.next("msg")
        self.assertEqual((m["from"], m["bid"], m["k"]), (br.conn, br.id, "hs"))
        dev2 = self.client("device")
        dev2.send({"t": "pair.open", "slot": slot})
        self.assertEqual(dev2.next("err")["code"], "slot_taken")
        dev.send({"t": "pair.close", "slot": slot})            # success is silent …
        dev.send({"t": "pair.close", "slot": "ZZZZ"})          # … so sync on this error (same socket, in order)
        self.assertEqual(dev.next("err")["code"], "slot_unknown")
        br.send(self.send_frame("pair:" + slot, 1, k="hs"))
        e = br.next("err")
        self.assertEqual((e["code"], e["ref"]), ("slot_unknown", 1))
        dev2.send({"t": "pair.open", "slot": slot})            # free again
        self.assertEqual(dev2.next("pair.opened")["slot"], slot)

    def test_pairing_rate_limit(self):
        dev = self.client("device")
        dev.send({"t": "pair.open", "slot": "AB12"})
        dev.next("pair.opened")
        br = self.client("browser")
        for i in range(3):
            br.send(self.send_frame("pair:AB12", i, k="hs"))
            self.assertEqual(dev.next("msg")["seq"], i)
        br.send(self.send_frame("pair:AB12", 3, k="hs"))
        e = br.next("err")
        self.assertEqual((e["code"], e["ref"]), ("rate_limited", 3))

    def test_slot_limit(self):
        devs = [self.client("device") for _ in range(5)]
        for i, d in enumerate(devs[:4]):
            d.send({"t": "pair.open", "slot": f"S{i}00"})
            d.next("pair.opened")
        devs[4].send({"t": "pair.open", "slot": "S400"})
        self.assertEqual(devs[4].next("err")["code"], "slot_limit")
        devs[0].send({"t": "pair.open", "slot": "S011"})   # replacing its own slot is fine
        self.assertEqual(devs[0].next("pair.opened")["slot"], "S011")

    def test_pairing_slot_expiry(self):
        if not self.t.can_advance():
            self.skipTest("needs clock control (in-process only)")
        dev = self.client("device")
        dev.send({"t": "pair.open", "slot": "EXP1"})
        dev.next("pair.opened")
        br = self.client("browser")
        self.t.advance(601)
        br.send(self.send_frame("pair:EXP1", 5, k="hs"))
        self.assertEqual(br.next("err")["code"], "slot_unknown")

    # -- limits --
    def test_ping_autoresponse(self):
        for role in ("device", "browser"):
            c = self.client(role)
            c.send('{"t":"ping"}')
            self.assertEqual(c.next("pong"), {"t": "pong"})

    def test_oversize_frame_closes_1009(self):
        c = self.client("browser")
        c.send('{"t":"send","b":"' + "A" * 100_000 + '"}')
        self.assertEqual(c.closed_with(), 1009)

    def test_oversize_b_is_too_large(self):
        dev = self.client("device")
        br = self.client("browser")
        br.send(self.send_frame(dev.id, 9, b=b64(b"\x01" * (48 * 1024 + 3))))
        e = br.next("err")
        self.assertEqual((e["code"], e["ref"]), ("too_large", 9))
        ok = b64(b"\x01" * (48 * 1024))
        br.send(self.send_frame(dev.id, 10, b=ok))
        self.assertEqual(dev.next("msg")["b"], ok)
        dev.send(self.send_frame(br.conn, 11, b=b64(b"\x02" * (60 * 1024))))
        self.assertEqual(br.next("msg")["seq"], 11)
        dev.send(self.send_frame(br.conn, 12, b=b64(b"\x02" * (60 * 1024 + 3))))
        self.assertEqual(dev.next("err")["code"], "too_large")

    def test_bad_frames_close_after_five(self):
        c = self.client("browser")
        bad = ["not json", "[1,2]", '{"t":"nope"}', '{"t":"send","to":"x","seq":1,"k":"e","b":"AA"}']
        for f in bad:
            c.send(f)
            self.assertEqual(c.next("err")["code"], "bad_frame")
        c.send('{"t":"send"}')
        self.assertEqual(c.closed_with(), 1008)

    def test_binary_frame_is_bad(self):
        c = self.client("browser")
        c.ws.send_bytes(b'{"t":"ping"}')
        self.assertEqual(c.next("err")["code"], "bad_frame")

    def test_fragmented_text_frame(self):
        c = self.client("browser")
        c.ws.send_raw_text_frames([b'{"t":"pres', b'ence.g', b'et"}'])
        self.assertEqual(c.next("presence")["t"], "presence")

    def test_rate_limit(self):
        c = self.client("browser")
        target = rand_peer()
        for i in range(60):
            try:
                c.send(self.send_frame(target, i))
            except (OSError, W.WebSocketError):
                break
        codes = []
        try:
            while True:
                f = c.recv()
                if f.get("t") == "err":
                    codes.append(f["code"])
        except W.ConnectionClosed as e:
            self.assertEqual(e.code, 4029)
        self.assertIn("rate_limited", codes)
        self.assertIn("device_offline", codes)

    def test_room_caps(self):
        devs = [self.client("device") for _ in range(8)]
        self.assertEqual(len(devs), 8)
        self.assert_http(lambda: self.raw("device", rand_peer()), 429, "room_full")
        bid = rand_peer()
        ticket = self.t.ticket(self.room, "browser", bid)      # tickets are not single-use
        for _ in range(24):
            self.client("browser", bid, ticket=ticket)
        self.assert_http(lambda: self.raw("browser", bid, ticket=ticket), 429, "room_full")
        # a reconnecting device replaces itself even when the room is full
        self.client("device", devs[0].id)
        self.assertEqual(devs[0].closed_with(), 4009)


def main(argv=None) -> int:
    global TARGET
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", required=True, help="ws://127.0.0.1:8788 or wss://pixel-relay.<sub>.workers.dev")
    p.add_argument("--server", help="Vercel base URL for /api/relay_ticket (when RELAY_SECRET is not available)")
    p.add_argument("--room")
    p.add_argument("--key")
    p.add_argument("--origin", default=DEFAULT_ORIGIN)
    p.add_argument("-k", dest="pattern", help="only tests whose name contains this")
    p.add_argument("-v", action="store_true")
    args = p.parse_args(argv)
    TARGET = RemoteTarget(args.url, os.environ.get("RELAY_SECRET"), args.server, args.room, args.key, args.origin)
    ConformanceTests.target = TARGET
    loader = unittest.TestLoader()
    if args.pattern:
        loader.testNamePatterns = [f"*{args.pattern}*"]
    suite = loader.loadTestsFromTestCase(ConformanceTests)
    res = unittest.TextTestRunner(verbosity=2 if args.v else 1).run(suite)
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
