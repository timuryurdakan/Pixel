"""Patron Odası P7: end-to-end scenarios through the real page (docs/PATRON_ODASI.md §9 P7, §10/2–4).

Real page ⇄ dev_relay ⇄ real connector (temp home) with the routed fake `claude` of test_boss_run (it picks its reply
by the prompt, so parallel orders are deterministic, and logs when each run started and ended). The happy path with
plan → approve → Belge → Test approval → report is test_boss_ui.py; this file adds:

- two non-conflicting orders in parallel (§10/2: at most 2 at once) through the review to the report;
- the plan refused (Reddet): no specialist runs, no role agent is created;
- İptal while an order runs: the order's task is cancelled, the report says the work is partial;
- the cost cap hit by an order: the job stops with `cost_cap`, the next order never starts, the panel says why;
- the browser unpaired mid-job (from the page's own devices page, and revoked on the computer): the job ends as
  `revoked`, the running task is cancelled and that browser no longer gets the panel's actions;
- the Test order's approval answered on the computer while the panel is open (P6 gap): the gate card goes away
  without a reload.

Skipped without Chrome / Chromium. Nothing touches ~/.claude or ~/.pixel-office.
"""

import json
import os
import sys
import time
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
import _v3_fakes as F
import test_boss_ui as UI
from _cdp import CHROME, Chrome
from test_boss_run import ROUTED_CLAUDE, order, plan
from test_v3_ui import open_room

from pixel_connector import boss as B

NONCE = UI.NONCE
REPORT = "Rapor E2E-41c7"
vis = UI.vis

ORDERS_RUNNING = "document.querySelectorAll('#boss-job .boss-order.st-running').length"
JOB_STATE = "(document.querySelector('#boss-job .boss-card') || {}).className || ''"
END_NOTE = ("[...document.querySelectorAll('#boss-job .wf-run-note')].map(e => e.textContent)"
            ".find(t => t.startsWith('İş durdu') || t.startsWith('İş bitti')) || ''")
