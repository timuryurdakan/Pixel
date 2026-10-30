"""E1a: the Boss's departments (docs/EVRENSEL_PLATFORM_ARASTIRMA_RAPORU.md §2.3, docs/GOREV_PANOSU.md E1a).

A `boss.ask` may name a department; the Boss then plans only with that department's roles. The software department
is the old role library and the default, so older browsers keep working. The new roles write text files only: no
command, no `dev`, no `auto`. Unit tests on the pure parts, then one job end to end with the fake engine.
"""

import json
import unittest

import _helpers  # noqa: F401  (sys.path)

from test_boss_desk import N1, N2, DeskBase, reply
from test_boss_plan import order, plan

from pixel_connector import app as A
from pixel_connector import boss as B

NEW_DEPARTMENTS = ("icerik", "akademik", "girisim", "kisisel")


def content_plan(**kw):
    p = {"v": 1, "summary": "Blog yazısı ve gözden geçirme.",
         "orders": [{"id": "o1", "role": "yazar", "title": "Blog yazısı", "goal": "Kahve hakkında bir yazı yaz.",
                     "accept": ["yazilar/kahve.md var"], "files": ["yazilar/kahve.md"]},
                    {"id": "o2", "role": "editor", "title": "Gözden geçir", "goal": "Yazıyı oku, not yaz.",
                     "accept": ["Kısa bir rapor"], "after": ["o1"]}]}
    p.update(kw)
    return p


class DepartmentLibraryTests(unittest.TestCase):
    def test_the_software_department_is_the_old_library_and_the_default(self):
        self.assertEqual(B.DEFAULT_DEPARTMENT, "yazilim")
        self.assertEqual(set(B.DEPARTMENTS), {"yazilim", *NEW_DEPARTMENTS})
        self.assertEqual(set(B.DEPARTMENTS["yazilim"]["roles"]), set(B.ROLES))
        for rid, role in B.ROLES.items():
            self.assertIs(B.ALL_ROLES[rid], role)

    def test_new_roles_only_read_or_edit_and_never_run_commands(self):
        for dep in NEW_DEPARTMENTS:
            roles = B.DEPARTMENTS[dep]["roles"]
            self.assertTrue(2 <= len(roles) <= 4, dep)
            for rid in roles:
                r = B.ALL_ROLES[rid]
                self.assertIn(r["profile"], ("readonly", "edit"), rid)
                self.assertEqual(r["allow"], [], rid)
                self.assertFalse(r["confirm"], rid)
                self.assertNotIn(rid, B.ROLES)                       # the software library is unchanged
                self.assertIn(r["badge"], B.BADGES)
            self.assertTrue(any(B.ALL_ROLES[rid]["profile"] == "readonly" for rid in roles), dep)   # a reviewer

    def test_role_ids_are_unique_across_departments(self):
        seen = {}
        for dep, d in B.DEPARTMENTS.items():
            for rid in d["roles"]:
                self.assertNotIn(rid, seen, "%s in %s and %s" % (rid, dep, seen.get(rid)))
                seen[rid] = dep
        self.assertEqual(set(seen), set(B.ALL_ROLES))
        self.assertNotIn(B.BOSS_ROLE["id"], B.ALL_ROLES)

    def test_public_list_has_ids_names_and_role_ids_only(self):
        pub = B.departments_public()
        self.assertEqual([d["id"] for d in pub], list(B.DEPARTMENTS))
        for d in pub:
            self.assertEqual(set(d), {"id", "name", "roles"})
            self.assertEqual(d["roles"], list(B.DEPARTMENTS[d["id"]]["roles"]))


