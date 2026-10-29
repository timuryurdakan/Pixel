"""Patron Odası P6: the Boss's panel in the real page (headless browser over CDP).

Real page ⇄ dev_relay ⇄ real connector (temp home) with a fake `claude` that replays one stream-json reply per run: the
Boss's plan (two orders: Belge, then Test after it), the Belge order, the Test order and the Boss's review. Covers:
clicking the Boss opens the panel (unpaired: the info text only); after pairing the brief / folder / cost cap form, a
cap above the device ceiling refused in the page, `boss.ask`; the plan card with roles, estimate and cap, and the
"Plan onayı · Siz" card on the Task Board; a reload brings the plan back (`boss.get`); Onayla; the Test order's
approval (`workflow.answer`); the final report as plain text; "Hafızayı sıfırla" with its confirmation; the XSS canary
(in the brief, plan and report) never becomes markup; 1280/390/360 px with no horizontal scroll and ≥ 44 px buttons on
phones; a viewer (no key) only gets the info text; no console errors. Screenshots go to $PIXEL_UI_SHOTS when set.
Skipped without Chrome / Chromium. Nothing touches ~/.claude or ~/.pixel-office.
"""

import json
import os
import time
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
import _v3_fakes as F
from _cdp import CHROME, Chrome
from test_v3_ui import InProcPairing, no_hscroll, open_room, pair_once

from pixel_connector import boss as B

NONCE = "aaaa1111"
XSS = "<img src=x onerror=window.__pwnedP6=1>"
BRIEF = "Belge ve test ekle " + XSS
REPORT = "Rapor UI-6d2e " + XSS
BOSS_INFO_UNPAIRED = "Patrona iş vermek için önce bir bilgisayar eşleştirin."

CLICK_BOSS = """(async () => {
  const { BOSS_SEAT, TILE } = await import('/js/office.js');
  const c = document.getElementById('office');
  const r = c.getBoundingClientRect();
  const R = window.pixelOffice.renderer;
  const x = r.left + (BOSS_SEAT.x * TILE + 8) * r.width / R.worldW;
  const y = r.top + (BOSS_SEAT.y * TILE + 8) * r.height / R.worldH;
  c.dispatchEvent(new MouseEvent('click', { clientX: x, clientY: y, bubbles: true }));
  return true;
})()"""
DLG_OPEN = "document.getElementById('dlg-boss').open"
VISIBLE = "(s => { const e = document.querySelector(s); return !!e && !e.closest('[hidden]') && e.getClientRects().length > 0; })(%s)"
NO_MARKUP = "!document.querySelector('#dlg-boss img') && window.__pwnedP6 === undefined"
INSIDE = ("(() => { const r = document.getElementById('dlg-boss').getBoundingClientRect();"
          " return r.left >= 0 && r.right <= window.innerWidth + 0.5 && r.width > 0; })()")
TARGETS_OK = ("[...document.querySelectorAll('#dlg-boss button')].filter(b => b.getClientRects().length && !b.closest('[hidden]'))"
              ".every(b => b.getBoundingClientRect().height >= 44)")


