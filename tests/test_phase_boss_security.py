"""Patron Odası security review S29 (docs/SECURITY_AUDIT.md §13, 2026-10-29). Written first, confirmed failing before
the fixes.

S29-01  A `boss.ask` waiting for the computer's confirmation (`confirm.agentCreate`) still started the planning task
        (up to 0.20 $) when the confirmation was given after the asking browser had been revoked: a revoked browser
        caused new work (S26-02 says it never does). `BossDesk.launch` now refuses a revoked browser (`not_allowed`).
S29-02  A revoke done on the computer (CLI `peers revoke`, `/ctl/peers/revoke`, or another browser) closed the
        revoked browser's channel without telling it: the page kept showing the last Boss panel (brief, plan,
        "Çalışıyor", İptal) until a reload, and its requests timed out. The connector now sends `peer.revoked {bid}`
        to that browser's open channels before closing them; the page forgets the device (tests/js/boss_security).
S29-03  The approval before each Test order (§10/3) said the Test specialist "runs test commands in this folder". The
        test commands run code the specialists wrote (test files, conftest.py, package.json scripts) with the user's
        own rights, not limited to the folder or to the job's caps. The local approval item now carries that warning
        as its preview (the panel says the same, tests/js/boss_security).
S29-04  A Patron or role agent could be a handoff target (`task.start.handoff`): the Test role agent then ran a task
        without its role policy — its stored `dev` profile with the device's dev command list, no approval gate, no
        Agent/Task deny — and the Patron outside the job's caps in its own session. The handoff target is now checked
        like `task.start` (`not_allowed`), and `precheck` refuses any policy-less task on a Boss-managed agent.
"""

import time
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
import _v3_fakes as F
import test_boss_e2e as P7
import test_boss_ui as UI
from _cdp import CHROME, Chrome
from test_boss_desk import N1, DeskBase, reply
from test_boss_run import RunBase, order, plan, review_reply
from test_connector_tasks import PAIR_ITER
from test_v4_workflows import FlowBase

from pixel_connector import boss as B
from pixel_connector.tasks import TaskError


def wait_for(pred, timeout=10.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("%s not reached" % what)


class RevokedBeforeConfirmTests(DeskBase):
    """S29-01: the computer's confirmation of a revoked browser's boss.ask starts nothing."""
    connector_overrides = {"confirm": {"task": "never", "agentCreate": True, "runtime": False, "peerRevoke": False,
                                       "timeoutS": 120}}

    def test_confirmed_after_the_revoke_starts_no_planning_task(self):
        phone = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, phone, iterations=PAIR_ITER)
        phone.connect_device(self.dev)
        self.script(self.fixture(reply(N1)))
        phone.send_e(self.dev, {"type": "boss.ask", "id": "q1", "workspace": {"id": "pixel"}, "brief": "x"})
        wait_for(lambda: [p for p in self.conn.approvals.list() if p["kind"] == "agentCreate"],
                 what="confirmation on the computer")
        (pend,) = [p for p in self.conn.approvals.list() if p["kind"] == "agentCreate"]
        self.assertTrue(self.conn.revoke(phone.bid))
        self.conn.approvals.resolve(pend["id"], True)          # the user confirms the stale card afterwards
        time.sleep(0.3)
        self.assertEqual(self.conn.boss.jobs, {})
        self.assertEqual(self.calls(), [])                       # no planning task, no money spent
        self.assertEqual([a for a in self.conn.agents.list() if a.get("boss")], [])
        self.assertEqual(self.audit("boss.ask"), [])
        phone.close()

    def test_a_confirmed_ask_of_an_active_browser_still_starts(self):
        self.script(self.fixture(reply(N1)))
        self.br.send_e(self.dev, {"type": "boss.ask", "id": "q2", "workspace": {"id": "pixel"}, "brief": "x"})
        wait_for(lambda: [p for p in self.conn.approvals.list() if p["kind"] == "agentCreate"],
                 what="confirmation on the computer")
        (pend,) = [p for p in self.conn.approvals.list() if p["kind"] == "agentCreate"]
        self.conn.approvals.resolve(pend["id"], True)
        r = self.br.next_msg(lambda m: m.get("re") == "q2")
        self.assertEqual(r["type"], "boss.ack", r)
        self.at(r["job"]["job"], "approval")


