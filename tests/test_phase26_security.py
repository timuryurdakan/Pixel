"""v4 security review S26 (docs/SECURITY_AUDIT.md §12, 2026-10-26). Written first, confirmed failing before the fixes.

S26-02  Revoking a paired browser did not stop work it had already queued in the v4 features: a workflow it started kept
        starting its next steps (up to 8 tasks, up to 4 h with a user template), and a pending handoff still started B's
        task when A ended. Before v4 a revoked browser could not cause any *new* task to start. Now `peer.revoke` ends
        that browser's active workflows (`cancelled`, code `revoked`), a run never advances for a revoked browser, and a
        handoff of a revoked browser's task fails with `not_allowed`.
S26-03  `Workspaces.ensure_sub` checked each created component with `lstat` and then created the next one by path. A
        local process that swapped a checked component for a symlink in between made the connector create the rest of
        the path outside the workspace (the final `resolve()` refused it, but the folders were already made). On POSIX
        the components are now created and opened relative to directory descriptors with O_NOFOLLOW.

2026-10-26, connector 3.5.0 (user decision on the two open items):
S26-04  `workflow.start.maxCostUsd` could raise the run's cost cap above the template's `guardrails.maxCostUsd` (up to
        20 $). The template value is now a hard ceiling: a browser may only lower it; a higher value is `bad_request`
        (no silent clamp).
S26-08  Revoking a paired browser left its ordinary tasks alive: queued ones and ones waiting for the local confirmation
        still started later, a running one ran to the end. `peer.revoke` now cancels every unfinished task that browser
        started (`task.end` status `cancelled`, error code `revoked`; audit `task.cancel` with `reason: revoked`).
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import _helpers  # noqa: F401  (sys.path)
from test_connector_tasks import Base
from test_v4_workflows import FlowBase

from pixel_connector import workflows as W
from pixel_connector import workspaces as WS
from pixel_connector.workspaces import WorkspaceDenied, Workspaces


def wait_for(pred, timeout=15.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("%s not reached" % what)


class RevokedBrowserWorkflowTests(FlowBase):
    def test_revoking_the_starting_browser_ends_its_workflow(self):
        self.llm.words = ["kelime "] * 200
        self.llm.delay = 0.03
        rid = self.started()
        self.wait_run(rid, lambda r: self.step(r, "plan")["state"] == "running" and self.step(r, "plan")["task"])
        self.assertTrue(self.conn.revoke(self.br.bid, by="local"))
        run = self.wait_run(rid, lambda r: r["state"] not in ("waiting", "running", "approval"), what="run end")
        self.assertEqual((run["state"], run["code"]), ("cancelled", "revoked"), run)
        self.assertEqual(self.step(run, "yaz")["state"], "cancelled")
        time.sleep(0.3)
        self.assertEqual(len(self.chat_inputs()), 1)                   # the next step never started

    def test_a_revoked_browsers_run_never_advances(self):
        """Even if the revoke notification raced with a step's end, the next step checks the starting browser."""
        self.llm.words = ["kelime "] * 40
        self.llm.delay = 0.03
        rid = self.started()
        self.wait_run(rid, lambda r: self.step(r, "plan")["state"] == "running" and self.step(r, "plan")["task"])
        with mock.patch.object(self.conn.workflows, "peer_revoked", lambda bid: None, create=True), \
                mock.patch.object(self.conn.scheduler, "cancel_for_bid", lambda bid, code: [], create=True):
            self.conn.revoke(self.br.bid, by="local")          # both S26-02 and S26-08 notifications lost the race
        run = self.wait_run(rid, lambda r: r["state"] not in ("waiting", "running", "approval"), what="run end")
        self.assertEqual((run["state"], run["code"]), ("cancelled", "revoked"), run)
        self.assertEqual(self.step(run, "plan")["state"], "done")
        self.assertEqual(len(self.chat_inputs()), 1)


    def test_step_task_cancelled_by_the_revoke_ends_the_run_as_revoked(self):
        """S26-08 alone (the workflow notification lost the race): the step's task ends `cancelled/revoked` and the
        run takes that code instead of a plain user cancel."""
        self.llm.words = ["kelime "] * 200
        self.llm.delay = 0.03
        rid = self.started()
        self.wait_run(rid, lambda r: self.step(r, "plan")["state"] == "running" and self.step(r, "plan")["task"])
        with mock.patch.object(self.conn.workflows, "peer_revoked", lambda bid: None, create=True):
            self.conn.revoke(self.br.bid, by="local")
        run = self.wait_run(rid, lambda r: r["state"] not in ("waiting", "running", "approval"), what="run end")
        self.assertEqual((run["state"], run["code"]), ("cancelled", "revoked"), run)
        self.assertEqual((self.step(run, "plan")["state"], self.step(run, "plan")["code"]), ("cancelled", "revoked"))
        self.assertEqual(len(self.chat_inputs()), 1)


