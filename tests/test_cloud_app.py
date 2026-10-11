import http.client
import io
import json
import logging
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer

import _helpers  # noqa: F401  (sys.path)
from _fake_upstash import FakeUpstash

from pixel_cloud import ids
from pixel_cloud import settings as S
from pixel_cloud.app import App, Request
from pixel_cloud.storage import MemoryStorage, StorageError, UpstashStorage
from pixel_cloud.vercel import ApiHandler, make_handler

SECRET = "x" * 48
logging.getLogger("pixel_cloud").setLevel(logging.CRITICAL)   # expected warnings stay out of the output


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def mk_app(env=None, storage=None, mapper=None, clock=None):
    clock = clock or FakeClock()
    env = {"PIXEL_SECRET": SECRET, **(env or {})}
    return App(storage=storage if storage is not None else MemoryStorage(clock), env=env, clock=clock,
               mapper=mapper), clock


class Base(unittest.TestCase):
    def setUp(self):
        self.app, self.clock = mk_app()

    def call(self, method, path, body=None, headers=None, ip="10.0.0.1", app=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        return (app or self.app).dispatch(Request(method, path, headers or {}, body=body, client_ip=ip))

    def room(self, app=None, name="Oda", ip="10.9.9.9"):
        r = self.call("POST", "/api/rooms", {"name": name}, {"Host": "office.test"}, ip=ip, app=app)
        self.assertEqual(r.status, 201, r.body)
        d = r.json()
        return d["roomId"], d["writeKey"]

    def status(self, room, key, body, ip="10.0.0.1", app=None):
        return self.call("POST", f"/api/status?room={room}", body, {"Authorization": f"Bearer {key}"}, ip=ip, app=app)

    def state(self, room, app=None):
        r = self.call("GET", f"/api/state?room={room}", app=app)
        self.assertEqual(r.status, 200, r.body)
        return r

    def assert_error(self, r, status, code):
        self.assertEqual(r.status, status, r.body)
        self.assertEqual(r.json()["error"], code)
        self.assertIn("message", r.json())
        self.assertEqual(r.header("Cache-Control"), "no-store")
        self.assertEqual(r.header("Access-Control-Allow-Origin"), "*")
        self.assertEqual(r.header("Content-Type"), "application/json; charset=utf-8")


class ConfigHealthTests(Base):
    def test_config(self):
        r = self.call("GET", "/api/config")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.json(), {"mode": "cloud", "version": "2.0.0", "createRequiresToken": False,
                                    "storage": "memory", "poll": {"activeMs": 10000, "calmMs": 30000, "emptyMs": 60000},
                                    "limits": {"maxAgentsPerRoom": 48, "maxBatch": 10}})
        self.assertEqual(r.header("Cache-Control"), "public, max-age=60, s-maxage=300")
        app, _ = mk_app({"PIXEL_CREATE_TOKEN": "pw", "PIXEL_POLL_ACTIVE_MS": "15000", "PIXEL_POLL_CALM_MS": "1"})
        d = self.call("GET", "/api/config", app=app).json()
        self.assertTrue(d["createRequiresToken"])
        self.assertEqual(d["poll"], {"activeMs": 15000, "calmMs": 3000, "emptyMs": 60000})
        self.assertNotIn("pw", json.dumps(d))

    def test_health(self):
        r = self.call("GET", "/api/health")
        self.assertEqual(r.json(), {"ok": True, "version": "2.0.0", "storage": "memory", "storageOk": True})
        self.assertEqual(r.header("Cache-Control"), "no-store")
        r = self.call("HEAD", "/api/health")
        self.assertEqual(r.status, 200)

    def test_routing_errors(self):
        self.assert_error(self.call("GET", "/api/nope"), 404, "not_found")
        self.assert_error(self.call("GET", "/api/state/"), 404, "not_found")
        r = self.call("DELETE", "/api/state?room=x")
        self.assert_error(r, 405, "method_not_allowed")
        self.assertEqual(r.header("Allow"), "GET, HEAD")
        r = self.call("GET", "/api/status")
        self.assert_error(r, 405, "method_not_allowed")
        self.assertEqual(r.header("Allow"), "OPTIONS, POST")
        self.assert_error(self.call("PUT", "/api/config"), 405, "method_not_allowed")

    def test_preflight(self):
        for p in ("/api/status", "/api/rename"):
            r = self.call("OPTIONS", p + "?room=abc")
            self.assertEqual(r.status, 204)
            self.assertEqual(r.body, b"")
            self.assertEqual(r.header("Access-Control-Allow-Origin"), "*")
            self.assertEqual(r.header("Access-Control-Allow-Methods"), "POST, OPTIONS")
            self.assertEqual(r.header("Access-Control-Allow-Headers"), "Authorization, Content-Type")
            self.assertEqual(r.header("Access-Control-Max-Age"), "86400")


