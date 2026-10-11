import json
import unittest

import _helpers  # noqa: F401  (sys.path)

from pixel_cloud import derive, ids
from pixel_cloud import settings as S
from pixel_cloud.validate import parse_update

ROOM = "k7q2m4x5z6a7b2c3d4e5f6g7h2"
NOW = 1_791_723_961_000
V1_KEYS = {"id", "name", "project", "projectPath", "title", "state", "tool", "detail", "lastMessage", "lastPrompt",
           "isSubagent", "parentId", "agentType", "model", "colorSeed", "toolCount", "startedAt", "stateSince",
           "updatedAt"}


def rec(st="working", age_s=0.0, sub=False, fin=False, tool="Bash", lbl=None, **kw):
    ts = NOW - int(age_s * 1000)
    r = {"v": 1, "owner": "Timur", "oid": "o_timur1", "proj": "Pixel", "st": st, "tool": tool, "lbl": lbl,
         "sub": sub, "par": None, "atype": None, "src": "claude-code", "col": 0, "ts": ts, "ss": ts - 5000,
         "sa": ts - 60000, "tc": 3}
    if fin:
        r["fin"] = True
    r.update(kw)
    return r


class TransitionTests(unittest.TestCase):
    def d(self, r):
        return derive.derive_record(r, NOW)

    def test_rule1_purge(self):
        self.assertTrue(self.d(rec(age_s=S.PURGE_AFTER + 1)).purge)
        self.assertFalse(self.d(rec(st="sleeping", age_s=S.PURGE_AFTER)).purge)

    def test_rule2_sub_sleeping(self):
        self.assertFalse(self.d(rec(st="sleeping", sub=True, age_s=121)).visible)
        self.assertTrue(self.d(rec(st="sleeping", sub=True, age_s=120)).visible)
        self.assertTrue(self.d(rec(st="working", sub=True, age_s=121)).visible)

    def test_rule2b_finished(self):
        self.assertFalse(self.d(rec(st="sleeping", fin=True, age_s=301)).visible)
        d = self.d(rec(st="sleeping", fin=True, age_s=300))
        self.assertTrue(d.visible)
        self.assertEqual(d.state, "sleeping")

    def test_rule3_sub_stale(self):
        self.assertFalse(self.d(rec(st="working", sub=True, age_s=1801)).visible)
        self.assertTrue(self.d(rec(st="working", sub=True, age_s=1800)).visible)

    def test_rule4_main_remove(self):
        self.assertFalse(self.d(rec(st="sleeping", age_s=2701)).visible)
        self.assertTrue(self.d(rec(st="sleeping", age_s=2700)).visible)
        self.assertFalse(self.d(rec(st="working", age_s=2701)).purge)

    def test_rule5_idle_to_sleep(self):
        d = self.d(rec(st="idle", tool=None, lbl="Durduruldu", age_s=181))
        self.assertEqual((d.state, d.since, d.tool, d.label), ("sleeping", NOW - 181000 + 180000, None, None))
        d = self.d(rec(st="idle", tool=None, age_s=180))
        self.assertEqual(d.state, "idle")

    def test_rule6_active_stale(self):
        for st in ("working", "thinking", "waiting_for_user"):
            d = self.d(rec(st=st, age_s=1801))
            self.assertEqual((d.state, d.tool, d.since), ("sleeping", None, NOW - 1801000 + 1800000), st)
            d = self.d(rec(st=st, age_s=1800))
            self.assertEqual((d.state, d.tool), (st, "Bash"))

    def test_rule7_stored(self):
        r = rec(st="waiting_for_user", tool="Bash", lbl="İzin bekliyor", age_s=10)
        d = self.d(r)
        self.assertEqual((d.state, d.since, d.tool, d.label), ("waiting_for_user", r["ss"], "Bash", "İzin bekliyor"))
        self.assertEqual(self.d(rec(st="garbage")).state, "idle")