class RevokePushTests(DeskBase):
    """S29-02: the revoked browser is told before its channel closes; nobody else is."""

    def test_revoke_on_the_computer_tells_the_revoked_browser(self):
        phone = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, phone, iterations=PAIR_ITER)
        phone.connect_device(self.dev)
        self.assertTrue(self.conn.revoke(phone.bid))             # `pixel-connector peers revoke` / /ctl/peers/revoke
        m = phone.next_msg(lambda m: m.get("type") == "peer.revoked", 5.0)
        self.assertEqual(m, {"type": "peer.revoked", "bid": phone.bid})
        with self.assertRaises(AssertionError):                   # the other paired browser hears nothing
            self.br.next_msg(lambda m: m.get("type") == "peer.revoked", 0.5)
        self.assertEqual([r["by"] for r in self.conn.audit.read() if r["ev"] == "peer.revoke"], ["local"])
        phone.close()

    def test_revoke_by_another_browser_tells_the_revoked_one(self):
        phone = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, phone, iterations=PAIR_ITER)
        phone.connect_device(self.dev)
        r = self.req({"type": "peer.revoke", "bid": phone.bid})
        self.assertEqual((r["type"], r["bid"]), ("peer.revoked", phone.bid))
        m = phone.next_msg(lambda m: m.get("type") == "peer.revoked", 5.0)
        self.assertEqual(m, {"type": "peer.revoked", "bid": phone.bid})
        self.assertNotIn("re", m)
        phone.close()

    def test_unpairing_itself_gets_only_its_reply(self):
        phone = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, phone, iterations=PAIR_ITER)
        phone.connect_device(self.dev)
        r = phone.request(self.dev, {"type": "peer.revoke", "bid": phone.bid})
        self.assertEqual((r["type"], r["bid"]), ("peer.revoked", phone.bid))
        with self.assertRaises(AssertionError):                   # no second, unsolicited notice
            phone.next_msg(lambda m: m.get("type") == "peer.revoked", 0.5)
        phone.close()


class TestGateWarningTests(RunBase):
    """S29-03: the approval before a Test order says what running the tests really allows."""

    def test_the_local_approval_item_carries_the_warning(self):
        p = plan(order("o1", "frontend", "Renk", ["web/a.css"]), order("o2", "test", "Sinama", ["tests/t.py"], ["o1"]))
        self.order_route("o1", "Renk", self.step_fx("o1 bitti"))
        self.order_route("o2", "Sinama", self.step_fx("testler geçti"))
        self.review_route(self.review_fx(review_reply(), 0.06))
        jid = self.start(p)
        self.wait_job(jid, lambda j: [o["state"] for o in j["orderStates"]] == ["done", "approval"],
                      what="Test order waiting")
        (pend,) = [x for x in self.conn.approvals.list() if x["kind"] == "workflow"]
        self.assertEqual(pend["title"], "Patron · Test: Sinama")
        self.assertEqual(pend["preview"], B.GATE_WARNING)
        for words in ("sizin yetkilerinizle", "klasörle", "tavan"):
            self.assertIn(words, B.GATE_WARNING)
        self.req({"type": "boss.cancel", "job": jid})
        self.ended(jid)


