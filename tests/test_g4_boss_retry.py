"""G4: `boss.retry_step`, re-running one failed order of the latest Boss job (connector side; the UI comes after G3).

Real connector against dev_relay with the routed fake `claude` of test_boss_run. Checked: a failed order runs again
while the orders already done are kept (not re-run; their outputs still reach the review), the orders that never ran
because the run stopped run as approved, the job goes on to the review and its report; the spend of the whole job
(planning, the failed run, the retry) stays under the same cap and the retry gets only what is left; the Test order's
approval is asked again; at most one retry per order; and the refusals: wrong message, an order that did not fail,
an unknown order, a job still active or not the latest, another job active, no budget / turns / time left, a revoked
browser. The audit record carries ids and numbers only.
"""

import types
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
from test_boss_run import RunBase, arg, order, plan, review_reply
from test_connector_tasks import PAIR_ITER

from pixel_connector import boss as B
from pixel_connector import workflows as W

FAIL = {"subtype": "error_during_execution", "is_error": True}


class RetryBase(RunBase):
    def retry(self, jid, oid, browser=None, **extra):
        msg = dict({"type": "boss.retry_step", "job": jid, "order": oid}, **extra)
        return (browser or self.br).request(self.dev, msg)

    def failed_job(self, **ask):
        """o1 fails (after o2 is done), o3 (after o1) never runs: the job ends with error/engine_error."""
        p = plan(order("o1", "frontend", "Bozuk", ["web/a.css"]), order("o2", "backend", "Hizli", ["lib/b.py"]),
                 order("o3", "docs", "Sonra", ["README.md"], ["o1"]))
        self.order_route("o1", "Bozuk", self.step_fx("hata", delay=0.3, **FAIL), self.step_fx("o1 tamam"),
                         self.step_fx("hata yine", **FAIL))
        self.order_route("o2", "Hizli", self.step_fx("o2 cikti OUT-o2"))
        self.order_route("o3", "Sonra", self.step_fx("o3 tamam"))
        jid = self.start(p, **ask)
        got = self.ended(jid)
        self.assertEqual((got["job"]["state"], got["job"]["code"]), ("error", "engine_error"))
        self.assertEqual([(o["state"], o["code"]) for o in got["job"]["orderStates"]],
                         [("error", "engine_error"), ("done", None), ("cancelled", None)])
        return jid, got


