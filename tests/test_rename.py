"""Renaming agents: cloud POST /api/rename (§15.3) and local POST /api/agents/rename (§15.4)."""

import http.client
import json
import logging
import os
import shutil
import socket
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_cloud import ids
from pixel_cloud import settings as S
from pixel_cloud.app import App, Request
from pixel_cloud.storage import MemoryStorage
from pixel_office import config
from pixel_office.httpd import make_server
from pixel_office.models import AgentInfo, State, hash_name
from pixel_office.names_store import MAX_AGENT_ENTRIES, Namer, NamesStore
from pixel_office.reported import ReportedAgents
from pixel_office.store import AgentStore

logging.getLogger("pixel_cloud").setLevel(logging.CRITICAL)


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# -- cloud ------------------------------------------------------------------------------------------

class CloudRenameTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.app = App(storage=MemoryStorage(self.clock), env={"PIXEL_SECRET": "z" * 40}, clock=self.clock, mapper=None)
        r = self.call("POST", "/api/rooms", {"name": "Oda"})
        self.rid, self.key = r.json()["roomId"], r.json()["writeKey"]
        r = self.status({"agents": [{"session": "s1", "owner": "Timur", "ownerId": "o_timur", "state": "working"},
                                    {"session": "s1", "sub": "x", "owner": "Timur", "ownerId": "o_timur",
                                     "state": "working", "agentType": "Explore"},
                                    {"session": "s2", "owner": "Ayşe", "state": "idle", "name": "Kâtip"}]})
        self.main, self.sub, self.other = r.json()["ids"]

    def call(self, method, path, body=None, headers=None, ip="10.0.0.1"):
        return self.app.dispatch(Request(method, path, headers or {}, body=json.dumps(body).encode()
                                         if body is not None else None, client_ip=ip))

    def status(self, body):
        return self.call("POST", f"/api/status?room={self.rid}", body, {"Authorization": f"Bearer {self.key}"})

    def rename(self, body, key=None, ip="10.0.0.1"):
        return self.call("POST", f"/api/rename?room={self.rid}", body, {"Authorization": f"Bearer {key or self.key}"}, ip)

    def agents(self):
        self.clock.t += S.MICRO_CACHE_S
        return {a["id"]: a for a in self.call("GET", f"/api/state?room={self.rid}").json()["agents"]}

    def test_agent_scope_and_reset(self):
        r = self.rename({"scope": "agent", "agentId": self.main, "name": "  Dilek​ "})
        self.assertEqual(r.status, 200)
        d = r.json()
        self.assertEqual((d["ok"], d["name"], d["scope"]), (True, "Dilek", "agent"))
        self.assertEqual(d["serverTime"], int(self.clock.t * 1000))
        self.assertEqual(r.header("Cache-Control"), "no-store")
        a = self.agents()[self.main]
        self.assertEqual((a["name"], a["nameSource"], a["defaultName"]), ("Dilek", "custom", "Timur"))
        r = self.rename({"scope": "agent", "agentId": self.main, "name": None})
        self.assertEqual(r.json()["name"], None)
        a = self.agents()[self.main]
        self.assertEqual((a["name"], a["nameSource"]), ("Timur", "auto"))
        self.assertNotIn("n:" + self.main, self.app.storage.read_room(self.rid))

    def test_owner_scope_precedence(self):
        self.rename({"scope": "owner", "ownerId": "o_timur", "name": "Robo-Timur"})
        ags = self.agents()
        self.assertEqual((ags[self.main]["name"], ags[self.main]["nameSource"]), ("Robo-Timur", "owner"))
        self.assertEqual(ags[self.sub]["name"], "Explore")                     # owner default: main agents only
        # a new session of the same owner inherits the owner default
        new = self.status({"session": "s9", "owner": "Timur", "ownerId": "o_timur", "state": "idle"}).json()["ids"][0]
        self.assertEqual(self.agents()[new]["name"], "Robo-Timur")
        # agent scope beats owner scope; reported name is below both
        self.rename({"scope": "agent", "agentId": self.main, "name": "Dilek"})
        a = self.agents()[self.main]
        self.assertEqual((a["name"], a["nameSource"], a["defaultName"]), ("Dilek", "custom", "Robo-Timur"))
        oid_other = self.agents()[self.other]["ownerId"]
        self.rename({"scope": "owner", "ownerId": oid_other, "name": "Ayşe'nin botu"})
        a = self.agents()[self.other]
        self.assertEqual((a["name"], a["nameSource"], a["defaultName"]), ("Ayşe'nin botu", "owner", "Kâtip"))

    def test_rename_survives_state_updates(self):
        self.rename({"scope": "agent", "agentId": self.main, "name": "Dilek"})
        for st in ("thinking", "working", "idle", "waiting_for_user"):
            self.clock.t += 5
            self.status({"session": "s1", "owner": "Timur", "ownerId": "o_timur", "state": st, "name": "Ignored"})
            a = self.agents()[self.main]
            self.assertEqual((a["name"], a["state"]), ("Dilek", st))
        self.status({"session": "s1", "owner": "Timur", "ownerId": "o_timur", "state": "offline"})
        self.assertNotIn(self.main, self.agents())
        self.assertIn("n:" + self.main, self.app.storage.read_room(self.rid))   # harmless, ages out
        self.status({"session": "s1", "owner": "Timur", "ownerId": "o_timur", "state": "idle"})
        self.assertEqual(self.agents()[self.main]["name"], "Dilek")

    def test_rename_unknown_agent_is_allowed(self):
        r = self.rename({"scope": "agent", "agentId": "a" + "0" * 16, "name": "Ghost"})
        self.assertEqual(r.status, 200)

    def test_errors(self):
        r = self.rename({"scope": "agent", "agentId": self.main, "name": "x"}, key="B" * 32)
        self.assertEqual((r.status, r.json()["error"]), (401, "unauthorized"))
        r = self.call("POST", "/api/rename?room=nope", {"scope": "agent"}, {"Authorization": f"Bearer {self.key}"})
        self.assertEqual(r.status, 404)
        for body in ({"scope": "agent", "agentId": "bad", "name": "x"}, {"scope": "owner", "ownerId": "a b", "name": "x"},
                     {"scope": "room", "name": "x"}, {"scope": "agent", "agentId": self.main, "name": "‮​"},
                     {"scope": "agent", "agentId": self.main, "name": 5}):
            r = self.rename(body)
            self.assertEqual((r.status, r.json()["error"]), (400, "bad_request"), body)
        r = self.rename({"scope": "agent", "agentId": self.main, "name": "x" * 2000})
        self.assertEqual((r.status, r.json()["error"]), (413, "payload_too_large"))
        r = self.rename({"scope": "agent", "agentId": self.main, "name": "x" * 50})
        self.assertEqual(r.json()["name"], "x" * 24)

    def test_rate_limit_per_room(self):
        codes = [self.rename({"scope": "agent", "agentId": self.main, "name": f"N{i}"}, ip=f"1.0.0.{i}").status
                 for i in range(11)]
        self.assertEqual(codes, [200] * 10 + [429])
        self.clock.t += 6
        self.assertEqual(self.rename({"scope": "agent", "agentId": self.main, "name": "ok"}).status, 200)

    def test_cost_one_command_and_no_extra_read(self):
        st = self.app.storage
        self.rename({"scope": "agent", "agentId": self.main, "name": "A"})
        c0 = st.commands
        self.clock.t += 7
        self.rename({"scope": "agent", "agentId": self.main, "name": "B"})     # existing field → 1 HSET
        self.assertEqual(st.commands - c0, 1)
        self.clock.t += 7
        c0 = st.commands
        self.rename({"scope": "agent", "agentId": self.main, "name": None})    # reset → 1 HDEL
        self.assertEqual(st.commands - c0, 1)
        c0 = st.commands
        self.agents()
        self.assertEqual(st.commands - c0, 1)                                   # read stays 1 HGETALL

    def test_name_field_limit(self):
        rename_ok = 0
        for i in range(S.MAX_NAME_FIELDS):
            self.clock.t += 6.1
            r = self.rename({"scope": "agent", "agentId": "a%016x" % i, "name": f"N{i}"}, ip=f"2.0.{i}.1")
            rename_ok += r.status == 200
        self.assertEqual(rename_ok, S.MAX_NAME_FIELDS)
        self.clock.t += 6.1
        r = self.rename({"scope": "owner", "ownerId": "o_new1", "name": "Full"})
        self.assertEqual((r.status, r.json()["error"]), (429, "room_full"))
        self.assertNotIn("no:o_new1", self.app.storage.read_room(self.rid))
        # updating an existing name still works
        self.clock.t += 6.1
        self.assertEqual(self.rename({"scope": "agent", "agentId": "a%016x" % 3, "name": "Again"}).status, 200)
        # after 6 h the n: fields of absent agents are stale and make room (keep agents alive meanwhile)
        self.clock.t += S.PURGE_AFTER + 1
        r = self.rename({"scope": "owner", "ownerId": "o_new1", "name": "Fits"})
        self.assertEqual(r.status, 200)
        fields = self.app.storage.read_room(self.rid)
        self.assertIn("no:o_new1", fields)
        self.assertNotIn("n:" + "a%016x" % 3, fields)    # agent absent and name older than 6 h → purged
        self.assertLessEqual(sum(1 for k in fields if k.startswith(("n:", "no:"))), S.MAX_NAME_FIELDS)

    def test_owner_name_purge_h19(self):
        # Fill room names with owner renames for absent owners
        for i in range(S.MAX_NAME_FIELDS):
            self.clock.t += 6.1
            r = self.rename({"scope": "owner", "ownerId": f"o_{i:04d}", "name": f"Owner{i}"}, ip=f"3.0.{i // 256}.{i % 256}")
            self.assertEqual(r.status, 200)

        # 97th rename exceeds limit before PURGE_AFTER
        self.clock.t += 6.1
        r = self.rename({"scope": "owner", "ownerId": "o_excess", "name": "Ex"})
        self.assertEqual((r.status, r.json()["error"]), (429, "room_full"))

        # After PURGE_AFTER, absent owner names become stale and are purged, making room
        self.clock.t += S.PURGE_AFTER + 1
        r = self.rename({"scope": "owner", "ownerId": "o_excess", "name": "Fits"})
        self.assertEqual(r.status, 200)
        fields = self.app.storage.read_room(self.rid)
        self.assertIn("no:o_excess", fields)
        self.assertLessEqual(sum(1 for k in fields if k.startswith(("n:", "no:"))), S.MAX_NAME_FIELDS)



