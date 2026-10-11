"""Local POST /api/status (CLOUD.md §16): parity with the cloud, CSRF guard, key, derive ticker, SSE."""

import getpass
import http.client
import json
import logging
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_cloud import ids
from pixel_cloud.app import App, Request
from pixel_cloud.storage import MemoryStorage
from pixel_office.httpd import make_server
from pixel_office.names_store import Namer, NamesStore
from pixel_office.reported import ReportedAgents
from pixel_office.store import AgentStore

logging.getLogger("pixel_cloud").setLevel(logging.CRITICAL)


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Server:
    def __init__(self, tmp, write_key=None, extensions=True, clock=time.time):
        self.names = NamesStore(Path(tmp) / "names.json")
        self.store = AgentStore("live", namer=Namer(self.names))
        self.reported = ReportedAgents(self.store, clock=clock)
        web = Path(tmp) / "web"
        web.mkdir(exist_ok=True)
        (web / "index.html").write_text("x")
        kw = dict(reported=self.reported, names=self.names, write_key=write_key) if extensions else {}
        self.srv = make_server("127.0.0.1", 0, self.store, web, **kw)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.srv.shutting_down.set()
        self.srv.shutdown()
        self.srv.server_close()

    def request(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json"} if body is not None else {}
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            return r, json.loads(data) if data else None
        except ValueError:
            return r, data


class LocalStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Server(self.tmp.name)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def post(self, body, headers=None, path="/api/status"):
        return self.s.request("POST", path, body, headers)

    def test_write_read_and_response_shape(self):
        r, d = self.post({"session": "my-agent", "name": "Kâtip", "owner": "Timur", "project": "notes",
                          "state": "working", "tool": "llm", "source": "custom"}, path="/api/status?room=ignored")
        self.assertEqual(r.status, 200)
        self.assertEqual(set(d), {"ok", "ids", "serverTime"})
        oid = ids.default_owner_id("Timur")
        self.assertEqual(d["ids"], [ids.local_agent_id(oid, "my-agent", None)])
        self.assertTrue(d["ids"][0].startswith("r"))
        r, snap = self.s.request("GET", "/api/agents")
        a = snap["agents"][0]
        self.assertEqual((a["name"], a["nameSource"], a["project"], a["state"], a["tool"], a["detail"], a["source"]),
                         ("Kâtip", "reported", "notes", "working", "llm", "Çalışıyor", "custom"))
        self.assertIsNone(a["projectPath"])
        r, d = self.post({"session": "my-agent", "owner": "Timur", "state": "offline"})
        self.assertEqual(self.s.request("GET", "/api/agents")[1]["agents"], [])

    def test_owner_defaults_to_os_user(self):
        r, d = self.post({"session": "x", "state": "idle"})
        self.assertEqual(r.status, 200)
        try:
            user = getpass.getuser() or "local"
        except Exception:
            user = "local"
        self.assertEqual(d["ids"][0], ids.local_agent_id(ids.default_owner_id(user[:24]), "x", None))

    def test_subagent_and_auto_names(self):
        r, d = self.post({"agents": [{"session": "s", "owner": "T", "state": "working"},
                                     {"session": "s", "sub": "k", "owner": "T", "state": "working", "agentType": "Plan"}]})
        main, sub = d["ids"]
        agents = {a["id"]: a for a in self.s.request("GET", "/api/agents")[1]["agents"]}
        self.assertEqual(agents[sub]["parentId"], main)
        self.assertEqual((agents[sub]["name"], agents[sub]["isSubagent"]), ("Plan", True))
        self.assertEqual(agents[main]["nameSource"], "auto")

    def test_parity_with_cloud(self):
        clock = FakeClock()
        app = App(storage=MemoryStorage(clock), env={"PIXEL_SECRET": "q" * 40}, clock=clock, mapper=None)
        room = app.dispatch(Request("POST", "/api/rooms", {}, body=b"{}")).json()
        bodies = [
            {"session": "s", "owner": "T", "state": "idle"},
            {"session": "s", "owner": "T", "state": "dancing"},
            {"session": "s/x", "owner": "T", "state": "idle"},
            {"session": "s", "owner": "T", "state": "idle", "project": "a/b"},
            {"session": "s", "owner": "T", "state": "working", "final": True},
            {"session": "s", "owner": "T", "state": "sleeping", "final": True},
            {"agents": [{"session": f"s{i}", "owner": "T", "state": "idle"} for i in range(11)]},
            {"agents": [{"session": "s1", "owner": "T", "state": "idle"}, {"session": "s2", "owner": "T", "state": "idle"}]},
            {"session": "s", "owner": "T", "state": "idle", "color": 99},
            {"session": "s", "owner": "T", "state": "idle", "pad": "x" * 5000},
            [1], "notjson",
        ]
        for b in bodies:
            raw = b.encode() if isinstance(b, str) else json.dumps(b).encode()
            c = app.dispatch(Request("POST", f"/api/status?room={room['roomId']}",
                                     {"Authorization": f"Bearer {room['writeKey']}"}, body=raw, client_ip=str(id(b))))
            lr, ld = self.post(raw)
            self.assertEqual(lr.status, c.status, b)
            if c.status == 200:
                self.assertEqual(set(ld), set(c.json()))
                self.assertEqual(len(ld["ids"]), len(c.json()["ids"]))
            else:
                self.assertEqual(ld["error"], c.json()["error"], b)
                self.assertEqual(ld["message"], c.json()["message"], b)

    def test_csrf_guard(self):
        b = {"session": "s", "owner": "T", "state": "idle"}
        r, d = self.post(b, headers={"Origin": "http://evil.example"})
        self.assertEqual((r.status, d["error"]), (403, "forbidden"))
        r, d = self.post(b, headers={"Origin": "null"})
        self.assertEqual(r.status, 403)
        r, d = self.post(b, headers={"Origin": f"http://127.0.0.1:{self.s.port + 1}"})
        self.assertEqual(r.status, 403)
        for ct in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""):
            r, d = self.post(json.dumps(b).encode(), headers={"Content-Type": ct})
            self.assertEqual((r.status, d["error"]), (400, "bad_request"), ct)
        for origin in (f"http://127.0.0.1:{self.s.port}", f"http://localhost:{self.s.port}"):
            r, _ = self.post(b, headers={"Origin": origin})
            self.assertEqual(r.status, 200, origin)
        r, _ = self.post(b, headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(r.status, 200)
        self.assertEqual(len(self.s.reported.ids()), 1)
        # preflight is never approved
        r, _ = self.s.request("OPTIONS", "/api/status", headers={"Origin": "http://evil.example",
                                                                 "Access-Control-Request-Method": "POST"})
        self.assertEqual(r.status, 405)
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    def test_body_cap(self):
        r, d = self.post(b'{"session":"s","owner":"T","state":"idle","x":"' + b"y" * 5000 + b'"}')
        self.assertEqual((r.status, d["error"]), (413, "payload_too_large"))
        s = socket.create_connection(("127.0.0.1", self.s.port), timeout=5)
        s.sendall(b"POST /api/status HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Content-Length: 99999999\r\n\r\n")
        data = s.recv(65536)
        s.close()
        self.assertIn(b" 413 ", data.split(b"\r\n")[0])

    def test_health_features_and_405(self):
        r, d = self.s.request("GET", "/api/health")
        self.assertEqual(d["features"], ["rename", "status"])
        self.assertEqual((d["ok"], d["mode"]), (True, "live"))
        for p in ("/api/agents", "/api/statuses", "/", "/api/agents/rename/x"):
            r, d = self.post({"a": 1}, path=p)
            self.assertEqual((r.status, d["error"]), (405, "method not allowed"), p)

    def test_config_endpoint_v2_only(self):
        # Phase 9: the v2 local server answers /api/config with mode "local" (+ features); the page treats
        # anything but mode "cloud" as local. A bare v1 make_server keeps answering 404.
        r, d = self.s.request("GET", "/api/config")
        self.assertEqual(r.status, 200)
        self.assertEqual(d["mode"], "local")
        self.assertEqual(d["features"], ["rename", "status"])
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        with tempfile.TemporaryDirectory() as tmp:
            v1 = Server(tmp, extensions=False)
            try:
                r, _ = v1.request("GET", "/api/config")
                self.assertEqual(r.status, 404)
                r, d = v1.request("GET", "/api/health")
                self.assertNotIn("features", d)
            finally:
                v1.close()

    def test_webmanifest_mime(self):
        (self.s.srv.web_root / "manifest.webmanifest").write_text('{"name":"x"}')
        r, _ = self.s.request("GET", "/manifest.webmanifest")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "application/manifest+json")

    def test_room_full(self):
        for i in range(6):
            r, _ = self.post({"agents": [{"session": f"s{i}-{j}", "owner": "T", "state": "idle"} for j in range(8)]})
            self.assertEqual(r.status, 200)
        r, d = self.post({"session": "extra", "owner": "T", "state": "idle"})
        self.assertEqual((r.status, d["error"]), (429, "room_full"))
        self.assertEqual(r.getheader("Retry-After"), "60")
        r, _ = self.post({"session": "s0-0", "owner": "T", "state": "working"})
        self.assertEqual(r.status, 200)

    def test_sse_broadcast(self):
        s = socket.create_connection(("127.0.0.1", self.s.port), timeout=5)
        s.sendall(b"GET /events HTTP/1.1\r\nHost: x\r\n\r\n")
        f = s.makefile("rb")

        def next_event():
            ev = {}
            while True:
                line = f.readline().decode().rstrip("\n")
                if not line:
                    if "event" in ev:
                        return ev
                    continue
                if line.startswith(":"):
                    continue
                k, _, v = line.partition(": ")
                ev[k] = v
        self.assertEqual(next_event()["event"], "snapshot")
        _, d = self.post({"session": "b", "owner": "T", "state": "thinking"})
        ev = next_event()
        self.assertEqual((ev["event"], json.loads(ev["data"])["id"]), ("agent_added", d["ids"][0]))
        self.post({"session": "b", "owner": "T", "state": "working", "tool": "Bash"})
        ev = next_event()
        self.assertEqual((ev["event"], json.loads(ev["data"])["state"]), ("agent_updated", "working"))
        self.post({"session": "b", "owner": "T", "state": "offline"})
        ev = next_event()
        self.assertEqual((ev["event"], json.loads(ev["data"])), ("agent_removed", {"id": d["ids"][0]}))
        f.close()
        s.close()