class RetryFlowTests(RetryBase):
    def test_retry_reruns_the_failed_order_keeps_done_work_and_reaches_the_review(self):
        self.review_route(self.review_fx(review_reply(), 0.07))
        jid, before = self.failed_job()
        spent = before["job"]["costUsd"]
        r = self.retry(jid, "o1")
        self.assertEqual(r["type"], "boss.ok", r)
        got = self.ended(jid)
        job, report = got["job"], got["report"]
        self.assertEqual((job["state"], job["code"], job["round"]), ("done", None, 0))
        self.assertEqual([(o["id"], o["state"]) for o in job["orderStates"]],
                         [("o1", "done"), ("o2", "done"), ("o3", "done")])
        self.assertEqual([c["name"] for c in self.calls()].count("o2"), 1)          # done work not repeated
        self.assertEqual(sorted(c["name"] for c in self.calls()), ["o1", "o1", "o2", "o3", "plan", "review"])
        self.assertIn("OUT-o2", self.by_name("review")["stdin"])                    # carried output reaches review
        left = int((1.0 - spent) * 100 + 1e-6) / 100.0
        retried = [c for c in self.calls() if c["name"] == "o1"][1]
        self.assertLessEqual(float(arg(retried, "--max-budget-usd")), left)          # only what is left of the cap
        self.assertGreater(job["costUsd"], spent)
        self.assertLessEqual(job["costUsd"], 1.0)
        self.assertFalse(report["partial"])
        self.assertEqual([o["id"] for o in report["orders"]], ["o1", "o2", "o3"])   # one round, not a second list
        self.assertIn("o1 · Frontend · Bozuk: bitti", report["report"])
        (rec,) = self.audit("boss.retry_step")
        self.assertEqual((rec["job"], rec["order"], rec["by"], rec["round"]), (jid, "o1", self.br.bid, 0))
        self.assertNotIn("Bozuk", str(rec))
        self.assertEqual(self.retry(jid, "o1")["code"], "not_retryable")             # the job is done now

    def test_at_most_one_retry_per_order(self):
        p = plan(order("o1", "frontend", "Bozuk", ["web/a.css"]))
        self.order_route("o1", "Bozuk", self.step_fx("hata", **FAIL), self.step_fx("hata yine", **FAIL))
        jid = self.start(p)
        self.ended(jid)
        self.assertEqual(self.retry(jid, "o1")["type"], "boss.ok")
        got = self.ended(jid)
        self.assertEqual((got["job"]["state"], got["job"]["orderStates"][0]["state"]), ("error", "error"))
        self.assertEqual(self.retry(jid, "o1")["code"], "retry_used")
        self.assertEqual([c["name"] for c in self.calls()], ["plan", "o1", "o1"])
        self.assertEqual(len(self.audit("boss.retry_step")), 1)

    def test_test_order_asks_for_approval_again(self):
        p = plan(order("o1", "test", "Sinama", ["tests/t.py"]))
        self.order_route("o1", "Sinama", self.step_fx("hata", **FAIL), self.step_fx("geçti"))
        jid = self.start(p)
        self.wait_job(jid, lambda j: j["orderStates"] and j["orderStates"][0]["state"] == "approval", what="gate")
        (pend,) = [x for x in self.conn.approvals.list() if x["kind"] == "workflow"]
        self.assertTrue(self.conn.approvals.resolve(pend["id"], True, by="cli"))
        got = self.ended(jid)
        self.assertEqual(got["job"]["orderStates"][0]["state"], "error")
        self.assertEqual(self.retry(jid, "o1")["type"], "boss.ok")
        self.wait_job(jid, lambda j: j["orderStates"] and j["orderStates"][0]["state"] == "approval",
                      what="gate again")
        self.assertEqual([c["name"] for c in self.calls()], ["plan", "o1"])         # nothing ran before the answer
        (pend,) = [x for x in self.conn.approvals.list() if x["kind"] == "workflow"]
        self.assertTrue(self.conn.approvals.resolve(pend["id"], False, by="cli"))   # denied: skipped, fail closed
        got = self.ended(jid)
        self.assertEqual((got["job"]["state"], got["job"]["orderStates"][0]["code"]), ("done", "denied"))
        self.assertEqual([c["name"] for c in self.calls()], ["plan", "o1"])


class RetryRefusalTests(RetryBase):
    def test_bad_messages_and_orders_that_did_not_fail(self):
        jid, before = self.failed_job()
        self.assertEqual(self.retry(jid, "o2")["code"], "not_retryable")             # done
        self.assertEqual(self.retry(jid, "o3")["code"], "not_retryable")             # never ran
        self.assertEqual(self.retry(jid, "o9")["code"], "not_retryable")             # not in the plan
        self.assertEqual(self.retry(jid, B.REVIEW_STEP_ID)["code"], "bad_request")
        for bad in ("x", "", None, 1, "o10"):
            self.assertEqual(self.retry(jid, bad)["code"], "bad_request", bad)
        self.assertEqual(self.retry(jid, "o1", extra=1)["code"], "bad_request")
        self.assertEqual(self.retry("b_AAAAAAAAAA", "o1")["code"], "unknown_job")
        self.assertEqual(self.retry("nope", "o1")["code"], "bad_request")
        after = self.conn.boss.get(jid)
        self.assertEqual((after["job"]["state"], after["report"]), ("error", before["report"]))
        self.assertEqual(self.audit("boss.retry_step"), [])

    def test_active_job_or_another_job_or_an_older_job(self):
        jid, _ = self.failed_job()
        p2 = plan(order("o1", "frontend", "Uzun", ["web/a.css"]))
        self.order_route("u1", "Uzun", self.step_fx("uzun", hang=30))
        self.plan_route(p2)
        jid2 = self.asked()
        self.at(jid2, "approval")
        self.assertEqual(self.retry(jid, "o1")["code"], "busy")                      # another job is active
        self.assertEqual(self.retry(jid2, "o1")["code"], "not_retryable")            # active itself
        self.req({"type": "boss.cancel", "job": jid2})
        self.at(jid2, "cancelled")
        self.assertEqual(self.retry(jid, "o1")["code"], "not_retryable")             # no longer the latest job

    def test_no_budget_left(self):
        p = plan(order("o1", "frontend", "Pahali", ["web/a.css"]))
        self.order_route("o1", "Pahali", self.step_fx("hata", cost=0.25, **FAIL))
        jid = self.start(p, maxCostUsd=0.3)                                           # 0.05 planning + 0.25
        got = self.ended(jid)
        self.assertEqual(got["job"]["state"], "error")
        self.assertEqual(self.retry(jid, "o1")["code"], "cost_cap")
        after = self.conn.boss.get(jid)
        self.assertEqual((after["job"]["state"], after["report"]), ("error", got["report"]))
        self.assertEqual(self.audit("boss.retry_step"), [])
        job = self.conn.boss.jobs[jid]
        job.cost = 0.0
        job.turns = B.RUN_MAX_TURNS
        self.assertEqual(self.retry(jid, "o1")["code"], "max_turns")
        job.turns = 0
        job.run_time = B.RUN_TIMEOUT_S
        self.assertEqual(self.retry(jid, "o1")["code"], "timeout")

    def test_revoked_browser_and_a_retry_bound_to_the_browser_that_asked(self):
        phone = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, phone, iterations=PAIR_ITER)
        phone.connect_device(self.dev)
        jid, _ = self.failed_job()
        self.conn.revoke(phone.bid, by=self.br.bid)
        gone = types.SimpleNamespace(bid=phone.bid, label="Telefon")
        with self.assertRaises(B.BossError) as cm:
            self.conn.boss.retry(gone, {"type": "boss.retry_step", "job": jid, "order": "o1"})
        self.assertEqual(cm.exception.code, "not_allowed")
        local = types.SimpleNamespace(bid=None, label="yerel")
        with self.assertRaises(B.BossError) as cm:
            self.conn.boss.retry(local, {"type": "boss.retry_step", "job": jid, "order": "o1"})
        self.assertEqual(cm.exception.code, "not_allowed")                         # paired browsers only
        self.assertEqual(self.conn.boss.get(jid)["job"]["state"], "error")
        phone.close()


