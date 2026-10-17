"""Phase 13: Claude Code adapter (V3.md §8.1) against a fake `claude` that replays tests/fixtures/claude_stream_*.jsonl.

Checks argv per profile (never the prompt, never --dangerously-skip-permissions / bypassPermissions), stdin = prompt,
child env (PIXEL_OFFICE_DISABLE=1, no PIXEL_OFFICE_* secrets), parts, usage/cost (incl. resume deltas and subagents),
office states, error mapping, session resume + "session not found" retry, cancel escalation.
"""

import ast
import os
import re
import signal
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)
import _v3_fakes as F

from pixel_connector.engines import claude as CL
from pixel_connector.engines.base import TaskContext

ROOT = Path(__file__).resolve().parent.parent
PROFILES = {"readonly": {"allow": [], "deny": []}, "edit": {"allow": [], "deny": ["Read(./secrets/**)"]},
            "dev": {"allow": ["Bash(npm test *)", "Bash(git status *)"], "deny": []}, "auto": {"enabled": False}}


class Events:
    def __init__(self):
        self.parts, self.lives, self.offices = [], [], []
        self.end_args = None
        self.done = threading.Event()

    def part(self, p):
        self.parts.append(p)

    def live(self, **u):
        self.lives.append(u)

    def office(self, state, tool=None, label=None):
        self.offices.append((state, tool))

    def end(self, status, result, usage, cost_usd, cost_basis, turns=None, error=None, denials=0):
        self.end_args = {"status": status, "result": result, "usage": usage, "cost": cost_usd, "basis": cost_basis,
                         "turns": turns, "error": error, "denials": denials}
        self.done.set()

    def wait(self, t=20):
        assert self.done.wait(t), "engine did not end"
        return self.end_args

    def kinds(self, k):
        return [p for p in self.parts if p["k"] == k]


