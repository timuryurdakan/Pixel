"""Patron Odası P2: the Boss's desk, ask → plan → approval (docs/PATRON_ODASI.md §4.3, §4.4, §4.6, §9 P2, §10).

Unit tests for `boss.ask` parsing, the plan in the Boss's reply (untrusted: markers, one JSON object, sizes, schema),
the task policy, the Claude argv allow override / sub-agent deny (engines/claude.build_argv) and `boss.maxCostUsd` in
connector.json; then the real connector against dev_relay with a fake `claude` only: plan → approval card → approve /
deny / timeout, one "only valid JSON" retry, cost caps, `remote.agentCreate`, revoke, memory reset, busy, and that the
brief and the plan never reach `boss.state`, the audit log, the relay, the office server or the agent list.
"""

import json
import os
import time
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
import _v3_fakes as F
from test_connector_tasks import PAIR_ITER, Base

from pixel_connector import boss as B
from pixel_connector import config as CFG
from pixel_connector.engines import claude as CL

BRIEF_CANARY = "BRIEF-CANARY-b055"
PLAN_CANARY = "PLAN-CANARY-41c7"
N1, N2 = "aaaa1111", "bbbb2222"


def plan_obj(**kw):
    p = {"v": 1, "summary": "Koyu tema ekle ve testini yaz.",
         "orders": [{"id": "o1", "role": "frontend", "title": "Koyu tema renkleri",
                     "goal": "Koyu tema renk değişkenlerini ekle " + PLAN_CANARY, "accept": ["Renkler değişir"],
                     "files": ["web/css/style.css"]},
                    {"id": "o2", "role": "test", "title": "Koyu tema testi", "goal": "Bir test yaz.",
                     "accept": ["Test tests/ altında"], "files": ["tests/test_dark.py"], "after": ["o1"]}]}
    p.update(kw)
    return p


def reply(nonce, body=None, before="Projeyi okudum.\n", after="\n"):
    start, end = B.plan_markers(nonce)
    if body is None:
        body = json.dumps(plan_obj(), ensure_ascii=False)
    return "%s%s\n%s\n%s%s" % (before, start, body, end, after)


# -- unit: the plan in the Boss's reply ----------------------------------------------------------------------------
class ExtractPlanTests(unittest.TestCase):
    def bad(self, text, code="bad_plan", detail=None, nonce=N1, **kw):
        with self.assertRaises(B.BossError) as cm:
            B.extract_plan(text, nonce, **kw)
        self.assertEqual(cm.exception.code, code)
        if detail is not None:
            self.assertEqual(str(cm.exception), detail)
        return cm.exception

    def test_valid_plan_with_text_around_and_a_code_fence(self):
        p = B.extract_plan(reply(N1), N1)
        self.assertEqual([o["id"] for o in p["orders"]], ["o1", "o2"])
        self.assertEqual(p["orders"][1]["after"], ["o1"])
        fenced = reply(N1, "```json\n%s\n```" % json.dumps(plan_obj()))
        self.assertEqual(B.extract_plan(fenced, N1), p)

    def test_markers_must_be_this_requests_and_exactly_one_block(self):
        self.bad(json.dumps(plan_obj()), detail="block")                     # bare JSON without markers
        self.bad(reply(N2), detail="block")                                  # another request's markers
        self.bad(reply(N1) + reply(N1), detail="block")                      # two blocks
        start, end = B.plan_markers(N1)
        self.bad("%s\n{}\n%s" % (end, start), detail="block")                # end before start
        self.bad("%s\n%s" % (start, json.dumps(plan_obj())), detail="block")  # no end
        self.bad("", detail="output")
        self.bad(None, detail="output")

    def test_block_is_exactly_one_json_object(self):
        one = json.dumps(plan_obj())
        for body in (one + one, "[%s]" % one, "plan: " + one, "{'v': 1}", '"text"', "",
                     one[:-1] + ', "v": 1}',                                      # duplicate key
                     one.replace('"v": 1', '"v": NaN'), "[" * 10000 + "]" * 10000):
            self.bad(reply(N1, body), detail="json")

    def test_size_limits(self):
        self.bad(reply(N1, after="x" * B.RESULT_MAX), detail="output")
        big = json.dumps(plan_obj(summary="s"), ensure_ascii=False)[:-1] + "," + " " * B.PLAN_JSON_MAX + "}"
        self.bad(reply(N1, big), detail="size")

    def test_schema_hidden_characters_and_disabled_roles(self):
        self.bad(reply(N1, json.dumps(plan_obj(cost=0.01))), detail="plan")              # unknown field
        o = plan_obj()
        o["orders"][0]["profile"] = "auto"                                                # the Boss can't pick a profile
        self.bad(reply(N1, json.dumps(o)), detail="orders[0]")
        o = plan_obj()
        o["orders"][0]["title"] = "Ad‮gizli"
        self.bad(reply(N1, json.dumps(o)), detail="orders[0].title")                      # \u escape in JSON
        self.bad(reply(N1, json.dumps(o, ensure_ascii=False)), detail="orders[0].title")  # raw bidi character
        self.bad(reply(N1, json.dumps(plan_obj(orders=[]))), detail="orders")
        self.bad(reply(N1), code="role_unavailable", enabled_profiles=("readonly", "edit"))   # test role needs dev

    def test_prompts_carry_markers_roles_and_the_brief_last(self):
        p = B.planning_prompt("İstek " + BRIEF_CANARY, N1)
        start, end = B.plan_markers(N1)
        self.assertIn(start, p)
        self.assertIn(end, p)
        self.assertTrue(p.endswith(B.PLAN_BRIEF_HEAD + "\nİstek " + BRIEF_CANARY))
        for rid in B.ROLES:
            self.assertIn("- %s (" % rid, p)
        self.assertLessEqual(len(p.encode("utf-8")), 32768)
        r = B.retry_prompt("json", N2)
        self.assertIn(B.EXTRACT_REASONS["json"], r)
        self.assertIn(B.plan_markers(N2)[0], r)
        self.assertIn("alan orders[0].title", B.retry_prompt("orders[0].title", N2))
        self.assertNotEqual(B.new_nonce(), B.new_nonce())
        self.assertRegex(B.new_nonce(), r"^[0-9a-f]{8}$")
        self.assertRegex(B.new_job_id(), B.JOB_ID_RE.pattern)