class DepartmentPlanTests(unittest.TestCase):
    def test_a_plan_may_only_use_its_departments_roles(self):
        icerik = B.DEPARTMENTS["icerik"]["roles"]
        B.validate_plan(content_plan(), roles=icerik)
        with self.assertRaises(B.BossError) as cm:
            B.validate_plan(plan(order(role="frontend")), roles=icerik)
        self.assertEqual((cm.exception.code, str(cm.exception)), ("bad_plan", "orders[0].role"))
        with self.assertRaises(B.BossError):
            B.validate_plan(content_plan())                          # default: the software department
        B.validate_plan(plan(order(role="frontend")))

    def test_disabled_edit_profile_still_makes_a_role_unavailable(self):
        with self.assertRaises(B.BossError) as cm:
            B.validate_plan(content_plan(), enabled_profiles=("readonly",), roles=B.DEPARTMENTS["icerik"]["roles"])
        self.assertEqual(cm.exception.code, "role_unavailable")

    def test_prompts_list_only_the_departments_roles(self):
        p = B.planning_prompt("Bir blog yazısı", N1, "icerik")
        self.assertIn(B.ALL_ROLES["yazar"]["instruction"], p)
        self.assertNotIn(B.ROLES["frontend"]["instruction"], p)
        self.assertIn(B.ROLES["frontend"]["instruction"], B.planning_prompt("x", N1))
        valid = B.validate_plan(content_plan(), roles=B.DEPARTMENTS["icerik"]["roles"])
        r = B.review_prompt(valid, N2, True, "icerik")
        self.assertIn(B.ALL_ROLES["yazar"]["instruction"], r)
        self.assertNotIn(B.ROLES["frontend"]["instruction"], r)

    def test_ask_department_is_allow_listed(self):
        base = {"type": "boss.ask", "workspace": {"id": "pixel"}, "brief": "Bir şey"}
        self.assertEqual(B.parse_ask(base, 1.0)["department"], "yazilim")
        for dep in B.DEPARTMENTS:
            self.assertEqual(B.parse_ask(dict(base, department=dep), 1.0)["department"], dep)
        for bad in ("", "x", "İcerik", "ICERIK", " icerik", None, 1, ["icerik"], {"id": "icerik"}, "patron"):
            with self.assertRaises(B.BossError) as cm:
                B.parse_ask(dict(base, department=bad), 1.0)
            self.assertEqual(cm.exception.code, "bad_request", bad)


class DepartmentDeskTests(DeskBase):
    def test_device_info_lists_the_departments(self):
        info = self.req({"type": "peer.hello", "label": "x"})
        self.assertIn("bossDepartments", info["features"])
        self.assertEqual(info["bossDepartments"], B.departments_public())
        self.assertIn("bossDepartments", A.FEATURES)

    def test_a_content_job_plans_with_content_roles_end_to_end(self):
        self.script(self.fixture(reply(N1, json.dumps(content_plan(), ensure_ascii=False)), cost=0.04))
        jid = self.asked(department="icerik")
        job = self.at(jid, "approval")
        self.assertEqual(job["department"], "icerik")
        stdin = self.calls()[0]["stdin"]
        self.assertIn(B.ALL_ROLES["yazar"]["instruction"], stdin)
        self.assertNotIn(B.ROLES["frontend"]["instruction"], stdin)
        card = self.br.next_msg(lambda m: m.get("type") == "boss.plan" and m["job"] == jid)
        self.assertEqual(set(card["roles"]), {"yazar", "editor"})
        self.assertEqual(self.audit("boss.ask")[0]["dept"], "icerik")

    def test_a_software_role_in_a_content_job_is_refused_then_retried(self):
        wrong = json.dumps(plan(order(role="frontend")))
        self.nonces = [N1, N2]
        self.script(self.fixture(reply(N1, wrong)), self.fixture(reply(N2, wrong)))
        jid = self.asked(department="icerik")
        self.assertEqual(self.at(jid, "error")["code"], "bad_plan")
        self.assertIn("alan orders[0].role", self.calls()[1]["stdin"])
        self.assertEqual(self.boss_approvals(), [])

    def test_without_a_department_the_old_flow_is_unchanged(self):
        self.script(self.fixture(reply(N1), cost=0.04))
        jid = self.asked()
        self.assertEqual(self.at(jid, "approval")["department"], "yazilim")
        self.assertIn(B.ROLES["frontend"]["instruction"], self.calls()[0]["stdin"])


if __name__ == "__main__":
    unittest.main()
