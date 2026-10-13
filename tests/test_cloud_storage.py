import unittest

import _helpers  # noqa: F401  (sys.path)
from _fake_upstash import FakeUpstash

from pixel_cloud import settings as S
from pixel_cloud import storage as st


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class MemoryStorageTests(unittest.TestCase):
    def test_hash_ops_and_ttl(self):
        clk = FakeClock()
        m = st.MemoryStorage(clk)
        self.assertEqual(m.read_room("r"), {})
        m.create_room("r", '{"name":"x"}')
        self.assertAlmostEqual(m.ttl("r"), S.ROOM_TTL)
        self.assertEqual(m.put_agents("r", {"a1": "1", "a2": "2"}, refresh_ttl=False), {"a1": True, "a2": True})
        self.assertEqual(m.put_agents("r", {"a1": "x"}, refresh_ttl=False), {"a1": False})
        self.assertEqual(m.count("r"), 3)
        self.assertEqual(sorted(m.keys("r")), ["_meta", "a1", "a2"])
        m.delete("r", ["a2", "nope"])
        m.delete("r", [])
        self.assertEqual(m.read_room("r"), {"_meta": '{"name":"x"}', "a1": "x"})
        clk.t += S.ROOM_TTL - 10
        m.put_agents("r", {"a3": "3"}, refresh_ttl=True)      # TTL refreshed by a write
        clk.t += 20
        self.assertEqual(m.count("r"), 3)
        clk.t += S.ROOM_TTL
        self.assertEqual(m.read_room("r"), {})                 # expired
        self.assertTrue(m.ping())
        m.put_agents("q", {"a": "1"}, refresh_ttl=False)
        m.delete("q", ["a"])
        self.assertEqual(m.keys("q"), [])

    def test_from_env(self):
        self.assertIsInstance(st.storage_from_env({}), st.MemoryStorage)
        self.assertIsInstance(st.storage_from_env({"VERCEL": "1"}), st.UnconfiguredStorage)
        for u, t in (("KV_REST_API_URL", "KV_REST_API_TOKEN"), ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN")):
            s = st.storage_from_env({u: "https://x.upstash.io", t: "tok-SECRET"})
            self.assertIsInstance(s, st.UpstashStorage)
            self.assertNotIn("tok-SECRET", repr(s))
        # Custom Marketplace prefixes (e.g. STORAGE_) are found by suffix.
        for u, t in (("STORAGE_KV_REST_API_URL", "STORAGE_KV_REST_API_TOKEN"),
                     ("STORAGE_REST_API_URL", "STORAGE_REST_API_TOKEN")):
            self.assertIsInstance(st.storage_from_env({u: "https://x.upstash.io", t: "tok"}), st.UpstashStorage)
        self.assertIsInstance(st.storage_from_env({"STORAGE_REST_API_URL": "https://x"}), st.MemoryStorage)
        self.assertIsInstance(st.storage_from_env({"KV_REST_API_URL": "https://x"}), st.MemoryStorage)
        self.assertIsInstance(st.storage_from_env({"KV_REST_API_URL": "ftp://x", "KV_REST_API_TOKEN": "t"}),
                              st.UnconfiguredStorage)
        u = st.UnconfiguredStorage()
        self.assertFalse(u.ping())
        with self.assertRaises(st.StorageNotConfigured):
            u.read_room("r")


class UpstashStorageTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeUpstash().__enter__()
        self.s = st.UpstashStorage(self.fake.url, self.fake.token, timeout=2)

    def tearDown(self):
        self.s.close()
        self.fake.__exit__()

    def test_operations(self):
        self.assertTrue(self.s.ping())
        self.s.create_room("r", '{"name":"Oda"}')
        self.assertEqual(self.fake.ttl["po:room:r"], S.ROOM_TTL)
        self.assertEqual(self.s.put_agents("r", {"a1": "x"}, refresh_ttl=False), {"a1": True})
        self.assertEqual(self.s.put_agents("r", {"a1": "y", "a2": "z"}, refresh_ttl=True), {"a1": False, "a2": True})
        self.assertEqual(self.s.read_room("r"), {"_meta": '{"name":"Oda"}', "a1": "y", "a2": "z"})
        self.assertEqual(self.s.count("r"), 3)
        self.assertEqual(sorted(self.s.keys("r")), ["_meta", "a1", "a2"])
        self.s.delete("r", ["a1", "a2"])
        self.s.delete("r", [])
        self.assertEqual(self.s.read_room("r"), {"_meta": '{"name":"Oda"}'})
        self.assertEqual(self.s.read_room("missing"), {})
        ops = [c[0] for c in self.fake.calls]
        self.assertEqual(ops, ["PING", "HSET", "EXPIRE", "HSET", "HSET", "HSET", "EXPIRE", "HGETALL", "HLEN", "HKEYS",
                               "HDEL", "HGETALL", "HGETALL"])

    def test_unicode_roundtrip(self):
        self.s.put_agents("r", {"a1": '{"owner":"Ayşe ☕"}'}, refresh_ttl=False)
        self.assertEqual(self.s.read_room("r")["a1"], '{"owner":"Ayşe ☕"}')

    def test_keep_alive_and_reconnect(self):
        self.s.ping()
        self.s.ping()
        self.fake.fail_mode = "drop"          # server drops the next request → reconnect once
        self.assertTrue(self.s.ping())
        self.assertEqual(self.fake.requests, 4)

    def test_errors(self):
        self.fake.fail_mode = "http500"
        with self.assertRaises(st.StorageError):
            self.s.read_room("r")
        self.fake.fail_mode = "error"
        with self.assertRaises(st.StorageError):
            self.s.count("r")
        w = st.UpstashStorage(self.fake.url, "wrong-TOKEN-xyz")
        self.assertFalse(w.ping())
        with self.assertRaises(st.StorageError) as cm:
            w.read_room("r")
        w.close()
        self.assertNotIn("wrong-TOKEN-xyz", str(cm.exception))
        with self.assertRaises(st.StorageError):
            self.s.command("NOPE", "x")

    def test_unreachable(self):
        dead = st.UpstashStorage("http://127.0.0.1:9", "t", timeout=0.5)
        with self.assertRaises(st.StorageError):
            dead.read_room("r")
        self.assertFalse(dead.ping())
        dead.close()


if __name__ == "__main__":
    unittest.main()