class ParseAskTests(unittest.TestCase):
    def parse(self, ceiling=1.0, **kw):
        msg = {"type": "boss.ask", "id": "r1", "workspace": {"id": "pixel"}, "brief": "Koyu tema ekle"}
        msg.update(kw)
        return B.parse_ask(msg, ceiling)

    def code(self, code, ceiling=1.0, **kw):
        with self.assertRaises(B.BossError) as cm:
            self.parse(ceiling, **kw)
        self.assertEqual(cm.exception.code, code, kw)

    def test_defaults_and_lowering(self):
        self.assertEqual(self.parse(), {"workspace": {"id": "pixel", "sub": None}, "brief": "Koyu tema ekle",
                                        "maxCostUsd": 1.0, "department": "yazilim"})
        self.assertEqual(self.parse(maxCostUsd=0.5)["maxCostUsd"], 0.5)
        self.assertEqual(self.parse(ceiling=2.5)["maxCostUsd"], 2.5)
        self.assertEqual(self.parse(workspace={"id": "pixel", "sub": "site/v1"})["workspace"]["sub"], "site/v1")

    def test_refusals(self):
        self.code("bad_request", profile="auto")                 # no profile, engine or model from the browser
        self.code("bad_request", engine="claude")
        self.code("bad_request", allow=["Bash(*)"])
        self.code("bad_request", maxCostUsd=1.5)                 # above the ceiling: refused, not clamped
        self.code("bad_request", maxCostUsd=0.05)
        self.code("bad_request", maxCostUsd="0.5")
        self.code("bad_request", maxCostUsd=True)
        self.code("bad_request", brief="   ")
        self.code("bad_request", brief=5)
        self.code("bad_request", brief="Gizli‮metin")
        self.code("too_large", brief="ş" * 2049)
        self.code("bad_request", workspace="pixel")
        self.code("bad_request", workspace={"id": "pixel", "path": "/etc"})
        self.code("workspace_denied", workspace={"id": "pixel", "sub": "../dis"})
        for fields, msg in ((B.JOB_FIELDS, {"type": "boss.get", "job": "b_x"}),
                            (B.APPROVE_FIELDS, {"type": "boss.approve", "job": "b_abcdefghij", "extra": 1})):
            with self.assertRaises(B.BossError):
                B.parse_job_msg(msg, fields)
        self.assertIsNone(B.parse_job_msg({"type": "boss.get"}, B.JOB_FIELDS, need_job=False))


class TaskPolicyTests(unittest.TestCase):
    def test_boss_and_role_policies(self):
        p = B.task_policy("patron", B.PLAN_MAX_TURNS)
        self.assertEqual((p.role, p.profile, p.allow, p.deny, p.max_turns),
                         ("patron", "readonly", [], ("Agent", "Task"), 10))
        t = B.task_policy("test", 5)
        self.assertEqual((t.profile, t.allow), ("dev", B.role_allow_rules("test")))
        self.assertEqual(B.task_policy("review", 3).allow, [])
        for bad in (0, 11, 2.5, True):
            with self.assertRaises(B.BossError):
                B.task_policy("patron", bad)
        with self.assertRaises(B.BossError):
            B.task_policy("admin", 3)


