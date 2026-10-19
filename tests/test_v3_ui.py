"""Phase 14: the v3 UI end to end in headless Chrome over CDP (V3.md §11, §17.3).

Real page (web/index.html + control/prompt/devices/usage.js) ⇄ dev_relay ⇄ real connector (fake Ollama, fake
`claude`), all on free ports with temp homes. Covers: pairing wizard (wrong code, SAS refusal, correct code with
matching SAS), new-agent dialog, the character entering the office, streamed output with markdown-lite and XSS
strings rendered as text, the office state (working → idle), cancel, Claude tool rows and the honest cost label,
budget gauge + block, runtime start/stop (allow-listed harmless argv), model load/unload, a second tab watching, an
unpaired second browser (no prompt box), the phone layout at 390 and 360 px, unpairing, and no console errors.
A v2 room without a relay shows no v3 controls.

`run_scenario()` is also used by a manual run against `dev_server.py --relay` plus a `pixel-connector run` process
(see PROJECT.md, Aşama 14 log), with `CtlPairing` instead of `InProcPairing`.

Skipped when google-chrome / chromium is not installed. Nothing touches ~/.claude or ~/.pixel-office.
"""

import json
import os
import time
import unittest
import urllib.request

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
import _v3_fakes as F
from _cdp import CHROME, Chrome

from pixel_cloud.app import App
from pixel_cloud.storage import MemoryStorage
import dev_server
import threading


# -- pairing adapters (device side) ------------------------------------------------------------------
class InProcPairing:
    def __init__(self, conn):
        self.conn = conn
        self.p = None

    def start(self):
        self.p = self.conn.router.start_pairing()
        assert self.p.wait({"open"}, 5) == "open", self.p.state
        return self.p.code

    def wait_confirm(self, timeout=30):
        st = self.p.wait({"confirm", "failed", "expired"}, timeout)
        return st, self.p.sas, self.p.reason

    def confirm(self, ok):
        assert self.p.confirm(ok)

    def peers(self):
        return self.conn.peers.public_list()


class CtlPairing:
    """Device side through the connector's loopback control API (a `pixel-connector run` process)."""

    def __init__(self, home):
        from pixel_connector.approvals import ctl_request
        self.req = lambda m, p, b=None, t=30: ctl_request(home, m, p, b, t)

    def start(self):
        for _ in range(100):
            st, d = self.req("POST", "/ctl/pair/start")
            if st == 200:
                break
            time.sleep(0.2)
        assert st == 200, (st, d)
        st, d = self.req("GET", "/ctl/pair/status?wait=5&until=open")
        assert d["state"] == "open", d
        return d["code"]

    def wait_confirm(self, timeout=30):
        st, d = self.req("GET", "/ctl/pair/status?wait=%d&until=confirm,failed,expired" % min(25, timeout), None, timeout + 5)
        return d["state"], d.get("sas"), d.get("reason")

    def confirm(self, ok):
        st, d = self.req("POST", "/ctl/pair/confirm", {"ok": bool(ok)})
        assert st == 200, (st, d)

    def peers(self):
        return self.req("GET", "/ctl/peers")[1]["peers"]


# -- page helpers -------------------------------------------------------------------------------------
def open_room(page, base, room, key=None):
    page.navigate(base + "/rehber.html")
    page.eval("localStorage.setItem('po-guide-seen','1')")
    page.navigate(base + "/?room=%s%s" % (room, "#key=" + key if key else ""))
    page.wait("document.getElementById('conn-status').dataset.status === 'live'", 20, what="room live")


def no_hscroll(page):
    return page.eval("document.documentElement.scrollWidth <= window.innerWidth + 1")


def click_text(page, selector, text):
    ok = page.eval("(() => { const e = [...document.querySelectorAll(%s)].find(x => x.textContent.trim() === %s);"
                   " if (!e) return false; e.click(); return true; })()" % (json.dumps(selector), json.dumps(text)))
    assert ok, (selector, text)


def wrong(code):
    s = code.replace("-", "")
    sec = "00000000" if s[4:] != "00000000" else "11111111"
    return s[:4] + sec