class KeyAndV1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_key_required_when_set(self):
        s = Server(self.tmp.name, write_key="k3y-Secret-123")
        try:
            b = {"session": "s", "owner": "T", "state": "idle"}
            r, d = s.request("POST", "/api/status", b)
            self.assertEqual((r.status, d["error"]), (401, "unauthorized"))
            r, d = s.request("POST", "/api/status", b, {"Authorization": "Bearer wrong"})
            self.assertEqual(r.status, 401)
            r, d = s.request("POST", "/api/agents/rename", {"id": "x", "name": "y"})
            self.assertEqual(r.status, 401)
            r, d = s.request("POST", "/api/status", b, {"Authorization": "Bearer k3y-Secret-123"})
            self.assertEqual(r.status, 200)
        finally:
            s.close()

    def test_v1_server_unchanged(self):
        s = Server(self.tmp.name, extensions=False)
        try:
            r, d = s.request("GET", "/api/health")
            self.assertEqual(d, {"ok": True, "mode": "live", "agents": 0})
            r, d = s.request("POST", "/api/status", {"session": "s", "owner": "T", "state": "idle"})
            self.assertEqual((r.status, d), (405, {"error": "method not allowed"}))
            r, d = s.request("POST", "/api/agents/rename", {"id": "x"})
            self.assertEqual(r.status, 405)
        finally:
            s.close()

    def test_server_main_flags(self):
        import server
        a = server.parse_args(["--names-file", str(Path(self.tmp.name) / "n.json"), "--write-key", "abcdefgh12"])
        self.assertEqual((a.names_file.name, a.write_key), ("n.json", "abcdefgh12"))
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                server.parse_args(["--write-key", "short"])
        self.assertTrue(server.is_loopback("127.0.0.1"))
        self.assertTrue(server.is_loopback("localhost"))
        self.assertFalse(server.is_loopback("0.0.0.0"))