# -- unit: the Claude argv (engines/claude.build_argv) ------------------------------------------------------------
class ArgvOverrideTests(unittest.TestCase):
    PROFILES = {"dev": {"allow": ["Bash(git *)", "Bash(npm *)"], "deny": ["Bash(rm *)"]}, "auto": {"enabled": True}}

    def argv(self, profile, **kw):
        return CL.build_argv("/x/claude", profile, self.PROFILES, "sid", False, None, 10, None, (2, 1, 301), **kw)

    @staticmethod
    def rules(argv, flag):
        i = argv.index(flag) + 1
        out = []
        while i < len(argv) and not argv[i].startswith("--"):
            out.append(argv[i])
            i += 1
        return out

    def test_override_replaces_the_device_list(self):
        plain = self.argv("dev")
        self.assertEqual(self.rules(plain, "--allowedTools"), ["Edit(./**)", "Bash(git *)", "Bash(npm *)"])
        a = self.argv("dev", allow_override=["Bash(python3 -m unittest *)", "Bash(npm test *)"])
        self.assertEqual(self.rules(a, "--allowedTools"),
                         ["Edit(./**)", "Bash(python3 -m unittest *)", "Bash(npm test *)"])
        self.assertNotIn("Bash(git *)", a)                       # never widened with the device's list
        self.assertEqual(self.rules(self.argv("dev", allow_override=[]), "--allowedTools"), ["Edit(./**)"])
        self.assertNotIn("--allowedTools", self.argv("readonly", allow_override=[]))
        self.assertEqual(self.rules(self.argv("edit", allow_override=[]), "--allowedTools"), ["Edit(./**)"])
        # the deny lists stay: built-in + profile + the task's extra tools
        deny = self.rules(self.argv("dev", allow_override=[], extra_deny=("Agent", "Task")), "--disallowedTools")
        self.assertEqual(deny[:len(CL.BUILTIN_DENY)], CL.BUILTIN_DENY)
        self.assertEqual(deny[len(CL.BUILTIN_DENY):], ["Bash(rm *)", "Agent", "Task"])
        self.assertNotIn("Agent", self.argv("dev"))              # no change for ordinary tasks

    def test_bad_overrides_are_refused(self):
        for rules in (["Bash"], ["Bash(*)"], ["Bash( *)"], ["Bash(rm -rf / *)"], ["Bash(a; b *)"], ["Bash(a && b *)"],
                      ["Bash(a | b *)"], ["Bash($(x) *)"], ["Edit(/**)"], ["Read(~/**)"], [5], ["Bash(git)"],
                      ["Bash(bypassPermissions *)"]):
            with self.assertRaises(ValueError, msg=rules):
                self.argv("dev", allow_override=rules)
        with self.assertRaises(ValueError):
            self.argv("auto", allow_override=[])                 # auto never takes a task policy
        for profile in ("readonly", "edit"):
            with self.assertRaises(ValueError):
                self.argv(profile, allow_override=["Bash(pytest *)"])
        for deny in (["Bash(*)"], ["agent"], [""], ["A" * 40]):
            with self.assertRaises(ValueError, msg=deny):
                self.argv("readonly", extra_deny=deny)

    def test_every_role_list_passes_the_engine_check(self):
        for rid, role in B.ROLES.items():
            pol = B.task_policy(rid, 1)
            a = self.argv(pol.profile, allow_override=pol.allow, extra_deny=pol.deny)
            self.assertIn("Agent", self.rules(a, "--disallowedTools"))
            for f in CL.FORBIDDEN_ARGS:
                self.assertNotIn(f, " ".join(a))


# -- unit: connector.json boss.maxCostUsd ----------------------------------------------------------------------------
class BossConfigTests(unittest.TestCase):
    def validate(self, boss=None):
        raw = CFG.initial_config("https://x.example", "a" * 26, "k" * 20, "Timur", "Cihaz", None)
        if boss is None:
            raw.pop("boss")
        else:
            raw["boss"] = boss
        return CFG.validate(raw)

    def test_default_valid_and_invalid_values(self):
        d, errors, warnings = self.validate()
        self.assertEqual((d["boss"], errors), ({"maxCostUsd": 1.0}, []))
        raw = CFG.initial_config("https://x.example", "a" * 26, "k" * 20, "Timur", "Cihaz", None)
        self.assertEqual(raw["boss"], {"maxCostUsd": 1.0})
        d, errors, warnings = CFG.validate(raw)
        self.assertEqual((d["boss"]["maxCostUsd"], errors), (1.0, []))
        self.assertEqual([w for w in warnings if "boss" in w], [])
        for ok in (0.1, 2.5, 3):
            d, errors, _ = self.validate({"maxCostUsd": ok})
            self.assertEqual((d["boss"]["maxCostUsd"], errors), (float(ok), []))
        for bad in (3.5, 0.05, 0, -1, "2", True, None, float("nan")):
            d, errors, _ = self.validate({"maxCostUsd": bad})
            self.assertEqual(d["boss"]["maxCostUsd"], 1.0, bad)          # safe default kept
            self.assertTrue(any(e.startswith("boss.maxCostUsd") for e in errors), bad)
        d, errors, _ = self.validate([1])
        self.assertEqual((d["boss"]["maxCostUsd"], errors), (1.0, ["boss: must be an object"]))
        d, errors, warnings = self.validate({"maxCostUsd": 2.0, "max": 9})
        self.assertEqual((d["boss"]["maxCostUsd"], errors), (2.0, []))
        self.assertIn("unknown key 'boss.max' ignored", warnings)