class SnapshotTests(unittest.TestCase):
    def fields(self, *recs, extra=None):
        f = {"_meta": json.dumps({"name": "Kod Kulübü", "createdAt": 1, "v": 1})}
        for i, r in enumerate(recs):
            f[r.pop("_id", "a%016x" % i)] = json.dumps(r)
        f.update(extra or {})
        return f

    def test_schema_and_detail(self):
        snap = derive.snapshot(ROOM, self.fields(rec(tool="Read"), rec(tool="mcp", lbl=None, oid="o_other1"),
                                                 rec(tool=None, st="thinking", oid="o_x1234")), NOW)
        p = snap.payload
        self.assertEqual((p["mode"], p["serverTime"], p["room"]), ("cloud", NOW, {"id": ROOM, "name": "Kod Kulübü"}))
        a = p["agents"][0]
        self.assertTrue(V1_KEYS <= set(a))
        self.assertTrue({"owner", "ownerId", "ownerColor", "source", "nameSource", "defaultName"} <= set(a))
        self.assertEqual({x["detail"] for x in p["agents"]}, {"Dosya okuyor", "Çalışıyor", None})
        self.assertEqual(a["colorSeed"], derive.crc32(a["id"]))
        self.assertEqual(derive.detail_for("Bash", "Derleniyor"), "Derleniyor")
        self.assertEqual(derive.detail_for("MCP", None), "Araç kullanıyor (MCP)")

    def test_activity_poll_ttl(self):
        cases = [((rec(st="working"),), "active", S.POLL_ACTIVE_MS, "s-maxage=5"),
                 ((rec(st="waiting_for_user"), rec(st="idle")), "active", S.POLL_ACTIVE_MS, "s-maxage=5"),
                 ((rec(st="idle", tool=None),), "calm", S.POLL_CALM_MS, "s-maxage=15"),
                 ((rec(st="working", age_s=1801),), "calm", S.POLL_CALM_MS, "s-maxage=15"),   # derived sleeping
                 ((), "empty", S.POLL_EMPTY_MS, "s-maxage=5"),
                 ((rec(age_s=99999),), "empty", S.POLL_EMPTY_MS, "s-maxage=5")]
        for recs, act, poll, ttl in cases:
            snap = derive.snapshot(ROOM, self.fields(*[dict(r) for r in recs]), NOW)
            self.assertEqual((snap.activity, snap.payload["pollMs"]), (act, poll))
            self.assertEqual(derive.cache_control(snap.activity), f"public, max-age=0, {ttl}")

    def test_purge_and_corrupt(self):
        old = rec(age_s=S.PURGE_AFTER + 5, _id="a" + "1" * 16)
        f = self.fields(old, extra={"a" + "2" * 16: "{not json", "n:a" + "3" * 16: json.dumps({"name": "X", "ts": NOW - S.PURGE_AFTER * 1000 - 1}),
                                    "n:a" + "4" * 16: json.dumps({"name": "Y", "ts": NOW}), "garbage": "1"})
        snap = derive.snapshot(ROOM, f, NOW)
        self.assertEqual(sorted(snap.purge), sorted(["a" + "1" * 16, "a" + "2" * 16, "n:a" + "3" * 16]))
        self.assertEqual(snap.payload["agents"], [])

    def test_sort_and_orphans(self):
        p_id = "a" + "f" * 16
        sub = rec(sub=True, par="a" + "e" * 16, atype="Explore", sa=NOW - 1000, ss=NOW - 500, _id="a" + "0" * 16)
        main = rec(sa=NOW - 999999, _id=p_id)
        sub2 = rec(sub=True, par=p_id, atype=None, sa=NOW - 5000, ss=NOW - 500, _id="a" + "1" * 16)
        p = derive.snapshot(ROOM, self.fields(sub, main, sub2), NOW).payload
        self.assertEqual([a["id"] for a in p["agents"]], [p_id, "a" + "1" * 16, "a" + "0" * 16])
        by = {a["id"]: a for a in p["agents"]}
        self.assertIsNone(by["a" + "0" * 16]["parentId"])          # parent gone → top level
        self.assertEqual(by["a" + "1" * 16]["parentId"], p_id)
        self.assertEqual(by["a" + "0" * 16]["name"], "Explore")
        self.assertEqual(by["a" + "1" * 16]["name"], "Alt-ajan")
        self.assertEqual(by[p_id]["name"], "Timur")

    def test_owner_colors(self):
        self.assertEqual(derive.assign_owner_colors({"b": 3, "a": 3, "c": 4}),
                         {"a": derive.OWNER_COLORS[3], "b": derive.OWNER_COLORS[4], "c": derive.OWNER_COLORS[5]})
        many = derive.assign_owner_colors({f"o{i:02d}": 0 for i in range(16)})
        self.assertEqual(len(set(many.values())), 16)
        self.assertEqual(derive.assign_owner_colors({"z": 15, "y": 15})["z"], derive.OWNER_COLORS[0])
        # deterministic per snapshot, subagents share their owner's colour
        p = derive.snapshot(ROOM, self.fields(rec(oid="o_aaaa", col=2), rec(oid="o_bbbb", col=2),
                                              rec(oid="o_aaaa", sub=True)), NOW).payload
        cols = {a["ownerId"]: set() for a in p["agents"]}
        for a in p["agents"]:
            cols[a["ownerId"]].add(a["ownerColor"])
        self.assertEqual(cols, {"o_aaaa": {derive.OWNER_COLORS[2]}, "o_bbbb": {derive.OWNER_COLORS[3]}})

    def test_name_precedence(self):
        aid = "a" + "5" * 16
        r = rec(nm="Kâtip", _id=aid)
        def names(extra):
            p = derive.snapshot(ROOM, self.fields(dict(r), extra=extra), NOW).payload
            a = p["agents"][0]
            return a["name"], a["nameSource"], a["defaultName"]
        self.assertEqual(names({}), ("Kâtip", "reported", "Kâtip"))
        self.assertEqual(names({"no:o_timur1": json.dumps({"name": "Robo", "ts": 1})}), ("Robo", "owner", "Kâtip"))
        self.assertEqual(names({"no:o_timur1": json.dumps({"name": "Robo", "ts": 1}),
                                "n:" + aid: json.dumps({"name": "Dilek", "ts": 1})}), ("Dilek", "custom", "Robo"))
        r.pop("nm")
        self.assertEqual(names({}), ("Timur", "auto", "Timur"))
        self.assertEqual(names({"n:" + aid: json.dumps({"name": "Dilek", "ts": 1})}), ("Dilek", "custom", "Timur"))
        # owner default never applies to subagents
        r.update(sub=True, atype="Plan")
        self.assertEqual(names({"no:o_timur1": json.dumps({"name": "Robo", "ts": 1})}), ("Plan", "auto", "Plan"))

    def test_make_record(self):
        u = parse_update({"session": "s", "owner": "Timur", "state": "idle", "stateAgeMs": 2000,
                          "startedAgeMs": 60000, "toolCount": 4, "name": "N", "color": 7})
        r = derive.make_record(u, owner_id="o_x1234", parent_id=None, now_ms=NOW)
        self.assertEqual((r["ts"], r["ss"], r["sa"], r["tc"], r["col"], r["nm"]), (NOW, NOW - 2000, NOW - 60000, 4, 7, "N"))
        self.assertNotIn("fin", r)
        self.assertLessEqual(len(derive.dump_record(r).encode()), 600)
        u = parse_update({"session": "s", "owner": "Timur", "state": "idle"})
        r = derive.make_record(u, owner_id="o_x1234", parent_id=None, now_ms=NOW, prev=("idle", NOW - 9000, NOW - 99000))
        self.assertEqual((r["ss"], r["sa"], r["col"]), (NOW - 9000, NOW - 99000, derive.crc32("o_x1234") % 16))
        r = derive.make_record(u, owner_id="o_x1234", parent_id=None, now_ms=NOW, prev=("working", NOW - 9000, NOW - 99000))
        self.assertEqual((r["ss"], r["sa"]), (NOW, NOW - 99000))
        big = parse_update({"session": "s" * 64, "sub": "u" * 64, "owner": "ğ" * 24, "project": "ş" * 32,
                            "state": "sleeping", "tool": "t" * 32, "label": "ü" * 40, "agentType": "ö" * 24,
                            "name": "ç" * 24, "final": True, "source": "s" * 16})
        r = derive.make_record(big, owner_id="o" * 32, parent_id=ids.agent_id(ROOM, "o" * 32, "s" * 64, None), now_ms=NOW)
        self.assertLess(len(derive.dump_record(r).encode()), 800)


if __name__ == "__main__":
    unittest.main()
