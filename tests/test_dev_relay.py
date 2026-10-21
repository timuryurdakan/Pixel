"""Runs the relay conformance suite in-process against tools/dev_relay.py (V3.md §4.4, §17.1), plus the
dev_relay-only checks. Set PIXEL_RELAY_CONFORMANCE_URL (+ RELAY_SECRET or the *_SERVER/_ROOM/_KEY variables) to
also run the suite against a deployed Worker."""

import ast
import json
import os
import re
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)
import relay_conformance as RC

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import dev_relay  # noqa: E402

from pixel_connector import ws as W  # noqa: E402

SECRET = "d" * 43
ORIGIN = "http://127.0.0.1:8787"


class DevTarget(RC.Target):
    """dev_relay on a free port in this process, with a controllable clock offset."""

    def __init__(self) -> None:
        self.offset = 0.0
        self.secret = SECRET.encode()
        self.origin = ORIGIN
        self.relay = dev_relay.Relay(SECRET, ORIGIN, clock=lambda: time.time() + self.offset)
        self.server, self.url = dev_relay.serve_in_thread(self.relay, "127.0.0.1", 0)
        self.name = self.url

    def ticket(self, room, role, pid, iat_offset=0):
        return RC.R.mint_ticket(self.secret, room, role, pid, int(time.time() + self.offset) + iat_offset)[0]

    def can_advance(self):
        return True

    def advance(self, seconds):
        self.offset += seconds

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


_target = None


def setUpModule():
    global _target
    _target = DevTarget()
    DevRelayConformance.target = _target


def tearDownModule():
    if _target:
        _target.stop()


class DevRelayConformance(RC.ConformanceTests):
    target = None

    def setUp(self):
        self.target.offset = 0.0
        super().setUp()


if os.environ.get("PIXEL_RELAY_CONFORMANCE_URL"):
    class DeployedRelayConformance(RC.ConformanceTests):
        target = None

        @classmethod
        def setUpClass(cls):
            cls.target = RC.target_from_env()
            super().setUpClass()


del RC.ConformanceTests   # don't collect the unconfigured base class from this module