class RevokedBrowserHandoffTests(FlowBase):
    def test_handoff_of_a_revoked_browsers_task_does_not_start_b(self):
        """Defence in depth behind S26-08: even if A's task survived the revoke (the cancel raced with A's end), the
        handoff checks the browser again. The S26-08 cancel is switched off here to reach that check."""
        self.llm.words = ["kelime "] * 40
        self.llm.delay = 0.03
        a = self.create(name="Mimar")
        b = self.create(name="Yazar")
        ack = self.req({"type": "task.start", "agent": a, "prompt": "Plan yap", "handoff": {"agent": b}})
        self.assertEqual(ack["type"], "task.ack", ack)
        self.br.next_msg(lambda m: m.get("type") == "task.chunk" and m["task"] == ack["task"])
        with mock.patch.object(self.conn.scheduler, "cancel_for_bid", lambda bid, code: [], create=True):
            self.assertTrue(self.conn.revoke(self.br.bid, by="local"))
        rec = wait_for(lambda: [r for r in self.conn.audit.read() if r["ev"] == "task.handoff"], what="handoff record")
        self.assertEqual((rec[0]["result"], rec[0].get("next")), ("not_allowed", None), rec)
        time.sleep(0.3)
        self.assertEqual(len(self.chat_inputs()), 1)                   # B never ran


class TemplateCostCeilingTests(unittest.TestCase):
    """S26-04: the template's guardrails.maxCostUsd is a ceiling for workflow.start, not a default."""

    def setUp(self):
        self.t = W.validate_template(W.BUILTIN_TEMPLATES[0], False)          # maxCostUsd 1.0

    def parse(self, **kw):
        msg = {"type": "workflow.start", "template": "statik-site", "workspace": {"id": "isler", "sub": "site"},
               "brief": "Kafe sitesi"}
        msg.update(kw)
        return W.parse_start(msg, self.t)

    def test_lower_or_equal_is_accepted(self):
        self.assertEqual(self.t["guardrails"]["maxCostUsd"], 1.0)
        self.assertEqual(self.parse()["maxCostUsd"], 1.0)
        self.assertEqual(self.parse(maxCostUsd=1.0)["maxCostUsd"], 1.0)
        self.assertEqual(self.parse(maxCostUsd=1)["maxCostUsd"], 1.0)
        self.assertEqual(self.parse(maxCostUsd=0.01)["maxCostUsd"], 0.01)

    def test_above_the_template_is_refused_not_clamped(self):
        for v in (1.01, 1.5, 20, 20.0):
            with self.subTest(v=v):
                with self.assertRaises(W.WorkflowError) as cm:
                    self.parse(maxCostUsd=v)
                self.assertEqual(cm.exception.code, "bad_request")

    def test_user_template_ceiling(self):
        t = W.validate_template({"id": "ucuz", "name": "Ucuz", "guardrails": {"maxCostUsd": 0.25},
                                 "steps": [{"id": "a", "role": "Tek", "engine": "claude", "prompt": "x"}]}, False)
        msg = {"type": "workflow.start", "template": "ucuz", "workspace": {"id": "isler", "sub": None}, "brief": ""}
        self.assertEqual(W.parse_start(dict(msg, maxCostUsd=0.25), t)["maxCostUsd"], 0.25)
        with self.assertRaises(W.WorkflowError) as cm:
            W.parse_start(dict(msg, maxCostUsd=0.26), t)
        self.assertEqual(cm.exception.code, "bad_request")


class TemplateCostCeilingFlowTests(FlowBase):
    def test_workflow_start_above_the_template_is_bad_request(self):
        r = self.start_wf(maxCostUsd=1.5)                                    # template() has maxCostUsd 1.0
        self.assertEqual((r["type"], r.get("code")), ("error", "bad_request"), r)
        self.assertEqual(self.conn.workflows.runs, {})
        self.assertEqual(self.chat_inputs(), [])


