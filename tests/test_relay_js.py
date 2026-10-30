"""Runs the relay's JavaScript tests without Node (V3.md §16.4): relay/test/*.test.js (the same files Workers Builds
runs with `node --test`) and tests/js/relay_client.test.js (web/js/relay.js) execute as ES modules in headless Chrome,
with `node:test` / `node:assert/strict` mapped to tests/js_shims/ through an import map. WebCrypto (HMAC) is Chrome's.

Skipped when google-chrome / chromium is not installed."""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

ROOT = Path(__file__).resolve().parent.parent
TEST_FILES = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "relay" / "test").glob("*.test.js")) + \
    sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests" / "js").glob("*.test.js"))
CHROME = next((shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
               if shutil.which(n)), None)

PAGE = """<!doctype html><meta charset="utf-8"><title>relay js tests</title>
<script type="importmap">{"imports": {"node:test": "/tests/js_shims/node_test.js",
                                      "node:assert/strict": "/tests/js_shims/node_assert.js",
                                      "cloudflare:workers": "/tests/js_shims/cloudflare_workers.js"}}</script>
<script>localStorage.setItem('po-lang', 'tr');   // F4-2: suites assert the Turkish (source) texts</script>
<script type="module">
const files = %s;
const post = (obj) => fetch('/__result', {method: 'POST', body: JSON.stringify(obj)});
try {
  const { run } = await import('/tests/js_shims/node_test.js');
  const out = [];
  for (const f of files) {
    await import('/' + f);
    for (const r of await run()) out.push({file: f, ...r});
  }
  await post({results: out});
} catch (e) {
  await post({fatal: String(e && e.stack || e)});
}
</script>"""


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/__runner.html":
            body = (PAGE % json.dumps(TEST_FILES)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.server.result = json.loads(self.rfile.read(n) or b"{}")
        self.server.done.set()
        self.send_response(204)
        self.end_headers()


_Handler.extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript",
                           ".json": "application/json"}


def run_in_chrome(timeout: float = 90.0) -> dict:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.done = threading.Event()
    srv.result = None
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    th.start()
    profile = tempfile.mkdtemp(prefix="pixel-relay-js-")
    proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                             "--disable-extensions", f"--user-data-dir={profile}", "--remote-debugging-port=0",
                             f"http://127.0.0.1:{srv.server_address[1]}/__runner.html"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not srv.done.wait(timeout):
            raise AssertionError("headless Chrome did not report results")
        return srv.result
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(profile, ignore_errors=True)


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class RelayJsTests(unittest.TestCase):
    def test_js_suites_pass_in_headless_chrome(self):
        res = run_in_chrome()
        self.assertNotIn("fatal", res, res.get("fatal"))
        results = res["results"]
        files = {r["file"] for r in results}
        self.assertEqual(files, set(TEST_FILES))
        failed = [f"{r['file']} › {r['name']}\n{r['error']}" for r in results if not r["ok"]]
        self.assertFalse(failed, "\n\n".join(failed))
        self.assertGreaterEqual(len(results), 25)


if __name__ == "__main__":
    unittest.main()