PANEL_ACTIONS = ("[...document.querySelectorAll('#dlg-boss button')].filter(b => b.getClientRects().length"
                 " && !b.closest('[hidden]') && !b.matches('.dlg-head .close'))"
                 ".map(b => b.textContent.trim())")


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class BossE2E(unittest.TestCase):
    # The page helpers of the P6 happy path, shared rather than copied.
    pair = UI.BossPanelUi.pair
    open_boss = UI.BossPanelUi.open_boss
    fixture = UI.BossPanelUi.fixture

    def setUp(self):
        self.env = E.V3Env()
        self.fake_dir = os.path.join(self.env.tmp, "fakeclaude")
        claude = F.write_fake_claude(self.fake_dir)
        with open(claude, "w", encoding="utf-8") as f:
            f.write(ROUTED_CLAUDE.replace("PYTHON", sys.executable, 1).replace("FIXTURES", repr(str(F.FIXTURES)), 1))
        os.environ["CLAUDE_FAKE_DIR"] = self.fake_dir
        self.routes = []
        self._save_routes()
        self.home = self.env.connector_home("dev", engines={"claude": {"path": claude, "enabled": True}},
                                            limits={"tasksPerMinute": 60})
        self.conn = self.env.connector(self.home)
        self.conn.boss.nonce = lambda: NONCE
        self.gets = []                                  # every boss.get the connector answered
        orig_get = self.conn.h_boss_get
        self.conn.h_boss_get = lambda peer, msg: self.gets.append(msg) or orig_get(peer, msg)
        self.fx = os.path.join(self.env.tmp, "bossfx")
        os.makedirs(self.fx, exist_ok=True)
        self._n = 0

    def tearDown(self):
        self.conn.stop()
        self.env.stop()
        os.environ.pop("CLAUDE_FAKE_DIR", None)

    # -- the routed fake Claude --------------------------------------------------------------------------------------
    def _save_routes(self):
        with open(os.path.join(self.fake_dir, "routes.json"), "w", encoding="utf-8") as f:
            json.dump(self.routes, f, ensure_ascii=False)

    def route(self, name, match, text, cost=0.05, delay=0.0, hang=0.0):
        self.routes.append({"name": name, "match": match, "steps": [
            {"fixture": self.fixture(text, cost)["fixture"], "delay": delay, "hang": hang}]})
        self._save_routes()

    def plan_route(self, p):
        start, end = B.plan_markers(NONCE)
        self.route("plan", B.PLAN_HEAD, "Projeyi okudum.\n%s\n%s\n%s\n" % (start, json.dumps(p, ensure_ascii=False), end))

    def order_route(self, oid, title, text=None, **kw):
        self.route(oid, "İş emri: " + title, text or "%s bitti." % oid, **kw)

    def review_route(self):
        r_start, r_end = B.review_markers(NONCE)
        self.route("review", B.REVIEW_HEAD, "%s\n%s\n%s\n%s\n" % (REPORT, r_start,
                                                                json.dumps({"v": 1, "decision": "accept"}), r_end))

    def names(self):
        return [c["name"] for c in F.calls(self.fake_dir)]

    def spans(self):
        ends = {}
        try:
            with open(os.path.join(self.fake_dir, "ends.jsonl"), encoding="utf-8") as f:
                for line in f:
                    e = json.loads(line)
                    ends[e["pid"]] = e
        except FileNotFoundError:
            pass
        return {c["name"]: (c["t0"], ends[c["pid"]]["t1"], ends[c["pid"]].get("signal", False))
                for c in F.calls(self.fake_dir) if c["pid"] in ends}

    def specialists(self):
        return [a for a in self.conn.agents.list() if a.get(B.SPECIALIST_FIELD)]

    def job(self):
        return self.conn.boss.get(None)

    def wait_conn(self, pred, timeout=20.0, what="connector state"):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return
            time.sleep(0.05)
        self.fail("%s not reached: %s" % (what, json.dumps(self.job()["job"], ensure_ascii=False)))

    # -- page flow ---------------------------------------------------------------------------------------------------
    def paired_page(self, chrome):
        A = chrome.page(width=1280, height=900)
        open_room(A, self.env.base, self.env.room, self.env.key)
        A.wait("!!document.querySelector('#btn-devices:not([hidden])')", 15)
        self.pair(A)
        A.wait("!!window.pixelOffice.v3.bossStore && window.pixelOffice.v3.bossStore.loaded.size === 1", 15,
               what="boss.get after connecting")
        return A

    def ask(self, A, brief="İki iş emri", cost="0.50"):
        self.open_boss(A)
        A.wait(vis("#boss-form") + " && document.querySelector('#boss-form [name=workspace]').options.length > 0",
               10, what="form")
        A.type("#boss-form [name=brief]", brief)
        A.eval("document.querySelector('#boss-form [name=cost]').value = %s" % json.dumps(cost))
        A.click("#boss-form [name=ask]")
        A.wait("!!document.querySelector('#boss-job .boss-plan .boss-approve')", 30, what="plan card")

    def approve(self, A):
        A.click("#boss-job .boss-approve")

    def report_text(self, A, timeout=40):
        A.wait("!!document.querySelector('#boss-job .boss-report')", timeout, what="report")
        return A.text("#boss-job .boss-report")

    def end_note(self, A):
        return A.eval(END_NOTE)

    # -- scenarios ---------------------------------------------------------------------------------------------------
    def test_two_orders_in_parallel_to_review_and_report(self):
        self.plan_route(plan(order("o1", "frontend", "Renkler", ["web/a.css"]),
                             order("o2", "docs", "Belgeler", ["README.md"]), summary="İki paralel iş"))
        self.order_route("o1", "Renkler", hang=2.0)
        self.order_route("o2", "Belgeler", hang=2.0)
        self.review_route()
        with Chrome() as chrome:
            A = self.paired_page(chrome)
            self.ask(A)
            self.approve(A)
            A.wait(ORDERS_RUNNING + " === 2", 20, what="both orders running at once in the panel")
            # The Boss on stage follows the panel's store (P7: one boss.get per connection, not two).
            A.wait("(j => !!j && j.state === 'running')(window.pixelOffice.bossScene.job())", 5, what="scene job")
            self.assertEqual(len(self.gets), 1)
            self.assertEqual(len(self.specialists()), 2)
            report = self.report_text(A)
            self.assertIn(REPORT, report)
            self.assertIn("Bağlayıcının özeti:", report)
            self.assertFalse(A.eval("/iş yarım kaldı/.test(document.querySelector('#boss-job').textContent)"))
            self.assertEqual(self.end_note(A), "İş bitti.")
            self.assertTrue(A.eval(vis("#boss-form")))
            self.assertEqual(A.errors, [])
        self.assertEqual(sorted(self.names()), ["o1", "o2", "plan", "review"])
        sp = self.spans()
        (a0, a1, _), (b0, b1, _) = sp["o1"], sp["o2"]
        self.assertTrue(a0 < b1 and b0 < a1, "the two orders did not overlap: %r" % sp)
        self.assertGreater(sp["review"][0], max(a1, b1))                    # the review after both
        got = self.job()
        self.assertEqual((got["job"]["state"], got["report"]["partial"]), ("done", False))
        self.assertEqual([o["state"] for o in got["job"]["orderStates"]], ["done", "done"])

    def test_plan_refused_runs_no_specialist(self):
        self.plan_route(plan(order("o1", "docs", "Belgeler", ["README.md"])))
        self.order_route("o1", "Belgeler")
        with Chrome() as chrome:
            A = self.paired_page(chrome)
            self.ask(A)
            A.click("#boss-job .boss-deny")
            A.wait("/st-cancelled/.test(%s)" % JOB_STATE, 10, what="job cancelled")
            self.assertEqual(self.end_note(A), "İş durdu: Reddedildi.")
            self.assertFalse(A.eval("!!document.querySelector('#boss-job .boss-approve, #boss-job .boss-cancel')"))
            self.assertTrue(A.eval(vis("#boss-form")))                    # a new job can be given
            self.assertEqual(A.errors, [])
        time.sleep(0.5)
        self.assertEqual(self.names(), ["plan"])
        self.assertEqual(self.specialists(), [])
        got = self.job()["job"]
        self.assertEqual((got["state"], got["code"], got["run"]), ("cancelled", "denied", None))

    def test_cancel_mid_run_stops_the_order_and_reports_partial(self):
        self.plan_route(plan(order("o1", "docs", "Belgeler", ["README.md"]),
                             order("o2", "frontend", "Renkler", ["web/a.css"], ["o1"])))
        self.order_route("o1", "Belgeler", hang=30.0)
        self.order_route("o2", "Renkler")
        self.review_route()
        with Chrome() as chrome:
            A = self.paired_page(chrome)
            self.ask(A)
            self.approve(A)
            A.wait(ORDERS_RUNNING + " === 1", 20, what="o1 running")
            A.click("#boss-job .boss-cancel")
            A.wait("/st-cancelled/.test(%s)" % JOB_STATE, 10, what="job cancelled")
            self.assertEqual(self.end_note(A), "İş durdu: İptal edildi.")
            report = self.report_text(A, 20)
            self.assertIn("Rapor (iş yarım kaldı)", A.text("#boss-job"))
            self.assertIn("Bağlayıcının özeti:", report)
            self.assertFalse(A.eval("!!document.querySelector('#boss-job .boss-cancel')"))
            self.assertTrue(A.eval(vis("#boss-form")))
            self.assertEqual(A.errors, [])
        self.assertEqual(self.names(), ["plan", "o1"])                        # neither o2 nor the review ran
        self.assertTrue(self.spans()["o1"][2], "o1's run was not interrupted")
        got = self.job()
        self.assertEqual((got["job"]["state"], got["job"]["code"], got["report"]["partial"]),
                         ("cancelled", "cancelled", True))

    def test_cost_cap_stops_the_job_with_its_code(self):
        self.plan_route(plan(order("o1", "docs", "Belgeler", ["README.md"]),
                             order("o2", "frontend", "Renkler", ["web/a.css"], ["o1"])))
        self.order_route("o1", "Belgeler", cost=0.60)                    # the run has 0.50 − 0.05 planning left
        self.order_route("o2", "Renkler")
        self.review_route()
        with Chrome() as chrome:
            A = self.paired_page(chrome)
            self.ask(A, cost="0.50")
            self.approve(A)
            A.wait("/st-error/.test(%s)" % JOB_STATE, 20, what="job stopped")
            self.assertEqual(self.end_note(A), "İş durdu: Maliyet tavanı doldu.")
            self.report_text(A, 20)
            self.assertIn("Rapor (iş yarım kaldı)", A.text("#boss-job"))
            self.assertEqual(A.errors, [])
        self.assertEqual(self.names(), ["plan", "o1"])                        # o2 never started, no review
        got = self.job()
        self.assertEqual((got["job"]["state"], got["job"]["code"], got["report"]["partial"]), ("error", "cost_cap", True))
        self.assertEqual(got["report"]["code"], "cost_cap")

    def test_unpairing_the_page_mid_job_revokes_it_and_removes_the_actions(self):
        self.plan_route(plan(order("o1", "docs", "Belgeler", ["README.md"])))
        self.order_route("o1", "Belgeler", hang=30.0)
        with Chrome() as chrome:
            A = self.paired_page(chrome)
            self.ask(A)
            self.approve(A)
            A.wait(ORDERS_RUNNING + " === 1", 20, what="o1 running")
            A.eval("document.getElementById('dlg-boss').close()")
            A.click("#btn-devices")
            A.wait("!!document.querySelector('.dev-card .dev-foot .btn')")
            A.click(".dev-card .dev-foot .btn")                        # window.confirm is auto-accepted
            A.wait("/eşleşmedi/.test(document.querySelector('#devices-page .dev-body').textContent)", 20,
                   what="unpaired")
            A.click("[data-dev-close]")
            self.wait_conn(lambda: self.job()["job"]["state"] == "cancelled", what="job revoked")
            self.open_boss(A)
            self.assertEqual(A.text("#boss-info"), UI.BOSS_INFO_UNPAIRED)
            self.assertFalse(A.eval(vis("#boss-form")))
            self.assertFalse(A.eval(vis("#boss-job")))
            self.assertEqual(A.eval(PANEL_ACTIONS), [])
            self.assertFalse(A.eval("window.pixelOffice.renderer.boardData.waiting.some(c => c.target === 'boss')"))
            self.assertEqual(A.errors, [])
        self.assertEqual(self.job()["job"]["code"], "revoked")
        self.wait_conn(lambda: "o1" in self.spans(), 10, what="o1 ended")
        self.assertTrue(self.spans()["o1"][2], "o1's run was not interrupted")
        self.assertEqual(self.names(), ["plan", "o1"])

    def test_revoked_on_the_computer_mid_job(self):
        self.plan_route(plan(order("o1", "docs", "Belgeler", ["README.md"])))
        self.order_route("o1", "Belgeler", hang=30.0)
        with Chrome() as chrome:
            A = self.paired_page(chrome)
            self.ask(A)
            self.approve(A)
            A.wait(ORDERS_RUNNING + " === 1", 20, what="o1 running")
            bid = self.conn.peers.public_list()[0]["bid"]
            self.assertTrue(self.conn.revoke(bid))                         # `pixel-connector peers revoke`
            self.wait_conn(lambda: self.job()["job"]["state"] == "cancelled", what="job revoked")
            self.assertEqual(self.job()["job"]["code"], "revoked")
            self.wait_conn(lambda: "o1" in self.spans(), 10, what="o1 ended")
            self.assertTrue(self.spans()["o1"][2], "o1's run was not interrupted")
            # The computer drops the revoked browser's channel without telling it; after a reload the handshake is
            # refused and the panel has no actions any more.
            A.navigate(A.eval("location.href"))
            A.wait("!!(window.pixelOffice.v3 && window.pixelOffice.v3.bossStore)", 20)
            time.sleep(1.5)
            self.open_boss(A)
            A.wait(vis("#boss-info"), 10, what="info text")
            self.assertFalse(A.eval(vis("#boss-form")))
            self.assertFalse(A.eval(vis("#boss-job")))
            self.assertEqual(A.eval(PANEL_ACTIONS), [])
        self.assertEqual(self.names(), ["plan", "o1"])

    def test_test_approval_answered_on_the_computer_updates_the_open_panel(self):
        self.plan_route(plan(order("o1", "docs", "Belgeler", ["README.md"]),
                             order("o2", "test", "Testler", ["tests/test_a.py"], ["o1"])))
        self.order_route("o1", "Belgeler")
        self.order_route("o2", "Testler", hang=4.0)
        self.review_route()
        with Chrome() as chrome:
            A = self.paired_page(chrome)
            self.ask(A)
            self.approve(A)
            A.wait("!!document.querySelector('#boss-job .boss-gate-approve')", 30, what="Test order approval")
            pending = [p for p in self.conn.approvals.list() if p["kind"] == "workflow"]
            self.assertEqual(len(pending), 1)
            self.assertEqual(self.conn.approvals.answer(pending[0]["id"], True, None, by="local"), "ok")
            A.wait("!document.querySelector('#boss-job .boss-gate-approve') && " + ORDERS_RUNNING + " === 1", 3,
                   what="the gate card gone, the Test order running (no reload)")
            self.assertFalse(self.spans().get("o2"), "o2 already ended: the check above proves nothing")
            self.assertIn(REPORT, self.report_text(A))
            self.assertEqual(A.errors, [])
        self.assertEqual(self.names(), ["plan", "o1", "o2", "review"])


if __name__ == "__main__":
    unittest.main()