class RevokedBrowserTaskTests(Base):
    """S26-08: `peer.revoke` cancels the revoked browser's unfinished ordinary tasks."""

    def setUp(self):
        super().setUp()
        self.conn.scheduler.limits["tasksPerMinute"] = 60

    def audit(self, ev):
        return [r for r in self.conn.audit.read() if r["ev"] == ev]

    def end_record(self, task):
        rec = wait_for(lambda: [r for r in self.audit("task.end") if r["task"] == task], what="task.end " + task)
        return rec[0]

    def chat_inputs(self):
        return [r["body"]["messages"][-1]["content"] for r in self.llm.requests if r["path"] == "/api/chat"]

    def test_running_and_queued_tasks_are_cancelled(self):
        self.llm.words = ["kelime "] * 400
        self.llm.delay = 0.03
        key = self.create()
        run = self.req({"type": "task.start", "agent": key, "prompt": "uzun iş"})
        self.br.next_msg(lambda m: m.get("type") == "task.chunk" and m["task"] == run["task"])
        q = self.req({"type": "task.start", "agent": key, "prompt": "sıradaki iş"})
        self.assertEqual((q["type"], q["queued"]), ("task.ack", True), q)
        self.assertTrue(self.conn.revoke(self.br.bid, by="local"))
        for t in (run["task"], q["task"]):
            rec = self.end_record(t)
            self.assertEqual((rec["status"], rec.get("code")), ("cancelled", "revoked"), rec)
        cancels = {r["task"]: r for r in self.audit("task.cancel")}
        self.assertEqual(set(cancels), {run["task"], q["task"]})
        self.assertEqual({r.get("reason") for r in cancels.values()}, {"revoked"})
        wait_for(lambda: not self.conn.scheduler.runs, what="scheduler empty")
        time.sleep(0.3)
        self.assertEqual(len(self.chat_inputs()), 1)                   # the queued task never reached the engine

    def test_other_browsers_and_local_tasks_are_untouched(self):
        key = self.create()
        self.llm.words = ["kelime "] * 200
        self.llm.delay = 0.02
        local = self.conn.scheduler.submit(self.conn.agents.get(key), "yerel iş", False, None, "Yerel", False)
        self.assertEqual(self.conn.scheduler.cancel_for_bid("b" * 22, "revoked"), [])
        self.assertTrue(self.conn.revoke(self.br.bid, by="local"))
        rec = self.end_record(local["task"])
        self.assertEqual(rec["status"], "success", rec)


class RevokedBrowserConfirmTests(Base):
    connector_overrides = {"confirm": {"task": "always", "agentCreate": False, "runtime": False, "peerRevoke": False,
                                       "timeoutS": 120}}

    def test_task_waiting_for_local_confirmation_is_cancelled(self):
        key = self.create()
        ack = self.req({"type": "task.start", "agent": key, "prompt": "onay bekleyen iş"})
        self.assertEqual(ack["confirm"], "pending")
        self.assertEqual(len([p for p in self.conn.approvals.list() if p["kind"] == "task"]), 1)
        self.assertTrue(self.conn.revoke(self.br.bid, by="local"))
        rec = wait_for(lambda: [r for r in self.conn.audit.read() if r["ev"] == "task.end" and r["task"] == ack["task"]],
                       what="task.end")[0]
        self.assertEqual((rec["status"], rec.get("code")), ("cancelled", "revoked"), rec)
        self.assertEqual([p for p in self.conn.approvals.list() if p["kind"] == "task"], [])   # card gone
        self.assertEqual(self.conn.scheduler.runs, {})
        self.assertEqual([r for r in self.llm.requests if r["path"] == "/api/chat"], [])


class EnsureSubRaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.base = os.path.join(self.home, "PixelOfficeIsler")
        os.makedirs(os.path.join(self.base, "a"))
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.outside)
        self.ws = Workspaces([{"id": "isler", "name": "İşler", "path": "~/PixelOfficeIsler", "children": True}],
                             self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _swap(self):
        a = os.path.join(os.path.realpath(self.base), "a")
        os.rename(a, a + "-asil")
        os.symlink(self.outside, a)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_component_swapped_for_a_symlink_after_its_check_creates_nothing_outside(self):
        real_lstat = os.lstat
        target = os.path.join(os.path.realpath(self.base), "a")
        state = {"done": False}

        def racing_lstat(path, *args, **kw):
            st = real_lstat(path, *args, **kw)
            if not state["done"] and os.fspath(path) == target:
                state["done"] = True
                self._swap()                    # the attacker wins the race right after the check
            return st

        with mock.patch.object(WS.os, "lstat", racing_lstat):
            try:
                self.ws.ensure_sub("isler", "a/b/c")
            except WorkspaceDenied:
                pass
        self.assertEqual(os.listdir(self.outside), [])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_and_file_components_are_refused(self):
        os.symlink(self.outside, os.path.join(self.base, "kacis"))
        with self.assertRaises(WorkspaceDenied):
            self.ws.ensure_sub("isler", "kacis/x")
        with open(os.path.join(self.base, "dosya"), "w") as f:
            f.write("x")
        with self.assertRaises(WorkspaceDenied):
            self.ws.ensure_sub("isler", "dosya/x")
        self.assertEqual(os.listdir(self.outside), [])
        p = self.ws.ensure_sub("isler", "yeni/alt")
        self.assertTrue(os.path.isdir(p))
        self.assertEqual(os.stat(os.path.join(self.base, "yeni")).st_mode & 0o777 & ~0o700, 0)


if __name__ == "__main__":
    unittest.main()