class BossAgentHandoffTests(FlowBase):
    """S29-04: a Boss-managed agent (the Patron, a role agent) takes no task outside the Boss desk, also not as a
    handoff target. The Test role agent's stored `dev` profile would otherwise run with the device's dev command list,
    without its approval gate and without the Agent/Task deny; the Patron would run outside the job's caps in the
    Patron's own session."""

    def managed(self, **tag):
        key = self.create(engine="claude", name="Uzman", model=None, profile="dev" if "bossRole" in tag else "readonly")
        self.conn.agents.put(dict(self.conn.agents.get(key), **tag))
        return key

    def test_task_start_with_a_handoff_to_a_role_agent_or_the_patron_is_refused(self):
        a = self.create(name="Mimar")
        for tag in ({B.SPECIALIST_FIELD: "test/claude"}, {"boss": "pixel"}):
            with self.subTest(tag=tag):
                b = self.managed(**tag)
                r = self.req({"type": "task.start", "agent": a, "prompt": "Plan yap", "handoff": {"agent": b}})
                self.assertEqual((r["type"], r.get("code")), ("error", "not_allowed"), r)
        time.sleep(0.3)
        self.assertEqual(self.chat_inputs(), [])                       # A never started either
        self.assertEqual(F.calls(self.fake_dir), [])

    def test_the_handoff_is_checked_again_when_a_ends(self):
        self.llm.words = ["kelime "] * 40
        self.llm.delay = 0.03
        a = self.create(name="Mimar")
        b = self.create(engine="claude", name="Yazar", model=None)
        ack = self.req({"type": "task.start", "agent": a, "prompt": "Plan yap", "handoff": {"agent": b}})
        self.assertEqual(ack["type"], "task.ack", ack)
        self.conn.agents.put(dict(self.conn.agents.get(b), **{B.SPECIALIST_FIELD: "test/claude", "profile": "dev"}))
        wait_for(lambda: [r for r in self.conn.audit.read() if r["ev"] == "task.handoff"], 15.0, what="handoff record")
        rec = [r for r in self.conn.audit.read() if r["ev"] == "task.handoff"]
        self.assertEqual((rec[0]["result"], rec[0].get("next")), ("not_allowed", None), rec)
        time.sleep(0.3)
        self.assertEqual(F.calls(self.fake_dir), [])                   # B never ran

    def test_a_policy_less_task_on_a_managed_agent_is_refused_by_the_scheduler(self):
        b = self.managed(**{B.SPECIALIST_FIELD: "test/claude"})
        with self.assertRaises(TaskError) as cm:
            self.conn.scheduler.submit(self.conn.agents.get(b), "x", True, None, "yerel", False)
        self.assertEqual(cm.exception.code, "not_allowed")
        self.assertEqual(self.conn.scheduler.runs, {})                 # (with a policy: test_boss_run, unchanged)


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class RevokePushPageTests(unittest.TestCase):
    """S29-02 through the real page: revoked on the computer while the panel is open, no reload."""
    setUp = P7.BossE2E.setUp
    tearDown = P7.BossE2E.tearDown
    _save_routes = P7.BossE2E._save_routes
    route = P7.BossE2E.route
    plan_route = P7.BossE2E.plan_route
    order_route = P7.BossE2E.order_route
    fixture = P7.BossE2E.fixture
    pair = P7.BossE2E.pair
    open_boss = P7.BossE2E.open_boss
    paired_page = P7.BossE2E.paired_page
    ask = P7.BossE2E.ask
    approve = P7.BossE2E.approve
    job = P7.BossE2E.job
    wait_conn = P7.BossE2E.wait_conn
    spans = P7.BossE2E.spans

    def test_the_open_panel_drops_the_job_when_the_computer_revokes_the_page(self):
        self.plan_route(plan(order("o1", "docs", "Belgeler", ["README.md"])))
        self.order_route("o1", "Belgeler", hang=30.0)
        with Chrome() as chrome:
            A = self.paired_page(chrome)
            self.ask(A)
            self.approve(A)
            A.wait(P7.ORDERS_RUNNING + " === 1", 20, what="o1 running")
            bid = self.conn.peers.public_list()[0]["bid"]
            self.assertTrue(self.conn.revoke(bid))                         # `pixel-connector peers revoke`
            self.wait_conn(lambda: self.job()["job"]["state"] == "cancelled", what="job revoked")
            A.wait(UI.vis("#boss-info") + " && !" + UI.vis("#boss-job"), 10,
                   what="the open panel shows the info text (no reload)")
            self.assertEqual(A.text("#boss-info"), UI.BOSS_INFO_UNPAIRED)
            self.assertFalse(A.eval(UI.vis("#boss-form")))
            self.assertEqual(A.eval(P7.PANEL_ACTIONS), [])
            self.assertEqual(A.eval("window.pixelOffice.v3.hub.pairedCount"), 0)
            self.assertFalse(A.eval("window.pixelOffice.renderer.boardData.waiting.some(c => c.target === 'boss')"))
            self.assertEqual(A.errors, [])
        self.wait_conn(lambda: "o1" in self.spans(), 10, what="o1 ended")
        self.assertTrue(self.spans()["o1"][2], "o1's run was not interrupted")


if __name__ == "__main__":
    unittest.main()
