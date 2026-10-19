import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

from pixel_office.httpd import make_server
from pixel_office.models import AgentInfo, State
from pixel_office.store import AgentStore


def agent(aid: str, state: State = State.THINKING) -> AgentInfo:
    return AgentInfo(id=aid, name="Ada", project="p", state=state, started_at=1)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "web"
        (root / "js").mkdir(parents=True)
        (root / "index.html").write_text("<!doctype html><title>x</title>")
        (root / "js" / "a.js").write_text("export const A = 1;")
        (root / "css").mkdir()
        (root / "css" / "s.css").write_text("body{}")
        (Path(self.tmp.name) / "secret.txt").write_text("top secret")
        self.store = AgentStore("demo")
        self.srv = make_server("127.0.0.1", 0, self.store, root)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutting_down.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def get(self, path: str, method: str = "GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def raw_get(self, path: str) -> bytes:
        """Send the request path verbatim (http.client would not normalise it either, but be explicit)."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
            data = b""
            while chunk := s.recv(65536):
                data += chunk
        return data

    def test_static(self):
        r, body = self.get("/")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual(int(r.getheader("Content-Length")), len(body))
        r, body = self.get("/js/a.js?v=1")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(body, b"export const A = 1;")
        r, _ = self.get("/css/s.css")
        self.assertEqual(r.getheader("Content-Type"), "text/css; charset=utf-8")

    def test_not_found_and_traversal(self):
        for p in ("/nope.js", "/js/", "/../secret.txt", "/js/../../secret.txt", "/%2e%2e/secret.txt",
                  "/..%2fsecret.txt", "/js/%00a.js", "/js"):
            data = self.raw_get(p)
            self.assertTrue(data.startswith(b"HTTP/1.0 404"), (p, data[:60]))
            self.assertNotIn(b"top secret", data)
        r, body = self.get("/nope")
        self.assertEqual(json.loads(body), {"error": "not found"})

    def test_method_not_allowed(self):
        r, body = self.get("/api/agents", "POST")
        self.assertEqual(r.status, 405)
        self.assertEqual(json.loads(body)["error"], "method not allowed")

    def test_api(self):
        self.store.upsert(agent("a"))
        r, body = self.get("/api/agents")
        self.assertEqual(r.status, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("application/json"))
        d = json.loads(body)
        self.assertEqual(d["mode"], "demo")
        self.assertEqual([a["id"] for a in d["agents"]], ["a"])
        r, body = self.get("/api/health")
        self.assertEqual(json.loads(body), {"ok": True, "mode": "demo", "agents": 1})

    def _read_event(self, f) -> dict:
        ev = {}
        while True:
            line = f.readline().decode()
            if line in ("\n", ""):
                if ev:
                    return ev
                if line == "":
                    raise EOFError
                continue
            k, _, v = line.rstrip("\n").partition(": ")
            ev[k] = v

    def test_sse(self):
        self.store.upsert(agent("a"))
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        f = s.makefile("rb")
        status = f.readline()
        self.assertIn(b"200", status)
        headers = {}
        while (line := f.readline()) not in (b"\r\n", b""):
            k, _, v = line.decode().partition(":")
            headers[k.lower()] = v.strip()
        self.assertEqual(headers["content-type"], "text/event-stream; charset=utf-8")
        self.assertEqual(headers["cache-control"], "no-cache")
        self.assertEqual(self._read_event(f), {"retry": "2000"})
        snap = self._read_event(f)
        self.assertEqual(snap["event"], "snapshot")
        self.assertEqual(snap["id"], "1")
        self.assertEqual([a["id"] for a in json.loads(snap["data"])["agents"]], ["a"])

        self.store.upsert(agent("b"))
        self.store.upsert(agent("a", State.WORKING))
        self.store.remove("b")
        evs = [self._read_event(f) for _ in range(3)]
        self.assertEqual([e["event"] for e in evs], ["agent_added", "agent_updated", "agent_removed"])
        self.assertEqual([e["id"] for e in evs], ["2", "3", "4"])
        self.assertEqual(json.loads(evs[1]["data"])["state"], "working")
        self.assertEqual(json.loads(evs[2]["data"]), {"id": "b"})
        self.assertEqual(self.store.subscriber_count(), 1)

        # client disconnects → handler unsubscribes on the next write
        f.close()
        s.close()
        deadline = time.time() + 5
        i = 0
        while self.store.subscriber_count() and time.time() < deadline:
            i += 1
            self.store.upsert(agent(f"z{i}"))
            time.sleep(0.05)
        self.assertEqual(self.store.subscriber_count(), 0)

    def test_sse_ends_on_shutdown(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /events HTTP/1.0\r\n\r\n")
        f = s.makefile("rb")
        f.readline()
        deadline = time.time() + 2
        while not self.store.subscriber_count() and time.time() < deadline:
            time.sleep(0.01)
        self.srv.shutting_down.set()
        deadline = time.time() + 3
        while self.store.subscriber_count() and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.store.subscriber_count(), 0)
        f.close()
        s.close()


if __name__ == "__main__":
    unittest.main()
