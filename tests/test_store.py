import threading
import unittest

import _helpers  # noqa: F401  (sys.path)

from pixel_office.models import AgentInfo, State, color_seed_for, name_for
from pixel_office.config import NAMES
from pixel_office.store import AgentStore


def agent(aid: str, state: State = State.THINKING, started: int = 1000) -> AgentInfo:
    return AgentInfo(id=aid, name=name_for(aid), project="p", state=state, started_at=started)


class ModelTests(unittest.TestCase):
    def test_identity_helpers(self):
        import zlib
        self.assertEqual(color_seed_for("abc"), zlib.crc32(b"abc"))
        self.assertEqual(name_for("abc"), NAMES[zlib.crc32(b"abc") % len(NAMES)])
        self.assertEqual(agent("x").to_dict()["state"], "thinking")

    def test_name_overrides(self):
        import json, os, tempfile
        from pathlib import Path
        from unittest import mock
        from pixel_office import config
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "names.json"
            with mock.patch.object(config, "NAMES_FILE", f):
                self.assertEqual(name_for("abc"), NAMES[color_seed_for("abc") % len(NAMES)])  # no file
                f.write_text(json.dumps({"abc": " Dilek ", "bad": 3}))
                self.assertEqual(name_for("abc"), "Dilek")
                self.assertEqual(name_for("bad"), NAMES[color_seed_for("bad") % len(NAMES)])
                f.write_text("{not json")
                os.utime(f, (1, 1))  # force an mtime change
                self.assertEqual(name_for("abc"), NAMES[color_seed_for("abc") % len(NAMES)])


class StoreTests(unittest.TestCase):
    def test_events(self):
        s = AgentStore("live")
        q = s.subscribe()
        self.assertTrue(s.upsert(agent("a")))
        self.assertFalse(s.upsert(agent("a")))                 # unchanged → no event
        self.assertTrue(s.upsert(agent("a", State.WORKING)))
        self.assertTrue(s.remove("a"))
        self.assertFalse(s.remove("a"))
        evs = [q.get_nowait() for _ in range(q.qsize())]
        self.assertEqual([e.name for e in evs], ["agent_added", "agent_updated", "agent_removed"])
        self.assertEqual([e.seq for e in evs], [1, 2, 3])
        self.assertEqual(evs[1].data["state"], "working")
        self.assertEqual(evs[2].data, {"id": "a"})

    def test_snapshot_sorted_and_subscribe_with_snapshot(self):
        s = AgentStore("demo")
        s.upsert(agent("b", started=5))
        s.upsert(agent("a", started=5))
        s.upsert(agent("c", started=1))
        q, snap, seq = s.subscribe_with_snapshot()
        self.assertEqual(snap["mode"], "demo")
        self.assertIsInstance(snap["serverTime"], int)
        self.assertEqual([a["id"] for a in snap["agents"]], ["c", "a", "b"])
        self.assertEqual(seq, 3)
        self.assertTrue(q.empty())
        s.upsert(agent("d"))
        self.assertEqual(q.get_nowait().seq, 4)
        s.unsubscribe(q)
        s.unsubscribe(q)   # idempotent
        self.assertEqual(s.subscriber_count(), 0)

    def test_store_keeps_copies(self):
        s = AgentStore("live")
        a = agent("a")
        s.upsert(a)
        a.state = State.SLEEPING          # mutating caller's object must not affect the store
        self.assertEqual(s.get("a").state, State.THINKING)
        self.assertTrue(s.upsert(a))

    def test_slow_subscriber_gets_overflow_sentinel(self):
        s = AgentStore("live")
        q = s.subscribe(maxsize=2)
        for i in range(5):
            s.upsert(agent(f"x{i}"))
        self.assertEqual(s.subscriber_count(), 0)
        self.assertEqual(q.get_nowait().name, "overflow")

    def test_thread_safety(self):
        s = AgentStore("live")
        q = s.subscribe(maxsize=100000)

        def work(n):
            for i in range(200):
                s.upsert(agent(f"{n}-{i}"))
        ts = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(s.ids()), 1600)
        seqs = [q.get_nowait().seq for _ in range(q.qsize())]
        self.assertEqual(seqs, list(range(1, 1601)))


if __name__ == "__main__":
    unittest.main()