class ArgvTests(unittest.TestCase):
    def argv(self, profile, profiles=PROFILES, version=(2, 1, 301), **kw):
        return CL.build_argv("/opt/claude", profile, profiles, "11111111-2222-4333-8444-555555555555",
                             kw.get("resume", False), kw.get("model"), 50, kw.get("budget"), version, kw.get("bare", False))

    def test_profiles(self):
        ro = self.argv("readonly")
        self.assertEqual(ro[:6], ["/opt/claude", "-p", "--output-format", "stream-json", "--verbose",
                                  "--include-partial-messages"])
        self.assertEqual(ro[ro.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(ro[ro.index("--permission-prompts") + 1], "none")
        self.assertNotIn("--allowedTools", ro)
        ed = self.argv("edit")
        i = ed.index("--allowedTools")
        self.assertEqual(ed[i + 1:i + 4], ["Edit", "Write", "NotebookEdit"])
        self.assertIn("Read(./secrets/**)", ed)
        dv = self.argv("dev")
        i = dv.index("--allowedTools")
        self.assertEqual(dv[i + 1:i + 6], ["Edit", "Write", "NotebookEdit", "Bash(npm test *)", "Bash(git status *)"])
        with self.assertRaises(ValueError):
            self.argv("auto")
        au = self.argv("auto", dict(PROFILES, auto={"enabled": True}))
        self.assertEqual(au[au.index("--permission-mode") + 1], "auto")
        with self.assertRaises(ValueError):
            self.argv("bypass")
        for a in (ro, ed, dv, au):
            j = a.index("--disallowedTools")
            self.assertEqual(a[j + 1:j + 8], CL.BUILTIN_DENY)
            self.assertIn("--session-id", a)
            self.assertEqual(a[a.index("--max-turns") + 1], "50")

    def test_never_forbidden_flags_or_prompt(self):
        for prof in ("readonly", "edit", "dev", "auto"):
            for resume in (False, True):
                for budget in (None, 0.5):
                    a = self.argv(prof, dict(PROFILES, auto={"enabled": True}), resume=resume, budget=budget,
                                  model="sonnet", bare=True)
                    joined = " ".join(a)
                    self.assertNotIn("dangerously", joined)
                    self.assertNotIn("bypassPermissions", joined)
                    self.assertNotIn("PROMPT", joined)
        evil = dict(PROFILES, dev={"allow": ["Bash(x) --dangerously-skip-permissions"], "deny": []})
        with self.assertRaises(ValueError):
            self.argv("dev", evil)

    def test_resume_model_budget_bare_and_old_version(self):
        a = self.argv("edit", resume=True, model="opus", budget=1.234, bare=True)
        self.assertEqual(a[a.index("--resume") + 1], "11111111-2222-4333-8444-555555555555")
        self.assertNotIn("--session-id", a)
        self.assertEqual(a[a.index("--model") + 1], "opus")
        self.assertEqual(a[a.index("--max-budget-usd") + 1], "1.23")
        self.assertEqual(a[-1], "--bare")
        old = self.argv("edit", version=(2, 1, 258))
        self.assertNotIn("--permission-prompts", old)

    def test_source_has_no_path_that_emits_bypass(self):
        src = (ROOT / "pixel_connector" / "engines" / "claude.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        allowed = set()
        doc = tree.body[0]
        if isinstance(doc, ast.Expr):
            allowed.add(id(doc.value))                       # module docstring
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "FORBIDDEN_ARGS" for t in node.targets):
                allowed.update(id(n) for n in ast.walk(node))
        hits = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and ("dangerously" in n.value or "bypassPermissions" in n.value) and id(n) not in allowed]
        # Only the FORBIDDEN_ARGS guard tuple (and the docstring) may mention them.
        self.assertEqual(hits, [])
        self.assertIn("FORBIDDEN_ARGS = (", src)
        for p in (ROOT / "pixel_connector").rglob("*.py"):
            other = p.read_text(encoding="utf-8")
            self.assertNotIn("shell=True", other, p.name)
            if p.name not in ("claude.py", "config.py"):        # config.py: only in its rejection message
                self.assertNotIn("dangerously", other, p.name)

    def test_tool_summary(self):
        s = CL.tool_summary
        self.assertEqual(s("Read", {"file_path": "/w/src/app.py"}, "/w"), "src/app.py")
        self.assertEqual(s("Edit", {"file_path": "/etc/passwd"}, "/w"), "/etc/passwd")
        self.assertEqual(s("Grep", {"pattern": "TODO"}, "/w"), "TODO")
        self.assertEqual(len(s("Bash", {"command": "x" * 500}, "/w")), 120)
        self.assertEqual(s("WebFetch", {"url": "https://docs.example.com/a?b=c"}, None), "docs.example.com")
        self.assertEqual(s("Task", {"description": "Kodu tara"}, None), "Kodu tara")
        self.assertEqual(s("Mystery", {"x": 1}, None), "")

    def test_child_env(self):
        env = CL.child_env({"PATH": "/bin", "PIXEL_OFFICE_KEY": "secret", "PIXEL_OFFICE_HOME": "/x", "HOME": "/h"})
        self.assertEqual(env, {"PATH": "/bin", "HOME": "/h", "PIXEL_OFFICE_DISABLE": "1"})

    def test_canceller_escalation_with_fake_clock(self):
        sent, waits = [], []

        class P:
            pid = 4242
        c = CL.Canceller(P(), 5.0, 10.0, signal_fn=sent.append, wait_fn=lambda t: waits.append(t) or False)
        c.run()
        self.assertEqual(sent, [signal.SIGINT, signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(waits, [5.0, 10.0])
        sent.clear()
        c2 = CL.Canceller(P(), 5.0, 10.0, signal_fn=sent.append, wait_fn=lambda t: True)
        c2.run()
        self.assertEqual(sent, [signal.SIGINT])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_dir = os.path.join(self.tmp.name, "fake")
        self.path = F.write_fake_claude(self.fake_dir)
        self.ws = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.ws)
        self.cfg = {"path": self.path, "enabled": True, "bare": False, "showThinking": False, "maxTurns": 50,
                    "maxBudgetUsdPerTask": 2.0, "defaultModel": None, "cancelGraceS": 5.0, "cancelTermS": 10.0}
        self.limits = []
        self.env = dict(os.environ, FAKE_CLAUDE_DIR=self.fake_dir, PIXEL_OFFICE_KEY="must-not-leak",
                        PIXEL_OFFICE_URL="http://x")
        self.eng = CL.ClaudeEngine(self.cfg, PROFILES, env=self.env, ledger_note=self.limits.append)
        self.agent = {"key": "a1", "name": "Kâtip", "engine": "claude", "claudeSession": None,
                      "claudeSessionStarted": False, "claudeTotals": None}
        self.saved = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_task(self, plan, prompt="Hatayı düzelt PROMPT-CANARY", profile="edit", fresh=False, budget=None,
                 cancel_after=None):
        F.set_plan(self.fake_dir, plan)
        ev = Events()
        ctx = TaskContext("t_0123456789", self.agent, prompt, self.ws, None, profile, fresh, budget,
                          save_agent=lambda a: self.saved.append(dict(a)))
        run = self.eng.start(ctx, ev)
        if cancel_after is not None:
            time.sleep(cancel_after)
            t0 = time.time()
            run.cancel()
            end = ev.wait(30)
            return ev, end, run, time.time() - t0
        return ev, ev.wait(), run

    def test_available(self):
        av = self.eng.available()
        self.assertEqual(av, {"available": True, "version": "2.1.301", "auth": "subscription"})
        e2 = CL.ClaudeEngine(dict(self.cfg), PROFILES, env=dict(self.env, FAKE_CLAUDE_AUTH="api_key"))
        self.assertEqual(e2.available()["auth"], "api_key")
        e3 = CL.ClaudeEngine(dict(self.cfg, path=os.path.join(self.tmp.name, "nope")), PROFILES, env=self.env)
        self.assertFalse(e3.available()["available"])

    def test_success_with_tools(self):
        ev, end, run = self.run_task([{"fixture": "claude_stream_success_tools.jsonl"}])
        self.assertEqual(end["status"], "success")
        self.assertEqual(end["result"], "Hata `main` içinde, düzelttim.")
        self.assertEqual(end["usage"], {"inTok": 12, "outTok": 160, "cacheRead": 30000, "cacheWrite": 2100,
                                        "thinkTok": None, "estimated": False})
        self.assertAlmostEqual(end["cost"], 0.0421)
        self.assertEqual(end["basis"], "claude-estimate")
        self.assertEqual(end["turns"], 3)
        self.assertEqual(self.eng.last_init["capabilities"], ["interrupt_receipt_v1", "future_capability_xyz"])
        self.assertEqual(ev.parts[0], {"k": "note", "v": "Model: claude-sonnet-4-5-20250929"})
        self.assertEqual("".join(p["v"] for p in ev.kinds("text")), "Dosyayı okuyorum.Hata `main` içinde, düzelttim.")
        self.assertEqual(ev.kinds("tool"), [{"k": "tool", "name": "Read", "v": "src/app.py"},
                                            {"k": "tool", "name": "Grep", "v": "TODO"}])
        self.assertEqual(ev.kinds("tool_done"), [{"k": "tool_done", "name": "Read", "ok": True},
                                                 {"k": "tool_done", "name": "Grep", "ok": True}])
        self.assertNotIn("CANARY-TOOL-RESULT", repr(ev.parts))          # tool result content is never forwarded
        self.assertEqual(ev.offices[0], ("thinking", None))
        self.assertIn(("working", "Read"), ev.offices)
        self.assertIn(("working", "Grep"), ev.offices)
        # live counters are approximate (msg_01B never got a message_delta, so its placeholder 1 stays)
        self.assertEqual(ev.lives[-1], {"inTok": 12, "outTok": 161, "cacheRead": 30000, "cacheWrite": 2100})
        c = F.calls(self.fake_dir)[0]
        self.assertEqual(c["stdin"], "Hatayı düzelt PROMPT-CANARY")
        self.assertNotIn("PROMPT-CANARY", " ".join(c["argv"]))
        self.assertEqual(c["cwd"], os.path.realpath(self.ws))
        self.assertEqual(c["disable"], "1")
        self.assertEqual(c["office_env"], ["PIXEL_OFFICE_DISABLE"])
        sid = c["argv"][c["argv"].index("--session-id") + 1]
        self.assertEqual(str(uuid.UUID(sid)), sid)
        self.assertTrue(self.agent["claudeSessionStarted"])
        self.assertEqual(self.agent["claudeSession"], sid)

    def test_resume_uses_delta_of_cumulative_totals(self):
        self.run_task([{"fixture": "claude_stream_success_tools.jsonl"}])
        sid = self.agent["claudeSession"]
        ev, end, run = self.run_task([{"fixture": "claude_stream_resume.jsonl"}])
        argv = F.calls(self.fake_dir)[0]["argv"]
        self.assertEqual(argv[argv.index("--resume") + 1], sid)
        self.assertNotIn("--session-id", argv)
        self.assertEqual(end["usage"]["inTok"], 5)
        self.assertEqual(end["usage"]["outTok"], 40)
        self.assertEqual(end["usage"]["cacheRead"], 32000)
        self.assertEqual(end["usage"]["cacheWrite"], 300)
        self.assertAlmostEqual(end["cost"], 0.01, places=6)
        self.assertEqual(self.agent["claudeTotals"]["costUsd"], 0.0521)
        self.assertEqual(ev.kinds("tool")[0], {"k": "tool", "name": "Write", "v": "README.md"})

    def test_fresh_starts_new_session(self):
        self.run_task([{"fixture": "claude_stream_success_tools.jsonl"}])
        old = self.agent["claudeSession"]
        ev, end, run = self.run_task([{"fixture": "claude_stream_success_tools.jsonl"}], fresh=True)
        argv = F.calls(self.fake_dir)[0]["argv"]
        self.assertIn("--session-id", argv)
        self.assertNotEqual(argv[argv.index("--session-id") + 1], old)
        self.assertAlmostEqual(end["cost"], 0.0421)          # fresh session: no delta against the old totals

    def test_resume_missing_session_retries_once(self):
        self.agent.update(claudeSession=str(uuid.uuid4()), claudeSessionStarted=True)
        ev, end, run = self.run_task([
            {"stderr": "No conversation found with session ID: x", "exit": 1},
            {"fixture": "claude_stream_success_tools.jsonl"}])
        cs = F.calls(self.fake_dir)
        self.assertEqual(len(cs), 2)
        self.assertIn("--resume", cs[0]["argv"])
        self.assertIn("--session-id", cs[1]["argv"])
        self.assertIn({"k": "note", "v": "Yeni oturum başlatıldı"}, ev.parts)
        self.assertEqual(end["status"], "success")

    def test_permission_denied(self):
        ev, end, run = self.run_task([{"fixture": "claude_stream_permission_denied.jsonl"}])
        self.assertEqual(end["denials"], 1)
        d = ev.kinds("denied")[0]
        self.assertEqual(d["name"], "Bash")
        self.assertIn("don't ask mode", d["v"])
        self.assertEqual(ev.kinds("tool")[0]["v"], "npm test -- --watch=false")
        self.assertEqual(ev.kinds("tool_done")[0], {"k": "tool_done", "name": "Bash", "ok": False})

    def test_rate_limit_and_retry(self):
        ev, end, run = self.run_task([{"fixture": "claude_stream_rate_limit.jsonl"}])
        self.assertEqual(ev.kinds("retry"), [{"k": "retry", "attempt": 1, "waitMs": 500, "v": "overloaded"}])
        self.assertEqual(self.limits[0]["status"], "allowed_warning")
        self.assertEqual(self.limits[0]["utilization"], 0.87)
        self.assertEqual(end["status"], "success")

    def test_rejected_by_subscription_limit(self):
        ev, end, run = self.run_task([{"fixture": "claude_stream_rejected.jsonl"}])
        self.assertTrue(any(p["v"].startswith("Claude kullanım sınırına ulaşıldı") for p in ev.kinds("note")))
        self.assertEqual(end["status"], "error")
        self.assertTrue(end["usage"]["estimated"])          # zeroed error_during_execution → live estimate
        self.assertIsNone(end["usage"]["outTok"])
        self.assertIsNone(end["cost"])

    def test_max_budget_and_max_turns(self):
        ev, end, run = self.run_task([{"fixture": "claude_stream_max_budget.jsonl"}], budget=0.5)
        argv = F.calls(self.fake_dir)[0]["argv"]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.50")
        self.assertEqual(end["status"], "budget")
        self.assertEqual(end["error"]["code"], "budget_exceeded")
        self.assertAlmostEqual(end["cost"], 0.51)
        ev, end, run = self.run_task([{"fixture": "claude_stream_max_turns.jsonl"}])
        self.assertEqual((end["status"], end["error"]["code"]), ("error", "max_turns"))

    def test_crash_without_result(self):
        ev, end, run = self.run_task([{"fixture": "claude_stream_crash.jsonl", "stderr": "Error: boom", "exit": 1}])
        self.assertEqual(end["status"], "error")
        self.assertEqual(end["error"], {"code": "engine_exit", "message": "Error: boom"})
        self.assertTrue(end["usage"]["estimated"])
        self.assertEqual(end["usage"]["inTok"], 3)
        self.assertIsNone(end["usage"]["outTok"])

    def test_garbage_lines_are_skipped(self):
        ev, end, run = self.run_task([{"fixture": "claude_stream_garbage.jsonl"}])
        self.assertEqual(end["status"], "success")
        self.assertEqual(end["result"], "Sağlam.")

    def test_subagent_text_not_streamed_but_costed(self):
        ev, end, run = self.run_task([{"fixture": "claude_stream_subagent.jsonl"}])
        self.assertNotIn("ALT-AJAN-METNI", "".join(p.get("v", "") for p in ev.parts))
        self.assertEqual(ev.kinds("tool"), [{"k": "tool", "name": "Task", "v": "Kodu tabanını tara".replace("Kodu ", "Kod ")}])
        self.assertTrue(all(l["inTok"] < 999 for l in ev.lives))      # subagent message_start not counted live
        self.assertEqual(end["usage"]["inTok"], 7 + 999)                # but modelUsage includes it
        self.assertAlmostEqual(end["cost"], 0.03)

    def test_cancel_sigint(self):
        ev, end, run, took = self.run_task([{"fixture": "claude_stream_slow.jsonl", "delay": 0.05}], cancel_after=0.8)
        self.assertEqual(end["status"], "cancelled")
        self.assertLess(took, 3)
        self.assertEqual(run.canceller.sent, ["INT"])
        self.assertTrue(ev.kinds("text"))

    def test_cancel_escalates_to_term_and_kill(self):
        self.cfg["cancelGraceS"] = 0.3
        self.cfg["cancelTermS"] = 0.3
        ev, end, run, took = self.run_task([{"fixture": "claude_stream_slow.jsonl", "delay": 0.05,
                                             "ignore_int": True}], cancel_after=0.5)
        self.assertEqual(end["status"], "cancelled")
        self.assertEqual(run.canceller.sent, ["INT", "TERM"])
        ev, end, run, took = self.run_task([{"fixture": "claude_stream_slow.jsonl", "delay": 0.05, "ignore_int": True,
                                             "ignore_term": True}], cancel_after=0.5)
        self.assertEqual(end["status"], "cancelled")
        self.assertEqual(run.canceller.sent, ["INT", "TERM", "KILL"])
        self.assertLess(took, 15)


if __name__ == "__main__":
    unittest.main()
