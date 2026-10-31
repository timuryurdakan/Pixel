"""F4-3a: the browser Boss shares the connector's rules (docs/F43_TASARIM.md §3).

web/js/boss_rules.js and tests/boss_plan_vectors.json are generated from pixel_connector/boss.py
(`python3 tests/_boss_shared.py --write`). If boss.py changes a limit, a role, a prompt or a validation outcome and
these files are not regenerated, this test fails, so the two Bosses cannot drift apart silently.
"""

import json
import unittest

import _boss_shared as S


class SharedRulesTests(unittest.TestCase):
    def test_rules_file_is_generated_from_boss_py(self):
        self.assertEqual(S.RULES_JS.read_text(encoding="utf-8"), S.rules_js(),
                         "run: python3 tests/_boss_shared.py --write")

    def test_vectors_match_the_connector(self):
        stored = json.loads(S.VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(stored, json.loads(json.dumps(S.vectors(), ensure_ascii=False)),
                         "run: python3 tests/_boss_shared.py --write")

    def test_vectors_cover_both_outcomes(self):
        v = S.vectors()
        codes = {c["expect"].get("code", "ok") for c in v["plans"]}
        self.assertTrue({"ok", "bad_plan", "role_unavailable"} <= codes)
        self.assertTrue({"ok", "bad_plan"} <= {c["expect"].get("code", "ok") for c in v["blocks"]})

    def test_rules_name_no_secret_or_connector_only_detail(self):
        r = S.rules()
        self.assertNotIn("allow", json.dumps(r["roles"]))         # command allow lists stay in the connector
        self.assertEqual(set(r["roles"]), {k for d in r["departments"] for k in d["roles"]})


if __name__ == "__main__":
    unittest.main()
