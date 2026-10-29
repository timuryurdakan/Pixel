"""G4: opening the `pixel-connector pair` QR link on a phone (web/js/main.js + devices.js), headless Chrome over CDP.

Real page ⇄ dev_relay ⇄ real connector. The link `/?room=<id>#key=<k>&pair=<code>` (the `--qr-key` form; without the
key the phone must already know the room) opens the pairing dialog at step 3 with the code filled in, strips the
fragment from the address bar at once (also on a first visit, which would otherwise go to the guide), and does NOT
start pairing by itself: the device still sees an open, untouched code until the user presses "Eşleştir", and the SAS
is still confirmed on the computer. A tampered fragment (wrong form, lowercase, extra text, script) opens nothing; a
used code fails like a typed one.
"""

import time
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
from _cdp import CHROME, Chrome
from test_v3_ui import InProcPairing


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class PairLinkUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = E.V3Env()
        try:
            cls.conn = cls.env.connector(cls.env.connector_home())
            cls.dev = InProcPairing(cls.conn)
            cls.chrome = Chrome()
        except BaseException:
            cls.env.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.chrome.close()
        cls.conn.stop()
        cls.env.stop()

    def link(self, frag):
        return "%s/?room=%s#%s" % (self.env.base, self.env.room, frag)

    def open_link(self, frag):
        page = self.chrome.page(width=390, height=844, mobile=True)
        page.navigate(self.link(frag))
        page.wait("document.getElementById('conn-status') && "
                  "document.getElementById('conn-status').dataset.status === 'live'", 20, what="room live")
        return page

    def test_link_fills_the_code_strips_it_and_pairs_only_after_the_user_and_the_sas(self):
        code = self.dev.start()
        c12 = code.replace("-", "")
        page = self.open_link("key=%s&pair=%s" % (self.env.key, c12))          # first visit: guide never seen
        page.wait("document.getElementById('dlg-pair').open", 20, what="pair dialog")
        self.assertEqual(page.eval("location.hash"), "")
        self.assertNotIn(c12, page.eval("location.href"))
        self.assertEqual(page.eval("location.pathname + location.search"), "/?room=" + self.env.room)
        self.assertEqual(page.eval("document.getElementById('pair-code').value"),
                         "-".join((c12[:4], c12[4:8], c12[8:])))
        self.assertIn("Kod QR'dan yazıldı", page.text("#pair-status"))
        self.assertFalse(page.eval("document.querySelector('#dlg-pair [data-step=\"3\"]').hidden"))
        time.sleep(1.5)
        self.assertEqual(self.dev.p.state, "open")                             # no auto-submit
        self.assertTrue(page.eval("document.getElementById('pair-sas').hidden"))
        page.click("#pair-submit")
        page.wait("!document.getElementById('pair-sas').hidden", 30, what="SAS shown")
        sas_page = page.text("#pair-sas .sas-code")
        st, sas_dev, _ = self.dev.wait_confirm()
        self.assertEqual((st, sas_page), ("confirm", sas_dev))
        self.dev.confirm(True)
        page.wait("/ok|error/.test(document.getElementById('pair-status').className)", 40, what="pair result")
        self.assertIn("Eşleşti", page.text("#pair-status"))
        self.assertEqual(len(self.dev.peers()), 1)
        self.assertEqual(page.errors, [])
        page.close()
        # the same link again: the code is used up (single use), so it fails like a typed one
        page = self.open_link("key=%s&pair=%s" % (self.env.key, c12))
        page.wait("document.getElementById('dlg-pair').open", 20, what="pair dialog")
        page.click("#pair-submit")
        page.wait("/error/.test(document.getElementById('pair-status').className)", 60, what="pair error")
        self.assertEqual(len(self.dev.peers()), 1)
        page.close()

    def test_tampered_fragment_opens_nothing_and_is_stripped(self):
        code = self.dev.start().replace("-", "")
        bad = [code.lower(), code[:11], code + "A", code[:11] + "U", "%3Cscript%3Ealert(1)%3C%2Fscript%3E",
               code + "%26key%3Dx"]
        for value in bad:
            page = self.open_link("key=%s&pair=%s" % (self.env.key, value))
            page.wait("!!document.querySelector('#btn-devices:not([hidden])')", 20, what="v3 controls")
            time.sleep(0.3)
            self.assertFalse(page.eval("document.getElementById('dlg-pair').open"), value)
            self.assertEqual(page.eval("location.hash"), "", value)
            self.assertEqual(page.errors, [], value)
            page.close()
        self.assertEqual(self.dev.p.state, "open")


if __name__ == "__main__":
    unittest.main()