# -- the real connector --------------------------------------------------------------------------------------------
class DeskBase(Base):
    def setUp(self):
        super().setUp()
        self.conn.scheduler.limits["tasksPerMinute"] = 60
        self.nonces = []
        self.conn.boss.nonce = lambda: self.nonces.pop(0) if self.nonces else N1
        self.fx = os.path.join(self.env.tmp, "bossfx")
        os.makedirs(self.fx, exist_ok=True)
        self._n = 0

    def fixture(self, text, cost=0.05, turns=3, subtype="success", is_error=False, out=100):
        """A stream-json run whose final reply is `text` (absolute path: the fake replays it as given)."""
        self._n += 1
        path = os.path.join(self.fx, "r%d.jsonl" % self._n)
        lines = [
            {"type": "system", "subtype": "init", "session_id": "{SESSION}", "cwd": "{CWD}", "model": "claude-x",
             "tools": ["Read", "Agent"], "permissionMode": "dontAsk"},
            {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_start", "message": {
                "id": "m%d" % self._n, "usage": {"input_tokens": 10, "output_tokens": 1}}}},
            {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "content_block_delta", "delta": {
                "type": "text_delta", "text": text}}},
            {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": turns, "result": text,
             "total_cost_usd": cost, "session_id": "{SESSION}",
             "usage": {"input_tokens": 10 * self._n, "output_tokens": out * self._n}}]
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return path

    def script(self, *steps):
        F.set_plan(self.fake_dir, [s if isinstance(s, dict) else {"fixture": s} for s in steps])

    def ask(self, browser=None, **kw):
        msg = {"type": "boss.ask", "workspace": {"id": "pixel"}, "brief": "İstek " + BRIEF_CANARY}
        msg.update(kw)
        return (browser or self.br).request(self.dev, msg)

    def asked(self, **kw):
        r = self.ask(**kw)
        self.assertEqual(r["type"], "boss.ack", r)
        self.assertRegex(r["job"]["job"], B.JOB_ID_RE.pattern)
        return r["job"]["job"]

    def wait_job(self, jid, pred, timeout=20.0, what="job state"):
        end = time.time() + timeout
        last = None
        while time.time() < end:
            last = self.conn.boss.get(jid)["job"]
            if pred(last):
                return last
            time.sleep(0.02)
        raise AssertionError("%s not reached: %s" % (what, json.dumps(last, ensure_ascii=False)))

    def at(self, jid, state):
        return self.wait_job(jid, lambda j: j["state"] == state, what=state)

    def calls(self):
        return F.calls(self.fake_dir)

    def audit(self, ev=None):
        """Audit records of one event (exact name), or every boss.* record."""
        return [r for r in self.conn.audit.read() if r["ev"] == ev or (ev is None and r["ev"].startswith("boss."))]

    def patron(self):
        found = [a for a in self.conn.agents.list() if a.get("boss")]
        self.assertEqual(len(found), 1, found)
        return found[0]

    def boss_approvals(self):
        return [p for p in self.conn.approvals.list() if p["kind"] == "boss"]

    def states(self, browser=None, jid=None):
        out = []
        while True:
            try:
                m = (browser or self.br).next_msg(lambda m: m.get("type") == "boss.state"
                                                  and (jid is None or m["job"] == jid), 0.2)
            except AssertionError:
                return out
            out.append(m)

    @staticmethod
    def rules(argv, flag):
        return ArgvOverrideTests.rules(argv, flag)


