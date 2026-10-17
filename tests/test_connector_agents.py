"""Phase 13: office presence of connector agents (V3.md §5.1): v2 Sender with `ctl`, session "c_<key>", source
"connector", 20-minute keep-alive and ages clamped to the server's validator limits; the agent registry."""

import os
import tempfile
import unittest

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import agents as A


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.posts = []
        pa = A.load_pixel_agent()
        target = {"url": "https://office.example", "room": "a" * 26, "key": "k" * 40, "owner": "Timur", "ownerId": None}
        sender = A._make_sender(pa, target, self.clock, post=lambda ups, timeout: self.posts.append(ups) or 200,
                                start_thread=False)
        self.rep = A.OfficeReporter("https://office.example", "a" * 26, "k" * 40, "Timur", "abcdefghijklmnop",
                                    sender=sender, clock=self.clock)
        self.agent = {"key": "a1b2c3", "name": "Kâtip"}

    def test_payload_and_keepalive(self):
        self.rep.report(self.agent, "Pixel", "working", tool="Read")
        self.rep.sender.tick(force=True)
        u = self.posts[-1][0]
        self.assertEqual(u["ctl"], "abcdefghijklmnop.a1b2c3")
        self.assertEqual((u["session"], u["source"], u["project"], u["name"], u["owner"]),
                         ("c_a1b2c3", "connector", "Pixel", "Kâtip", "Timur"))
        self.assertEqual((u["state"], u["tool"], u["toolCount"]), ("working", "Read", 1))
        for k in u:
            self.assertFalse(k.startswith("_"))
        n = len(self.posts)
        self.clock.t += 600                      # v2 would re-send after 300 s; the connector waits 20 min
        self.rep.sender.tick()
        self.assertEqual(len(self.posts), n)
        self.clock.t += 601
        self.rep.sender.tick()
        self.assertEqual(len(self.posts), n + 1)
        self.clock.t += 3 * 86400                # ages never exceed what /api/status accepts
        self.rep.sender.tick()
        self.assertEqual(self.posts[-1][0]["stateAgeMs"], 86_400_000)
        self.rep.offline(self.agent, "Pixel")
        self.rep.sender.tick(force=True)
        self.assertEqual(self.posts[-1][0]["state"], "offline")


class StoreTests(unittest.TestCase):
    def test_create_update_delete(self):
        with tempfile.TemporaryDirectory() as h:
            st = A.AgentStore(h)
            a = st.create("  Kâtip‮ ", "claude", None, {"id": "pixel", "sub": None}, "edit",
                          {"period": "day", "unit": "usd", "limit": 3, "action": "block"})
            self.assertRegex(a["key"], r"^a[0-9a-f]{6}$")
            self.assertEqual(a["name"], "Kâtip")
            for bad in (dict(name=""), dict(engine="gpt"), dict(model="-x"), dict(profile="bypass"),
                        dict(engine="ollama", model=None), dict(budget={"period": "year"})):
                kw = dict(name="X", engine="claude", model=None, workspace={"id": "w"}, profile="edit", budget=None)
                kw.update(bad)
                with self.subTest(bad=bad):
                    with self.assertRaises(A.AgentError):
                        st.create(**kw)
            st.update(a["key"], {"name": "Yeni", "profile": "readonly", "budget": None})
            again = A.AgentStore(h).get(a["key"])
            self.assertEqual((again["name"], again["profile"], again["budget"]), ("Yeni", "readonly", None))
            self.assertEqual(os.stat(os.path.join(h, "agents.json")).st_mode & 0o777, 0o600)
            st.delete(a["key"])
            with self.assertRaises(A.AgentError):
                A.AgentStore(h).get(a["key"])


if __name__ == "__main__":
    unittest.main()
