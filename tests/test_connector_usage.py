"""Phase 13: SQLite ledger, period boundaries in a fixed time zone, weighted token metric, gauge states, `block`
budgets and the --max-budget-usd cap (V3.md §10); the audit log (§6.7)."""

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

from pixel_connector.audit import AuditLog
from pixel_connector.usage import Budgets, Ledger, gauge_state, period_start_ms, weighted_tokens

TZ = timezone(timedelta(hours=3))          # Europe/Istanbul (no DST)


def ms(*a):
    return int(datetime(*a, tzinfo=TZ).timestamp() * 1000)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class PeriodTests(unittest.TestCase):
    def test_boundaries(self):
        now = ms(2026, 10, 17, 1, 30)                 # Saturday 01:30 local (Friday 22:30 UTC)
        self.assertEqual(period_start_ms("day", now, TZ), ms(2026, 10, 17))
        self.assertEqual(period_start_ms("week", now, TZ), ms(2026, 10, 12))        # Monday
        self.assertEqual(period_start_ms("month", now, TZ), ms(2026, 10, 1))
        self.assertEqual(period_start_ms("week", ms(2026, 10, 12, 0, 0), TZ), ms(2026, 10, 12))
        with self.assertRaises(ValueError):
            period_start_ms("year", now, TZ)

    def test_weight_and_states(self):
        self.assertEqual(weighted_tokens(1000, 500, 250000, 12000), 1000 + 500 + 12000 + 25000)
        self.assertEqual([gauge_state(p) for p in (0, 79, 80, 99, 100, 999)], ["ok", "ok", "warn", "warn", "over", "over"])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(ms(2026, 10, 17, 12, 0) / 1000)
        self.led = Ledger(os.path.join(self.tmp.name, "usage.sqlite"), self.clock, TZ)

    def tearDown(self):
        self.led.close()
        self.tmp.cleanup()

    def add(self, task, agent, engine, started, usage, cost, status="success", prompt="p"):
        self.led.start_task(task, agent, engine, None, "sha", prompt, "bid", started)
        self.led.end_task(task, status, usage, cost, "claude-estimate" if engine == "claude" else "local", "ok",
                          started + 1000)

    def test_totals_periods_and_lists(self):
        u = {"inTok": 100, "outTok": 50, "cacheRead": 1000, "cacheWrite": 10, "estimated": False}
        self.add("t_aaaaaaaaaa", "a1", "claude", ms(2026, 10, 17, 9), u, 0.5)
        self.add("t_bbbbbbbbbb", "a1", "claude", ms(2026, 10, 16, 23, 59), u, 0.25)     # yesterday, same week
        self.add("t_cccccccccc", "a2", "ollama", ms(2026, 10, 17, 10), {"inTok": 20, "outTok": 30}, 0.0)
        self.add("t_dddddddddd", "a1", "claude", ms(2026, 9, 30, 12), u, 1.0)           # last month
        day = self.led.totals("day")
        self.assertEqual((day["tasks"], day["inTok"], day["costUsd"]), (2, 120, 0.5))
        self.assertEqual(day["tokens"], 100 + 50 + 10 + 100 + 20 + 30)
        self.assertEqual(self.led.totals("week", agent="a1")["tasks"], 2)
        self.assertEqual(self.led.totals("month")["tasks"], 3)
        self.assertEqual(self.led.totals("day", engine="ollama")["tokens"], 50)
        self.assertEqual(self.led.by_agent("day"), [{"agent": "a1", "tokens": 260, "costUsd": 0.5},
                                                    {"agent": "a2", "tokens": 50, "costUsd": 0.0}])
        lst = self.led.list_tasks("a1", 2)
        self.assertEqual([t["task"] for t in lst], ["t_aaaaaaaaaa", "t_bbbbbbbbbb"])
        self.assertEqual(self.led.get_task("t_cccccccccc")["status"], "success")
        mode = stat.S_IMODE(os.stat(os.path.join(self.tmp.name, "usage.sqlite")).st_mode)
        self.assertEqual(mode & 0o077, 0)

    def test_purge_keeps_numbers(self):
        self.add("t_old0000000", "a1", "claude", ms(2026, 8, 1), {"inTok": 1}, 0.1, prompt="eski istem")
        self.assertEqual(self.led.purge(30), 1)
        row = self.led.get_task("t_old0000000")
        self.assertIsNone(row["prompt"])
        self.assertEqual(row["in_tok"], 1)

    def test_budgets_block_and_cap(self):
        cfg = [{"scope": "device", "period": "day", "unit": "usd", "limit": 1.0, "action": "block"},
               {"scope": "engine:ollama", "period": "day", "unit": "tokens", "limit": 100, "action": "warn"}]
        agents = [("a1", {"period": "day", "unit": "tokens", "limit": 1000, "action": "block"})]
        b = Budgets(self.tmp.name, cfg, self.led, lambda: agents)
        ok, cap = b.check_task("a1", "claude", 2.0)
        self.assertEqual((ok, cap), (True, 1.0))
        self.add("t_aaaaaaaaaa", "a1", "claude", ms(2026, 10, 17, 9), {"inTok": 100, "outTok": 100}, 0.755)
        ok, cap = b.check_task("a1", "claude", 2.0)
        self.assertEqual((ok, cap), (True, 0.24))                   # floor(0.245) — never above the remainder
        ev = {x["scope"]: x for x in b.evaluate()}
        self.assertEqual((ev["device"]["pct"], ev["device"]["state"]), (76, "ok"))
        self.assertEqual((ev["agent:a1"]["used"], ev["agent:a1"]["pct"]), (200, 20))
        self.add("t_bbbbbbbbbb", "a1", "claude", ms(2026, 10, 17, 10), {"inTok": 10}, 0.24)
        self.assertEqual(b.check_task("a1", "claude", 2.0), (False, None))     # remaining 0.005 < 0.01
        self.assertEqual(b.check_task("a2", "ollama"), (False, None))           # device block applies to all
        # warn budgets never block
        b2 = Budgets(self.tmp.name, [cfg[1]], self.led, lambda: [])
        self.add("t_cccccccccc", "a2", "ollama", ms(2026, 10, 17, 11), {"inTok": 500}, 0.0)
        self.assertEqual(b2.evaluate()[0]["state"], "over")
        self.assertEqual(b2.check_task("a2", "ollama"), (True, None))

    def test_remote_budgets_and_locks(self):
        b = Budgets(self.tmp.name, [{"scope": "device", "period": "day", "unit": "usd", "limit": 1, "action": "warn"}],
                    self.led)
        self.assertEqual(b.set({"scope": "device", "period": "day", "unit": "usd", "limit": 5}), (False, "locked"))
        self.assertEqual(b.set({"scope": "engine:claude", "period": "week", "unit": "usd", "limit": 5,
                                "action": "block"}), (True, ""))
        self.assertEqual(b.set({"scope": "agent:a1", "period": "day", "unit": "usd", "limit": 5}),
                         (False, "use_agent_update"))
        self.assertEqual(b.set({"scope": "engine:claude", "period": "week", "unit": "x", "limit": 5}),
                         (False, "bad_request"))
        b3 = Budgets(self.tmp.name, [], self.led)               # persisted
        self.assertEqual([x["scope"] for x in b3.all()], ["engine:claude"])
        self.assertEqual(b3.set({"scope": "engine:claude", "delete": True}), (True, ""))
        self.assertEqual(Budgets(self.tmp.name, [], self.led).all(), [])


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "audit.log")

    def tearDown(self):
        self.tmp.cleanup()

    def test_prompt_hash_only_by_default(self):
        a = AuditLog(self.path)
        a.write("task.start", bid="k3abc", agent="a1", prompt="GİZLİ İSTEM CANARY", task="t_x", sub=None)
        rec = a.read()[0]
        self.assertEqual(rec["ev"], "task.start")
        self.assertEqual(rec["promptLen"], len("GİZLİ İSTEM CANARY".encode()))
        self.assertEqual(len(rec["promptSha256"]), 64)
        self.assertNotIn("prompt", rec)
        self.assertNotIn("sub", rec)
        self.assertNotIn("CANARY", Path(self.path).read_text(encoding="utf-8"))
        self.assertRegex(rec["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d$")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        AuditLog(self.path, store_prompts=True).write("task.start", prompt="tam metin")
        self.assertEqual(AuditLog(self.path).read()[-1]["prompt"], "tam metin")

    def test_rotation_keeps_three_files(self):
        a = AuditLog(self.path, max_bytes=300)
        for i in range(40):
            a.write("denied", reason="rate %d" % i)
        files = sorted(f for f in os.listdir(self.tmp.name) if f.startswith("audit.log"))
        self.assertEqual(files, ["audit.log", "audit.log.1", "audit.log.2"])
        for f in files:
            self.assertLessEqual(os.path.getsize(os.path.join(self.tmp.name, f)), 300)
        self.assertEqual(json.loads(Path(self.path).read_text().splitlines()[-1])["reason"], "rate 39")


if __name__ == "__main__":
    unittest.main()