class TickerTests(unittest.TestCase):
    """Time-based transitions use the same pixel_cloud.derive rules as the cloud."""

    def setUp(self):
        self.clock = FakeClock()
        self.store = AgentStore("live")
        self.rep = ReportedAgents(self.store, clock=self.clock)

    def apply(self, **kw):
        from pixel_cloud.validate import parse_update
        return self.rep.apply([parse_update(dict({"session": "s", "owner": "T"}, **kw))])[0]

    def state(self, aid):
        a = self.store.get(aid)
        return a.state.value if a else None

    def test_transitions(self):
        idle = self.apply(state="idle")
        work = self.apply(session="w", state="working", tool="Bash")
        t0 = int(self.clock.t * 1000)
        self.clock.t += 180
        self.rep.tick()
        self.assertEqual((self.state(idle), self.state(work)), ("idle", "working"))
        self.clock.t += 1
        self.rep.tick()
        self.assertEqual(self.state(idle), "sleeping")
        self.assertEqual(self.store.get(idle).state_since, t0 + 180_000)
        self.clock.t += 1620
        self.rep.tick()
        self.assertEqual(self.state(work), "sleeping")
        self.assertIsNone(self.store.get(work).tool)
        self.clock.t += 900
        self.rep.tick()
        self.assertEqual((self.state(idle), self.state(work)), (None, None))
        self.assertEqual(self.rep.ids(), set())

    def test_final_and_subagents(self):
        fin = self.apply(state="sleeping", final=True)
        sub = self.apply(session="p", sub="c", state="sleeping")
        self.clock.t += 121
        self.rep.tick()
        self.assertIsNone(self.state(sub))
        self.assertEqual(self.state(fin), "sleeping")
        self.clock.t += 180
        self.rep.tick()
        self.assertIsNone(self.state(fin))

    def test_state_since_kept_without_ages(self):
        aid = self.apply(state="working", tool="Read")
        t0 = int(self.clock.t * 1000)
        self.clock.t += 10
        self.apply(state="working", tool="Edit")
        a = self.store.get(aid)
        self.assertEqual((a.state_since, a.started_at, a.tool), (t0, t0, "Edit"))

    def test_thread(self):
        rep = ReportedAgents(self.store, tick=0.01)
        rep.start()
        time.sleep(0.05)
        rep.stop()
        rep.join(1)
        self.assertFalse(rep.is_alive())


if __name__ == "__main__":
    unittest.main()