# -- local ------------------------------------------------------------------------------------------

def tagent(aid, path="/home/u/proj", sub=False, state=State.WORKING, name=None):
    return AgentInfo(id=aid, name=name or hash_name(aid), project=os.path.basename(path), project_path=path,
                     state=state, is_subagent=sub, parent_id="p" if sub else None, started_at=1)


class LocalRenameBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.names_path = d / "data" / "pixel-office" / "names.json"
        self.legacy = d / "legacy-names.json"
        self.patch = mock.patch.object(config, "NAMES_FILE", self.legacy)
        self.patch.start()
        self.names = NamesStore(self.names_path)
        self.store = AgentStore("live", namer=Namer(self.names))
        self.reported = ReportedAgents(self.store)
        web = d / "web"
        web.mkdir()
        (web / "index.html").write_text("x")
        self.srv = make_server("127.0.0.1", 0, self.store, web, reported=self.reported, names=self.names)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.srv.shutting_down.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.patch.stop()
        self.tmp.cleanup()

    def post(self, path, body, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", path, body=json.dumps(body).encode() if not isinstance(body, bytes) else body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, json.loads(data) if data else None

    def rename(self, body, **kw):
        return self.post("/api/agents/rename", body, **kw)


class LocalRenameTests(LocalRenameBase):
    def test_agent_scope_persist_reset(self):
        self.store.upsert(tagent("sess-1"))
        self.assertFalse(self.names_path.exists())
        r, d = self.rename({"id": "sess-1", "name": " Dilek ", "scope": "agent"})
        self.assertEqual(r.status, 200)
        self.assertTrue(d["ok"])
        self.assertEqual((d["agent"]["name"], d["agent"]["nameSource"], d["agent"]["defaultName"]),
                         ("Dilek", "custom", hash_name("sess-1")))
        self.assertTrue(self.names_path.exists())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.names_path.stat().st_mode), 0o600)
        data = json.loads(self.names_path.read_text())
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["agents"]["sess-1"]["name"], "Dilek")
        self.assertEqual(data["projects"], {})
        self.assertEqual(list(self.names_path.parent.iterdir()), [self.names_path])   # no temp files left
        # producer keeps upserting the auto name: the rename survives
        for st in (State.THINKING, State.IDLE, State.SLEEPING):
            self.store.upsert(tagent("sess-1", state=st))
            self.assertEqual(self.store.get("sess-1").name, "Dilek")
        # persisted: a fresh store/namer on the same file sees it
        s2 = AgentStore("live", namer=Namer(NamesStore(self.names_path)))
        s2.upsert(tagent("sess-1"))
        self.assertEqual(s2.snapshot()["agents"][0]["name"], "Dilek")
        r, d = self.rename({"id": "sess-1", "name": None})
        self.assertEqual((d["agent"]["name"], d["agent"]["nameSource"]), (hash_name("sess-1"), "auto"))

    def test_project_scope_and_precedence(self):
        self.store.upsert(tagent("a1", "/w/pixel"))
        self.store.upsert(tagent("a2", "/w/pixel"))
        self.store.upsert(tagent("sub", "/w/pixel", sub=True))
        self.store.upsert(tagent("b1", "/w/other"))
        r, d = self.rename({"id": "a1", "name": "Pixelci", "scope": "project"})
        self.assertEqual(r.status, 200)
        names = {a["id"]: (a["name"], a["nameSource"]) for a in self.store.snapshot()["agents"]}
        self.assertEqual(names["a1"], ("Pixelci", "project"))
        self.assertEqual(names["a2"], ("Pixelci", "project"))
        self.assertEqual(names["sub"][1], "auto")                 # project scope: main agents only
        self.assertEqual(names["b1"][1], "auto")
        self.store.upsert(tagent("a3", "/w/pixel"))                # a new session in that project
        self.assertEqual(self.store.get("a3").name, "Pixelci")
        self.legacy.write_text(json.dumps({"a2": "Legacy"}))
        self.store.refresh_names()
        self.assertEqual(self.store.get("a2").name, "Pixelci")     # project (2) beats file (3)
        self.rename({"id": "a2", "name": "Özel"})
        a2 = self.store.snapshot()["agents"]
        a2 = next(a for a in a2 if a["id"] == "a2")
        self.assertEqual((a2["name"], a2["nameSource"], a2["defaultName"]), ("Özel", "custom", "Pixelci"))
        self.rename({"id": "a1", "name": None, "scope": "project"})
        a2 = next(a for a in self.store.snapshot()["agents"] if a["id"] == "a2")
        self.assertEqual(a2["defaultName"], "Legacy")
        a1 = next(a for a in self.store.snapshot()["agents"] if a["id"] == "a1")
        self.assertEqual((a1["name"], a1["nameSource"]), (hash_name("a1"), "auto"))
        self.rename({"id": "a2", "name": None})
        a2 = next(a for a in self.store.snapshot()["agents"] if a["id"] == "a2")
        self.assertEqual((a2["name"], a2["nameSource"]), ("Legacy", "file"))

    def test_reported_agent_precedence(self):
        r, d = self.post("/api/status", {"session": "bot", "owner": "Timur", "state": "working", "name": "Kâtip"})
        aid = d["ids"][0]
        a = self.store.get(aid)
        self.assertEqual((a.name, a.name_source), ("Kâtip", "reported"))
        r, d = self.rename({"id": aid, "name": "Dilek"})
        self.assertEqual((d["agent"]["name"], d["agent"]["defaultName"]), ("Dilek", "Kâtip"))
        self.post("/api/status", {"session": "bot", "owner": "Timur", "state": "idle", "name": "Yeni"})
        self.assertEqual(self.store.get(aid).name, "Dilek")         # rename survives status updates
        r, d = self.rename({"id": aid, "name": "x", "scope": "project"})
        self.assertEqual((r.status, d["error"]), (400, "bad_request"))   # reported agents have no projectPath

    def test_sse_agent_updated(self):
        self.store.upsert(tagent("sess-1"))
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        f = s.makefile("rb")
        buf = b""
        while b"event: snapshot" not in buf:
            buf += f.readline()
        f.readline(); f.readline()
        self.rename({"id": "sess-1", "name": "Dilek"})
        ev = {}
        while "data" not in ev:
            line = f.readline().decode().rstrip("\n")
            if line and not line.startswith(":"):
                k, _, v = line.partition(": ")
                ev[k] = v
        self.assertEqual(ev["event"], "agent_updated")
        data = json.loads(ev["data"])
        self.assertEqual((data["id"], data["name"], data["nameSource"]), ("sess-1", "Dilek", "custom"))
        f.close()
        s.close()

    def test_errors_and_guard(self):
        self.store.upsert(tagent("sess-1"))
        r, d = self.rename({"id": "nope", "name": "x"})
        self.assertEqual((r.status, d["error"]), (404, "not_found"))
        r, d = self.rename({"id": "sess-1", "name": "​"})
        self.assertEqual((r.status, d["error"]), (400, "bad_request"))
        r, d = self.rename({"id": "sess-1", "name": "x", "scope": "owner"})
        self.assertEqual(r.status, 400)
        r, d = self.rename({"id": "sess-1", "name": "x"}, headers={"Content-Type": "text/plain"})
        self.assertEqual((r.status, d["error"]), (400, "bad_request"))
        r, d = self.rename({"id": "sess-1", "name": "x"}, headers={"Origin": "http://evil.example"})
        self.assertEqual((r.status, d["error"]), (403, "forbidden"))
        r, d = self.rename({"id": "sess-1", "name": "ok"}, headers={"Origin": f"http://localhost:{self.port}"})
        self.assertEqual(r.status, 200)
        r, d = self.rename(b'{"id":"sess-1","name":"' + b"x" * 2000 + b'"}')
        self.assertEqual((r.status, d["error"]), (413, "payload_too_large"))
        self.assertEqual(self.store.get("sess-1").name, "ok")
        self.store.upsert(tagent("subby", sub=True))
        r, d = self.rename({"id": "subby", "name": "x", "scope": "project"})
        self.assertEqual(r.status, 400)

    def test_names_store_cap_and_bad_file(self):
        ns = NamesStore(Path(self.tmp.name) / "cap" / "names.json")
        ns.set("agent", "x", None)                                  # reset without file: nothing created
        self.assertFalse(ns.path.exists())
        with mock.patch("pixel_office.names_store.time.time", side_effect=[i for i in range(MAX_AGENT_ENTRIES + 5)]):
            for i in range(MAX_AGENT_ENTRIES + 5):
                ns.set("agent", f"id{i}", f"N{i}")
        data = json.loads(ns.path.read_text())
        self.assertEqual(len(data["agents"]), MAX_AGENT_ENTRIES)
        self.assertNotIn("id0", data["agents"])                    # oldest pruned
        self.assertIn(f"id{MAX_AGENT_ENTRIES + 4}", data["agents"])
        ns.path.write_text("{broken")
        os.utime(ns.path, (1, 1))
        self.assertIsNone(NamesStore(ns.path).agent("id5"))
        ns.path.write_text(json.dumps({"agents": {"a": {"name": "Ti‮mur\x00"}, "b": {"name": 5}}}))
        os.utime(ns.path, (2, 2))
        n2 = NamesStore(ns.path)
        self.assertEqual((n2.agent("a"), n2.agent("b")), ("Timur", None))


class NamesStoreAtomicWriteTests(unittest.TestCase):
    """Phase 10: the local names file is replaced atomically; a failed write keeps the old file intact."""

    def test_failed_replace_keeps_previous_file_and_leaves_no_temp(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        path = d / "names.json"
        st = NamesStore(path)
        st.set("agent", "a1", "Dilek")
        before = path.read_bytes()
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with mock.patch("pixel_office.names_store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                st.set("agent", "a2", "Kâtip")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([p.name for p in d.iterdir()], ["names.json"])      # temp file cleaned up
        self.assertEqual(NamesStore(path).agent("a1"), "Dilek")


if __name__ == "__main__":
    unittest.main()
