"""Connector 3.5.0: the outdated-connector strip in the real page (headless Chrome over CDP).

Real page ⇄ dev_relay ⇄ real connector (temp home, fake .pyz digest so the device counts as outdated against the
served web/connector/SHA256SUMS). Covers: the strip with `pixel-connector update` for a 3.5.0+ connector and the
install-command variant (button opens Cihazlar → Cihaz ekle) for an older one, the device card hint, dismissal
remembered per published SHA across a reload, layout at 1280/390/360 px (no horizontal scroll, ≥ 44 px touch targets),
and no strip for a viewer (no key), the mini window (?view=mini) or an unpaired browser. No console errors.
Skipped without Chrome. Nothing touches ~/.claude or ~/.pixel-office.
"""

import os
import time
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
from _cdp import CHROME, Chrome
from test_v3_ui import InProcPairing, no_hscroll, open_room, pair_once

from pixel_connector.update import parse_sums

FAKE_PYZ = "0" * 64
BANNER_SHOWN = "!document.getElementById('update-banner').hidden"
TARGETS_OK = ("[...document.querySelectorAll('#update-banner button')].every(b => {"
              " const r = b.getBoundingClientRect(); return r.height >= 44 && r.width >= 44; })")
INSIDE = ("(() => { const r = document.getElementById('update-banner').getBoundingClientRect();"
          " return r.left >= 0 && r.right <= window.innerWidth + 0.5 && r.width > 0; })()")


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class UpdateBannerUi(unittest.TestCase):
    def setUp(self):
        self.env = E.V3Env()
        self.home = self.env.connector_home("dev")
        self.conn = self.env.connector(self.home)
        self.conn.pyz_sha256 = FAKE_PYZ                  # "runs" a build the server no longer publishes
        sums = (E.ROOT / "web" / "connector" / "SHA256SUMS").read_text()
        self.published = parse_sums(sums)["pixel-connector.pyz"]

    def tearDown(self):
        self.conn.stop()
        self.env.stop()

    def pair(self, page):
        dev = InProcPairing(self.conn)
        page.wait("!!document.querySelector('#btn-devices:not([hidden])')", 15)
        page.click("#btn-devices")
        page.click("[data-dev-add]")
        page.wait("document.getElementById('dlg-pair').open")
        page.click("#dlg-pair [data-step='1'] [data-goto='2']")
        page.click("#dlg-pair [data-step='2'] [data-goto='3']")
        self.assertIn("Eşleşti", pair_once(page, dev, dev.start(), decide=lambda a, b: a == b))
        page.eval("document.getElementById('dlg-pair').close()")
        page.wait("!!document.querySelector('.dev-card .dev-dot.on')", 20, what="device online")

    def layout(self, page, name):
        shots = os.environ.get("PIXEL_UI_SHOTS")
        for w, h in ((1280, 900), (390, 844), (360, 740)):
            page.viewport(w, h, mobile=w < 600)
            page.wait("window.innerWidth === %d" % w)
            time.sleep(0.2)
            with self.subTest(width=w, variant=name):
                self.assertTrue(no_hscroll(page))
                self.assertTrue(page.eval(INSIDE))
                self.assertTrue(page.eval(TARGETS_OK))
            if shots:
                page.screenshot(os.path.join(shots, "ui_update_%s_%d.png" % (name, w)))
        page.viewport(1280, 900)
        page.wait("window.innerWidth === 1280")

    def test_strip(self):
        with Chrome() as chrome:
            A = chrome.page(width=1280, height=900)
            open_room(A, self.env.base, self.env.room, self.env.key)
            self.pair(A)
            # the device card says the same thing
            A.wait("/pixel-connector update/.test((document.querySelector('.dev-card .dev-update') || {}).textContent)",
                   10, what="card hint")
            A.click("[data-dev-close]")

            # ---- 3.5.0+: the update command ------------------------------------------------------------------
            A.wait(BANNER_SHOWN, 15, what="strip")
            self.assertTrue(A.text("#update-banner").startswith("Bilgisayarınızdaki bağlayıcı eski."))
            self.assertEqual(A.text("#update-banner .ub-cmd"), "pixel-connector update")
            self.assertEqual(A.eval("document.querySelector('#update-banner [role=status]').tagName"), "P")
            self.assertTrue(A.eval("document.body.classList.contains('update-banner-on')"))
            self.assertEqual(A.eval("document.querySelector('#update-banner .ub-close').tabIndex"), 0)
            self.layout(A, "cmd")
            A.click("#update-banner .ub-close")
            A.wait("document.getElementById('update-banner').hidden", 5)
            self.assertEqual(A.eval("localStorage.getItem('po-update-dismissed')"), self.published)
            A.navigate(A.eval("location.href"))                          # dismissed for this release
            A.wait("!!document.querySelector('#btn-devices:not([hidden])')", 15)
            A.click("#btn-devices")
            A.wait("!!document.querySelector('.dev-card .dev-dot.on')", 20, what="device online again")
            A.click("[data-dev-close]")
            time.sleep(1.0)
            self.assertTrue(A.eval("document.getElementById('update-banner').hidden"))

            # ---- before 3.5.0: no update command → install command ------------------------------------------
            real_info = self.conn.device_info
            self.conn.device_info = lambda: dict(real_info(), connector="3.4.1")
            A.eval("localStorage.removeItem('po-update-dismissed')")
            A.navigate(A.eval("location.href"))
            A.wait(BANNER_SHOWN, 25, what="strip for an old connector")
            self.assertIn("kurulum komutunu", A.text("#update-banner"))
            self.assertFalse(A.eval("!!document.querySelector('#update-banner .ub-cmd')"))
            self.layout(A, "install")
            A.click("#update-banner .ub-install")
            A.wait("document.getElementById('dlg-pair').open", 5, what="wizard")
            self.assertFalse(A.eval("document.querySelector(\"#dlg-pair [data-step='1']\").hidden"))
            A.eval("document.getElementById('dlg-pair').close()")
            self.assertEqual(A.errors, [])

            # ---- mini window (same paired profile): no strip ---------------------------------------------------
            M = chrome.page(width=360, height=300)
            M.navigate(self.env.base + "/?room=%s&view=mini#key=%s" % (self.env.room, self.env.key))
            M.wait("document.body.classList.contains('mini')", 15)
            time.sleep(2.0)
            self.assertTrue(M.eval("document.getElementById('update-banner').hidden"))
            self.assertEqual(M.errors, [])

        # ---- a fresh profile: viewer without the key, then unpaired with the key: no strip ----------------------
        with Chrome() as other:
            V = other.page(width=390, height=844, mobile=True)
            open_room(V, self.env.base, self.env.room)
            time.sleep(1.0)
            self.assertTrue(V.eval("document.getElementById('btn-devices').hidden"))      # really a viewer
            self.assertTrue(V.eval("document.getElementById('update-banner').hidden"))
            self.assertEqual(V.errors, [])
            V.close()
            B = other.page(width=1280, height=900)
            open_room(B, self.env.base, self.env.room, self.env.key)
            B.wait("!!document.querySelector('#btn-devices:not([hidden])')", 15)
            time.sleep(1.0)
            self.assertTrue(B.eval("document.getElementById('update-banner').hidden"))
            self.assertEqual(B.errors, [])


if __name__ == "__main__":
    unittest.main()