class DevRelayOnlyTests(unittest.TestCase):
    def test_no_secret_means_503(self):
        relay = dev_relay.Relay(None, ORIGIN)
        srv, url = dev_relay.serve_in_thread(relay, "127.0.0.1", 0)
        try:
            with self.assertRaises(W.HandshakeError) as cm:
                W.connect(url + "/v1/rooms/" + "a" * 26 + "/ws", protocols=["pixel-relay.v1", "tk.x"])
            self.assertEqual(cm.exception.status, 503)
            with urllib.request.urlopen(url.replace("ws:", "http:") + "/health", timeout=5) as r:
                self.assertEqual(json.loads(r.read()), {"ok": True, "v": 1})
        finally:
            srv.shutdown()
            srv.server_close()

    def test_rooms_are_dropped_when_empty(self):
        t = _target
        room = RC.rand_room()
        pid = RC.rand_peer()
        ws = W.connect(f"{t.url}/v1/rooms/{room}/ws", protocols=["pixel-relay.v1", "tk." + t.ticket(room, "device", pid)])
        ws.recv(timeout=5)
        self.assertIn(room, t.relay.rooms)
        ws.close(wait=1)
        for _ in range(50):
            if room not in t.relay.rooms:
                break
            time.sleep(0.05)
        self.assertNotIn(room, t.relay.rooms)

    def test_validate_frame_matrix(self):
        v = dev_relay.validate_frame
        dev, br = "abcdefghijklmnop", "c:AAAAAAAAAAAA"
        ok = lambda f, role: v(json.dumps(f), role)["ok"]   # noqa: E731
        self.assertTrue(ok({"t": "send", "to": dev, "seq": 0, "k": "e", "b": "AAAA"}, "browser"))
        self.assertTrue(ok({"t": "send", "to": dev, "seq": 1.0, "k": "hs", "b": "AAA", "ch": None}, "browser"))
        self.assertTrue(ok({"t": "send", "to": br, "seq": 2, "k": "e", "b": "AA", "ch": "A" * 16}, "device"))
        self.assertTrue(ok({"t": "send", "to": "pair:K7Q2", "seq": 2, "k": "hs", "b": "AA"}, "browser"))
        for f in ({"t": "send", "to": dev, "seq": -1, "k": "e", "b": "AA"},
                  {"t": "send", "to": dev, "seq": True, "k": "e", "b": "AA"},
                  {"t": "send", "to": dev, "seq": 1, "k": "x", "b": "AA"},
                  {"t": "send", "to": dev, "seq": 1, "k": "e", "b": "A"},
                  {"t": "send", "to": dev, "seq": 1, "k": "e", "b": ""},
                  {"t": "send", "to": dev, "seq": 1, "k": "e", "b": "AA=="},
                  {"t": "send", "to": dev, "seq": 1, "k": "e", "b": "AA", "ch": "short"},
                  {"t": "send", "to": "pair:K7QI", "seq": 1, "k": "e", "b": "AA"},
                  {"t": "send", "to": "DEVICE", "seq": 1, "k": "e", "b": "AA"},
                  {"t": "hello", "v": 1, "app": "x" * 41},
                  {"t": "hello", "v": 1, "caps": "e2e1"},
                  {"t": "pair.open", "slot": "k7q2"}, {"t": 5}, {"x": 1}):
            with self.subTest(f=f):
                self.assertEqual(v(json.dumps(f), "browser")["code"] if f.get("t") != "pair.open"
                                 else v(json.dumps(f), "device")["code"], "bad_frame")
        self.assertEqual(v('{"t":"hello","v":true}', "device")["code"], "version")
        self.assertEqual(v('{"t":"send","to":"abcdefghijklmnop","seq":1,"k":"e","b":"AA","x":NaN}', "browser")["code"],
                         "bad_frame")
        big = "A" * (49152 * 4 // 3 + 4)
        self.assertEqual(v(json.dumps({"t": "send", "to": dev, "seq": 3, "k": "e", "b": big}), "browser"),
                         {"ok": False, "code": "too_large", "ref": 3})


class DevServerRelayFlagTests(unittest.TestCase):
    def test_dev_server_relay_flag(self):
        from unittest import mock
        import dev_server
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RELAY_SECRET", None)
            srv, url, secret, generated = dev_server.start_relay(0, 8787)
            try:
                self.assertTrue(generated)
                self.assertEqual(os.environ["RELAY_SECRET"], secret)
                self.assertRegex(url, r"^ws://127\.0\.0\.1:\d+$")
                self.assertEqual(os.environ["PIXEL_RELAY_URL"], url)
                self.assertGreaterEqual(len(os.environ["RELAY_SECRET"]), 32)
                from pixel_cloud.settings import Settings
                st = Settings.from_env()
                self.assertTrue(st.relay_enabled)
                self.assertEqual(st.relay_url, url)
                csp = dict(dev_server.security_headers(url))["Content-Security-Policy"]
                self.assertIn(f"connect-src 'self' {url};", csp)
                self.assertIn("connect-src 'self';", dict(dev_server.SECURITY_HEADERS)["Content-Security-Policy"])
                with urllib.request.urlopen(url.replace("ws:", "http:") + "/health", timeout=5) as r:
                    self.assertEqual(json.loads(r.read()), {"ok": True, "v": 1})
            finally:
                srv.shutdown()
                srv.server_close()
        self.assertTrue(dev_server.parse_args(["--relay"]).relay)


class StaticInvariantTests(unittest.TestCase):
    """V3.md §3.3 / §4.3: no logging, no storage, no timers or alarms in the relay; `b` is only copied."""

    def test_worker_sources(self):
        for name in ("lib.js", "core.js", "room.js", "index.js"):
            src = (ROOT / "relay" / "src" / name).read_text(encoding="utf-8")
            code = re.sub(r"//.*", "", src)
            with self.subTest(file=name):
                for banned in ("console.", "setTimeout", "setInterval", "setAlarm", ".storage", "fetch(\"http",
                               "fetch('http", "eval(", "new Function"):
                    self.assertNotIn(banned, code)
        core = (ROOT / "relay" / "src" / "core.js").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"\bf\.b\b", core)), 1)            # copied once into the msg frame
        self.assertNotRegex(core, r"(atob|b64urlDecode)\(\s*f\.b")

    def test_worker_has_no_runtime_dependencies(self):
        pkg = json.loads((ROOT / "relay" / "package.json").read_text(encoding="utf-8"))
        self.assertNotIn("dependencies", pkg)
        self.assertEqual(list(pkg["devDependencies"]), ["wrangler"])
        self.assertRegex(pkg["devDependencies"]["wrangler"], r"^4\.\d+\.\d+$")     # exact pin
        for name in ("lib.js", "core.js", "room.js", "index.js"):
            src = (ROOT / "relay" / "src" / name).read_text(encoding="utf-8")
            for spec in re.findall(r"""(?:from|import)\s+['"]([^'"]+)['"]""", src):
                self.assertTrue(spec.startswith("./") or spec == "cloudflare:workers", spec)

    def test_wrangler_config(self):
        text = (ROOT / "relay" / "wrangler.jsonc").read_text(encoding="utf-8")
        cfg = json.loads(re.sub(r'(?m)^((?:[^"/\n]|"(?:[^"\\\n]|\\.)*")*)//.*$', r"\1", text))
        self.assertEqual(cfg["name"], "pixel-relay")
        self.assertEqual(cfg["main"], "src/index.js")
        self.assertGreaterEqual(cfg["compatibility_date"], "2026-04-07")
        self.assertEqual(cfg["durable_objects"]["bindings"], [{"name": "ROOMS", "class_name": "RoomRelay"}])
        self.assertEqual(cfg["migrations"], [{"tag": "v1", "new_sqlite_classes": ["RoomRelay"]}])
        self.assertNotIn("RELAY_SECRET", json.dumps(cfg["vars"]))
        self.assertIn("https://pixel-rouge-phi.vercel.app", cfg["vars"]["ALLOWED_ORIGINS"])

    def test_dev_relay_never_logs(self):
        tree = ast.parse((ROOT / "tools" / "dev_relay.py").read_text(encoding="utf-8"))
        prints = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print"]
        self.assertLessEqual(len(prints), 3)            # start-up banner and two start-up errors only
        self.assertNotIn("logging", {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names})

    def test_constants_match_worker(self):
        lib = (ROOT / "relay" / "src" / "lib.js").read_text(encoding="utf-8")

        def js(name):
            return re.search(rf"export const {name} = ([0-9_]+);", lib).group(1).replace("_", "")
        for name in ("MAX_FRAME", "MAX_B_BROWSER", "MAX_B_DEVICE", "MAX_DEVICES", "MAX_BROWSERS", "MAX_SLOTS",
                     "SLOT_TTL_MS", "BAD_FRAME_LIMIT", "BAD_FRAME_WINDOW_MS", "RATE_STRIKE_LIMIT"):
            with self.subTest(name=name):
                self.assertEqual(int(js(name)), getattr(dev_relay, name))
        self.assertIn("device: [20, 100]", lib)
        self.assertIn("browser: [5, 30]", lib)
        self.assertIn("pair: [3 / 60, 3]", lib)
        self.assertEqual(dev_relay.RATE, {"device": (20.0, 100), "browser": (5.0, 30), "pair": (3 / 60, 3)})


if __name__ == "__main__":
    unittest.main()