def vis(sel):
    return VISIBLE % json.dumps(sel)


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class BossPanelUi(unittest.TestCase):
    def setUp(self):
        self.env = E.V3Env()
        self.fake_dir = os.path.join(self.env.tmp, "fakeclaude")
        claude = F.write_fake_claude(self.fake_dir)
        os.environ["CLAUDE_FAKE_DIR"] = self.fake_dir
        self.home = self.env.connector_home("dev", engines={"claude": {"path": claude, "enabled": True}},
                                            limits={"tasksPerMinute": 30})
        self.conn = self.env.connector(self.home)
        self.conn.boss.nonce = lambda: NONCE
        self.fx = os.path.join(self.env.tmp, "bossfx")
        os.makedirs(self.fx, exist_ok=True)
        self._n = 0
        self.shots = os.environ.get("PIXEL_UI_SHOTS")

    def tearDown(self):
        self.conn.stop()
        self.env.stop()
        os.environ.pop("CLAUDE_FAKE_DIR", None)

    # -- the fake Claude's replies ---------------------------------------------------------------------------------
    def fixture(self, text, cost=0.05):
        self._n += 1
        path = os.path.join(self.fx, "r%d.jsonl" % self._n)
        lines = [
            {"type": "system", "subtype": "init", "session_id": "{SESSION}", "cwd": "{CWD}", "model": "claude-x",
             "tools": ["Read"], "permissionMode": "dontAsk"},
            {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "content_block_delta", "delta": {
                "type": "text_delta", "text": text}}},
            {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "result": text,
             "total_cost_usd": cost, "session_id": "{SESSION}", "usage": {"input_tokens": 10, "output_tokens": 20}}]
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return {"fixture": path}

    def script(self):
        plan = {"v": 1, "summary": "Belge ve test " + XSS, "orders": [
            {"id": "o1", "role": "docs", "title": "Belge " + XSS, "goal": "README'ye kısa bir bölüm ekle.",
             "accept": ["Bölüm var " + XSS], "files": ["README.md"]},
            {"id": "o2", "role": "test", "title": "Testleri çalıştır", "goal": "Testleri çalıştır.",
             "accept": ["Testler geçer"], "files": ["tests/test_a.py"], "after": ["o1"]}]}
        start, end = B.plan_markers(NONCE)
        r_start, r_end = B.review_markers(NONCE)
        F.set_plan(self.fake_dir, [
            self.fixture("Projeyi okudum.\n%s\n%s\n%s\n" % (start, json.dumps(plan, ensure_ascii=False), end)),
            self.fixture("README güncellendi."),
            self.fixture("Testler geçti."),
            self.fixture("%s\n%s\n%s\n%s\n" % (REPORT, r_start, json.dumps({"v": 1, "decision": "accept"}), r_end))])

    # -- page helpers -----------------------------------------------------------------------------------------------
    def pair(self, page):
        dev = InProcPairing(self.conn)
        page.click("#btn-devices")
        page.click("[data-dev-add]")
        page.wait("document.getElementById('dlg-pair').open")
        page.click("#dlg-pair [data-step='1'] [data-goto='2']")
        page.click("#dlg-pair [data-step='2'] [data-goto='3']")
        self.assertIn("Eşleşti", pair_once(page, dev, dev.start(), decide=lambda a, b: a == b))
        page.eval("document.getElementById('dlg-pair').close()")
        page.wait("!!document.querySelector('.dev-card .dev-dot.on')", 20, what="device online")
        page.click("[data-dev-close]")

    def open_boss(self, page):
        page.eval("document.getElementById('dlg-boss').open && document.getElementById('dlg-boss').close()")
        page.eval(CLICK_BOSS)
        page.wait(DLG_OPEN, 5, what="Boss panel")

    def layout(self, page, name):
        for w, h in ((1280, 900), (390, 844), (360, 740)):
            page.viewport(w, h, mobile=w < 600)
            page.wait("window.innerWidth === %d" % w)
            time.sleep(0.3)
            with self.subTest(width=w, variant=name):
                self.assertTrue(no_hscroll(page))
                self.assertTrue(page.eval(INSIDE))
                if w < 600:
                    self.assertTrue(page.eval(TARGETS_OK))
            if self.shots:
                page.screenshot(os.path.join(self.shots, "ui_boss_%s_%d.png" % (name, w)))
        page.viewport(1280, 900)
        page.wait("window.innerWidth === 1280")

    # -- the flow ---------------------------------------------------------------------------------------------------
    def test_panel_flow(self):
        self.script()
        with Chrome() as chrome:
            A = chrome.page(width=1280, height=900)
            open_room(A, self.env.base, self.env.room, self.env.key)
            A.wait("!!document.querySelector('#btn-devices:not([hidden])')", 15)

            # ---- unpaired (but with the key): info text only ----------------------------------------------------
            self.open_boss(A)
            self.assertEqual(A.text("#boss-info"), BOSS_INFO_UNPAIRED)
            self.assertFalse(A.eval(vis("#boss-form")))
            self.assertFalse(A.eval(vis("#boss-job")))
            A.eval("document.getElementById('dlg-boss').close()")

            self.pair(A)
            A.wait("!!window.pixelOffice.v3.bossStore && window.pixelOffice.v3.bossStore.loaded.size === 1", 15,
                   what="boss.get after connecting")

            # ---- the form ---------------------------------------------------------------------------------------
            self.open_boss(A)
            A.wait(vis("#boss-form") + " && document.querySelector('#boss-form [name=workspace]').options.length > 0",
                   10, what="form")
            self.assertFalse(A.eval(vis("#boss-info")))
            self.assertEqual(A.eval("document.querySelector('#boss-form [name=cost]').value"), "1.00")
            self.assertEqual(A.eval("document.querySelector('#boss-form [name=cost]').max"), "1")
            self.layout(A, "form")
            A.type("#boss-form [name=brief]", BRIEF)
            A.eval("document.querySelector('#boss-form [name=cost]').value = '1.5'")
            A.click("#boss-form [name=ask]")
            A.wait(vis("#boss-error"), 5, what="cap error")
            self.assertIn("Maliyet tavanı", A.text("#boss-error"))
            time.sleep(0.3)
            self.assertEqual(F.calls(self.fake_dir), [])                      # nothing was asked
            A.eval("document.querySelector('#boss-form [name=cost]').value = '0.5'")
            A.click("#boss-form [name=ask]")

            # ---- the plan card and the Task Board ----------------------------------------------------------------
            A.wait("!!document.querySelector('#boss-job .boss-plan .boss-approve')", 30, what="plan card")
            card = A.text("#boss-job .boss-plan")
            for part in ("Belge ve test " + XSS, "Belge " + XSS, "Bölüm var " + XSS, "README.md", "o1 bitince başlar",
                         "Kesin tavan: $0.50", "Tahmini maliyet: $"):
                self.assertIn(part, card)
            self.assertTrue(A.eval(NO_MARKUP))
            self.assertFalse(A.eval(vis("#boss-form")))
            calls = F.calls(self.fake_dir)
            self.assertEqual(len(calls), 1)
            self.assertIn(BRIEF, calls[0]["stdin"])
            self.assertEqual(calls[0]["argv"][calls[0]["argv"].index("--max-budget-usd") + 1], "0.20")
            self.assertTrue(A.eval("window.pixelOffice.renderer.boardData.waiting.some(c => c.target === 'boss'"
                                   " && c.agentName === 'Siz' && c.title === 'Plan onayı')"))
            self.layout(A, "plan")

            # ---- reload: boss.get brings the plan back ------------------------------------------------------------
            A.navigate(A.eval("location.href"))
            A.wait("!!(window.pixelOffice.v3 && window.pixelOffice.v3.bossStore"
                   " && window.pixelOffice.v3.bossStore.pendingPlans().length === 1)", 30, what="plan after reload")
            A.click("#btn-board")
            A.wait("[...document.querySelectorAll('#board-content .board-card')].some(c => /Plan onayı/.test(c.textContent))",
                   5, what="board card")
            A.eval("[...document.querySelectorAll('#board-content .board-card')].find(c => /Plan onayı/.test(c.textContent)).click()")
            A.wait(DLG_OPEN, 5, what="panel from the board card")
            A.wait("!!document.querySelector('#boss-job .boss-plan .boss-approve')", 5)

            # ---- approve → Belge → the Test order's approval → report --------------------------------------------
            A.click("#boss-job .boss-approve")
            A.wait("!!document.querySelector('#boss-job .boss-gate-approve')", 40, what="Test order approval")
            self.assertIn("Onay bekliyor", A.text("#boss-job .boss-order.st-approval"))
            self.layout(A, "gate")
            A.click("#boss-job .boss-gate-approve")
            A.wait("!!document.querySelector('#boss-job .boss-report')", 40, what="report")
            report = A.text("#boss-job .boss-report")
            self.assertIn(REPORT, report)
            self.assertIn("Bağlayıcının özeti:", report)
            self.assertEqual(A.eval("document.querySelector('#boss-job .boss-report').children.length"), 0)
            self.assertTrue(A.eval(NO_MARKUP))
            self.assertTrue(A.eval(vis("#boss-form")))                       # ready for the next job
            self.assertEqual(len(F.calls(self.fake_dir)), 4)
            self.layout(A, "report")

            # ---- Hafızayı sıfırla ------------------------------------------------------------------------------------
            A.click("[data-boss-reset]")
            A.wait(vis("#boss-reset-confirm"), 5)
            A.click("[data-boss-reset-yes]")
            A.wait(vis("#boss-note"), 10, what="reset done")
            self.assertIn("sıfırlandı", A.text("#boss-note"))
            boss_agent = next(a for a in self.conn.agents.list() if a.get("boss"))
            self.assertIsNone(boss_agent.get("claudeSession"))
            self.assertEqual(A.errors, [])

            # ---- mini window: clicking the Boss opens nothing -----------------------------------------------------
            M = chrome.page(width=360, height=300)
            M.navigate(self.env.base + "/?room=%s&view=mini#key=%s" % (self.env.room, self.env.key))
            M.wait("document.body.classList.contains('mini')", 15)
            M.eval(CLICK_BOSS)
            time.sleep(0.5)
            self.assertFalse(M.eval(DLG_OPEN))
            self.assertEqual(M.errors, [])

        # ---- a viewer (no key): the info text only -------------------------------------------------------------------
        with Chrome() as other:
            V = other.page(width=390, height=844, mobile=True)
            open_room(V, self.env.base, self.env.room)
            self.open_boss(V)
            self.assertIn("yalnızca izleme bağlantısıyla", V.text("#boss-info"))
            self.assertFalse(V.eval(vis("#boss-form")))
            self.assertFalse(V.eval(vis("#boss-job")))
            self.assertTrue(no_hscroll(V))
            if self.shots:
                V.screenshot(os.path.join(self.shots, "ui_boss_viewer_390.png"))
            self.assertEqual(V.errors, [])


if __name__ == "__main__":
    unittest.main()
