import unittest
from collections import Counter

from _helpers import T0

from pixel_office.simulator import DemoSimulator
from pixel_office.store import AgentStore

VALID_STATES = {"working", "thinking", "waiting_for_user", "idle", "sleeping"}


def run(seconds: float, seed: int = 7, dt: float = 0.5):
    store = AgentStore("demo")
    q = store.subscribe(maxsize=1_000_000)
    sim = DemoSimulator(store, seed=seed)
    t = T0
    while t <= T0 + seconds:
        sim.step(t)
        t += dt
    events = [q.get_nowait() for _ in range(q.qsize())]
    return store, events


class SimulatorTests(unittest.TestCase):
    def test_staggered_spawn(self):
        store = AgentStore("demo")
        sim = DemoSimulator(store, seed=1)
        sim.step(T0)
        self.assertEqual(store.ids(), {"demo-1"})
        sim.step(T0 + 4)
        self.assertEqual(store.ids(), {"demo-1", "demo-2", "demo-3"})
        sim.step(T0 + 8)
        self.assertEqual(store.ids(), {f"demo-{i}" for i in range(1, 6)})
        projects = {store.get(f"demo-{i}").project for i in range(1, 6)}
        self.assertEqual(projects, {"pixel-office", "web-shop", "ml-pipeline", "docs-site", "api-gateway"})

    def test_lifecycle_over_ten_minutes(self):
        for seed in (1, 2, 3):
            store, events = run(600, seed=seed)
            names = Counter(e.name for e in events)
            states = Counter(e.data["state"] for e in events if e.name != "agent_removed")
            self.assertEqual(set(states), VALID_STATES, f"seed {seed}: {states}")
            self.assertGreater(names["agent_removed"], 0)
            added = [e.data["id"] for e in events if e.name == "agent_added"]
            self.assertIn("demo-6", added)                       # replacements spawn
            subs = [e.data for e in events if e.name == "agent_added" and e.data["isSubagent"]]
            self.assertTrue(subs, "demo subagent spawned")
            self.assertTrue(all(s["parentId"] and s["agentType"] == "Explore" for s in subs))
            tools = {e.data["tool"] for e in events if e.name != "agent_removed" and e.data["tool"]}
            self.assertTrue({"Bash", "Read", "Edit", "Agent"} <= tools, tools)
            for e in events:
                if e.name == "agent_removed":
                    continue
                d = e.data
                if d["state"] in ("working", "waiting_for_user"):
                    self.assertTrue(d["tool"], d)
                else:
                    self.assertIsNone(d["tool"], d)
            # population stays around five main agents
            mains = [i for i in store.ids() if ":" not in i]
            self.assertTrue(3 <= len(mains) <= 5, mains)

    def test_state_changes_are_lively(self):
        _, events = run(300, seed=4)
        last: dict[str, tuple[str, float]] = {}
        gaps = []
        for e in events:
            if e.name == "agent_removed":
                continue
            d = e.data
            prev = last.get(d["id"])
            if prev and prev[0] != d["state"]:
                gaps.append((d["stateSince"] - prev[1]) / 1000)
            if not prev or prev[0] != d["state"]:
                last[d["id"]] = (d["state"], d["stateSince"])
        self.assertTrue(gaps)
        self.assertLessEqual(max(gaps), 35)
        self.assertGreaterEqual(min(gaps), 1.5)

    def test_live_demo_agents_have_unique_names(self):
        # name_for() alone collides (e.g. demo-3 and demo-8 are both "Ada").
        for seed in (1, 2, 3):
            store = AgentStore("demo")
            sim = DemoSimulator(store, seed=seed)
            t = T0
            while t <= T0 + 900:
                sim.step(t)
                names = [a["name"] for a in store.snapshot()["agents"]]
                self.assertEqual(len(names), len(set(names)), f"seed {seed} t={t - T0}: {names}")
                t += 0.5

    def test_big_clock_jump_does_not_crash(self):
        store = AgentStore("demo")
        sim = DemoSimulator(store, seed=5)
        sim.step(T0)
        sim.step(T0 + 10_000)
        sim.step(T0 + 10_001)
        self.assertTrue(store.ids())


if __name__ == "__main__":
    unittest.main()
