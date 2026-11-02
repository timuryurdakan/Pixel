"""F4-4: the browser's ZIP writer (web/js/zip.js) produces files that an independent reader accepts.

The ZIP is built in headless Chrome by the real module and read here with Python's zipfile: names (UTF-8, folders),
contents, CRC (`testzip`), stored method, and a date that zipfile decodes. Skipped when Chrome / Chromium is missing.
"""

import base64
import functools
import http.server
import io
import json
import threading
import unittest
import zipfile
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)
from _cdp import CHROME, Chrome

ROOT = Path(__file__).resolve().parent.parent

ENTRIES = [("notlar.md", "# Başlık\nçğıöşü ✓\n"), ("site/index.html", "<!doctype html><p>Merhaba</p>"),
           ("site/css/stil.css", ""), ("boş satırlı.txt", "a\r\nb\n")]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class ZipWriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(_Quiet, directory=str(ROOT))
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.chrome = Chrome()
        cls.page = cls.chrome.page("http://127.0.0.1:%d/web/js/" % cls.httpd.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.chrome.close()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def build(self, entries, mtime="Date.UTC(2026, 10, 1, 12, 30, 44)"):
        js = """(async () => {
          const { buildZip } = await import('/web/js/zip.js');
          const enc = new TextEncoder();
          const z = buildZip(%s.map(([path, text]) => ({ path, data: enc.encode(text), mtime: %s })));
          let s = ''; for (const b of z) s += String.fromCharCode(b); return btoa(s);
        })()""" % (json.dumps(entries), mtime)
        return base64.b64decode(self.page.eval(js))

    def test_python_reads_what_the_browser_wrote(self):
        data = self.build(ENTRIES)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIsNone(z.testzip())                                  # every CRC matches
            self.assertEqual(z.namelist(), [p for p, _ in ENTRIES])
            for path, text in ENTRIES:
                self.assertEqual(z.read(path).decode("utf-8"), text)
                info = z.getinfo(path)
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertTrue(info.flag_bits & 0x800)                      # UTF-8 names
                self.assertEqual(info.date_time[0], 2026)

    def test_empty_and_old_dates(self):
        with zipfile.ZipFile(io.BytesIO(self.build([]))) as z:
            self.assertEqual(z.namelist(), [])
        with zipfile.ZipFile(io.BytesIO(self.build([["a.txt", "x"]], mtime="0"))) as z:
            self.assertEqual(z.getinfo("a.txt").date_time, (1980, 1, 1, 0, 0, 0))

    def test_crc32_known_value(self):
        self.assertEqual(self.page.eval(
            "import('/web/js/zip.js').then(m => m.crc32(new TextEncoder().encode('123456789')))"), 0xCBF43926)


if __name__ == "__main__":
    unittest.main()
