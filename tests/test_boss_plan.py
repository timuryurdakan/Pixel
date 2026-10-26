"""Patron Odası P1: plan schema and role library (docs/PATRON_ODASI.md §4.4, §4.5, §9 P1, §10 user decisions).

Pure tests, no engine, no network: the plan allow-list (every §4.4 rule, positive and negative), the built-in roles
(no `auto`, the Test role's narrow command list), the file-scope conflict check and the "at most 2 at a time" ready
list, the cost estimate and caps, the state table, and the steps built from a plan passing A10's template check.
"""

import copy
import json
import math
import unittest

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import boss as B
from pixel_connector import workflows as W


def order(oid="o1", role="frontend", **kw):
    o = {"id": oid, "role": role, "title": "Koyu tema renkleri",
         "goal": "web/css/style.css içinde koyu tema renk değişkenlerini ekle.",
         "accept": ["prefers-color-scheme: dark ile renkler değişir"], "files": ["web/css/style.css"], "after": []}
    o.update(kw)
    return o


def plan(*orders, **kw):
    p = {"v": 1, "summary": "Giriş sayfasına koyu tema ekle ve testini yaz.",
         "orders": list(orders) if orders else [order()]}
    p.update(kw)
    return p


def two_orders():
    return plan(order("o1"), order("o2", "test", title="Koyu tema testi", files=["tests/test_dark_theme.py"],
                                    after=["o1"]))


