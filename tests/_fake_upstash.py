"""A tiny fake of the Upstash Redis REST API (POST / and POST /pipeline) for storage tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeUpstash:
    def __init__(self, token: str = "test-token") -> None:
        self.token = token
        self.data: dict[str, dict[str, str]] = {}
        self.ttl: dict[str, int] = {}
        self.calls: list[list] = []          # every command executed (pipelines flattened)
        self.requests = 0
        self.fail_mode: str | None = None    # "http500" | "error" | "drop" (once each)
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                with fake.lock:
                    fake.requests += 1
                    mode, fake.fail_mode = fake.fail_mode, None
                if mode == "drop":
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                if self.headers.get("Authorization") != f"Bearer {fake.token}":
                    return self._send(401, {"error": "Unauthorized"})
                if mode == "http500":
                    return self._send(500, {"error": "boom"})
                cmds = json.loads(body)
                if self.path == "/pipeline":
                    out = [fake.exec(c) for c in cmds]
                    return self._send(200, out)
                if self.path == "/":
                    if mode == "error":
                        return self._send(400, {"error": "ERR wrong number of arguments"})
                    return self._send(200, fake.exec(cmds))
                return self._send(404, {"error": "not found"})

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.server.handle_error = lambda *a: None
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def exec(self, cmd: list) -> dict:
        with self.lock:
            self.calls.append(cmd)
            op = str(cmd[0]).upper()
            args = [str(a) for a in cmd[1:]]
            try:
                if op == "PING":
                    return {"result": "PONG"}
                key = args[0]
                h = self.data.get(key)
                if op == "HSET":
                    h = self.data.setdefault(key, {})
                    new = 0
                    for i in range(1, len(args), 2):
                        new += args[i] not in h
                        h[args[i]] = args[i + 1]
                    return {"result": new}
                if op == "HGETALL":
                    flat = []
                    for k, v in (h or {}).items():
                        flat += [k, v]
                    return {"result": flat}
                if op == "HDEL":
                    n = 0
                    for f in args[1:]:
                        if h and f in h:
                            del h[f]
                            n += 1
                    if h is not None and not h:
                        del self.data[key]
                    return {"result": n}
                if op == "HLEN":
                    return {"result": len(h or {})}
                if op == "HKEYS":
                    return {"result": list(h or {})}
                if op == "EXPIRE":
                    if h is None:
                        return {"result": 0}
                    self.ttl[key] = int(args[1])
                    return {"result": 1}
            except (IndexError, ValueError):
                pass
            return {"error": f"ERR unknown command '{op}'"}
