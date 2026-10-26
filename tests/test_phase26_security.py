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
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import _helpers  # noqa: F401  (sys.path)
from test_v4_workflows import FlowBase

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
        with mock.patch.object(self.conn.workflows, "peer_revoked", lambda bid: None, create=True):
            self.conn.revoke(self.br.bid, by="local")
        run = self.wait_run(rid, lambda r: r["state"] not in ("waiting", "running", "approval"), what="run end")
        self.assertEqual((run["state"], run["code"]), ("cancelled", "revoked"), run)
        self.assertEqual(self.step(run, "plan")["state"], "done")
        self.assertEqual(len(self.chat_inputs()), 1)


class RevokedBrowserHandoffTests(FlowBase):
    def test_handoff_of_a_revoked_browsers_task_does_not_start_b(self):
        self.llm.words = ["kelime "] * 40
        self.llm.delay = 0.03
        a = self.create(name="Mimar")
        b = self.create(name="Yazar")
        ack = self.req({"type": "task.start", "agent": a, "prompt": "Plan yap", "handoff": {"agent": b}})
        self.assertEqual(ack["type"], "task.ack", ack)
        self.br.next_msg(lambda m: m.get("type") == "task.chunk" and m["task"] == ack["task"])
        self.assertTrue(self.conn.revoke(self.br.bid, by="local"))
        rec = wait_for(lambda: [r for r in self.conn.audit.read() if r["ev"] == "task.handoff"], what="handoff record")
        self.assertEqual((rec[0]["result"], rec[0].get("next")), ("not_allowed", None), rec)
        time.sleep(0.3)
        self.assertEqual(len(self.chat_inputs()), 1)                   # B never ran


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
