"""F4-3b: the browser workflow runner shares the connector's rules (docs/F43B_TASARIM.md §3).

web/js/workflow_rules.js and tests/workflow_vectors.json are generated from pixel_connector/workflows.py and
tasks.handoff_prompt (`python3 tests/_workflow_shared.py --write`). If the connector changes a limit, a prompt text, the
state machine or a validation outcome and these files are not regenerated, this test fails, so the two runners cannot
drift apart silently. tests/js/browser_workflows.test.js runs the same vectors through the JS port.
"""

import json
import re
import unittest

import _workflow_shared as S
from pixel_connector import workflows as W

ROOT = S.ROOT


class SharedRulesTests(unittest.TestCase):
    def test_rules_file_is_generated_from_workflows_py(self):
        self.assertEqual(S.RULES_JS.read_text(encoding="utf-8"), S.rules_js(),
                         "run: python3 tests/_workflow_shared.py --write")

    def test_vectors_match_the_connector(self):
        stored = json.loads(S.VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(stored, json.loads(json.dumps(S.vectors(), ensure_ascii=False)),
                         "run: python3 tests/_workflow_shared.py --write")

    def test_vectors_cover_both_outcomes_and_every_template_field(self):
        v = S.vectors()
        details = {c["expect"].get("detail", "ok") for c in v["templates"]}
        for where in ("ok", "template", "id", "name", "description", "folder", "steps", "guardrails",
                      "guardrails.maxCostUsd", "guardrails.maxTurns", "guardrails.timeoutS", "steps[0].engine",
                      "steps[0].engines", "steps[0].profile", "steps[0].model", "steps[0].role", "steps[0].prompt",
                      "steps[0].useBrief", "steps[1].usePrevious", "steps[1].timeoutS", "steps[0].type"):
            self.assertIn(where, details)
        self.assertEqual({c["expect"].get("code", "ok") for c in v["prompts"]}, {"ok", "too_large"})
        self.assertIn(True, {c["expect"].get("truncated") for c in v["prompts"]})

    def test_the_builder_shaped_template_with_an_empty_description_is_what_the_connector_refuses(self):
        # The bug fixed with F4-3b: template_builder.js stored "" and the connector refused the whole template.
        bad = next(c for c in S.vectors()["templates"] if c["name"] == "builder_empty_description")
        self.assertEqual(bad["expect"], {"code": "bad_template", "detail": "description"})
        good = next(c for c in S.vectors()["templates"] if c["name"] == "builder_shaped")
        self.assertIn("ok", good["expect"])


class BrowserSideTests(unittest.TestCase):
    def test_runner_names_no_connector_only_capability(self):
        src = (ROOT / "web" / "js" / "browser_workflows.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", src)
        self.assertNotIn("workflow.save", src)               # C1: the browser device does not announce workflowSave
        self.assertIn("parseModel", src)

    def test_device_announces_workflows_but_not_workflow_save(self):
        src = (ROOT / "web" / "js" / "browser_device.js").read_text(encoding="utf-8")
        m = re.search(r"features: \[([^\]]*)\]", src)
        feats = re.findall(r"'([\w]+)'", m.group(1))
        self.assertIn("workflows", feats)
        self.assertNotIn("workflowSave", feats)

    def test_builtin_templates_run_in_a_browser(self):
        # C3: a built-in template with a dev step would be offered and then refused everywhere it matters.
        for t in W.BUILTIN_TEMPLATES:
            v = W.validate_template(t, False)
            self.assertTrue(all(s["profile"] in ("readonly", "edit") for s in v["steps"] if s["type"] == "agent"))


if __name__ == "__main__":
    unittest.main()