def pair_once(page, dev, code_text, decide=None):
    """Type a code into the open wizard (step 3) and submit. `decide(sas_page, sas_device)` → bool to answer on the
    device (None = the device never reaches the question). → status text."""
    page.type("#pair-code", code_text)
    page.click("#pair-submit")
    if decide is not None:
        page.wait("!document.getElementById('pair-sas').hidden", 30, what="SAS shown")
        sas_page = page.text("#pair-sas .sas-code")
        st, sas_dev, _ = dev.wait_confirm()
        assert st == "confirm", st
        dev.confirm(decide(sas_page, sas_dev))
    page.wait("/ok|error/.test(document.getElementById('pair-status').className)", 40, what="pair result")
    return page.text("#pair-status")


def run_scenario(t, base, room, key, dev, llm, fake_claude_dir, shots=None, chrome_factory=Chrome):
    """The whole Phase 14 flow. `t` is a unittest.TestCase (assertions), `dev` a pairing adapter."""
    shot = (lambda p, name: p.screenshot(os.path.join(shots, name))) if shots else (lambda p, name: None)
    with chrome_factory() as chrome:
        A = chrome.page(width=1280, height=900)
        open_room(A, base, room, key)
        A.wait("!!document.querySelector('#btn-devices:not([hidden])')", 15, what="Cihazlar button")
        t.assertTrue(A.eval("document.getElementById('btn-newagent').hidden"))

        # ---- pairing wizard: wrong code, SAS refusal, correct code --------------------------------
        A.click("#btn-devices")
        A.wait("!document.getElementById('devices-page').hidden")
        t.assertIn("eşleşmedi", A.text("#devices-page .dev-body"))
        A.click("[data-dev-add]")
        A.wait("document.getElementById('dlg-pair').open")
        t.assertIn("--room " + room, A.text("#dlg-pair [data-pcmd=linux]"))
        A.click("#dlg-pair [data-step='1'] [data-goto='2']")
        t.assertIn("pixel-connector pair", A.text("#dlg-pair [data-step='2']"))
        A.click("#dlg-pair [data-step='2'] [data-goto='3']")
        code = dev.start()
        msg = pair_once(A, dev, wrong(code))
        t.assertIn("Kod yanlış", msg)
        code = dev.start()
        msg = pair_once(A, dev, code, decide=lambda a, b: False)
        t.assertIn("reddedildi", msg)
        code = dev.start()
        A.type("#pair-code", code.replace("-", "").lower())
        c12 = code.replace("-", "")
        t.assertEqual(A.eval("document.getElementById('pair-code').value"), "-".join((c12[:4], c12[4:8], c12[8:])))   # auto-format
        seen = {}

        def decide(sas_page, sas_dev):
            seen["page"], seen["dev"] = sas_page, sas_dev
            shot(A, "ui_pair_sas.png")
            return sas_page == sas_dev
        msg = pair_once(A, dev, code, decide=decide)
        t.assertEqual(seen["page"], seen["dev"])
        t.assertIn("Eşleşti", msg)
        A.eval("document.getElementById('dlg-pair').close()")
        A.wait("!!document.querySelector('.dev-card .dev-dot.on')", 20, what="device online")
        A.wait("document.querySelectorAll('.rt').length >= 2", 15, what="runtimes")

        # ---- runtimes: start/stop (allow-listed harmless argv), model load/unload --------------------
        click_text(A, ".rt[data-runtime='dummy'] .rt-actions .btn", "Başlat")     # → ["true"]
        A.wait("(document.querySelector(\".rt[data-runtime='dummy'] .rt-msg\")||{}).textContent === 'Başlatıldı'", 20)
        click_text(A, ".rt[data-runtime='dummy'] .rt-actions .btn", "Durdur")     # → ["false"] (exit 1)
        A.wait("/Durdurulamadı \\(çıkış kodu 1\\)/.test((document.querySelector(\".rt[data-runtime='dummy'] .rt-msg\")||{}).textContent)", 20)
        A.click(".rt[data-runtime='ollama'] .rt-models li .btn")           # Yükle
        A.wait("/yüklendi/.test((document.querySelector(\".rt[data-runtime='ollama'] .rt-msg\")||{}).textContent)", 20)
        A.wait("/Yüklü ·/.test(document.querySelector(\".rt[data-runtime='ollama'] .rt-models\").textContent)", 10)
        shot(A, "ui_devices.png")
        A.click(".rt[data-runtime='ollama'] .rt-models li .btn")           # Boşalt
        A.wait("/boşaltıldı/.test((document.querySelector(\".rt[data-runtime='ollama'] .rt-msg\")||{}).textContent)", 20)
        t.assertEqual([r["path"] for r in llm.requests if r["path"] == "/api/generate"], ["/api/generate"] * 2)

        # ---- new agent (Ollama) → character enters, details panel opens with the prompt box --------
        A.click(".dev-card .dev-sec-head .btn.accent")
        A.wait("document.getElementById('dlg-agent').open && document.querySelectorAll('#agent-engines input').length > 0")
        A.eval("document.querySelector('#agent-engines input[value=ollama]').click()")
        A.type("#agent-form [name=name]", "<img src=x onerror=a>")
        shot(A, "ui_new_agent.png")
        A.click("#agent-form [name=create]")
        A.wait("!document.getElementById('dlg-agent').open", 20, what="dialog closed")
        A.wait("[...window.pixelOffice.agents.values()].some(a => a.control)", 40, what="character in the office")
        A.wait("!!document.querySelector('#agent-control .ctl-ta')", 20, what="prompt box")
        t.assertIn("<img", A.text(".agent-name"))
        t.assertTrue(A.eval("!!document.querySelector('.ctl-badge')"))

        # ---- prompt → streaming → end; office state working → idle -----------------------------------
        A.type("#agent-control .ctl-ta", "Merhaba **dünya**")
        A.eval("document.querySelector('#agent-control .ctl-ta').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true}))")
        A.wait("/Çalışıyor/.test(document.querySelector('.ctl-run').textContent)", 10, what="running")
        A.wait("document.querySelectorAll('.ctl-out .md-p').length > 0", 15, what="streamed text")
        A.wait("[...window.pixelOffice.agents.values()].some(a => a.control && a.state === 'working')", 30,
               what="character working")
        shot(A, "ui_streaming.png")
        A.wait("!!document.querySelector('.ctl-out .out-end')", 60, what="task end")
        t.assertIn("Tamamlandı", A.text(".ctl-out .out-end"))
        t.assertIn("maliyet yok", A.text(".ctl-out .out-end"))
        t.assertTrue(A.eval("!!document.querySelector('.ctl-out pre.md-code')"))           # fenced code
        t.assertTrue(A.eval("!!document.querySelector('.ctl-out strong')"))                # **bold**
        t.assertIn("<script>alert(1)</script>", A.text(".ctl-out"))                       # XSS stays text
        t.assertFalse(A.eval("!!document.querySelector('.ctl-out img, .ctl-out script, .agent-list img')"))
        t.assertFalse(A.eval("!!window.__xss"))
        A.wait("[...window.pixelOffice.agents.values()].some(a => a.control && a.state === 'idle')", 40,
               what="character back to coffee")
        A.wait("/Bugün: \\d/.test(document.querySelector('.ctl-usage .usage-line').textContent)", 15)

        # ---- second tab of the same browser watches; recent tasks -----------------------------------
        A2 = chrome.page(width=1100, height=850)
        open_room(A2, base, room, key)
        A2.wait("[...window.pixelOffice.agents.values()].some(a => a.control)", 20)
        A2.eval("(() => { const a = [...window.pixelOffice.agents.values()].find(a => a.control); document.querySelector(`[data-id=\"${a.id}\"]`).click(); })()")
        A2.wait("!!document.querySelector('#agent-control .ctl-ta:not(:disabled)')", 25, what="tab 2 ready")
        time.sleep(0.5)
        A.type("#agent-control .ctl-ta", "İkinci görev")
        A.click("#agent-control .ctl-send")
        A2.wait("document.querySelectorAll('.ctl-out .out-task').length > 0", 20, what="watched stream in tab 2")
        A2.wait("!!document.querySelector('.ctl-out .out-end')", 60)
        A.wait("document.querySelectorAll('.ctl-out .out-end').length >= 2", 60)
        A.eval("document.querySelector('.ctl-recent').open = true; document.querySelector('.ctl-recent').dispatchEvent(new Event('toggle'))")
        A.wait("document.querySelectorAll('.recent-task').length >= 2", 15, what="Son görevler")
        A2.close()

        # ---- budget: set → gauge, block → new task refused ---------------------------------------------
        A.eval("document.querySelector('#agent-control .budget-edit').open = true")
        A.eval("(() => { const f = document.querySelector('#agent-control .budget-form'); f.elements.limit.value = '50';"
               " f.elements.unit.value = 'tokens'; f.elements.action.value = 'block'; f.requestSubmit(); })()")
        A.wait("!!document.querySelector('#agent-control .gauge')", 15, what="gauge")
        g = A.text("#agent-control .gauge")
        t.assertRegex(g, r"Günlük bütçe %\d+ \(.* / 50 token\)")
        t.assertEqual(A.eval("document.querySelector('#agent-control .gauge').dataset.state"), "over")
        t.assertIn("Bütçe doldu", g)
        shot(A, "ui_budget.png")
        A.type("#agent-control .ctl-ta", "Bütçe dolu mu?")
        A.click("#agent-control .ctl-send")
        A.wait("/Bütçe sınırına ulaşıldı/.test(document.querySelector('#agent-control .ctl-msg').textContent)", 15)
        A.eval("document.querySelector('#agent-control .budget-edit').open = true")
        A.click("#agent-control .budget-form [name=del]")
        A.wait("!document.querySelector('#agent-control .gauge')", 15, what="gauge removed")

        # ---- Claude agent: cancel a running task; tool rows; honest cost label ----------------------------
        F.set_plan(fake_claude_dir, [{"fixture": "claude_stream_slow.jsonl", "delay": 0.05},
                                     {"fixture": "claude_stream_success_tools.jsonl", "delay": 0.05}])
        A.click("#btn-newagent")
        A.wait("document.getElementById('dlg-agent').open && !!document.querySelector('#agent-engines input[value=claude]:not(:disabled)')", 15)
        A.eval("document.querySelector('#agent-engines input[value=claude]').click()")
        A.wait("[...document.querySelector('#agent-form [name=workspace]').options].some(o => o.value === 'pixel')")
        t.assertFalse(A.eval("document.getElementById('agent-profile-row').hidden"))
        A.eval("document.querySelector('#agent-form [name=profile]').value = 'auto'; document.querySelector('#agent-form [name=profile]').dispatchEvent(new Event('change'))")
        A.eval("document.querySelector('#agent-form [name=profile]').value = 'edit'")
        A.type("#agent-form [name=name]", "Kâtip")
        A.click("#agent-form [name=create]")
        A.wait("!document.getElementById('dlg-agent').open", 20)
        A.wait("(() => { const a = window.pixelOffice.agents.get(window.pixelOffice.ui.selectedId); return a && a.name === 'Kâtip'; })()", 40,
               what="Claude agent selected")
        A.wait("!!document.querySelector('#agent-control .ctl-ta:not(:disabled)')", 20)
        t.assertIn("Düzenleyebilir", A.text("#agent-control .ctl-profile"))
        A.type("#agent-control .ctl-ta", "Uzun bir iş yap")
        A.click("#agent-control .ctl-send")
        A.wait("/parça 1/.test(document.querySelector('.ctl-out').textContent)", 20, what="claude stream")
        A.wait("!document.querySelector('#agent-control .ctl-stop').hidden")
        A.click("#agent-control .ctl-stop")
        A.wait("/Durduruldu/.test(document.querySelector('.ctl-out .out-end') ? document.querySelector('.ctl-out .out-end').textContent : '')", 30,
               what="cancelled")
        A.type("#agent-control .ctl-ta", "Dosyaları oku")
        A.click("#agent-control .ctl-send")
        A.wait("document.querySelectorAll('.ctl-out .out-end').length >= 2", 40)
        t.assertTrue(A.eval("!!document.querySelector('.ctl-out .part-tool.ok')"), A.text(".ctl-out"))
        t.assertIn("API eşdeğeri tahmini", A.text(".ctl-out"))
        t.assertIn("Claude", A.text("#agent-control .ctl-usage"))          # subscription limit honesty line
        shot(A, "ui_desktop_prompt.png")

        # ---- unpaired second browser: sees the characters, no prompt box ----------------------------------
        with chrome_factory() as chrome2:
            B = chrome2.page(width=1280, height=900)
            open_room(B, base, room, key)
            B.wait("document.querySelectorAll('#agent-list .agent').length >= 2", 20)
            B.eval("document.querySelector('#agent-list .agent').click()")
            B.wait("!!document.querySelector('#agent-control .ctl-unpaired')", 15, what="unpaired hint")
            t.assertFalse(B.eval("!!document.querySelector('#agent-control textarea')"))
            t.assertFalse(B.eval("!!document.querySelector('.ctl-badge')"))
            shot(B, "ui_unpaired_browser.png")
            t.assertEqual(B.errors, [])

        # ---- phone layout (390 and 360 px) ---------------------------------------------------------------
        for w, h in ((390, 844), (360, 740)):
            P = chrome.page(width=w, height=h, mobile=True)
            open_room(P, base, room, key)
            P.wait("!document.getElementById('tabbar').hidden && getComputedStyle(document.getElementById('tabbar')).display === 'flex'", 15)
            t.assertTrue(no_hscroll(P), "horizontal scroll in office tab at %d px" % w)
            P.click("#tabbar [data-tab=agents]")
            P.wait("document.querySelectorAll('#agent-list .agent').length >= 2", 20)
            P.eval("[...document.querySelectorAll('#agent-list .agent')].find(li => li.textContent.includes('Kâtip')).click()")
            P.wait("!!document.querySelector('#agent-control .ctl-ta:not(:disabled)')", 25, what="phone prompt box")
            box = P.eval("(() => { const r = document.querySelector('#agent-control .ctl-compose').getBoundingClientRect();"
                         " return {top: r.top, bottom: r.bottom, h: innerHeight}; })()")
            t.assertLessEqual(box["bottom"], box["h"] + 1, box)
            t.assertGreater(box["top"], box["h"] * 0.4, box)                     # sticky at the bottom
            t.assertTrue(no_hscroll(P), "horizontal scroll in the agent sheet at %d px" % w)
            if w == 390:
                shot(P, "ui_phone_agent.png")
            P.click("#sheet-back")
            P.click("#tabbar [data-tab=devices]")
            P.wait("!document.getElementById('devices-page').hidden && document.querySelectorAll('.rt').length >= 2", 20)
            t.assertTrue(no_hscroll(P), "horizontal scroll in the devices tab at %d px" % w)
            sz = P.eval("(() => { const b = document.querySelector('.rt .rt-btn'); const r = b.getBoundingClientRect(); return [r.width, r.height]; })()")
            t.assertGreaterEqual(sz[1], 44)
            if w == 390:
                shot(P, "ui_phone_devices.png")
            t.assertEqual(P.errors, [], "console errors at %d px" % w)
            P.close()

        # ---- unpair -----------------------------------------------------------------------------------------
        A.click("#btn-devices")
        A.wait("!!document.querySelector('.dev-card .dev-foot .btn')")
        n_before = len(dev.peers())
        A.click(".dev-card .dev-foot .btn")                    # window.confirm is auto-accepted by the driver
        A.wait("/eşleşmedi/.test(document.querySelector('#devices-page .dev-body').textContent)", 20, what="unpaired")
        end = time.time() + 10
        while time.time() < end and len(dev.peers()) >= n_before:
            time.sleep(0.2)
        t.assertEqual(len(dev.peers()), n_before - 1)
        A.click("[data-dev-close]")
        # `!!`: a bare DOM node comes back over CDP as {} (falsy in Python), so the condition must be a boolean.
        A.wait("!!document.querySelector('#agent-control .ctl-unpaired') || document.getElementById('agent-control').hidden", 10,
               what="paired-only controls gone after unpairing")
        t.assertFalse(A.eval("!!document.querySelector('#agent-control textarea, #agent-control .ctl-compose')"))
        t.assertFalse(A.eval("!!document.querySelector('.ctl-badge')"))
        t.assertEqual(A.errors, [])


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class V3UiTests(unittest.TestCase):
    def setUp(self):
        self.env = E.V3Env()
        self.llm = F.FakeLLM(words=["Merhaba ", "**dünya** ", "<script>alert(1)</script>", "\n\n```py\nprint(1)\n```\n"]
                             + ["kelime "] * 20 + ["bitti."], delay=0.25)
        self.fake_dir = os.path.join(self.env.tmp, "fakeclaude")
        claude = F.write_fake_claude(self.fake_dir)
        os.environ["FAKE_CLAUDE_DIR"] = self.fake_dir
        url = self.llm.url
        home = self.env.connector_home(
            engines={"claude": {"path": claude, "enabled": True}, "ollama": {"url": url, "enabled": True}},
            runtimes=[{"id": "ollama", "name": "Ollama sunucusu", "kind": "ollama", "url": url,
                       "status": {"http": url + "/api/version"}, "start": ["true"], "stop": ["true"],
                       "models": ["qwen2.5-coder:7b"]},
                      {"id": "dummy", "name": "Deneme", "start": ["true"], "stop": ["false"]}])
        self.conn = self.env.connector(home)

    def tearDown(self):
        self.conn.stop()
        self.llm.stop()
        self.env.stop()
        os.environ.pop("FAKE_CLAUDE_DIR", None)

    def test_full_flow(self):
        run_scenario(self, self.env.base, self.env.room, self.env.key, InProcPairing(self.conn), self.llm, self.fake_dir,
                     shots=os.environ.get("PIXEL_UI_SHOTS"))
        evs = [r["ev"] for r in self.conn.audit.read()]
        self.assertEqual(evs.count("pair.fail"), 2)
        self.assertIn("task.cancel", evs)
        self.assertIn("peer.revoke", evs)
        # privacy: no prompt text in any relay frame
        self.assertFalse(any("Merhaba **dünya**" in f or "İkinci görev" in f for f in self.env.relay_frames))


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class V2RoomWithoutRelayTests(unittest.TestCase):
    """v3 off (no relay env): the room page shows no v3 control at all (V3.md §11)."""

    def test_no_v3_controls(self):
        app = App(storage=MemoryStorage(), env={"PIXEL_SECRET": "d" * 40}, mapper=None)
        srv = dev_server.DevServer(("127.0.0.1", 0), app, dev_server.WEB_ROOT)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        try:
            req = urllib.request.Request(base + "/api/rooms", data=json.dumps({"name": "v2"}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            body = json.dumps({"session": "s1", "owner": "Ali", "project": "p", "state": "working", "source": "api",
                               "ctl": "abcdefghijklmnop.a1"}).encode()
            req = urllib.request.Request(base + "/api/status?room=" + d["roomId"], data=body, method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + d["writeKey"]})
            urllib.request.urlopen(req, timeout=10).read()
            with Chrome() as chrome:
                for w in (1280, 390):
                    p = chrome.page(width=w, height=844, mobile=w < 600)
                    open_room(p, base, d["roomId"], d["writeKey"])
                    p.wait("document.querySelectorAll('#agent-list .agent').length === 1", 15)
                    p.eval("document.querySelector('#agent-list .agent').click()")
                    time.sleep(0.5)
                    self.assertTrue(p.eval("document.getElementById('btn-devices').hidden"))
                    self.assertTrue(p.eval("document.getElementById('tabbar').hidden"))
                    self.assertTrue(p.eval("document.getElementById('agent-control').hidden"))
                    self.assertFalse(p.eval("document.body.classList.contains('v3')"))
                    self.assertFalse(p.eval("!!document.querySelector('.ctl-badge')"))
                    self.assertTrue(no_hscroll(p))
                    self.assertEqual(p.errors, [])
                    p.close()
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
