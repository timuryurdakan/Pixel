import http.client
import json
import logging
import socket
import tempfile
import threading
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

import dev_server
from pixel_cloud.app import App, Request
from pixel_cloud.demo import DemoWriter
from pixel_cloud.storage import MemoryStorage

logging.getLogger("pixel_cloud").setLevel(logging.CRITICAL)


class DevServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "web"
        (root / "hook").mkdir(parents=True)
        (root / "index.html").write_text("<!doctype html>")
        (root / "manifest.webmanifest").write_text("{}")
        (root / "sw.js").write_text("//")
        (root / "hook" / "pixel_hook.py").write_text("print('hi')")
        (Path(self.tmp.name) / "secret.txt").write_text("top secret")
        self.app = App(storage=MemoryStorage(), env={"PIXEL_SECRET": "d" * 40}, mapper=None)
        self.srv = dev_server.DevServer(("127.0.0.1", 0), self.app, root)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def get(self, path, method="GET", body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_static_and_headers(self):
        r, body = self.get("/")
        self.assertEqual((r.status, body), (200, b"<!doctype html>"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))
        r, _ = self.get("/manifest.webmanifest")
        self.assertEqual(r.getheader("Content-Type"), "application/manifest+json")
        r, _ = self.get("/sw.js")
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")
        r, _ = self.get("/hook/pixel_hook.py")
        self.assertEqual((r.getheader("Content-Type"), r.getheader("Cache-Control")), ("text/plain; charset=utf-8", "no-cache"))
        for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/hook/%00x", "/nope.js"):
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
                s.sendall(f"GET {p} HTTP/1.0\r\n\r\n".encode())
                data = b""
                while chunk := s.recv(65536):
                    data += chunk
            self.assertTrue(data.startswith(b"HTTP/1.1 404"), (p, data[:40]))
            self.assertNotIn(b"top secret", data)
        r, _ = self.get("/index.html", "POST", b"x")
        self.assertEqual(r.status, 405)

    def test_api_mounted_with_cache_hint(self):
        r, body = self.get("/api/config")
        self.assertEqual(json.loads(body)["mode"], "cloud")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        r, body = self.get("/api/rooms", "POST", b'{"name":"Dev"}')
        room = json.loads(body)
        self.assertEqual(r.status, 201)
        self.assertTrue(room["joinUrl"].startswith(f"http://127.0.0.1:{self.port}/?room="))
        r, _ = self.get(f"/api/state?room={room['roomId']}")
        self.assertEqual(r.getheader("Cache-Control"), "public, max-age=0, s-maxage=5")
        self.assertEqual(r.getheader("X-Pixel-Cache-Hint"), "s-maxage=5")
        r, _ = self.get("/api/unknown")
        self.assertEqual(r.status, 404)


class DemoTests(unittest.TestCase):
    def test_demo_writer_posts(self):
        app = App(storage=MemoryStorage(), env={"PIXEL_SECRET": "d" * 40, "PIXEL_CREATE_TOKEN": "pw"}, mapper=None)
        demo = DemoWriter(app, base_url="http://127.0.0.1:8787", seed=1)
        rid = demo.create_room()
        self.assertTrue(demo.join_url.startswith("http://127.0.0.1:8787/?room="))
        self.assertIn("#key=", demo.join_url)
        import time
        t = time.time()
        for i in range(40):
            demo.step(t + i * 3)
        st = app.dispatch(Request("GET", f"/api/state?room={rid}")).json()
        self.assertEqual(st["room"]["name"], "Demo Oda")
        owners = {a["owner"] for a in st["agents"]}
        self.assertEqual(owners, {"Timur", "Ayşe", "Mert"})
        self.assertEqual(len({a["ownerColor"] for a in st["agents"] if not a["isSubagent"]}), 3)


if __name__ == "__main__":
    unittest.main()
