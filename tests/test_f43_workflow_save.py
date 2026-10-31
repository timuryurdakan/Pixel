"""F4-3: `workflow.save` / `workflow.delete` from a paired browser (docs/ARASTIRMA2_ORTAK.md F4-3, E2 note).

workflows.json is what workflow.start trusts as this computer's own, so a change from a browser is checked exactly
like at load time, built-in ids and a broken file are refused, nothing is written before the confirmation on this
computer, a denied or expired confirmation writes nothing, the user's other templates are kept, the confirmation
shows steps (not prompts), and `remote.agentCreate` off refuses it.
"""

import json
import os
import time
import unittest

import _helpers  # noqa: F401  (sys.path)
from test_connector_tasks import Base

from pixel_connector import workflows as W

CANARY = "PROMPT-CANARY-f43-save"


def template(tid="blog-hatti", **kw):
    t = {"id": tid, "name": "Blog hattı", "description": "Yaz ve gözden geçir",
         "steps": [{"id": "yaz", "role": "Yazar", "engine": "claude", "profile": "edit",
                    "prompt": "Bir yazı yaz " + CANARY},
                   {"id": "onay", "type": "approval", "title": "Devam?"},
                   {"id": "oku", "role": "Editör", "engine": "claude", "profile": "readonly", "usePrevious": True,
                    "prompt": "Oku " + CANARY}]}
    t.update(kw)
    return t


class WorkflowSaveTests(Base):
    def path(self):
        return os.path.join(self.home, W.TEMPLATES_FILE)

    def stored(self):
        try:
            with open(self.path(), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def send(self, obj):
        self._n = getattr(self, "_n", 0) + 1
        rid = "s%d" % self._n
        self.br.send_e(self.dev, dict(obj, id=rid))
        return rid

    def pending(self, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            p = [x for x in self.conn.approvals.list() if x["kind"] == "agentCreate"]
            if p:
                return p[0]
            time.sleep(0.02)
        raise AssertionError("no confirmation on the computer")

    def answer(self, rid):
        return self.br.next_msg(lambda m: m.get("re") == rid, 15)

    def test_feature_is_announced(self):
        info = self.req({"type": "peer.hello", "label": "x"})
        self.assertIn("workflowSave", info["features"])

    def test_save_waits_for_the_computer_then_keeps_other_templates(self):
        mine = {"v": 1, "templates": [template("benim", name="Benim şablonum")]}
        with open(self.path(), "w", encoding="utf-8") as f:
            json.dump(mine, f)
        rid = self.send({"type": "workflow.save", "template": template()})
        pend = self.pending()
        self.assertEqual(self.stored(), mine)                         # nothing written before the confirmation
        self.assertIn("Blog hattı", pend["title"])
        self.assertIn("yaz: Yazar · claude · edit", pend["preview"])
        self.assertNotIn(CANARY, json.dumps(pend))                     # steps, never the prompts
        self.assertTrue(self.conn.approvals.resolve(pend["id"], True, by="cli"))
        self.assertEqual(self.answer(rid), {"type": "workflow.saved", "template": "blog-hatti", "re": rid})
        got = self.stored()
        self.assertEqual([t["id"] for t in got["templates"]], ["benim", "blog-hatti"])
        self.assertEqual(oct(os.stat(self.path()).st_mode & 0o777), "0o600")
        ids = [t["id"] for t in self.req({"type": "workflow.list"})["templates"]]
        self.assertIn("blog-hatti", ids)
        rid = self.send({"type": "workflow.save", "template": template(name="Blog hattı v2")})
        self.conn.approvals.resolve(self.pending()["id"], True, by="cli")
        self.answer(rid)
        self.assertEqual([t.get("name") for t in self.stored()["templates"]], ["Benim şablonum", "Blog hattı v2"])
        audit = [r for r in self.conn.audit.read() if r["ev"] == "workflow.save"]
        self.assertEqual([r["result"] for r in audit], ["ok", "ok"])
        self.assertNotIn(CANARY, json.dumps(audit))

    def test_denied_writes_nothing(self):
        rid = self.send({"type": "workflow.save", "template": template()})
        self.conn.approvals.resolve(self.pending()["id"], False, by="cli")
        self.assertEqual(self.answer(rid)["code"], "confirm_denied")
        self.assertIsNone(self.stored())

    def test_refused_before_asking(self):
        builtin = W.BUILTIN_TEMPLATES[0]["id"]
        for tpl, code in ((template(builtin), "bad_template"), (template(steps=[]), "bad_template"),
                          (template(extra=1), "bad_template"), (template("Büyük harf"), "bad_template"),
                          ("x", "bad_template")):
            r = self.req({"type": "workflow.save", "template": tpl})
            self.assertEqual(r.get("code"), code, tpl if isinstance(tpl, str) else tpl.get("id"))
        r = self.req({"type": "workflow.save", "template": template(), "path": "/etc/x"})
        self.assertEqual(r["code"], "bad_request")
        self.assertEqual(self.conn.approvals.list(), [])
        self.assertIsNone(self.stored())

    def test_a_broken_user_file_is_never_overwritten(self):
        with open(self.path(), "w", encoding="utf-8") as f:
            f.write("{ bozuk")
        rid = self.send({"type": "workflow.save", "template": template()})
        self.conn.approvals.resolve(self.pending()["id"], True, by="cli")
        self.assertEqual(self.answer(rid)["code"], "bad_template")
        with open(self.path(), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ bozuk")

    def test_template_cap(self):
        with open(self.path(), "w", encoding="utf-8") as f:
            json.dump({"v": 1, "templates": [template("t%d" % i) for i in range(W.MAX_TEMPLATES)]}, f)
        rid = self.send({"type": "workflow.save", "template": template()})
        self.conn.approvals.resolve(self.pending()["id"], True, by="cli")
        self.assertEqual(self.answer(rid)["code"], "bad_template")
        self.assertEqual(len(self.stored()["templates"]), W.MAX_TEMPLATES)

    def test_delete_only_user_templates_after_confirmation(self):
        with open(self.path(), "w", encoding="utf-8") as f:
            json.dump({"v": 1, "templates": [template("a-1"), template("b-2")]}, f)
        for tid in (W.BUILTIN_TEMPLATES[0]["id"], "yok", 5):
            self.assertEqual(self.req({"type": "workflow.delete", "template": tid})["code"], "unknown_template")
        rid = self.send({"type": "workflow.delete", "template": "a-1"})
        pend = self.pending()
        self.assertEqual(len(self.stored()["templates"]), 2)
        self.conn.approvals.resolve(pend["id"], True, by="cli")
        self.assertEqual(self.answer(rid), {"type": "workflow.deleted", "template": "a-1", "re": rid})
        self.assertEqual([t["id"] for t in self.stored()["templates"]], ["b-2"])

    def test_remote_agent_creation_off_refuses_both(self):
        self.conn.cfg["remote"]["agentCreate"] = False
        self.assertEqual(self.req({"type": "workflow.save", "template": template()})["code"], "not_allowed")
        self.assertEqual(self.req({"type": "workflow.delete", "template": "a"})["code"], "not_allowed")


if __name__ == "__main__":
    unittest.main()