class PriorGuardTests(RetryBase):
    def test_malformed_priors_are_refused(self):
        step = {"id": "o1", "type": "agent", "role": "Frontend", "roleId": "frontend", "engine": "claude",
                "profile": "edit", "model": None, "prompt": "x", "agent": "a1", "agentName": "Frontend", "inputs": [],
                "gate": False, "gateTitle": "", "gateTimeoutS": 60, "fresh": True, "maxTurns": 5, "final": False,
                "policy": lambda t: None}
        two = dict(step, id="o2")
        done = {"state": "done", "code": None, "result": "x", "costUsd": 0.1, "task": None}
        spec = {"name": "Patron", "peer": None, "label": "yerel", "workspace": {"id": "pixel", "sub": None},
                "limits": B.run_guardrails(1.0), "ready": lambda f, r: []}
        bad = [[dict(step, prior=done)],                                            # nothing left to run
               [dict(step, prior=dict(done, state="running")), two],
               [dict(step, prior=dict(done, result=None)), two],                    # done without an output
               [dict(step, prior=dict(done, state="error")), two],                  # failed with an "output"
               [dict(step, prior=dict(done, extra=1)), two],
               [dict(step, prior="done"), two]]
        for steps in bad:
            with self.assertRaises(W.WorkflowError) as cm:
                self.conn.workflows.launch_plan(dict(spec, steps=steps))
            self.assertEqual(cm.exception.code, "internal")
        self.assertEqual(self.conn.workflows.runs, {})


class RetryWhileRunningRevokeTests(RetryBase):
    def test_revoking_the_browser_that_retried_cancels_the_retry(self):
        phone = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, phone, iterations=PAIR_ITER)
        phone.connect_device(self.dev)
        p = plan(order("o1", "frontend", "Bozuk", ["web/a.css"]))
        self.order_route("o1", "Bozuk", self.step_fx("hata", **FAIL), self.step_fx("uzun", hang=30))
        jid = self.start(p)
        self.ended(jid)
        self.assertEqual(self.retry(jid, "o1", browser=phone)["type"], "boss.ok")
        self.wait_job(jid, lambda j: j["orderStates"] and j["orderStates"][0]["task"]
                      and [c["name"] for c in self.calls()].count("o1") == 2, what="retry running")
        self.conn.revoke(phone.bid, by=self.br.bid)
        got = self.ended(jid)
        self.assertEqual((got["job"]["state"], got["job"]["code"]), ("cancelled", "revoked"))
        self.assertEqual(self.retry(jid, "o1")["code"], "not_retryable")             # cancelled, not failed
        phone.close()


if __name__ == "__main__":
    unittest.main()
