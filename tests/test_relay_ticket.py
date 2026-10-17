"""Phase 12: relay tickets and the Vercel side of v3 (V3.md §6.2, §13, §17.1).

- `POST /api/relay_ticket`: status matrix 401/404/400/413/429/503, 0 storage calls, nothing logged, the ticket verifies.
- `relay/test/ticket_vectors.json`: the shared byte-level vectors that the Worker's node:test suite (and the headless
  Chrome run in test_relay_js.py) check too. Regenerate with `python3 tests/test_relay_ticket.py --write-vectors`.
- `/api/config` relay on/off, status field `ctl` → Agent JSON `control`, v2 parity.
"""

import json
import logging
import sys
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)
from test_cloud_app import SECRET, Base, FakeClock, mk_app

from pixel_cloud import relay as R
from pixel_cloud import settings as S
from pixel_cloud.app import App, Request
from pixel_cloud.storage import MemoryStorage

ROOT = Path(__file__).resolve().parent.parent


def load_dev_relay():
    if str(ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools"))
    import dev_relay
    return dev_relay
VECTORS = ROOT / "relay" / "test" / "ticket_vectors.json"
RELAY_SECRET = "r" * 43
RELAY_ENV = {"RELAY_SECRET": RELAY_SECRET, "PIXEL_RELAY_URL": "wss://pixel-relay.example.workers.dev"}

V_SECRET = "test-relay-secret-0123456789-abcdefghijklmnop"   # 45 chars, vectors only
V_ROOM = "abcdefghijklmnopqrstuvwxyz"
V_ROOM2 = "zyxwvutsrqponmlkjihgfedcba"
V_DEV = "devdevdevdevdev2"
V_BID = "browserbrowser23"
V_NOW = 1_792_100_000


def _signed(secret: str, payload_obj) -> str:
    """A correctly MACed ticket around an arbitrary payload (to test claim checks behind the MAC)."""
    raw = payload_obj if isinstance(payload_obj, bytes) else json.dumps(payload_obj, separators=(",", ":")).encode()
    payload = R.b64url_encode(raw)
    return f"v1.{payload}.{R.b64url_encode(R._mac(secret.encode(), payload))}"


def _noncanonical(s: str) -> str:
    """Same decoded bytes for a lenient decoder, but non-zero unused trailing bits (43-char MAC: 2 spare bits)."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    i = alphabet.index(s[-1])
    return s[:-1] + alphabet[i | 1 if i & 3 == 0 else i & ~3]


def build_vectors() -> dict:
    sec = V_SECRET.encode()
    nonce = bytes(range(8))
    good, _ = R.mint_ticket(sec, V_ROOM, "device", V_DEV, V_NOW, nonce=nonce)
    good_b, _ = R.mint_ticket(sec, V_ROOM, "browser", V_BID, V_NOW, nonce=b"\xff" * 8)
    claims = {"r": V_ROOM, "ro": "device", "id": V_DEV, "iat": V_NOW, "exp": V_NOW + 120, "n": R.b64url_encode(nonce)}
    _, payload, mac = good.split(".")
    flipped = mac[:-2] + ("A" if mac[-2] != "A" else "B") + mac[-1]

    def c(**kw):
        return {**claims, **kw}

    verify = [
        {"name": "good device", "ticket": good, "now": V_NOW, "room": V_ROOM, "ok": True,
         "claims": {k: claims[k] for k in ("r", "ro", "id", "iat", "exp")}},
        {"name": "good browser", "ticket": good_b, "now": V_NOW + 60, "room": V_ROOM, "ok": True,
         "claims": {"r": V_ROOM, "ro": "browser", "id": V_BID, "iat": V_NOW, "exp": V_NOW + 120}},
        {"name": "good, last second", "ticket": good, "now": V_NOW + 120, "room": V_ROOM, "ok": True,
         "claims": {k: claims[k] for k in ("r", "ro", "id", "iat", "exp")}},
        {"name": "good, iat 30 s ahead", "ticket": good, "now": V_NOW - 30, "room": V_ROOM, "ok": True,
         "claims": {k: claims[k] for k in ("r", "ro", "id", "iat", "exp")}},
        {"name": "expired", "ticket": good, "now": V_NOW + 121, "room": V_ROOM, "reason": "expired"},
        {"name": "future iat", "ticket": good, "now": V_NOW - 31, "room": V_ROOM, "reason": "not_yet_valid"},
        {"name": "wrong room", "ticket": good, "now": V_NOW, "room": V_ROOM2, "reason": "room"},
        {"name": "lifetime > 120", "ticket": _signed(V_SECRET, c(exp=V_NOW + 121)), "now": V_NOW, "room": V_ROOM,
         "reason": "lifetime"},
        {"name": "exp < iat", "ticket": _signed(V_SECRET, c(exp=V_NOW - 1)), "now": V_NOW, "room": V_ROOM,
         "reason": "lifetime"},
        {"name": "wrong role", "ticket": _signed(V_SECRET, c(ro="admin")), "now": V_NOW, "room": V_ROOM,
         "reason": "claims"},
        {"name": "bad id format", "ticket": _signed(V_SECRET, c(id="DEVDEVDEVDEVDEV2")), "now": V_NOW, "room": V_ROOM,
         "reason": "claims"},
        {"name": "bad room format", "ticket": _signed(V_SECRET, c(r="abc")), "now": V_NOW, "room": None,
         "reason": "claims"},
        {"name": "iat not an integer", "ticket": _signed(V_SECRET, c(iat="1792100000")), "now": V_NOW,
         "room": V_ROOM, "reason": "claims"},
        {"name": "missing nonce", "ticket": _signed(V_SECRET, {k: v for k, v in claims.items() if k != "n"}),
         "now": V_NOW, "room": V_ROOM, "reason": "claims"},
        {"name": "payload not an object", "ticket": _signed(V_SECRET, [1, 2]), "now": V_NOW, "room": V_ROOM,
         "reason": "claims"},
        {"name": "payload not JSON", "ticket": _signed(V_SECRET, b"\xffnot json"), "now": V_NOW, "room": V_ROOM,
         "reason": "claims"},
        {"name": "wrong MAC", "ticket": f"v1.{payload}.{flipped}", "now": V_NOW, "room": V_ROOM, "reason": "mac"},
        {"name": "other secret", "ticket": _signed("another-secret-another-secret-another-secret", claims),
         "now": V_NOW, "room": V_ROOM, "reason": "mac"},
        {"name": "short MAC", "ticket": f"v1.{payload}.{mac[:20]}", "now": V_NOW, "room": V_ROOM, "reason": "mac"},
        {"name": "wrong prefix", "ticket": "v2" + good[2:], "now": V_NOW, "room": V_ROOM, "reason": "format"},
        {"name": "two parts", "ticket": f"v1.{payload}", "now": V_NOW, "room": V_ROOM, "reason": "format"},
        {"name": "four parts", "ticket": good + ".x", "now": V_NOW, "room": V_ROOM, "reason": "format"},
        {"name": "empty payload", "ticket": f"v1..{mac}", "now": V_NOW, "room": V_ROOM, "reason": "format"},
        {"name": "bad base64 char", "ticket": f"v1.{payload[:-1]}+.{mac}", "now": V_NOW, "room": V_ROOM,
         "reason": "format"},
        {"name": "padding", "ticket": f"v1.{payload}.{mac}=", "now": V_NOW, "room": V_ROOM, "reason": "format"},
        {"name": "length 1 mod 4", "ticket": f"v1.A.{mac}", "now": V_NOW, "room": V_ROOM, "reason": "format"},
        {"name": "non-canonical trailing bits", "ticket": f"v1.{payload}.{_noncanonical(mac)}", "now": V_NOW,
         "room": V_ROOM, "reason": "format"},
        {"name": "too long", "ticket": "v1." + "A" * 600 + "." + mac, "now": V_NOW, "room": V_ROOM, "reason": "format"},
    ]
    for v in verify:
        v.setdefault("ok", False)
    protocols = [
        {"header": f"pixel-relay.v1, tk.{good}", "ticket": good},
        {"header": f"tk.{good},pixel-relay.v1", "ticket": good},
        {"header": f"tk.{good}", "ticket": None},
        {"header": "pixel-relay.v1", "ticket": None},
        {"header": f"pixel-relay.v1, tk.{good}, tk.{good}", "ticket": None},
        {"header": "pixel-relay.v1, tk.", "ticket": None},
        {"header": "", "ticket": None},
        {"header": "pixel-relay.v1, tk." + "A" * 1100, "ticket": None},
    ]
    return {
        "_comment": "Generated by tests/test_relay_ticket.py --write-vectors. Shared by pixel_cloud/relay.py, "
                    "tools/dev_relay.py and relay/src/lib.js; do not edit by hand.",
        "secret": V_SECRET, "macPrefix": R.MAC_PREFIX.decode(), "ttl": R.TICKET_TTL,
        "mint": [{"claims": claims, "nonceHex": nonce.hex(), "ticket": good},
                 {"claims": {"r": V_ROOM, "ro": "browser", "id": V_BID, "iat": V_NOW, "exp": V_NOW + 120,
                             "n": R.b64url_encode(b"\xff" * 8)}, "nonceHex": "ff" * 8, "ticket": good_b}],
        "verify": verify,
        "protocols": protocols,
        "b64url": [{"hex": h, "b64": R.b64url_encode(bytes.fromhex(h))}
                   for h in ("", "00", "ff", "0001", "fffe", "000102", "fbff", "68656c6c6f20776f726c64",
                             "00" * 32, "f0" * 33)],
    }


def write_vectors() -> None:
    VECTORS.parent.mkdir(parents=True, exist_ok=True)
    VECTORS.write_text(json.dumps(build_vectors(), indent=1) + "\n", encoding="utf-8")


class VectorTests(unittest.TestCase):
    def test_vector_file_is_current(self):
        self.assertTrue(VECTORS.is_file(), "run: python3 tests/test_relay_ticket.py --write-vectors")
        self.assertEqual(json.loads(VECTORS.read_text(encoding="utf-8")), build_vectors())

    def test_python_verifier_matches_vectors(self):
        v = build_vectors()
        sec = v["secret"].encode()
        for m in v["mint"]:
            cl = m["claims"]
            t, exp = R.mint_ticket(sec, cl["r"], cl["ro"], cl["id"], cl["iat"], nonce=bytes.fromhex(m["nonceHex"]))
            self.assertEqual(t, m["ticket"])
            self.assertEqual(exp, cl["exp"])
        for case in v["verify"]:
            with self.subTest(case["name"]):
                if case["ok"]:
                    self.assertEqual(R.verify_ticket(sec, case["ticket"], case["now"], case["room"]), case["claims"])
                else:
                    with self.assertRaises(R.TicketError) as cm:
                        R.verify_ticket(sec, case["ticket"], case["now"], case["room"])
                    self.assertEqual(cm.exception.reason, case["reason"])
        for p in v["protocols"]:
            self.assertEqual(R.ticket_from_protocols(p["header"]), p["ticket"], p["header"][:40])
        for b in v["b64url"]:
            self.assertEqual(R.b64url_decode(b["b64"]), bytes.fromhex(b["hex"]))

    def test_vectors_cover_the_17_1_matrix(self):
        reasons = {c.get("reason") for c in build_vectors()["verify"]}
        self.assertTrue({"expired", "not_yet_valid", "lifetime", "room", "claims", "mac", "format"} <= reasons)

    def test_ticket_shape(self):
        t, exp = R.mint_ticket(RELAY_SECRET.encode(), V_ROOM, "browser", V_BID, V_NOW)
        self.assertEqual(exp, V_NOW + 120)
        self.assertRegex(t, r"^v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$")
        self.assertLess(len(t), 300)
        t2, _ = R.mint_ticket(RELAY_SECRET.encode(), V_ROOM, "browser", V_BID, V_NOW)
        self.assertNotEqual(t, t2)                  # random nonce
        for bad in (("ROOM", "browser", V_BID), (V_ROOM, "admin", V_BID), (V_ROOM, "device", "x")):
            with self.assertRaises(ValueError):
                R.mint_ticket(RELAY_SECRET.encode(), *bad, V_NOW)

    def test_origin_rules(self):
        allowed = R.parse_origins(" https://a.example/ ,https://b.example,, ")
        self.assertEqual(allowed, ["https://a.example", "https://b.example"])
        self.assertTrue(R.origin_allowed("browser", "https://a.example", allowed))
        self.assertFalse(R.origin_allowed("browser", None, allowed))
        self.assertFalse(R.origin_allowed("browser", "https://evil.example", allowed))
        self.assertTrue(R.origin_allowed("device", None, allowed))
        self.assertFalse(R.origin_allowed("device", "https://a.example", allowed))


class SettingsTests(unittest.TestCase):
    def test_relay_settings(self):
        f = S.relay_from_env
        self.assertEqual(f({}), {"relay_secret": None, "relay_url": None})
        self.assertEqual(f(RELAY_ENV)["relay_url"], "wss://pixel-relay.example.workers.dev")
        self.assertEqual(f({**RELAY_ENV, "PIXEL_RELAY_URL": "wss://x.workers.dev/"})["relay_url"], "wss://x.workers.dev")
        for env in ({**RELAY_ENV, "RELAY_SECRET": "short"},                     # < 32 chars
                    {"RELAY_SECRET": RELAY_SECRET},                              # half configured
                    {"PIXEL_RELAY_URL": RELAY_ENV["PIXEL_RELAY_URL"]},
                    {**RELAY_ENV, "PIXEL_RELAY_URL": "https://x.workers.dev"},
                    {**RELAY_ENV, "PIXEL_RELAY_URL": "ws://evil.example"},
                    {**RELAY_ENV, "PIXEL_RELAY_URL": "wss://x.workers.dev/path"},
                    {**RELAY_ENV, "PIXEL_RELAY_URL": "wss://x\".dev"}):
            with self.subTest(env=env):
                self.assertEqual(f(env), {"relay_secret": None, "relay_url": None})
        dev = {**RELAY_ENV, "PIXEL_RELAY_URL": "ws://127.0.0.1:8788"}
        self.assertEqual(f(dev)["relay_url"], "ws://127.0.0.1:8788")
        self.assertIsNone(f(dev, on_vercel=True)["relay_url"])      # plain ws:// never on Vercel


class NoStorage:
    """Every storage access fails the test: the ticket endpoint must issue 0 storage commands."""
    name = "none"

    def __getattr__(self, item):
        raise AssertionError(f"storage.{item} used")


class TicketEndpointTests(Base):
    def setUp(self):
        self.clock = FakeClock()
        self.app = App(storage=MemoryStorage(self.clock), env={"PIXEL_SECRET": SECRET, **RELAY_ENV},
                       clock=self.clock, mapper=None)
        self.rid, self.key = self.room()

    def ticket(self, body=None, key=None, room=None, ip="10.0.0.1", app=None, raw=None):
        room = room or self.rid
        body = raw if raw is not None else json.dumps(body if body is not None else {"role": "device", "id": V_DEV}).encode()
        return self.call("POST", f"/api/relay_ticket?room={room}", body,
                         {"Authorization": f"Bearer {key or self.key}", "Content-Type": "application/json"},
                         ip=ip, app=app)

    def test_ok_and_verifies_with_dev_relay_verifier(self):
        r = self.ticket({"role": "browser", "id": V_BID})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(r.header("Cache-Control"), "no-store")
        d = r.json()
        self.assertEqual(set(d), {"url", "ticket", "exp"})
        self.assertEqual(d["url"], f"wss://pixel-relay.example.workers.dev/v1/rooms/{self.rid}/ws")
        self.assertEqual(d["exp"], int(self.clock.t) + 120)
        claims = R.verify_ticket(RELAY_SECRET.encode(), d["ticket"], int(self.clock.t), self.rid)
        self.assertEqual(claims["ro"], "browser")
        self.assertEqual(claims["id"], V_BID)
        dev_relay = load_dev_relay()
        self.assertEqual(dev_relay.verify_ticket(RELAY_SECRET.encode(), d["ticket"], int(self.clock.t), self.rid)["id"],
                         V_BID)

    def test_zero_storage_commands(self):
        app = App(storage=NoStorage(), env={"PIXEL_SECRET": SECRET, **RELAY_ENV}, clock=self.clock, mapper=None)
        r = self.ticket(app=app)
        self.assertEqual(r.status, 200, r.body)

    def test_errors(self):
        self.assert_error(self.ticket(key="x" * 32), 401, "unauthorized")
        self.assert_error(self.call("POST", f"/api/relay_ticket?room={self.rid}", b"{}"), 401, "unauthorized")
        self.assert_error(self.ticket(room="nope"), 404, "not_found")
        for body in ({"role": "admin", "id": V_DEV}, {"role": "device", "id": "short"}, {"role": "device"},
                     {"id": V_DEV}, [1], "x", {"role": "device", "id": V_DEV.upper()}):
            with self.subTest(body=body):
                self.assert_error(self.ticket(body), 400, "bad_request")
        self.assert_error(self.ticket(raw=b"{not json"), 400, "bad_request")
        self.assert_error(self.ticket(raw=b'{"role":"device","id":"' + b"a" * 300 + b'"}'), 413, "payload_too_large")
        self.assert_error(self.call("GET", f"/api/relay_ticket?room={self.rid}"), 405, "method_not_allowed")

    def test_not_configured(self):
        app, _ = mk_app()
        rid, key = self.room(app=app)
        r = self.call("POST", f"/api/relay_ticket?room={rid}", {"role": "device", "id": V_DEV},
                      {"Authorization": f"Bearer {key}"}, app=app)
        self.assert_error(r, 503, "relay_not_configured")

    def test_rate_limits(self):
        for i in range(30):
            self.assertEqual(self.ticket(ip=f"10.1.0.{i}").status, 200)
        r = self.ticket(ip="10.1.1.1")
        self.assert_error(r, 429, "rate_limited")            # per room
        self.assertTrue(r.header("Retry-After"))
        rid2, key2 = self.room(ip="10.9.9.8")
        self.assertEqual(self.ticket(room=rid2, key=key2, ip="10.1.1.1").status, 200)
        self.clock.t += 2
        self.assertEqual(self.ticket(ip="10.1.1.2").status, 200)   # 0.5/s refill
        rid3, key3 = self.room(ip="10.9.9.7")
        rid4, key4 = self.room(ip="10.9.9.6")
        for i in range(30):
            r = self.ticket(room=rid3, key=key3, ip="10.7.7.7") if i % 2 else self.ticket(room=rid4, key=key4,
                                                                                           ip="10.7.7.7")
            self.assertEqual(r.status, 200)
        self.assert_error(self.ticket(room=rid3, key=key3, ip="10.7.7.7"), 429, "rate_limited")   # per IP

    def test_preflight(self):
        r = self.call("OPTIONS", f"/api/relay_ticket?room={self.rid}")
        self.assertEqual(r.status, 204)
        self.assertIn("Authorization", r.header("Access-Control-Allow-Headers"))

    def test_nothing_logged(self):
        records = []

        class H(logging.Handler):
            def emit(self, rec):
                records.append(self.format(rec))

        h = H(level=logging.DEBUG)
        root = logging.getLogger()
        old = root.level
        root.addHandler(h)
        root.setLevel(logging.DEBUG)
        lg = logging.getLogger("pixel_cloud")
        old_pc = lg.level
        lg.setLevel(logging.DEBUG)
        try:
            d = self.ticket({"role": "browser", "id": V_BID}).json()
            self.ticket(raw=b"{bad")
        finally:
            root.removeHandler(h)
            root.setLevel(old)
            lg.setLevel(old_pc)
        text = "\n".join(records)
        for secret in (d["ticket"], self.key, RELAY_SECRET, V_BID):
            self.assertNotIn(secret, text)

    def test_api_file_and_routes(self):
        src = (ROOT / "api" / "relay_ticket.py").read_text(encoding="utf-8")
        self.assertIn("class handler(ApiHandler)", src)


class ConfigAndControlTests(Base):
    def test_config_relay_on_off(self):
        d = self.call("GET", "/api/config").json()
        self.assertIsNone(d["relay"])
        self.assertEqual(d["features"], [])
        app, _ = mk_app(RELAY_ENV)
        d = self.call("GET", "/api/config", app=app).json()
        self.assertEqual(d["relay"], {"url": "wss://pixel-relay.example.workers.dev", "v": 1})
        self.assertEqual(d["features"], ["control"])
        self.assertNotIn(RELAY_SECRET, json.dumps(d))

    def test_ctl_to_control(self):
        rid, key = self.room()
        base = {"session": "s1", "owner": "Ayşe", "project": "p", "state": "working"}
        r = self.status(rid, key, {**base, "ctl": f"{V_DEV}.a1"})
        self.assertEqual(r.status, 200, r.body)
        self.status(rid, key, {**base, "session": "s2"})
        agents = {a["owner"] + a.get("project", ""): a for a in self.state(rid).json()["agents"]}
        ctl = [a["control"] for a in self.state(rid).json()["agents"]]
        self.assertIn({"device": V_DEV, "agent": "a1"}, ctl)
        self.assertIn(None, ctl)
        self.assertTrue(agents)
        for bad in ("x.a1", f"{V_DEV}.", f"{V_DEV}.a b", f"{V_DEV.upper()}.a1", f"{V_DEV}.{'a' * 25}", 5):
            with self.subTest(ctl=bad):
                self.assert_error(self.status(rid, key, {**base, "ctl": bad}), 400, "bad_request")

    def test_v2_payload_unchanged(self):
        """Old clients never send ctl; their records and Agent JSON only gain `control: null`."""
        rid, key = self.room()
        self.status(rid, key, {"session": "s1", "owner": "Mert", "project": "p", "state": "thinking"})
        a = self.state(rid).json()["agents"][0]
        self.assertIsNone(a["control"])
        fields = self.app.storage.read_room(rid)
        rec = json.loads([v for k, v in fields.items() if k.startswith("a")][0])
        self.assertNotIn("ctl", rec)


class LocalServerIgnoresCtlTests(unittest.TestCase):
    """V3.md §13.2: the local server.py /api/status accepts `ctl` (shared validator) and ignores it."""

    def test_local_status_accepts_and_ignores_ctl(self):
        import tempfile
        from test_local_status import Server
        with tempfile.TemporaryDirectory() as tmp:
            srv = Server(tmp)
            try:
                r, _ = srv.request("POST", "/api/status", {"session": "s", "owner": "Timur", "state": "working",
                                                           "ctl": f"{V_DEV}.a1"})
                self.assertEqual(r.status, 200)
                r, snap = srv.request("GET", "/api/agents")
                self.assertEqual(len(snap["agents"]), 1)
                self.assertNotIn(V_DEV, json.dumps(snap))
            finally:
                srv.close()


class WebClientIgnoresUnknownFieldsTests(unittest.TestCase):
    """V3.md §13.2: v1/v2 consumers read only named Agent JSON fields, so `control` cannot break them."""

    def test_agents_and_ui_do_not_iterate_agent_objects(self):
        for name in ("agents.js", "ui.js", "cloud.js", "main.js"):
            src = (ROOT / "web" / "js" / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotRegex(src, r"Object\.(keys|entries|values)\(\s*(a|agent|ag)\s*\)")
                self.assertNotRegex(src, r"for\s*\(\s*(const|let|var)\s+\w+\s+in\s+(a|agent|ag)\s*\)")


if __name__ == "__main__":
    if "--write-vectors" in sys.argv:
        write_vectors()
        print(f"wrote {VECTORS}")
    else:
        unittest.main()
