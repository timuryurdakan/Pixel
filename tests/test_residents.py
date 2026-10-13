import unittest

import _helpers  # noqa: F401  (sys.path)
from test_cloud_app import Base

from pixel_cloud import ids
from pixel_cloud import residents as R
from pixel_cloud.validate import ValidationError

STATES = {"working", "thinking", "waiting_for_user", "idle", "sleeping"}
NOON = 1_800_000_000 - (1_800_000_000 + R.TZ_OFFSET) % 86400 + 12 * 3600   # 12:00 Istanbul


class Schedule(unittest.TestCase):
    def test_plan_covers_cycle(self):
        for c in range(50):
            plan = R._cycle_plan("seed", c, "backend")
            self.assertEqual(sum(p[0] for p in plan), R.CYCLE)
            self.assertTrue(all(p[1] in STATES for p in plan))

    def test_deterministic_and_lively(self):
        seen = set()
        for k in range(0, 4 * R.CYCLE, 20):
            a = R.state_at("x", "frontend", (NOON + k) * 1000)
            self.assertEqual(a, R.state_at("x", "frontend", (NOON + k) * 1000))
            self.assertLessEqual(a[1], (NOON + k) * 1000)
            seen.add(a[0])
        self.assertTrue({"working", "idle", "sleeping"} <= seen, seen)

    def test_night_sleep(self):
        st, since, *_ = R.state_at("x", "backend", (NOON + 15 * 3600) * 1000)   # 03:00
        self.assertEqual(st, "sleeping")
        self.assertEqual(since, (NOON + 14 * 3600) * 1000)                       # since 02:00

    def test_parse(self):
        rs = R.parse_residents(b'{"residents":[{"name":"Mete","role":"backend"},{"name":" Saltuk ","role":"x"}]}')
        self.assertEqual([(r.name, r.role, r.project) for r in rs],
                         [("Mete", "backend", "backend"), ("Saltuk", "fullstack", "fullstack")])
        for bad in (b'[]', b'{"residents":[{"name":""}]}', b'{"residents":[{"name":"a"}' + b',{"name":"a"}' * 6 + b']}'):
            with self.assertRaises(ValidationError):
                R.parse_residents(bad)
        self.assertTrue(ids.AGENT_ID_RE.match(R.resident_id("r", 0, "Mete")))


class Api(Base):
    def put(self, room, key, residents, auth=True):
        h = {"Authorization": f"Bearer {key}"} if auth else {}
        return self.call("POST", f"/api/residents?room={room}", {"residents": residents}, h)

    def test_residents_appear_rename_and_clear(self):
        self.clock.t = NOON
        room, key = self.room()
        self.assert_error(self.put(room, key, [{"name": "Mete"}], auth=False), 401, "unauthorized")
        r = self.put(room, key, [{"name": "Mete", "role": "backend", "project": "api"},
                                 {"name": "Saltuk", "role": "frontend", "project": "web"}])
        self.assertEqual(r.status, 200, r.body)
        mete_id = r.json()["ids"][0]
        agents = self.state(room).json()["agents"]
        self.assertEqual(sorted(a["name"] for a in agents), ["Mete", "Saltuk"])
        self.assertTrue(all(a["state"] in STATES for a in agents))
        self.assertNotEqual(agents[0]["ownerColor"], agents[1]["ownerColor"])
        # Rename works and survives later reads, even long after (no record to purge against).
        self.call("POST", f"/api/rename?room={room}", {"scope": "agent", "agentId": mete_id, "name": "Mete Usta"},
                  {"Authorization": f"Bearer {key}"})
        self.clock.t += 3 * 86400
        names = {a["id"]: a["name"] for a in self.state(room).json()["agents"]}
        self.assertEqual(names[mete_id], "Mete Usta")
        self.assertEqual(self.put(room, key, []).status, 200)
        self.assertEqual(self.state(room).json()["agents"], [])


if __name__ == "__main__":
    unittest.main()
