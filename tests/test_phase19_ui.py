"""Phase 19 (Package 5): the approval card in the real page (headless Chrome over CDP).

Real page ⇄ dev_relay ⇄ real connector (temp home, remote approvals on) ⇄ the real `pixel_hook.py hook --decide`
subprocess. Pairs through the wizard, then: a permission card (tool, summary rendered as text, agent, countdown, badge),
"Onayla" → the hook prints allow; a question card (radio + checkbox, "Gönder" only once every question has an answer)
→ the hook gets the chosen labels; a card answered elsewhere (CLI) disappears; the list moves onto the Cihazlar page;
the phone layout at 360 px has no horizontal scroll; no console errors. Skipped without Chrome.
Nothing touches ~/.claude or ~/.pixel-office.
"""

import json
import os
import subprocess
import sys
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
from _cdp import CHROME, Chrome
from test_phase19_approvals import HOOK, NO_OLLAMA, PH, QUESTION_INPUT, perm_event
from test_v3_ui import InProcPairing, no_hscroll, open_room, pair_once

XSS = "<img src=x onerror=window.__pwned=1> && echo ok"


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class ApprovalCardUi(unittest.TestCase):
    def setUp(self):
        self.env = E.V3Env()
        self.home = self.env.connector_home("dev", remoteApprovals={"enabled": True, "timeoutS": 120},
                                            engines=NO_OLLAMA)
        self.office = os.path.dirname(self.home)
        self.conn = self.env.connector(self.home, start_ctl=True)
        PH.write_json_atomic(os.path.join(self.office, "config.json"),
                             {"server": self.env.base, "room": self.env.room, "key": self.env.key, "name": "Timur",
                              "salt": "s" * 32, "enabled": True}, mode=0o600)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
            for f in (p.stdout, p.stderr):
                if f is not None:
                    f.close()
        self.conn.stop()
        self.env.stop()

    def hook(self, event):
        env = dict(os.environ, PIXEL_OFFICE_HOME=self.office)
        env.pop("PIXEL_OFFICE_DISABLE", None)
        p = subprocess.Popen([sys.executable, str(HOOK), "hook", "--decide"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        p.stdin.write(json.dumps(event).encode())
        p.stdin.close()
        self.procs.append(p)
        return p

    def output(self, p):
        out = p.stdout.read()
        p.wait(30)
        return json.loads(out) if out.strip() else None

    def test_cards(self):
        dev = InProcPairing(self.conn)
        with Chrome() as chrome:
            A = chrome.page(width=1280, height=900)
            open_room(A, self.env.base, self.env.room, self.env.key)
            A.wait("!!document.querySelector('#btn-devices:not([hidden])')", 15)
            A.click("#btn-devices")
            A.click("[data-dev-add]")
            A.wait("document.getElementById('dlg-pair').open")
            A.click("#dlg-pair [data-step='1'] [data-goto='2']")
            A.click("#dlg-pair [data-step='2'] [data-goto='3']")
            self.assertIn("Eşleşti", pair_once(A, dev, dev.start(), decide=lambda a, b: a == b))
            A.eval("document.getElementById('dlg-pair').close()")
            A.wait("!!document.querySelector('.dev-card .dev-dot.on')", 20, what="device online")
            A.wait("/Uzaktan onay: açık/.test(document.querySelector('.dev-card').textContent)", 10)
            A.click("[data-dev-close]")

            # ---- permission card → Onayla ---------------------------------------------------------------
            p = self.hook(perm_event(tool_input={"command": XSS}))
            A.wait("!document.getElementById('approvals').hidden && !!document.querySelector('#approvals .apv-card')",
                   20, what="approval card")
            self.assertEqual(A.text("#approvals .apv-tool"), "Bash")
            self.assertEqual(A.text("#approvals .apv-summary"), XSS)
            self.assertFalse(A.eval("!!document.querySelector('#approvals img') || !!window.__pwned"))
            self.assertIn("Claude Code · gizli-proje", A.text("#approvals .apv-agent"))
            A.wait("/^[0-9]+:[0-5][0-9]$/.test(document.querySelector('#approvals .apv-time').textContent)", 5)
            self.assertEqual(A.eval("document.getElementById('btn-devices').dataset.count"), "1")
            self.assertEqual(A.text("#approvals .apv-title"), "1 onay bekliyor")
            self.assertFalse(A.eval("[...document.querySelectorAll('#approvals button')].some(b => /hepsi/i.test(b.textContent))"))
            A.viewport(360, 740, mobile=True)
            A.wait("window.innerWidth === 360")
            self.assertTrue(no_hscroll(A))
            self.assertTrue(A.eval("(() => { const r = document.getElementById('approvals').getBoundingClientRect();"
                                   " return r.left >= 0 && r.right <= 360; })()"))
            self.assertEqual(A.eval("document.querySelector('#tabbar [data-tab=devices]').dataset.count"), "1")
            shots = os.environ.get("PIXEL_UI_SHOTS")
            if shots:
                A.screenshot(os.path.join(shots, "ui_approval_360.png"))
            A.click("#approvals [data-apv=allow]")
            self.assertEqual(self.output(p)["hookSpecificOutput"]["decision"], {"behavior": "allow"})
            A.wait("document.getElementById('approvals').hidden", 10, what="card gone")
            self.assertFalse(A.eval("'count' in document.getElementById('btn-devices').dataset"))

            # ---- question card → options → Gönder -------------------------------------------------------
            q = self.hook(perm_event("AskUserQuestion", QUESTION_INPUT))
            A.wait("document.querySelectorAll('#approvals .apv-q').length === 2", 20, what="question card")
            self.assertEqual(A.text("#approvals [data-apv=allow]"), "Gönder")
            self.assertTrue(A.eval("document.querySelector('#approvals [data-apv=allow]').disabled"))
            A.eval("document.querySelectorAll('#approvals .apv-q')[0].querySelectorAll('input')[1].click()")
            self.assertTrue(A.eval("document.querySelector('#approvals [data-apv=allow]').disabled"))
            A.eval("(() => { const i = document.querySelectorAll('#approvals .apv-q')[1].querySelectorAll('input');"
                   " i[0].click(); i[2].click(); })()")
            self.assertEqual(A.eval("document.querySelectorAll('#approvals .apv-q')[1].querySelector('input').type"),
                             "checkbox")
            self.assertTrue(no_hscroll(A))
            if shots:
                A.screenshot(os.path.join(shots, "ui_question_360.png"))
            A.click("#approvals [data-apv=allow]")
            dec = self.output(q)["hookSpecificOutput"]["decision"]
            self.assertEqual(dec["updatedInput"]["answers"], {"Hangi çatı?": "Vue", "Hangi araçlar?": "ESLint, Vitest"})

            # ---- answered elsewhere → the card disappears; the list moves onto the Cihazlar page ----------
            r = self.hook(perm_event(tool_input={"command": "make deploy"}))
            A.wait("!!document.querySelector('#approvals .apv-card')", 20)
            A.click("#tabbar [data-tab=devices]")
            A.wait("!document.getElementById('dev-approvals').hidden && !!document.querySelector('#dev-approvals .apv-card')"
                   " && document.getElementById('approvals').hidden", 10, what="card on the devices page")
            aid = A.eval("document.querySelector('#dev-approvals .apv-card').dataset.approval")
            self.assertEqual(self.conn.approvals.answer(aid, False, by="cli"), "ok")
            A.wait("document.getElementById('dev-approvals').hidden", 10, what="resolved elsewhere")
            self.assertEqual(self.output(r)["hookSpecificOutput"]["decision"]["behavior"], "deny")
            self.assertEqual(A.errors, [])


if __name__ == "__main__":
    unittest.main()