class PlanValidationTests(unittest.TestCase):
    def bad(self, raw, code="bad_plan", **kw):
        with self.assertRaises(B.BossError) as cm:
            B.validate_plan(raw, **kw)
        self.assertEqual(cm.exception.code, code)
        return cm.exception

    def test_design_example_is_valid_and_normalized(self):
        p = B.validate_plan(two_orders())
        self.assertEqual(p["v"], 1)
        self.assertEqual([o["id"] for o in p["orders"]], ["o1", "o2"])
        self.assertEqual(p["orders"][1]["after"], ["o1"])
        self.assertEqual(p["orders"][1]["role"], "test")
        # optional lists default to empty, texts are stripped
        raw = plan(order(title="  Başlık  "))
        del raw["orders"][0]["files"], raw["orders"][0]["after"]
        o = B.validate_plan(raw)["orders"][0]
        self.assertEqual((o["files"], o["after"], o["title"]), ([], [], "Başlık"))

    def test_input_is_not_modified(self):
        raw = two_orders()
        before = copy.deepcopy(raw)
        B.validate_plan(raw)
        self.assertEqual(raw, before)

    def test_unknown_fields_are_refused(self):
        self.bad(plan(extra=1))
        self.bad(plan(order(profile="dev")))           # the Boss can never pick a profile
        self.bad(plan(order(needs=[])))                # the draft name is not accepted
        self.bad(plan(order(engine="claude")))
        self.bad(plan(order(model="x")))

    def test_root_and_order_types(self):
        for raw in (None, [], "plan", 1, True):
            self.bad(raw)
        for orders in (None, {}, "o1", [None], ["o1"], [[order()]]):
            self.bad(plan(orders=orders))
        for k in ("id", "role", "title", "goal", "accept"):
            o = order()
            del o[k]
            self.bad(plan(o))

    def test_version_must_be_exactly_one(self):
        for v in (True, 1.0, "1", 2, 0, None, float("nan")):
            self.bad(plan(v=v))
        raw = plan()
        del raw["v"]
        self.bad(raw)

    def test_summary(self):
        B.validate_plan(plan(summary="x"))
        B.validate_plan(plan(summary="ş" * B.SUMMARY_MAX))
        for s in ("", "   ", "ş" * (B.SUMMARY_MAX + 1), "iki\nsatır", 5, None, ["x"], True):
            self.bad(plan(summary=s))

    def test_order_count_limits(self):
        B.validate_plan(plan(*[order("o%d" % i, files=["f%d.txt" % i]) for i in range(1, B.MAX_ORDERS + 1)]))
        self.bad(plan(*[order("o%d" % i) for i in range(1, B.MAX_ORDERS + 2)]))
        self.bad(plan(orders=[]))
        self.assertEqual(B.MAX_ORDERS, 4)                         # §10/4

    def test_fix_round_allows_at_most_two_orders(self):
        B.validate_plan(plan(order("o1"), order("o2")), fix_round=True)
        self.bad(plan(order("o1"), order("o2"), order("o3")), fix_round=True)
        self.assertEqual(B.MAX_FIX_ORDERS, 2)

    def test_order_ids(self):
        for oid in ("o1", "o9"):
            B.validate_plan(plan(order(oid)))
        for oid in ("o0", "o10", "O1", "x1", "o1\n", " o1", "", 1, None, True, "о1"):   # last: Cyrillic о
            self.bad(plan(order(oid)))
        self.bad(plan(order("o1"), order("o1")))

    def test_roles(self):
        for rid in B.ROLES:
            B.validate_plan(plan(order(role=rid)))
        for rid in ("patron", "boss", "Frontend", "admin", "", None, 1, ["test"]):
            self.bad(plan(order(role=rid)))

    def test_role_whose_profile_is_off_is_unavailable(self):
        self.bad(plan(order(role="test")), code="role_unavailable", enabled_profiles=("readonly", "edit"))
        B.validate_plan(plan(order(role="frontend")), enabled_profiles=("readonly", "edit"))
        self.bad(plan(order(role="review")), code="role_unavailable", enabled_profiles=())

    def test_title(self):
        B.validate_plan(plan(order(title="ğ" * B.TITLE_MAX)))
        for t in ("", " ", "ğ" * (B.TITLE_MAX + 1), "a\nb", "a\tb", 3, None):
            self.bad(plan(order(title=t)))

    def test_goal_bytes_are_counted_in_utf8(self):
        B.validate_plan(plan(order(goal="a" * B.GOAL_MAX_BYTES)))
        B.validate_plan(plan(order(goal="ş" * (B.GOAL_MAX_BYTES // 2))))          # 2 bytes each: exactly at the limit
        self.bad(plan(order(goal="ş" * (B.GOAL_MAX_BYTES // 2 + 1))))
        self.bad(plan(order(goal="a" * (B.GOAL_MAX_BYTES + 1))))
        self.bad(plan(order(goal="😀" * (B.GOAL_MAX_BYTES // 4 + 1))))
        B.validate_plan(plan(order(goal="birinci satır\nikinci satır\n\tgirintili")))
        for g in ("", "\n", "a\rb", 1, None):
            self.bad(plan(order(goal=g)))

    def test_accept(self):
        B.validate_plan(plan(order(accept=["x"] * B.ACCEPT_MAX_ITEMS)))
        B.validate_plan(plan(order(accept=["ı" * B.ACCEPT_MAX])))
        for a in ([], ["x"] * (B.ACCEPT_MAX_ITEMS + 1), ["ı" * (B.ACCEPT_MAX + 1)], ["a\nb"], [""], [1], "x", None,
                  {"x": 1}):
            self.bad(plan(order(accept=a)))

    def test_hidden_and_bidi_characters_are_refused_everywhere(self):
        for ch in ("\u202e", "\u2066", "\u200b", "\u200d", "\ufeff", "\x00", "\x1b", "\u2028", "\u2029"):
            self.bad(plan(summary="özet" + ch))
            self.bad(plan(order(title="başlık" + ch)))
            self.bad(plan(order(goal="hedef" + ch)))
            self.bad(plan(order(accept=["ölçüt" + ch])))
            self.bad(plan(order(files=["web/a" + ch + ".css"])))

    def test_file_paths(self):
        ok = ["web/css/style.css", "README.md", "web/js/*.js", "tests/**", "docs/Türkçe adı.md", "a-b_c.d/e"]
        self.assertEqual(B.validate_plan(plan(order(files=ok)))["orders"][0]["files"], ok)
        B.validate_plan(plan(order(files=[])))
        B.validate_plan(plan(order(files=["f%d" % i for i in range(B.FILES_MAX_ITEMS)])))
        B.validate_plan(plan(order(files=["a" * B.FILE_MAX])))
        bad = ["/etc/passwd", "../x", "a/../../b", "a/..", "..", "~/x", "~", "C:/x", "c:x", "C:\\x", "a\\b",
               "\\\\server\\share", "a//b", "a/", "/", "./a", "a/./b", ".", "", " ", "a\x00b", "a\nb", "a?b", "a[1]",
               "a{b,c}", "a" * (B.FILE_MAX + 1), 1, None, ["a"]]
        for f in bad:
            self.bad(plan(order(files=[f])))
        self.bad(plan(order(files=["f%d" % i for i in range(B.FILES_MAX_ITEMS + 1)])))
        self.bad(plan(order(files="web/a.css")))

    def test_after_only_names_earlier_orders(self):
        B.validate_plan(plan(order("o1"), order("o2", after=["o1"]), order("o3", after=["o1", "o2"])))
        # forward, self, unknown, cyclic, duplicate and wrong types
        self.bad(plan(order("o1", after=["o2"]), order("o2")))
        self.bad(plan(order("o1", after=["o1"])))
        self.bad(plan(order("o1"), order("o2", after=["o7"])))
        self.bad(plan(order("o1", after=["o2"]), order("o2", after=["o1"])))
        self.bad(plan(order("o1"), order("o2", after=["o1", "o1"])))
        for a in ("o1", None, [1], [None], {"o1": 1}):
            self.bad(plan(order("o1"), order("o2", after=a)))

    def test_order_prompt_must_fit_a_step_instruction(self):
        # each field at its own limit, together over the A10 step limit: refused, never cut
        huge = order(title="ğ" * B.TITLE_MAX, goal="ş" * (B.GOAL_MAX_BYTES // 2),
                     accept=["ı" * B.ACCEPT_MAX] * B.ACCEPT_MAX_ITEMS,
                     files=["d/" + "a" * (B.FILE_MAX - 4) + "%02d" % i for i in range(B.FILES_MAX_ITEMS)])
        e = self.bad(plan(huge, summary="ö" * B.SUMMARY_MAX))
        self.assertIn("orders[0]", str(e))

    def test_error_detail_names_the_field_and_never_echoes_text(self):
        canary = "PLAN-CANARY-5e1c"
        e = self.bad(plan(order(title=canary + "\u202e")))
        self.assertIn("orders[0].title", str(e))
        self.assertNotIn(canary, str(e))


class RoleLibraryTests(unittest.TestCase):
    def test_builtin_roles(self):
        self.assertEqual(set(B.ROLES), {"frontend", "backend", "test", "docs", "review"})
        profiles = {rid: r["profile"] for rid, r in B.ROLES.items()}
        self.assertEqual(profiles, {"frontend": "edit", "backend": "edit", "test": "dev", "docs": "edit",
                                    "review": "readonly"})
        for rid, r in B.ROLES.items():
            self.assertEqual(r["id"], rid)
            self.assertEqual(r["engine"], "claude")
            self.assertNotEqual(r["profile"], "auto")
            self.assertLessEqual(len(r["name"]), W.ROLE_MAX)
            self.assertIn(r["badge"], B.BADGES)
            self.assertGreater(r["costUsd"], 0)
            self.assertLessEqual(r["maxTurns"], B.RUN_MAX_TURNS)
            self.assertEqual(B.validate_role(r), r)
        self.assertEqual(B.BOSS_ROLE["profile"], "readonly")
        self.assertNotIn(B.BOSS_ROLE["id"], B.ROLES)              # a plan can never name the Boss

    def test_only_the_test_role_asks_first_and_has_commands(self):
        for rid, r in B.ROLES.items():
            self.assertEqual(r["confirm"], rid == "test")
            self.assertEqual(bool(r["allow"]), rid == "test")
        for rule in B.ROLES["test"]["allow"]:
            self.assertRegex(rule, r"^Bash\([a-z0-9 .-]+ \*\)\Z")
            self.assertIn(rule[5:-3], B.TEST_COMMANDS)

    def test_test_role_rules_are_its_own_never_the_device_dev_list(self):
        # the device's profiles.dev.allow (e.g. git, or a bare "Bash") is never part of a role's commands
        device_dev_allow = ["Bash", "Bash(git status *)", "Bash(rm *)"]
        rules = B.role_allow_rules("test")
        self.assertTrue(rules)
        self.assertFalse(set(rules) & set(device_dev_allow))
        self.assertEqual(B.role_allow_rules("frontend"), [])
        rules.append("Bash")                                      # a copy: the library itself cannot be changed
        self.assertNotIn("Bash", B.ROLES["test"]["allow"])

    def role(self, **kw):
        r = dict(B.ROLES["frontend"])
        r.update(kw)
        return r

    def bad_role(self, raw):
        with self.assertRaises(B.BossError) as cm:
            B.validate_role(raw)
        self.assertEqual(cm.exception.code, "bad_role")

    def test_role_validation_refusals(self):
        self.bad_role(self.role(profile="auto"))
        self.bad_role(self.role(profile="root"))
        self.bad_role(self.role(engine="ollama"))
        self.bad_role(self.role(extra=1))
        self.bad_role(self.role(id="Bad id"))
        self.bad_role(self.role(name="x" * (W.ROLE_MAX + 1)))
        self.bad_role(self.role(badge="skull"))
        self.bad_role(self.role(model="-x"))
        for cost in (0, -1, True, float("nan"), float("inf"), "0.1", B.RUN_COST_CEILING_MAX + 0.01):
            self.bad_role(self.role(costUsd=cost))
        for turns in (0, 1.5, True, B.RUN_MAX_TURNS + 1):
            self.bad_role(self.role(maxTurns=turns))
        self.bad_role(self.role(instruction="talimat\u202e"))
        self.bad_role(self.role(confirm="yes"))
        r = self.role()
        del r["instruction"]
        self.bad_role(r)

    def test_allow_rules_only_on_dev_and_only_narrow_test_commands(self):
        dev = dict(B.ROLES["test"])
        B.validate_role(dict(dev, allow=["Bash(pytest *)"]))
        B.validate_role(dict(dev, allow=[]))
        self.bad_role(self.role(allow=["Bash(pytest *)"]))        # an `edit` role takes no shell rules
        for rule in ("Bash", "Bash(*)", "Bash(* *)", "Bash(sh *)", "Bash(bash -c *)", "Bash(python3 *)",
                     "Bash(python3 -m unittest; rm -rf / *)", "Bash(npm test && curl x *)", "Bash(pytest | sh *)",
                     "Bash(pytest $(id) *)", "Bash(`id` *)", "Bash(git status *)", "Bash(npm *)", "Edit(**)",
                     "Read(~/.ssh/**)", "WebFetch", "Bash(pytest *)\n", "Bash(pytest\u202e *)", "", 1, None):
            self.bad_role(dict(dev, allow=[rule]))
        self.bad_role(dict(dev, allow="Bash(pytest *)"))


class FileConflictTests(unittest.TestCase):
    def test_exact_paths(self):
        self.assertTrue(B.files_conflict(["web/a.css"], ["web/a.css"]))
        self.assertTrue(B.files_conflict(["web/A.css"], ["web/a.css"]))          # case-insensitive file systems
        self.assertFalse(B.files_conflict(["web/a.css"], ["web/b.css"]))
        self.assertFalse(B.files_conflict(["web/a.css"], ["web/a.css.bak"]))
        self.assertFalse(B.files_conflict(["web/ab"], ["web/a"]))

    def test_directory_scopes(self):
        self.assertTrue(B.files_conflict(["web"], ["web/css/a.css"]))
        self.assertTrue(B.files_conflict(["web/css/a.css"], ["web"]))
        self.assertFalse(B.files_conflict(["web2"], ["web/a.css"]))

    def test_wildcards_conflict_below_their_fixed_part(self):
        self.assertTrue(B.files_conflict(["web/*.css"], ["web/a.css"]))
        self.assertTrue(B.files_conflict(["web/*.css"], ["web/js/a.js"]))        # unclear → sequential
        self.assertTrue(B.files_conflict(["*.py"], ["tests/x.py"]))
        self.assertTrue(B.files_conflict(["**"], ["a"]))
        self.assertTrue(B.files_conflict(["web/*/x.css"], ["web/css/y.css"]))
        self.assertFalse(B.files_conflict(["web/css/*.css"], ["web/js/a.js"]))
        self.assertFalse(B.files_conflict(["web/*.css"], ["tests/*.py"]))

    def test_empty_scope_is_unclear_and_conflicts(self):
        self.assertTrue(B.files_conflict([], ["a"]))
        self.assertTrue(B.files_conflict(["a"], []))
        self.assertTrue(B.files_conflict([], []))

    def test_any_pair_in_the_lists(self):
        self.assertTrue(B.files_conflict(["a", "b/c"], ["x", "b"]))
        self.assertFalse(B.files_conflict(["a", "b/c"], ["x", "b/d"]))

    def test_two_readonly_orders_never_conflict(self):
        p = B.validate_plan(plan(order("o1", "review", files=[]), order("o2", "review", files=[])))
        self.assertFalse(B.orders_conflict(p["orders"][0], p["orders"][1]))
        p = B.validate_plan(plan(order("o1", "review", files=["a"]), order("o2", "frontend", files=["a"])))
        self.assertTrue(B.orders_conflict(p["orders"][0], p["orders"][1]))


class ScheduleTests(unittest.TestCase):
    def run_all(self, p):
        """Simulate the engine: start what is ready, finish the oldest running order, repeat → list of started
        batches and the largest number running at once."""
        finished, running, batches, peak = set(), [], [], 0
        while len(finished) < len(p["orders"]):
            ready = B.ready_orders(p, finished, set(running))
            if ready:
                batches.append(ready)
                running.extend(ready)
            peak = max(peak, len(running))
            self.assertTrue(running, "deadlock")
            finished.add(running.pop(0))
        return batches, peak

    def test_independent_orders_run_two_at_a_time(self):
        p = B.validate_plan(plan(*[order("o%d" % i, files=["f%d" % i]) for i in range(1, 5)]))
        self.assertEqual(B.ready_orders(p, set(), set()), ["o1", "o2"])
        self.assertEqual(B.ready_orders(p, set(), {"o1"}), ["o2"])
        self.assertEqual(B.ready_orders(p, set(), {"o1", "o2"}), [])
        self.assertEqual(B.ready_orders(p, {"o1"}, {"o2"}), ["o3"])
        batches, peak = self.run_all(p)
        self.assertEqual(peak, B.MAX_PARALLEL)
        self.assertEqual(B.MAX_PARALLEL, 2)                       # §10/2
        self.assertEqual(batches, [["o1", "o2"], ["o3"], ["o4"]])

    def test_after_and_conflicts_keep_written_order(self):
        p = B.validate_plan(plan(order("o1", files=["web/a.css"]), order("o2", files=["web/a.css"]),
                                 order("o3", files=["docs/x.md"], after=["o1"]), order("o4", files=["tests/t.py"])))
        deps = B.dependencies(p)
        self.assertEqual(deps, {"o1": [], "o2": ["o1"], "o3": ["o1"], "o4": []})
        self.assertEqual(B.ready_orders(p, set(), set()), ["o1", "o4"])
        self.assertEqual(B.ready_orders(p, {"o1"}, {"o4"}), ["o2"])
        self.assertEqual(B.ready_orders(p, {"o1", "o4"}, set()), ["o2", "o3"])
        batches, peak = self.run_all(p)
        self.assertLessEqual(peak, 2)
        started = [o for b in batches for o in b]
        self.assertEqual(sorted(started), ["o1", "o2", "o3", "o4"])
        self.assertLess(started.index("o1"), started.index("o2"))

    def test_unclear_scope_runs_alone_in_order(self):
        p = B.validate_plan(plan(order("o1", files=["a"]), order("o2", files=[]), order("o3", files=["b"])))
        self.assertEqual(B.dependencies(p)["o2"], ["o1"])
        self.assertEqual(B.dependencies(p)["o3"], ["o2"])
        self.assertEqual(B.ready_orders(p, set(), set()), ["o1"])
        batches, _ = self.run_all(p)
        self.assertEqual(batches, [["o1"], ["o2"], ["o3"]])

    def test_max_parallel_argument_cannot_raise_the_limit(self):
        p = B.validate_plan(plan(*[order("o%d" % i, files=["f%d" % i]) for i in range(1, 5)]))
        self.assertEqual(B.ready_orders(p, set(), set(), max_parallel=1), ["o1"])
        self.assertEqual(B.ready_orders(p, set(), set(), max_parallel=4), ["o1", "o2"])


class CostTests(unittest.TestCase):
    def test_caps_from_user_decision(self):
        self.assertEqual(B.RUN_COST_DEFAULT, 1.0)
        self.assertEqual(B.RUN_COST_CEILING_MAX, 3.0)
        self.assertEqual((B.PLAN_COST_USD, B.PLAN_MAX_TURNS), (0.2, 10))
        self.assertEqual((B.RUN_TIMEOUT_S, B.RUN_MAX_TURNS), (45 * 60, 100))
        self.assertEqual((B.MAX_FIX_ROUNDS, B.MAX_FIX_ORDERS), (1, 2))

    def test_device_ceiling_from_connector_json(self):
        self.assertEqual(B.run_ceiling(None), 1.0)
        self.assertEqual(B.run_ceiling(3), 3.0)
        self.assertEqual(B.run_ceiling(0.5), 0.5)
        for v in (3.01, 5, 0, 0.05, -1, True, "2", float("nan"), float("inf"), [1]):
            with self.assertRaises(B.BossError) as cm:
                B.run_ceiling(v)
            self.assertEqual(cm.exception.code, "bad_config")

    def test_browser_may_only_lower(self):
        self.assertEqual(B.run_cost_cap(None, 1.0), 1.0)
        self.assertEqual(B.run_cost_cap(0.5, 1.0), 0.5)
        self.assertEqual(B.run_cost_cap(1.0, 1.0), 1.0)
        self.assertEqual(B.run_cost_cap(B.RUN_COST_MIN, 1.0), B.RUN_COST_MIN)
        for v in (1.01, 3.0, B.RUN_COST_MIN / 2, 0, -0.5, True, "0.5", float("nan"), float("inf")):
            with self.assertRaises(B.BossError) as cm:
                B.run_cost_cap(v, 1.0)
            self.assertEqual(cm.exception.code, "bad_request")      # refused, never clamped (S26-04)

    def test_planning_cap_never_exceeds_the_run_cap(self):
        self.assertEqual(B.plan_cost_cap(1.0), 0.2)
        self.assertEqual(B.plan_cost_cap(0.15), 0.15)

    def test_estimate(self):
        p = B.validate_plan(two_orders())
        want = B.PLAN_COST_USD + B.ROLES["frontend"]["costUsd"] + B.ROLES["test"]["costUsd"] + B.REVIEW_COST_USD
        self.assertTrue(math.isclose(B.estimate(p), round(want, 2)))
        want_fix = B.ROLES["frontend"]["costUsd"] + B.ROLES["test"]["costUsd"] + B.REVIEW_COST_USD
        self.assertTrue(math.isclose(B.estimate(p, fix_round=True), round(want_fix, 2)))

    def test_guardrails_are_a_valid_a10_guardrail_object(self):
        g = B.run_guardrails(0.5)
        self.assertEqual(g, {"maxCostUsd": 0.5, "maxTurns": 100, "timeoutS": 2700})


class StateTableTests(unittest.TestCase):
    def test_valid_path_and_fix_round(self):
        path = [B.IDLE, B.PLANNING, B.APPROVAL, B.RUNNING, B.REVIEWING, B.APPROVAL, B.RUNNING, B.REVIEWING,
                B.REPORTING, B.DONE]
        for cur, new in zip(path, path[1:]):
            W.check_transition(B.BOSS_TRANSITIONS, cur, new)
        for cur, new in ((B.PLANNING, B.ERROR), (B.APPROVAL, B.CANCELLED), (B.RUNNING, B.REPORTING),
                         (B.RUNNING, B.ERROR), (B.REVIEWING, B.CANCELLED), (B.REPORTING, B.ERROR)):
            W.check_transition(B.BOSS_TRANSITIONS, cur, new)

    def test_invalid_transitions(self):
        for cur, new in ((B.IDLE, B.RUNNING), (B.PLANNING, B.RUNNING), (B.APPROVAL, B.REVIEWING),
                         (B.DONE, B.PLANNING), (B.ERROR, B.IDLE), (B.CANCELLED, B.RUNNING), (B.RUNNING, B.DONE),
                         ("bogus", B.PLANNING)):
            with self.assertRaises(W.WorkflowError) as cm:
                W.check_transition(B.BOSS_TRANSITIONS, cur, new)
            self.assertEqual(cm.exception.code, "bad_transition")
        self.assertEqual(set(B.STATE_LABELS), set(B.BOSS_TRANSITIONS) | B.TERMINAL)


class PlanToStepsTests(unittest.TestCase):
    def template_for(self, p):
        return {"id": "patron", "name": "Patron", "guardrails": B.run_guardrails(1.0), "steps": B.plan_to_steps(p)}

    def test_steps_pass_the_a10_template_check(self):
        for p in (two_orders(), plan(*[order("o%d" % i, role=r, files=["f%d" % i]) for i, r in
                                         enumerate(("frontend", "backend", "docs", "review"), 1)])):
            v = B.validate_plan(p)
            t = W.validate_template(self.template_for(v), auto_enabled=False)
            ids = [s["id"] for s in t["steps"]]
            self.assertEqual(ids, [o["id"] for o in v["orders"]] + [B.REVIEW_STEP_ID])
            self.assertTrue(all(s["type"] == "agent" and s["engine"] == "claude" for s in t["steps"]))
            self.assertEqual(t["steps"][-1]["profile"], "readonly")

    def test_largest_valid_plan_still_fits(self):
        big = order(title="ğ" * B.TITLE_MAX, goal="a" * B.GOAL_MAX_BYTES, accept=["kısa"],
                    files=["web/a.css"])
        orders = [dict(big, id="o%d" % i, files=["f%d" % i]) for i in range(1, B.MAX_ORDERS + 1)]
        v = B.validate_plan(plan(*orders, summary="ö" * B.SUMMARY_MAX))
        W.validate_template(self.template_for(v), auto_enabled=False)

    def test_profile_comes_from_the_role(self):
        v = B.validate_plan(plan(order("o1", "review"), order("o2", "test", files=["t.py"])))
        steps = B.plan_to_steps(v)
        self.assertEqual([(s["role"], s["profile"]) for s in steps],
                         [("İnceleme", "readonly"), ("Test", "dev"), ("Patron", "readonly")])

    def test_instruction_is_fixed_text_plus_order_fields(self):
        v = B.validate_plan(two_orders())
        s = B.plan_to_steps(v)[0]
        o = v["orders"][0]
        for part in (B.ROLES["frontend"]["instruction"], v["summary"], o["title"], o["goal"], o["accept"][0],
                     o["files"][0], B.ORDER_RULES):
            self.assertIn(part, s["prompt"])
        self.assertTrue(s["prompt"].startswith(B.ORDER_HEAD))
        self.assertFalse(s["usePrevious"] if "usePrevious" in s else False)
        json.dumps(s)                                             # plain data


if __name__ == "__main__":
    unittest.main()
