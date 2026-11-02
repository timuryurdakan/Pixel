"""F4-6a (docs/F46A_TASARIM.md): the ACP client engine against a fake ACP agent (tests/_fake_acp_agent.py).

A1 config (absolute path, no package runners, off by default), A2 permission answers (never allow_always; readonly
refuses, edit asks the computer), fs/* through E3's folder rules, no terminal, A5 one process per task, A6 our API key
variables never reach the child, A7 line cap, setup timeout and cancel → kill.
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_connector.config import acp_from
from pixel_connector.engines import acp as A
from pixel_connector.engines.base import TaskContext

FAKE = str(Path(__file__).resolve().parent / "_fake_acp_agent.py")


class Events:
    def __init__(self):
        self.parts, self.office_states, self.done = [], [], threading.Event()
        self.end_args = None

    def part(self, p):
        self.parts.append(p)

    def live(self, **kw):
        pass

    def office(self, state, tool=None, label=None):
        self.office_states.append((state, tool))

    def end(self, status, result, usage, cost, basis, turns=None, error=None, denials=0):
        self.end_args = {"status": status, "result": result, "usage": usage, "cost": cost, "basis": basis,
                         "error": error}
        self.done.set()


class Approvals:
    def __init__(self, answer="ok"):
        self.answer, self.asked, self.cancelled = answer, [], []

    def request(self, kind, title, preview, agent, on_done, timeout_s=None):
        self.asked.append({"kind": kind, "title": title, "preview": preview, "timeout": timeout_s})
        p = mock.Mock(id="p%d" % len(self.asked))
        if self.answer is not None:
            threading.Timer(0.05, on_done, args=(self.answer,)).start()
        return p

    def cancel(self, pid):
        self.cancelled.append(pid)


class AcpEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.path.join(self.tmp.name, "isler")
        os.mkdir(self.cwd)
        Path(self.cwd, "a.txt").write_text("bir\niki\nüç\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_task(self, scenario, profile="edit", approvals=None, cancel_after=None, timeout=20):
        eng = A.AcpEngine({"id": "sahte", "name": "Sahte", "command": [sys.executable, FAKE, scenario], "enabled": True},
                          approvals=approvals, drop_env=["PIXEL_TEST_PROVIDER_KEY"])
        ev = Events()
        ctx = TaskContext("t_aaaaaaaaaa", {"key": "a1", "name": "Kodcu"}, "Görev metni", self.cwd, None, profile)
        run = eng.start(ctx, ev)
        if cancel_after is not None:
            threading.Timer(cancel_after, run.cancel).start()
        self.assertTrue(ev.done.wait(timeout), "the task never ended")
        return ev, run

    def test_text_and_tool_calls_reach_the_office(self):
        ev, run = self.run_task("text")
        self.assertEqual(ev.end_args["status"], "success")
        self.assertEqual(ev.end_args["result"], "Merhaba dünya")
        self.assertEqual((ev.end_args["cost"], ev.end_args["basis"]), (None, "unknown"))
        self.assertIn({"k": "tool", "name": "Read", "v": "a.txt"}, ev.parts)     # the path shown relative
        self.assertIn({"k": "tool_done", "name": "Read", "ok": True}, ev.parts)
        self.assertIn(("working", "llm"), ev.office_states)
        run.proc.wait(5)                                                         # A5: the process is gone

    def test_fs_methods_use_the_folder_rules(self):
        ev, _ = self.run_task("fs")
        self.assertEqual(ev.end_args["status"], "success")
        self.assertEqual(ev.end_args["result"], "okundu=iki;yazma=ok;dış=red;dış=red;dış=red;dış=red;")
        self.assertEqual(Path(self.cwd, "alt", "out.txt").read_text(encoding="utf-8"), "yazıldı")
        self.assertFalse(Path(self.tmp.name, "kacak.txt").exists())
        self.assertFalse(Path(self.cwd, ".env").exists())

    def test_readonly_cannot_write_through_fs(self):
        ev, _ = self.run_task("fs", profile="readonly")
        self.assertIn("yazma=read_only;", ev.end_args["result"])
        self.assertFalse(Path(self.cwd, "alt", "out.txt").exists())

    def test_permission_answers_never_allow_always(self):
        cases = [("perm_read", "readonly", "once", 0), ("perm_edit", "readonly", "no", 0),
                 ("perm_outside", "edit", "no", 1), ("perm_edit", "edit", "once", 0),
                 ("perm_execute", "readonly", "no", 0), ("perm_execute", "edit", "once", 1)]
        for scenario, profile, want, asks in cases:
            ap = Approvals("denied" if want == "no" and asks else "ok")
            ev, _ = self.run_task(scenario, profile=profile, approvals=ap)
            self.assertEqual(ev.end_args["result"], "izin=%s" % want, (scenario, profile))
            self.assertEqual(len(ap.asked), asks, (scenario, profile))
        ap = Approvals("ok")
        self.run_task("perm_execute", approvals=ap)
        self.assertEqual(ap.asked[0]["kind"], "acpTool")
        self.assertEqual(ap.asked[0]["timeout"], A.PERMISSION_TIMEOUT_S)
        self.assertIn("ls -la", ap.asked[0]["preview"])

    def test_unanswered_or_missing_approvals_reject(self):
        ev, _ = self.run_task("perm_execute", approvals=Approvals("timeout"))
        self.assertEqual(ev.end_args["result"], "izin=no")
        ev, _ = self.run_task("perm_execute", approvals=None)
        self.assertEqual(ev.end_args["result"], "izin=no")

    def test_no_terminal_and_unknown_methods(self):
        ev, _ = self.run_task("terminal")
        self.assertEqual(ev.end_args["result"], "terminal=%d" % A.ERR_METHOD)

    def test_our_provider_key_never_reaches_the_agent(self):
        with mock.patch.dict(os.environ, {"PIXEL_TEST_PROVIDER_KEY": "sk-CANARY-acp-0123456789"}):
            ev, _ = self.run_task("env")
        self.assertEqual(ev.end_args["result"], "env=YOK")

    def test_bad_agents_end_with_a_code(self):
        for scenario, code in [("garbage", "acp_protocol"), ("big", "acp_protocol"), ("v2", "acp_version"),
                               ("auth", "auth_required"), ("refusal", "refusal")]:
            ev, _ = self.run_task(scenario)
            self.assertEqual(ev.end_args["status"], "error", scenario)
            self.assertEqual(ev.end_args["error"]["code"], code, scenario)
        self.assertEqual(A.TEXT["auth_required"], A.AUTH_TEXT)

    def test_setup_timeout_kills_the_agent(self):
        with mock.patch.object(A, "SETUP_TIMEOUT_S", 0.5):
            ev, run = self.run_task("slow_setup")
        self.assertEqual(ev.end_args["error"]["code"], "acp_timeout")
        self.assertIsNotNone(run.proc.wait(5))

    def test_cancel_politely_then_by_force(self):
        ev, _ = self.run_task("cancel_ok", cancel_after=0.5)
        self.assertEqual(ev.end_args["status"], "cancelled")
        with mock.patch.object(A, "CANCEL_GRACE_S", 0.5):
            ev, run = self.run_task("hang", cancel_after=0.5)
        self.assertEqual(ev.end_args["status"], "cancelled")
        self.assertIsNotNone(run.proc.wait(5))

    def test_inside_means_under_the_folder_not_a_name_prefix(self):
        os.mkdir(self.cwd + "2")
        run = A.AcpRun(A.AcpEngine({"command": [sys.executable], "enabled": True}),
                       TaskContext("t_cccccccccc", {"key": "a1", "name": "K"}, "p", self.cwd, None, "edit"), Events())
        self.assertEqual(run._relative(os.path.join(self.cwd, "alt", "b.txt")), "alt/b.txt")
        for outside in (self.cwd + "2/a.txt", self.cwd, self.cwd + "/../x", "/etc/passwd", "a.txt", 5, None,
                        self.cwd + "/a\0b"):
            self.assertIsNone(run._relative(outside), outside)
        tc = {"kind": "read", "locations": [{"path": self.cwd + "2/a.txt"}]}
        self.assertEqual(A.decide_permission(tc, "readonly", lambda p: run._relative(p) is not None), "reject")

    def test_no_folder_no_task(self):
        eng = A.AcpEngine({"id": "s", "name": "S", "command": [sys.executable, FAKE], "enabled": True})
        ev = Events()
        eng.start(TaskContext("t_bbbbbbbbbb", {"key": "a1", "name": "K"}, "p", None, None, "edit"), ev)
        self.assertTrue(ev.done.wait(5))
        self.assertEqual(ev.end_args["error"]["code"], "workspace_required")


class DecisionTests(unittest.TestCase):
    def test_decide_permission(self):
        inside = lambda p: p.startswith("/ws/")                                   # noqa: E731
        loc = lambda *ps: [{"path": p} for p in ps]                              # noqa: E731
        d = A.decide_permission
        self.assertEqual(d({"kind": "read", "locations": loc("/ws/a")}, "readonly", inside), "allow")
        self.assertEqual(d({"kind": "read", "locations": loc("/ws/a", "/etc/x")}, "readonly", inside), "reject")
        self.assertEqual(d({"kind": "read", "locations": []}, "readonly", inside), "reject")
        self.assertEqual(d({"kind": "edit", "locations": loc("/ws/a")}, "readonly", inside), "reject")
        self.assertEqual(d({"kind": "edit", "locations": loc("/ws/a")}, "edit", inside), "allow")
        self.assertEqual(d({"kind": "move", "locations": loc("/ws/a", "/ws/b")}, "edit", inside), "allow")
        self.assertEqual(d({"kind": "edit", "locations": loc("/etc/x")}, "edit", inside), "ask")
        for kind in ("execute", "fetch", "search", "think", "other", None, 5):
            self.assertEqual(d({"kind": kind, "locations": loc("/ws/a")}, "edit", inside), "ask", kind)
            self.assertEqual(d({"kind": kind, "locations": loc("/ws/a")}, "readonly", inside), "reject", kind)
        self.assertEqual(d({"kind": "read", "locations": "x"}, "edit", inside), "ask")

    def test_pick_option(self):
        opts = [{"optionId": "a", "kind": "allow_always"}, {"optionId": "b", "kind": "allow_once"},
                {"optionId": "c", "kind": "reject_always"}, {"optionId": "d", "kind": "reject_once"}]
        self.assertEqual(A.pick_option(opts, True), "b")
        self.assertEqual(A.pick_option(opts, False), "d")
        self.assertIsNone(A.pick_option([{"optionId": "a", "kind": "allow_always"}], True))
        self.assertEqual(A.pick_option([{"optionId": "c", "kind": "reject_always"}], False), "c")
        self.assertIsNone(A.pick_option("x", True))


class ConfigTests(unittest.TestCase):
    def test_acp_entries(self):
        errs = []
        got = acp_from([{"id": "codex", "name": " Codex ", "command": ["/opt/codex/bin/codex-acp", "--x"]},
                        {"id": "on", "command": ["/usr/bin/agent"], "enabled": True}], errs)
        self.assertEqual(errs, [])
        self.assertEqual(got[0], {"id": "codex", "name": "Codex", "command": ["/opt/codex/bin/codex-acp", "--x"],
                                  "enabled": False})                              # A1: off unless enabled
        self.assertTrue(got[1]["enabled"])
        bad = [{"id": "r", "command": ["codex-acp"]}, {"id": "n", "command": ["/usr/bin/npx", "-y", "@x/acp"]},
               {"id": "u", "command": ["/home/u/.local/bin/uvx", "a"]}, {"id": "w", "command": ["C:/x/npx.cmd"]},
               {"id": "e", "command": []}, {"id": "s", "command": "/usr/bin/agent"},
               {"id": "Büyük", "command": ["/usr/bin/a"]}, {"id": "z", "command": ["/usr/bin/a\0b"]}]
        errs = []
        self.assertEqual(acp_from(bad, errs), [])
        self.assertEqual(len(errs), len(bad))
        errs = []
        acp_from([{"id": "d", "command": ["/a"]}, {"id": "d", "command": ["/b"]}], errs)
        self.assertEqual(errs, ["engines.acp[1]: duplicate id"])
        errs = []
        acp_from([{"id": "x%d" % i, "command": ["/a"]} for i in range(9)], errs)
        self.assertIn("engines.acp: at most 8 agents", errs)

    def test_availability_needs_enabled_and_an_executable(self):
        self.assertFalse(A.AcpEngine({"command": [sys.executable], "enabled": False}).available()["available"])
        self.assertTrue(A.AcpEngine({"command": [sys.executable], "enabled": True}).available()["available"])
        self.assertFalse(A.AcpEngine({"command": ["/yok/boyle/bir/sey"], "enabled": True}).available()["available"])


if __name__ == "__main__":
    unittest.main()