class DeskFlowTests(DeskBase):
    def test_plan_approval_card_approve_privacy_and_session(self):
        info = self.req({"type": "peer.hello", "label": "x"})
        self.assertIn("boss", info["features"])
        self.assertEqual(info["boss"], {"maxCostUsd": 1.0})
        self.script(self.fixture(reply(N1), cost=0.04))
        jid = self.asked()
        job = self.at(jid, "approval")
        self.assertEqual((job["orders"], job["estimateUsd"], job["maxCostUsd"], job["costUsd"], job["round"]),
                         (2, 0.9, 1.0, 0.04, 0))
        self.assertIsNotNone(job["expiresAt"])
        # the plan card reached the paired browser (E2E) with the validated plan, the estimate and the cap
        card = self.br.next_msg(lambda m: m.get("type") == "boss.plan" and m["job"] == jid)
        self.assertEqual(card["plan"], B.validate_plan(plan_obj()))
        self.assertEqual((card["estimateUsd"], card["maxCostUsd"]), (0.9, 1.0))
        self.assertEqual(set(card["roles"]), {"frontend", "test"})
        # ... and is a local approval item too (/onay, CLI), 15 minutes
        pend = self.boss_approvals()
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["title"], "Patron planı: 2 iş emri")
        self.assertAlmostEqual(pend[0]["expiresAt"] - pend[0]["createdAt"], B.APPROVAL_TIMEOUT_S * 1000, delta=5)
        # the planning task: Patron agent, readonly, no command allowed, Agent/Task denied, 10 turns, 0.20 $
        (call,) = self.calls()
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--allowedTools", argv)
        self.assertEqual(self.rules(argv, "--disallowedTools")[-2:], ["Agent", "Task"])
        self.assertEqual(argv[argv.index("--max-turns") + 1], "10")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.20")
        self.assertIn("--session-id", argv)
        self.assertIn("«Sadece okuma»", argv[argv.index("--append-system-prompt") + 1])
        self.assertTrue(call["stdin"].endswith("İstek " + BRIEF_CANARY))
        self.assertIn(B.plan_markers(N1)[0], call["stdin"])
        self.assertEqual(os.path.realpath(call["cwd"]),
                         os.path.realpath(os.path.join(self.env.tmp, "dev", "projects", "pixel")))
        # approve from the browser: approved, then (P3) the plan runs at once; the Test order o2 waits for its own
        # approval, so the job stays running
        ok = self.req({"type": "boss.approve", "job": jid, "approve": True})
        self.assertEqual((ok["type"], ok["job"]), ("boss.ok", jid))
        job = self.wait_job(jid, lambda j: j["state"] == "running" and [o["state"] for o in j["orderStates"]]
                            == ["done", "approval"], what="running, Test order waiting")
        self.assertEqual(self.boss_approvals(), [])
        self.assertEqual(self.req({"type": "boss.approve", "job": jid, "approve": True})["code"], "unknown_approval")
        got = self.req({"type": "boss.get"})
        self.assertEqual((got["job"]["job"], got["job"]["state"], got["plan"]["job"]), (jid, "running", jid))
        # a running job holds the desk; cancel frees it once its run has ended
        self.assertEqual(self.ask()["code"], "busy")
        self.assertEqual(self.req({"type": "boss.cancel", "job": jid})["type"], "boss.ok")
        self.assertEqual(self.at(jid, "cancelled")["code"], "cancelled")
        self.assertEqual(self.req({"type": "boss.cancel", "job": jid})["code"], "unknown_job")
        self.wait_job(jid, lambda j: not any(r.active for r in self.conn.workflows.runs.values()), what="run ended")
        # the next job resumes the same Patron session (memory)
        self.script(self.fixture(reply(N1), cost=0.06))
        jid2 = self.asked()
        self.at(jid2, "approval")
        argv2 = self.calls()[0]["argv"]
        self.assertEqual(argv2[argv2.index("--resume") + 1], argv[argv.index("--session-id") + 1])
        self.assertEqual(len([a for a in self.conn.agents.list() if a.get("boss")]), 1)
        self.req({"type": "boss.cancel", "job": jid2})
        self.at(jid2, "cancelled")
        # boss.state: ids, states, numbers only, in order
        seen = self.states(jid=jid)
        phases = [m["state"] for m in seen]
        self.assertEqual(sorted(set(phases)), ["approval", "approved", "cancelled", "planning", "running"])
        for once in ("approval", "approved", "planning"):
            self.assertEqual(phases.count(once), 1, phases)     # running/cancelled repeat as the run's orders change
        blob = json.dumps(self.states() + seen, ensure_ascii=False)
        for canary in (BRIEF_CANARY, PLAN_CANARY, "Koyu tema"):
            self.assertNotIn(canary, blob)
        # audit: ids, states, lengths, money; no brief, no plan text
        evs = [r["ev"] for r in self.audit()]
        self.assertEqual(evs[:6], ["boss.ask", "boss.planned", "boss.plan", "boss.approve", "boss.run", "boss.cancel"])
        ask = self.audit("boss.ask")[0]
        self.assertEqual((ask["briefLen"], ask["maxCostUsd"], ask["planCapUsd"]),
                         (len(("İstek " + BRIEF_CANARY).encode()), 1.0, 0.2))
        self.assertEqual(self.audit("boss.approve")[0]["by"], self.br.bid)
        self.assertGreater(self.audit("boss.plan")[0]["planLen"], 100)
        with open(os.path.join(self.home, "audit.log"), encoding="utf-8") as f:
            log = f.read()
        for canary in (BRIEF_CANARY, PLAN_CANARY, "Koyu tema"):
            self.assertNotIn(canary, log)
        # the Patron is no office character and no agent to command; the relay and the office see no text
        key = self.patron()["key"]
        self.office_settled()
        self.assertEqual([a for a in self.env.state()["agents"] if (a.get("control") or {}).get("agent") == key], [])
        self.assertNotIn(key, [a["key"] for a in self.req({"type": "agent.list"})["agents"]])
        relay = "\n".join(self.env.relay_frames)
        status = b"\n".join(self.env.status_bodies).decode()
        for canary in (BRIEF_CANARY, PLAN_CANARY, "Patron"):
            self.assertNotIn(canary, relay)
            self.assertNotIn(canary, status)

    def test_deny_from_another_paired_browser_and_timeout(self):
        other = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, other, iterations=PAIR_ITER)
        other.connect_device(self.dev)
        self.script(self.fixture(reply(N1)), self.fixture(reply(N1)))
        jid = self.asked()
        card = other.next_msg(lambda m: m.get("type") == "boss.plan", 20)    # every paired browser gets the card
        self.assertEqual(card["job"], jid)
        r = other.request(self.dev, {"type": "boss.approve", "job": jid, "approve": False})
        self.assertEqual(r["type"], "boss.ok")
        self.assertEqual(self.at(jid, "cancelled")["code"], "denied")
        self.assertEqual(self.audit("boss.deny")[0]["by"], other.bid)
        self.assertEqual(self.boss_approvals(), [])
        for bad in ({"approve": "yes"}, {"approve": 1}, {}, {"approve": True, "extra": 1}):
            r = self.req(dict({"type": "boss.approve", "job": jid}, **bad))
            self.assertEqual(r["code"], "bad_request", bad)
        # 15 minutes without an answer = denied (fail closed)
        jid2 = self.asked()
        self.at(jid2, "approval")
        real = self.conn.approvals.clock
        self.conn.approvals.clock = lambda: real() + B.APPROVAL_TIMEOUT_S + 1
        try:
            self.conn.approvals.tick()
        finally:
            self.conn.approvals.clock = real
        self.assertEqual(self.at(jid2, "cancelled")["code"], "approval_timeout")
        self.assertIn("boss.expire", [r["ev"] for r in self.audit()])
        self.assertEqual(self.boss_approvals(), [])
        other.close()

    def test_local_approval_answers_the_plan(self):
        self.script(self.fixture(reply(N1)))
        jid = self.asked()
        self.at(jid, "approval")
        (pend,) = self.boss_approvals()
        self.assertIn("Tahmini 0.90 $, tavan 1.00 $", pend["preview"])
        self.assertTrue(self.conn.approvals.resolve(pend["id"], True, by="cli"))
        self.at(jid, "running")                                        # approved, then P3 runs the plan
        self.assertEqual(self.audit("boss.approve")[0]["by"], "cli")
        self.assertIn("approved", [m["state"] for m in self.states(jid=jid)])

    def test_one_retry_then_a_valid_plan(self):
        self.nonces = [N1, N2]
        self.script(self.fixture("Planı yazmayı unuttum.", cost=0.03), self.fixture(reply(N2), cost=0.07))
        jid = self.asked()
        job = self.at(jid, "approval")
        c1, c2 = self.calls()
        self.assertIn(B.plan_markers(N1)[0], c1["stdin"])
        self.assertIn(B.EXTRACT_REASONS["block"], c2["stdin"])
        self.assertIn(B.plan_markers(N2)[0], c2["stdin"])
        self.assertNotIn(BRIEF_CANARY, c2["stdin"])                    # the session already has the brief
        self.assertIn("--resume", c2["argv"])                          # same session
        self.assertEqual(c2["argv"][c2["argv"].index("--max-budget-usd") + 1], "0.17")    # 0.20 − 0.03
        self.assertEqual(c2["argv"][c2["argv"].index("--max-turns") + 1], "7")             # 10 − 3
        self.assertAlmostEqual(job["costUsd"], 0.07)                   # 0.03 + (0.07 − 0.03 cumulative)
        self.assertEqual(self.audit("boss.retry")[0]["code"], "bad_plan")
        self.assertEqual(self.audit("boss.plan")[0]["attempts"], 2)

    def test_invalid_plan_twice_ends_with_bad_plan(self):
        o = plan_obj()
        o["orders"][0]["title"] = "Ad‮gizli"
        self.nonces = [N1, N2]
        self.script(self.fixture(reply(N1, "{bozuk")), self.fixture(reply(N2, json.dumps(o)), cost=0.08))
        jid = self.asked()
        job = self.at(jid, "error")
        self.assertEqual(job["code"], "bad_plan")
        self.assertEqual(len(self.calls()), 2)
        self.assertIn(B.EXTRACT_REASONS["json"], self.calls()[1]["stdin"])
        self.assertEqual(self.boss_approvals(), [])
        with self.assertRaises(AssertionError):
            self.br.next_msg(lambda m: m.get("type") == "boss.plan", 0.3)
        end = self.audit("boss.end")[0]
        self.assertEqual((end["state"], end["code"], end["attempts"]), ("error", "bad_plan", 2))

    def test_schema_error_is_named_in_the_retry(self):
        o = plan_obj()
        o["orders"][1]["role"] = "admin"
        self.nonces = [N1, N2]
        self.script(self.fixture(reply(N1, json.dumps(o))), self.fixture(reply(N2, json.dumps(plan_obj(extra=1)))))
        jid = self.asked()
        self.assertEqual(self.at(jid, "error")["code"], "bad_plan")
        self.assertIn("alan orders[1].role", self.calls()[1]["stdin"])

    def test_cost_caps(self):
        # the planning task stopped at its own budget
        self.script({"fixture": "claude_stream_max_budget.jsonl"})
        jid = self.asked()
        self.assertEqual(self.at(jid, "error")["code"], "cost_cap")
        self.assertEqual(self.boss_approvals(), [])
        # the browser lowered the run cap below the planning cap: Claude gets the lower one
        self.script(self.fixture(reply(N1)))
        jid = self.asked(maxCostUsd=0.15)
        job = self.at(jid, "approval")
        argv = self.calls()[0]["argv"]
        self.assertEqual((argv[argv.index("--max-budget-usd") + 1], job["maxCostUsd"]), ("0.15", 0.15))
        self.req({"type": "boss.cancel", "job": jid})
        # a reply that cost more than the whole run cap (Claude checks its budget only after a turn) is no plan
        self.req({"type": "boss.reset", "workspace": {"id": "pixel"}})      # fresh session: cost not cumulative
        self.script(self.fixture(reply(N1), cost=0.4))
        jid = self.asked(maxCostUsd=0.3)
        self.assertEqual(self.at(jid, "error")["code"], "cost_cap")
        # the browser can't raise it
        r = self.ask(maxCostUsd=1.5)
        self.assertEqual(r["code"], "bad_request")

    def test_request_refusals_and_busy(self):
        cases = [({"profile": "auto"}, "bad_request"), ({"brief": ""}, "bad_request"),
                 ({"workspace": {"id": "yok"}}, "workspace_denied"),
                 ({"workspace": {"id": "pixel", "sub": "../dis"}}, "workspace_denied"),
                 ({"workspace": {"id": "pixel", "sub": "olmayan"}}, "workspace_denied"),
                 ({"maxCostUsd": 5}, "bad_request")]
        for kw, code in cases:
            r = self.ask(**kw)
            self.assertEqual((r["type"], r.get("code")), ("error", code), kw)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.conn.boss.jobs, {})
        self.assertEqual([a for a in self.conn.agents.list() if a.get("boss")], [])
        self.script({"fixture": "claude_stream_slow.jsonl", "delay": 0.05, "hang": 10})
        jid = self.asked(workspace={"id": "pixel", "sub": "sub"})
        self.assertEqual(self.patron()["workspace"], {"id": "pixel", "sub": "sub"})
        self.assertEqual(self.ask()["code"], "busy")
        for t in ({"type": "boss.get", "job": "b_yokyokyoky"}, {"type": "boss.cancel", "job": "b_yokyokyoky"}):
            self.assertEqual(self.req(t)["code"], "unknown_job")
        self.assertEqual(self.req({"type": "boss.cancel", "job": "../x"})["code"], "bad_request")
        self.assertEqual(self.req({"type": "boss.cancel", "job": jid})["type"], "boss.ok")
        self.assertEqual(self.at(jid, "cancelled")["code"], "cancelled")

    def test_patron_agent_is_not_commandable_and_ignores_a_tampered_profile(self):
        self.script(self.fixture(reply(N1)))
        jid = self.asked()
        self.at(jid, "approval")
        self.req({"type": "boss.cancel", "job": jid})
        key = self.patron()["key"]
        r = self.req({"type": "agent.update", "agent": key, "profile": "dev"})
        self.assertEqual(r["code"], "not_allowed")
        r = self.req({"type": "task.start", "agent": key, "prompt": "ls"})
        self.assertEqual(r["code"], "not_allowed")
        # agents.json edited by hand: profile auto/dev, a model → the planning task stays readonly, no commands
        self.conn.agents.put(dict(self.patron(), profile="dev", model="opus"))
        self.script(self.fixture(reply(N1)))
        jid = self.asked()
        self.at(jid, "approval")
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--allowedTools", argv)
        self.assertNotIn("--model", argv)
        self.assertNotIn("Bash(git status *)", argv)
        self.assertEqual(self.patron()["profile"], "readonly")

    def test_policy_never_comes_from_a_browser(self):
        key = self.create(engine="claude", name="Gel", model=None, profile="dev")
        self.script(self.fixture("tamam"))
        ack = self.req({"type": "task.start", "agent": key, "prompt": "x", "policy": {"allow": ["Bash(*)"]},
                        "allow": ["Bash(*)"], "role": "test", "maxTurns": 1})
        self.assertEqual(ack["type"], "task.ack", ack)
        self.br.task_events(ack["task"])
        argv = self.calls()[0]["argv"]
        self.assertEqual(self.rules(argv, "--allowedTools"),
                         ["Edit(./**)", "Bash(git status *)", "Bash(git diff *)"])      # the device's own list
        self.assertNotIn("Bash(*)", argv)
        self.assertNotIn("Agent", argv)                                                 # ordinary task unchanged
        self.assertEqual(argv[argv.index("--max-turns") + 1], "50")

    def test_task_policy_overrides_the_agent_record_and_the_device_list(self):
        key = self.create(engine="claude", name="Gel", model=None, profile="dev")
        agent = self.conn.agents.get(key)

        def run(policy):
            self.script(self.fixture("tamam"))
            ack = self.conn.scheduler.submit(agent, "x", False, None, "yerel", False, policy=policy)
            self.assertEqual(self.wait_task_end(ack["task"])["status"], "success")
            return self.calls()[0]["argv"]
        argv = run(B.task_policy("patron", B.PLAN_MAX_TURNS))           # a dev agent record, a readonly task
        self.assertNotIn("--allowedTools", argv)
        self.assertEqual(self.rules(argv, "--disallowedTools")[-2:], ["Agent", "Task"])
        self.assertEqual(argv[argv.index("--max-turns") + 1], "10")
        argv = run(B.task_policy("test", 5))                            # the Test role: only its own commands
        self.assertEqual(self.rules(argv, "--allowedTools"), ["Edit(./**)"] + B.role_allow_rules("test"))
        self.assertNotIn("Bash(git status *)", argv)
        self.assertEqual(self.rules(argv, "--disallowedTools")[-2:], ["Agent", "Task"])
        self.assertEqual(argv[argv.index("--max-turns") + 1], "5")
        start = [r for r in self.conn.audit.read() if r["ev"] == "task.start"]
        self.assertEqual((start[-1]["profile"], start[-1]["role"]), ("dev", "test"))
        F.set_plan(self.fake_dir, [])
        from pixel_connector.tasks import TaskError
        with self.assertRaises(TaskError) as cm:                         # only a boss.TaskPolicy is accepted
            self.conn.scheduler.submit(agent, "x", False, None, "yerel", False, policy={"allow": ["Bash(*)"]})
        self.assertEqual(cm.exception.code, "internal")
        self.assertEqual(self.calls(), [])

    def wait_task_end(self, task):
        rec = None
        deadline = time.time() + 10
        while time.time() < deadline and rec is None:
            rec = next((r for r in self.conn.audit.read() if r["ev"] == "task.end" and r["task"] == task), None)
            time.sleep(0.02)
        self.assertIsNotNone(rec)
        return {"status": rec["status"], "error": {"code": rec.get("code")}}

    def test_revoke_cancels_planning_and_a_waiting_plan(self):
        phone = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, phone, iterations=PAIR_ITER)
        phone.connect_device(self.dev)
        self.script({"fixture": "claude_stream_slow.jsonl", "delay": 0.05, "hang": 10})
        jid = self.asked(browser=phone)
        self.wait_job(jid, lambda j: j["state"] == "planning" and self.calls())
        self.conn.revoke(phone.bid, by=self.br.bid)
        self.assertEqual(self.at(jid, "cancelled")["code"], "revoked")
        self.assertEqual(self.audit("boss.cancel")[0]["code"], "revoked")
        self.wait_job(jid, lambda j: not self.conn.scheduler.runs, what="planning task cancelled")
        # The run leaves scheduler.runs before its task.end is audited (tasks.py → app.py): wait for the record.
        task_end = []
        deadline = time.time() + 10
        while time.time() < deadline and not task_end:
            task_end = [r for r in self.conn.audit.read() if r["ev"] == "task.end"]
            time.sleep(0.02)
        self.assertTrue(task_end, "no task.end audited")
        self.assertEqual(task_end[-1]["status"], "cancelled")
        phone.close()
        # a plan waiting for approval
        phone2 = E.FakeBrowser(self.env, label="Tablet")
        E.pair(self.env, self.conn, phone2, iterations=PAIR_ITER)
        phone2.connect_device(self.dev)
        self.script(self.fixture(reply(N1)))
        jid = self.asked(browser=phone2)
        self.at(jid, "approval")
        self.conn.revoke(phone2.bid)
        self.assertEqual(self.at(jid, "cancelled")["code"], "revoked")
        self.assertEqual(self.boss_approvals(), [])
        phone2.close()

    def test_memory_reset(self):
        r = self.req({"type": "boss.reset", "workspace": {"id": "pixel"}})
        self.assertEqual((r["type"], r["job"], r["reset"]), ("boss.ok", None, False))
        self.script(self.fixture(reply(N1)))
        jid = self.asked()
        self.at(jid, "approval")
        self.assertEqual(self.req({"type": "boss.reset", "workspace": {"id": "pixel"}})["code"], "busy")
        self.req({"type": "boss.cancel", "job": jid})
        self.at(jid, "cancelled")
        self.assertTrue(self.patron()["claudeSessionStarted"])
        r = self.req({"type": "boss.reset", "workspace": {"id": "pixel"}})
        self.assertEqual((r["type"], r["reset"]), ("boss.ok", True))
        self.assertEqual((self.patron()["claudeSession"], self.patron()["claudeSessionStarted"]), (None, False))
        self.assertEqual(self.audit("boss.reset")[0]["agent"], self.patron()["key"])
        self.script(self.fixture(reply(N1)))
        jid = self.asked()
        self.at(jid, "approval")
        argv = self.calls()[0]["argv"]
        self.assertIn("--session-id", argv)
        self.assertNotIn("--resume", argv)
        self.assertEqual(self.req({"type": "boss.reset", "workspace": {"id": "pixel"}, "x": 1})["code"],
                         "bad_request")

    def test_connector_stop_ends_the_job(self):
        self.script(self.fixture(reply(N1)))
        jid = self.asked()
        self.at(jid, "approval")
        self.conn.boss.stop()
        job = self.conn.boss.get(jid)["job"]
        self.assertEqual((job["state"], job["code"]), ("cancelled", "stopped"))
        self.assertEqual(self.boss_approvals(), [])