class RoomTests(Base):
    def test_create(self):
        r = self.call("POST", "/api/rooms", {"name": " Kod‮ Kulübü "}, {"Host": "office.test"})
        self.assertEqual(r.status, 201)
        self.assertEqual(r.header("Cache-Control"), "no-store")
        d = r.json()
        self.assertTrue(ids.valid_room_id(d["roomId"]))
        self.assertEqual(d["name"], "Kod Kulübü")
        self.assertEqual(d["writeKey"], ids.write_key(SECRET.encode(), d["roomId"]))
        self.assertEqual(d["viewUrl"], f"http://office.test/?room={d['roomId']}")
        self.assertEqual(d["joinUrl"], f"{d['viewUrl']}#key={d['writeKey']}")
        self.assertEqual(d["createdAt"], int(self.clock.t * 1000))
        self.assertEqual(set(d), {"roomId", "name", "writeKey", "viewUrl", "joinUrl", "createdAt"})
        st = self.state(d["roomId"]).json()
        self.assertEqual(st["room"], {"id": d["roomId"], "name": "Kod Kulübü"})

    def test_defaults_and_hosts(self):
        r = self.call("POST", "/api/rooms", None, {"X-Forwarded-Host": "po.vercel.app", "X-Forwarded-Proto": "https",
                                                  "Host": "internal"})
        self.assertEqual(r.json()["name"], "Pixel Ofis")
        self.assertTrue(r.json()["viewUrl"].startswith("https://po.vercel.app/?room="))
        r = self.call("POST", "/api/rooms", {"name": ""}, {"Host": "evil host/x"}, ip="10.0.0.2")
        self.assertEqual(r.json()["name"], "Pixel Ofis")
        self.assertTrue(r.json()["viewUrl"].startswith("/?room="))
        app, _ = mk_app({"PIXEL_PUBLIC_URL": "https://pixel.example/"})
        r = self.call("POST", "/api/rooms", {}, {"Host": "other"}, app=app)
        self.assertTrue(r.json()["joinUrl"].startswith("https://pixel.example/?room="))

    def test_create_token(self):
        app, _ = mk_app({"PIXEL_CREATE_TOKEN": "s3cret"})
        self.assert_error(self.call("POST", "/api/rooms", {"name": "x"}, app=app), 403, "forbidden")
        self.assert_error(self.call("POST", "/api/rooms", {"createToken": "nope"}, app=app), 403, "forbidden")
        self.assert_error(self.call("POST", "/api/rooms", {"createToken": 5}, app=app), 403, "forbidden")
        self.assertEqual(self.call("POST", "/api/rooms", {"createToken": "s3cret"}, app=app).status, 201)

    def test_create_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.call("POST", "/api/rooms", {}, ip="1.1.1.1").status, 201)
        r = self.call("POST", "/api/rooms", {}, ip="1.1.1.1")
        self.assert_error(r, 429, "rate_limited")
        self.assertEqual(r.json()["retryAfter"], int(r.header("Retry-After")))
        self.assertGreater(r.json()["retryAfter"], 600)
        self.assertEqual(self.call("POST", "/api/rooms", {}, ip="2.2.2.2").status, 201)
        self.clock.t += 721
        self.assertEqual(self.call("POST", "/api/rooms", {}, ip="1.1.1.1").status, 201)

    def test_create_errors(self):
        self.assert_error(self.call("POST", "/api/rooms", b"{" + b" " * 1100 + b"}"), 413, "payload_too_large")
        self.assert_error(self.call("POST", "/api/rooms", b"[1]", ip="3.3.3.3"), 400, "bad_request")
        self.assert_error(self.call("POST", "/api/rooms", b"{bad", ip="3.3.3.4"), 400, "bad_request")
        self.assert_error(self.call("POST", "/api/rooms", {"name": 7}, ip="3.3.3.5"), 400, "bad_request")


