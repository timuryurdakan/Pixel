"""2026-11-01 security and cost check (docs/SECURITY_AUDIT.md §20): a device with no dollar budget for Claude gets
usage.DEFAULT_CLAUDE_BUDGET (5 $ a day, block). Any dollar budget of the user's own at device or engine:claude scope
replaces it; it is never written to budgets.json and never applies to other engines."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import _helpers  # noqa: F401  (sys.path)

from pixel_connector.usage import DEFAULT_CLAUDE_BUDGET, Budgets, Ledger

TZ = timezone(timedelta(hours=3))


def ms(*a):
    return int(datetime(*a, tzinfo=TZ).timestamp() * 1000)


class DefaultClaudeBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = ms(2026, 11, 1, 12, 0)
        self.led = Ledger(os.path.join(self.tmp.name, "usage.sqlite"), lambda: self.now / 1000, TZ)
        self.n = 0

    def tearDown(self):
        self.led.close()
        self.tmp.cleanup()

    def spend(self, engine, cost, agent="a1"):
        self.n += 1
        task = "t_%010d" % self.n
        self.led.start_task(task, agent, engine, None, "sha", "p", "bid", self.now - 60000)
        self.led.end_task(task, "success", {"inTok": 10}, cost, "claude-estimate", "ok", self.now - 59000)

    def scopes(self, b):
        return [(x["scope"], x.get("default", False)) for x in b.evaluate(self.now)]

    def test_no_budget_means_five_dollars_a_day_for_claude(self):
        self.assertEqual(DEFAULT_CLAUDE_BUDGET, {"scope": "engine:claude", "period": "day", "unit": "usd",
                                                 "limit": 5.0, "action": "block"})
        b = Budgets(self.tmp.name, [], self.led)
        self.assertEqual(b.all(), [])                                   # configured budgets: none
        self.assertEqual(self.scopes(b), [("engine:claude", True)])
        self.assertEqual(b.check_task("a1", "claude", 2.0, self.now), (True, 2.0))
        self.spend("claude", 4.2)
        self.assertEqual(b.check_task("a1", "claude", 2.0, self.now), (True, 0.8))     # the rest of today's 5 $
        self.spend("claude", 0.8)
        self.assertEqual(b.check_task("a1", "claude", 2.0, self.now), (False, None))
        self.assertEqual(b.check_task("a1", "ollama", None, self.now), (True, None))   # other engines: not this cap
        self.assertEqual(b.check_task("a1", "openai", None, self.now), (True, None))

    def test_a_new_day_starts_again(self):
        b = Budgets(self.tmp.name, [], self.led)
        self.spend("claude", 5.0)
        self.assertEqual(b.check_task("a1", "claude", 2.0, self.now), (False, None))
        self.now = ms(2026, 11, 2, 0, 1)
        self.assertEqual(b.check_task("a1", "claude", 2.0, self.now), (True, 2.0))

    def test_the_users_own_dollar_budget_replaces_it(self):
        own = {"scope": "device", "period": "day", "unit": "usd", "limit": 20, "action": "block"}
        b = Budgets(self.tmp.name, [own], self.led)
        self.assertEqual(self.scopes(b), [("device", False)])
        self.spend("claude", 6.0)
        self.assertEqual(b.check_task("a1", "claude", 2.0, self.now), (True, 2.0))
        r = Budgets(self.tmp.name, [], self.led)                         # set from a browser instead
        self.assertEqual(r.set({"scope": "engine:claude", "period": "week", "unit": "usd", "limit": 50,
                                "action": "warn"}), (True, ""))
        self.assertEqual(self.scopes(r), [("engine:claude", False)])
        with open(os.path.join(self.tmp.name, "budgets.json"), encoding="utf-8") as f:
            self.assertNotIn("default", f.read())
        self.assertEqual(r.set({"scope": "engine:claude", "delete": True}), (True, ""))
        self.assertEqual(self.scopes(r), [("engine:claude", True)])       # deleted: the default is back

    def test_token_or_agent_budgets_do_not_replace_it(self):
        b = Budgets(self.tmp.name, [{"scope": "engine:claude", "period": "day", "unit": "tokens", "limit": 10 ** 9,
                                     "action": "block"}], self.led, lambda: [("a1", {"period": "day", "unit": "usd",
                                                                                    "limit": 100, "action": "block"})])
        self.assertIn(("engine:claude", True), self.scopes(b))
        self.spend("claude", 5.0)
        self.assertEqual(b.check_task("a1", "claude", 2.0, self.now), (False, None))


if __name__ == "__main__":
    unittest.main()