class RemoteCreateOffTests(DeskBase):
    connector_overrides = {"remote": {"agentCreate": False, "agentDelete": True, "budgetEdit": True}}

    def test_boss_ask_is_not_allowed(self):
        r = self.ask()
        self.assertEqual((r["type"], r["code"]), ("error", "not_allowed"))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.conn.boss.jobs, {})


class ConfirmAgentCreateTests(DeskBase):
    connector_overrides = {"confirm": {"task": "never", "agentCreate": True, "runtime": False, "peerRevoke": False,
                                       "timeoutS": 120}}

    def test_ask_is_confirmed_on_the_computer_first(self):
        self.script(self.fixture(reply(N1)))
        self.br.send_e(self.dev, {"type": "boss.ask", "id": "q1", "workspace": {"id": "pixel"}, "brief": "x"})
        end = time.time() + 10
        while time.time() < end and not [p for p in self.conn.approvals.list() if p["kind"] == "agentCreate"]:
            time.sleep(0.02)
        (pend,) = [p for p in self.conn.approvals.list() if p["kind"] == "agentCreate"]
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.conn.boss.jobs, {})
        self.conn.approvals.resolve(pend["id"], False)
        r = self.br.next_msg(lambda m: m.get("re") == "q1")
        self.assertEqual(r["code"], "confirm_denied")
        self.assertEqual(self.conn.boss.jobs, {})


class CeilingTests(DeskBase):
    connector_overrides = {"boss": {"maxCostUsd": 2.0}}

    def test_device_ceiling_from_connector_json(self):
        self.assertEqual(self.req({"type": "peer.hello", "label": "x"})["boss"], {"maxCostUsd": 2.0})
        self.script(self.fixture(reply(N1)))
        jid = self.asked()
        job = self.at(jid, "approval")
        self.assertEqual(job["maxCostUsd"], 2.0)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.20")      # planning cap stays 0.20 $
        self.req({"type": "boss.cancel", "job": jid})
        self.assertEqual(self.ask(maxCostUsd=2.5)["code"], "bad_request")


if __name__ == "__main__":
    unittest.main()