class StateStatusTests(Base):
    def setUp(self):
        super().setUp()
        self.rid, self.key = self.room()

    def test_state_404_and_empty(self):
        for bad in ("", "?room=", "?room=ABC", "?room=" + "a" * 27, "?room=" + "1" * 26):
            self.assert_error(self.call("GET", "/api/state" + bad), 404, "not_found")
        other = ids.new_room_id()
        r = self.state(other)
        self.assertEqual(r.json(), {"mode": "cloud", "serverTime": int(self.clock.t * 1000),
                                    "room": {"id": other, "name": "Pixel Ofis"}, "pollMs": 60000, "agents": []})
        self.assertEqual(r.header("Cache-Control"), "public, max-age=0, s-maxage=5")

    def test_auth(self):
        body = {"session": "s", "owner": "T", "state": "idle"}
        self.assert_error(self.call("POST", f"/api/status?room={self.rid}", body), 401, "unauthorized")
        self.assert_error(self.status(self.rid, "A" * 32, body), 401, "unauthorized")
        self.assert_error(self.call("POST", f"/api/status?room={self.rid}", body, {"Authorization": f"Basic {self.key}"}),
                          401, "unauthorized")
        other, okey = self.room(name="b")
        self.assert_error(self.status(self.rid, okey, body), 401, "unauthorized")       # key of another room
        self.assert_error(self.status("bad", self.key, body), 404, "not_found")
        app2, _ = mk_app({"PIXEL_SECRET": "y" * 48}, storage=self.app.storage)
        self.assert_error(self.status(self.rid, self.key, body, app=app2), 401, "unauthorized")  # rotated secret
        self.assertEqual(self.state(self.rid).json()["agents"], [])

    def test_write_and_read(self):
        r = self.status(self.rid, self.key, {"session": "build-bot", "owner": "Timur", "project": "Pixel",
                                             "state": "working", "tool": "Bash", "startedAgeMs": 600000,
                                             "toolCount": 12, "source": "cursor", "ownerId": "o_Xk3abc"})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.header("Cache-Control"), "no-store")
        d = r.json()
        self.assertEqual(set(d), {"ok", "ids", "serverTime"})
        self.assertEqual(d["ids"], [ids.agent_id(self.rid, "o_Xk3abc", "build-bot", None)])
        st = self.state(self.rid)
        self.assertEqual(st.header("Cache-Control"), "public, max-age=0, s-maxage=5")
        a = st.json()["agents"][0]
        now = int(self.clock.t * 1000)
        self.assertEqual({k: a[k] for k in ("id", "name", "project", "state", "tool", "detail", "toolCount",
                                            "startedAt", "stateSince", "updatedAt", "owner", "ownerId", "source",
                                            "isSubagent", "parentId", "projectPath", "model")},
                         {"id": d["ids"][0], "name": "Timur", "project": "Pixel", "state": "working", "tool": "Bash",
                          "detail": "Komut çalıştırıyor", "toolCount": 12, "startedAt": now - 600000, "stateSince": now,
                          "updatedAt": now, "owner": "Timur", "ownerId": "o_Xk3abc", "source": "cursor",
                          "isSubagent": False, "parentId": None, "projectPath": None, "model": None})
        self.assertEqual(st.json()["pollMs"], 10000)

    def test_batch_subagent_offline(self):
        r = self.status(self.rid, self.key, {"agents": [
            {"session": "s1", "owner": "Ayşe", "state": "thinking"},
            {"session": "s1", "sub": "x", "owner": "Ayşe", "state": "working", "tool": "Grep", "agentType": "Explore"}]})
        main_id, sub_id = r.json()["ids"]
        agents = {a["id"]: a for a in self.state(self.rid).json()["agents"]}
        self.assertEqual(agents[sub_id]["parentId"], main_id)
        self.assertTrue(agents[sub_id]["isSubagent"])
        self.assertEqual(agents[sub_id]["name"], "Explore")
        self.clock.t += 3
        r = self.status(self.rid, self.key, {"session": "s1", "sub": "x", "owner": "Ayşe", "state": "offline"})
        self.assertEqual(r.json()["ids"], [sub_id])
        self.assertEqual([a["id"] for a in self.state(self.rid).json()["agents"]], [main_id])
        self.assertNotIn(sub_id, self.app.storage.read_room(self.rid))

    def test_state_since_without_ages(self):
        b = {"session": "s", "owner": "T", "state": "working", "tool": "Read"}
        self.status(self.rid, self.key, b)
        t0 = int(self.clock.t * 1000)
        self.clock.t += 10
        self.status(self.rid, self.key, dict(b, tool="Edit"))
        a = self.state(self.rid).json()["agents"][0]
        self.assertEqual((a["stateSince"], a["startedAt"], a["tool"]), (t0, t0, "Edit"))
        self.clock.t += 10
        self.status(self.rid, self.key, dict(b, state="idle"))
        a = self.state(self.rid).json()["agents"][0]
        self.assertEqual((a["stateSince"], a["startedAt"]), (t0 + 20000, t0))

    def test_read_time_transitions(self):
        self.status(self.rid, self.key, {"session": "s", "owner": "T", "state": "working", "tool": "Bash"})
        self.status(self.rid, self.key, {"session": "i", "owner": "T", "state": "idle"})
        self.clock.t += 181
        agents = {a["state"] for a in self.state(self.rid).json()["agents"]}
        self.assertEqual(agents, {"working", "sleeping"})
        self.clock.t += 1700
        r = self.state(self.rid)
        self.assertEqual({a["state"] for a in r.json()["agents"]}, {"sleeping"})
        self.assertEqual(r.header("Cache-Control"), "public, max-age=0, s-maxage=15")
        self.assertEqual(r.json()["pollMs"], 30000)
        self.clock.t += 900
        self.assertEqual(self.state(self.rid).json()["agents"], [])
        self.assertEqual(len(self.app.storage.read_room(self.rid)), 3)    # still stored until PURGE_AFTER
        self.clock.t += S.PURGE_AFTER
        self.state(self.rid)
        self.assertEqual(list(self.app.storage.read_room(self.rid)), ["_meta"])   # lazily purged

    def test_final(self):
        self.status(self.rid, self.key, {"session": "w", "owner": "T", "state": "sleeping", "final": True})
        self.clock.t += 300
        self.assertEqual(len(self.state(self.rid).json()["agents"]), 1)
        self.clock.t += 1
        self.assertEqual(self.state(self.rid).json()["agents"], [])

    def test_validation_errors(self):
        for body in ({"session": "s", "state": "idle"}, {"session": "s/x", "owner": "T", "state": "idle"},
                     {"session": "s", "owner": "T", "state": "idle", "project": "a/b"},
                     {"agents": [{"session": f"s{i}", "owner": "T", "state": "idle"} for i in range(11)]}, [1]):
            self.assert_error(self.status(self.rid, self.key, body), 400, "bad_request")
        self.assert_error(self.status(self.rid, self.key, b"\xff"), 400, "bad_request")

    def test_body_cap(self):
        big = {"session": "s", "owner": "T", "state": "idle", "pad": "x" * 4100}
        self.assert_error(self.status(self.rid, self.key, big), 413, "payload_too_large")
        ok = {"session": "s", "owner": "T", "state": "idle", "pad": "x" * 3900}
        self.assertEqual(self.status(self.rid, self.key, ok).status, 200)
        # declared Content-Length is checked before anything is read from the stream
        stream = io.BytesIO(b"never read")
        req = Request("POST", f"/api/status?room={self.rid}", {"Authorization": f"Bearer {self.key}",
                                                               "Content-Length": "5000"}, rfile=stream)
        r = self.app.dispatch(req)
        self.assert_error(r, 413, "payload_too_large")
        self.assertEqual(stream.tell(), 0)
        self.assertFalse(req.body_consumed)
        # unauthenticated: 401 before the body is touched
        stream = io.BytesIO(b"x" * 10)
        r = self.app.dispatch(Request("POST", f"/api/status?room={self.rid}", {"Content-Length": "10"}, rfile=stream))
        self.assertEqual((r.status, stream.tell()), (401, 0))
        r = self.app.dispatch(Request("POST", f"/api/status?room={self.rid}",
                                      {"Authorization": f"Bearer {self.key}", "Content-Length": "x"}, rfile=io.BytesIO()))
        self.assert_error(r, 400, "bad_request")

    def test_room_full(self):
        for i in range(S.MAX_AGENTS_PER_ROOM // 8):
            batch = {"agents": [{"session": f"s{i}-{j}", "owner": "T", "state": "idle"} for j in range(8)]}
            self.assertEqual(self.status(self.rid, self.key, batch, ip=f"9.0.0.{i}").status, 200)
        self.clock.t += 60
        r = self.status(self.rid, self.key, {"session": "one-more", "owner": "T", "state": "idle"})
        self.assert_error(r, 429, "room_full")
        self.assertEqual(len(self.state(self.rid).json()["agents"]), S.MAX_AGENTS_PER_ROOM)
        # existing agents can still update, and offline frees a slot
        self.assertEqual(self.status(self.rid, self.key, {"session": "s0-0", "owner": "T", "state": "working"}).status, 200)
        self.status(self.rid, self.key, {"session": "s0-0", "owner": "T", "state": "offline"})
        self.assertEqual(self.status(self.rid, self.key, {"session": "one-more", "owner": "T", "state": "idle"}).status, 200)

    def test_rate_limits(self):
        b = {"session": "s", "owner": "T", "state": "idle"}
        codes = [self.status(self.rid, self.key, b, ip=f"7.0.0.{i % 5}").status for i in range(31)]
        self.assertEqual(codes[:30], [200] * 30)                   # room burst 30
        r = self.status(self.rid, self.key, b, ip="7.0.0.9")
        self.assert_error(r, 429, "rate_limited")
        self.assertEqual(r.header("Retry-After"), "1")
        self.assertEqual(r.json()["retryAfter"], 1)
        self.clock.t += 1
        self.assertEqual(self.status(self.rid, self.key, b).status, 200)   # 2 tokens/s refill
        # per-IP bucket (4/s, burst 60) across rooms
        rooms = [self.room(name=f"r{i}", ip=f"8.0.0.{i}") for i in range(3)]
        n_ok = 0
        for i in range(70):
            rid, key = rooms[i % 3]
            n_ok += self.status(rid, key, b, ip="6.6.6.6").status == 200
        self.assertEqual(n_ok, 60)

    def test_micro_cache_and_invalidation(self):
        st = self.app.storage
        self.state(self.rid)
        before = st.commands
        self.state(self.rid)
        self.state(self.rid)
        self.assertEqual(st.commands, before)                      # served from the 2 s micro-cache
        self.status(self.rid, self.key, {"session": "s", "owner": "T", "state": "idle"})
        self.assertEqual(len(self.state(self.rid).json()["agents"]), 1)   # write invalidated it
        self.clock.t += 2
        before = st.commands
        self.state(self.rid)
        self.assertEqual(st.commands, before + 1)                 # one HGETALL per miss

    def test_expire_once_per_hour(self):
        st = self.app.storage
        b = {"session": "s", "owner": "T", "state": "idle"}
        self.status(self.rid, self.key, b)                         # room just created → no EXPIRE
        c0 = st.commands
        self.status(self.rid, self.key, b)
        self.assertEqual(st.commands - c0, 1)                      # 1 HSET
        self.clock.t += 3601
        c0 = st.commands
        self.status(self.rid, self.key, b)
        self.assertEqual(st.commands - c0, 2)                      # HSET + EXPIRE
        self.assertAlmostEqual(st.ttl(self.rid), S.ROOM_TTL)


class FailureTests(Base):
    def test_unconfigured_on_vercel(self):
        app = App(env={"VERCEL": "1", "PIXEL_SECRET": SECRET}, clock=FakeClock(), mapper=None)
        self.assertEqual(self.call("GET", "/api/health", app=app).json()["storage"], "unconfigured")
        self.assertFalse(self.call("GET", "/api/health", app=app).json()["storageOk"])
        rid = ids.new_room_id()
        self.assert_error(self.call("GET", f"/api/state?room={rid}", app=app), 503, "storage_not_configured")
        self.assert_error(self.call("POST", "/api/rooms", {}, app=app), 503, "storage_not_configured")
        key = ids.write_key(SECRET.encode(), rid)
        self.assert_error(self.status(rid, key, {"session": "s", "owner": "T", "state": "idle"}, app=app), 503,
                          "storage_not_configured")

    def test_missing_secret_on_vercel(self):
        app = App(storage=MemoryStorage(), env={"VERCEL": "1", "PIXEL_SECRET": "short"}, mapper=None)
        self.assertIsNone(app.settings.secret)
        self.assert_error(self.call("POST", "/api/rooms", {}, app=app), 503, "storage_not_configured")
        self.assert_error(self.status(ids.new_room_id(), "k" * 32, {}, app=app), 503, "storage_not_configured")
        dev = App(storage=MemoryStorage(), env={}, mapper=None)
        self.assertTrue(dev.settings.secret_generated)
        self.assertGreaterEqual(len(dev.settings.secret), 48)

    def test_storage_errors(self):
        class Broken(MemoryStorage):
            def read_room(self, *a):
                raise StorageError("down")
            put_agents = create_room = read_room
        app, _ = mk_app(storage=Broken())
        rid = ids.new_room_id()
        self.assert_error(self.call("GET", f"/api/state?room={rid}", app=app), 503, "storage_unavailable")
        self.assert_error(self.call("POST", "/api/rooms", {}, app=app), 503, "storage_unavailable")
        key = ids.write_key(SECRET.encode(), rid)
        self.assert_error(self.status(rid, key, {"session": "s", "owner": "T", "state": "idle"}, app=app), 503,
                          "storage_unavailable")

    def test_internal_error_hides_details(self):
        class Weird(MemoryStorage):
            def read_room(self, room):
                raise KeyError("secret-detail")
        app, _ = mk_app(storage=Weird())
        r = self.call("GET", f"/api/state?room={ids.new_room_id()}", app=app)
        self.assert_error(r, 500, "internal_error")
        self.assertNotIn(b"secret-detail", r.body)

    def test_no_secrets_in_logs(self):
        rid, key = self.room()
        logger = logging.getLogger("pixel_cloud")
        with self.assertLogs(logger, level="DEBUG") as cm:
            logger.warning("marker")
            self.status(rid, key, {"session": "s", "owner": "T", "state": "idle", "label": "PROMPT-CANARY"})
            self.status(rid, "wrong" * 6 + "xx", {"session": "s"})

            class Broken(MemoryStorage):
                def put_agents(self, *a):
                    raise StorageError("down")
            app, _ = mk_app(storage=Broken())
            self.status(rid, key, {"session": "s", "owner": "T", "state": "idle"}, app=app)
        text = "\n".join(cm.output)
        for secret in (key, SECRET, "PROMPT-CANARY", "Bearer"):
            self.assertNotIn(secret, text)


class CcHookTests(Base):
    def setUp(self):
        super().setUp()
        self.seen = []

        def mapper(event, cfg, last):
            self.seen.append((event.get("hook_event_name"), cfg, last))
            if event.get("hook_event_name") == "PreToolUse":
                return [{"session": "h" + cfg["salt"][:8], "state": "working", "tool": event.get("tool_name"),
                         "project": "Pixel", "name": "ignored", "owner": "spoofed"}]
            if event.get("hook_event_name") == "Boom":
                raise RuntimeError("mapper bug")
            return []
        self.app, self.clock = mk_app(mapper=mapper)
        self.rid, self.key = self.room()

    def hook(self, event, owner="Ay%C5%9Fe", oid=None, key=None, raw=None):
        h = {"Authorization": f"Bearer {key or self.key}"}
        if owner is not None:
            h["X-Pixel-Owner"] = owner
        if oid:
            h["X-Pixel-Owner-Id"] = oid
        return self.call("POST", f"/api/cc_hook?room={self.rid}", raw if raw is not None else event, h)

    def test_maps_and_stores_only_allow_listed(self):
        ev = {"hook_event_name": "PreToolUse", "session_id": "raw-session", "tool_name": "Bash",
              "tool_input": {"command": "CANARY-7f3a"}, "cwd": "/home/x/CANARY-7f3a"}
        r = self.hook(ev)
        self.assertEqual((r.status, r.json()), (200, {}))
        stored = json.dumps(self.app.storage.read_room(self.rid), ensure_ascii=False)
        self.assertNotIn("CANARY-7f3a", stored)
        self.assertNotIn("raw-session", stored)
        a = self.state(self.rid).json()["agents"][0]
        self.assertEqual((a["owner"], a["source"], a["tool"], a["name"], a["nameSource"]),
                         ("Ayşe", "claude-code", "Bash", "Ayşe", "auto"))
        self.assertIsNone(self.seen[0][2])                        # stateless: last_state None
        self.assertEqual(self.seen[0][1]["ownerId"], ids.default_owner_id("Ayşe"))

    def test_errors(self):
        self.assert_error(self.hook({}, key="A" * 32), 401, "unauthorized")
        self.assert_error(self.hook({"hook_event_name": "Stop"}, owner=None), 400, "bad_request")
        self.assert_error(self.hook({}, oid="bad id"), 400, "bad_request")
        self.assert_error(self.hook(None, raw=b"[1]"), 400, "bad_request")
        self.assertEqual(self.hook({"hook_event_name": "Boom"}).status, 200)   # mapper bugs never surface
        app, _ = mk_app(mapper=None)
        rid, key = self.room(app=app)
        r = self.call("POST", f"/api/cc_hook?room={rid}", {}, {"Authorization": f"Bearer {key}", "X-Pixel-Owner": "x"},
                      app=app)
        self.assert_error(r, 503, "hook_mapper_unavailable")

    def test_oversized_is_drained(self):
        body = b"x" * (S.MAX_CC_HOOK_BODY + 1)
        stream = io.BytesIO(body)
        req = Request("POST", f"/api/cc_hook?room={self.rid}", {"Authorization": f"Bearer {self.key}",
                                                                "Content-Length": str(len(body)),
                                                                "X-Pixel-Owner": "x"}, rfile=stream)
        r = self.app.dispatch(req)
        self.assertEqual((r.status, r.body), (204, b""))
        self.assertEqual(stream.tell(), len(body))
        self.assertTrue(req.body_consumed)
        self.assertEqual(self.seen, [])


class UpstashEndToEndTests(Base):
    def test_full_flow_against_fake_upstash(self):
        with FakeUpstash() as fake:
            storage = UpstashStorage(fake.url, fake.token, timeout=2)
            app, clock = mk_app(storage=storage)
            rid, key = self.room(app=app)
            fake.calls.clear()
            r = self.status(rid, key, {"agents": [{"session": "a", "owner": "T", "state": "working", "tool": "Edit"},
                                                  {"session": "b", "owner": "U", "state": "idle"}]}, app=app)
            self.assertEqual(r.status, 200)
            self.assertEqual([c[0] for c in fake.calls], ["HSET", "HSET", "HKEYS"])   # pipeline + new-field check
            fake.calls.clear()
            self.status(rid, key, {"session": "a", "owner": "T", "state": "thinking"}, app=app)
            self.assertEqual([c[0] for c in fake.calls], ["HSET"])                     # steady state: 1 command
            fake.calls.clear()
            st = self.state(rid, app=app).json()
            self.assertEqual([c[0] for c in fake.calls], ["HGETALL"])
            self.assertEqual({a["state"] for a in st["agents"]}, {"thinking", "idle"})
            self.assertEqual(st["room"]["name"], "Oda")
            r = self.call("POST", f"/api/rename?room={rid}", {"scope": "agent", "agentId": st["agents"][0]["id"],
                                                              "name": "Dilek"}, {"Authorization": f"Bearer {key}"}, app=app)
            self.assertEqual(r.status, 200)
            clock.t += 3
            names = {a["name"] for a in self.state(rid, app=app).json()["agents"]}
            self.assertIn("Dilek", names)
            fake.fail_mode = "http500"
            clock.t += 3
            self.assert_error(self.call("GET", f"/api/state?room={rid}", app=app), 503, "storage_unavailable")
            self.assertTrue(self.call("GET", "/api/health", app=app).json()["storageOk"])
            storage.close()


class AdapterTests(Base):
    """The BaseHTTPRequestHandler adapter used by Vercel and dev_server.py, over real sockets."""

    def setUp(self):
        super().setUp()
        app = self.app

        class H(ApiHandler):
            def dispatch_api(self, req):
                return app.dispatch(req)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_make_handler(self):
        h = make_handler()
        self.assertTrue(issubclass(h, ApiHandler))

    def test_roundtrip_keepalive_head(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/rooms", body=b'{"name":"HTTP"}', headers={"Content-Type": "application/json"})
        r = c.getresponse()
        d = json.loads(r.read())
        self.assertEqual(r.status, 201)
        self.assertTrue(d["viewUrl"].startswith(f"http://127.0.0.1:{self.port}/?room="))
        c.request("POST", f"/api/status?room={d['roomId']}", body=b'{"session":"s","owner":"T","state":"idle"}',
                  headers={"Authorization": f"Bearer {d['writeKey']}", "Content-Type": "application/json"})
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        r.read()
        c.request("HEAD", f"/api/state?room={d['roomId']}")
        r = c.getresponse()
        self.assertEqual((r.status, r.read()), (200, b""))
        self.assertEqual(r.getheader("Cache-Control"), "public, max-age=0, s-maxage=15")
        self.assertGreater(int(r.getheader("Content-Length")), 0)
        c.close()

    def test_declared_oversize_is_rejected_without_reading(self):
        rid, key = self.room()
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall((f"POST /api/status?room={rid} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {key}\r\n"
                   "Content-Type: application/json\r\nContent-Length: 999999\r\n\r\n").encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        s.close()
        self.assertTrue(data.startswith(b"HTTP/1.1 413"), data[:40])
        self.assertIn(b"Connection: close", data)
        self.assertIn(b"payload_too_large", data)

    def test_chunked_body(self):
        rid, key = self.room()
        body = b'{"session":"c","owner":"T","state":"thinking"}'
        chunks = b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body)
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall((f"POST /api/status?room={rid} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {key}\r\n"
                   "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n").encode() + chunks)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        s.close()
        self.assertTrue(data.startswith(b"HTTP/1.1 200"), data[:60])
        big = b"x" * 5000
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall((f"POST /api/status?room={rid} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {key}\r\n"
                   "Transfer-Encoding: chunked\r\n\r\n").encode() + b"%x\r\n%s\r\n0\r\n\r\n" % (len(big), big))
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        s.close()
        self.assertTrue(data.startswith(b"HTTP/1.1 413"), data[:60])

    def test_x_forwarded_for(self):
        for i in range(5):
            self.req("POST", "/api/rooms", b"{}", {"X-Forwarded-For": "5.5.5.5, 10.0.0.1"})
        r, _ = self.req("POST", "/api/rooms", b"{}", {"X-Forwarded-For": "5.5.5.5"})
        self.assertEqual(r.status, 429)
        r, _ = self.req("POST", "/api/rooms", b"{}", {"X-Forwarded-For": "5.5.5.6"})
        self.assertEqual(r.status, 201)


if __name__ == "__main__":
    unittest.main()
