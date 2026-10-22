"""pixel_agent.py (CLOUD.md §17): sender policy, library, pixel-run, pixel-proxy, privacy."""

import http.client
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

ROOT = Path(__file__).resolve().parent.parent
AGENT_PY = ROOT / "web" / "hook" / "pixel_agent.py"
CANARY = "CANARY-7f3a"
POLL = 0.05                   # serve_forever poll interval: shutdown() returns in ~50 ms instead of 0.5 s

spec = importlib.util.spec_from_file_location("pixel_agent_under_test", AGENT_PY)
pa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pa)


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Post:
    def __init__(self, status=200):
        self.batches = []
        self.status = status

    def __call__(self, updates, timeout):
        self.batches.append(updates)
        return self.status

    @property
    def flat(self):
        return [u for b in self.batches for u in b]


class FakeAgent:
    def __init__(self, name="x"):
        self.name = name
        self.reports = []
        self.finished = False

    def report(self, state, tool=None, label=None, final=False):
        self.reports.append((state, tool, label, final))

    def flush(self, timeout=2.0):
        pass

    @property
    def states(self):
        return [r[0] for r in self.reports]


class StatusSink:
    """A tiny /api/status server that records bodies (stands in for the local or cloud server)."""

    def __init__(self):
        self.bodies = []
        self.headers = []
        sink = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                sink.bodies.append((time.monotonic(), self.path, json.loads(body)))
                sink.on_body()
                sink.headers.append(dict(self.headers))
                out = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": POLL}, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def watch_cycle(self, idle_flag, again_flag):
        """Touch `idle_flag` once an "idle" update arrived and `again_flag` once "working" followed it, so the
        child process in PixelRunTests waits for what the server saw instead of sleeping past the debounce."""
        self.flags = (Path(idle_flag), Path(again_flag))

    def on_body(self):
        flags = getattr(self, "flags", None)
        if not flags:
            return
        states = [u["state"] for u in self.updates()]
        if "idle" in states:
            flags[0].touch()
            if "working" in states[states.index("idle"):]:
                flags[1].touch()

    def updates(self):
        out = []
        for _, _, b in self.bodies:
            out.extend(b["agents"] if "agents" in b else [b])
        return out

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


# ---------------------------------------------------------------------------------------------------
class SenderPolicyTests(unittest.TestCase):
    def mk(self, cloud):
        clock = FakeClock()
        post = Post()
        target = {"url": "http://x", "room": "r" * 26 if cloud else None, "key": "k" if cloud else None,
                  "owner": "Timur", "ownerId": None}
        s = pa.Sender(target, post=post, clock=clock, start_thread=False)
        return s, clock, post

    def agent(self, s, clock, name="bot", **kw):
        return pa.Agent(name, sender=s, clock=clock, register_atexit=False, url="http://x", **kw)

    def test_local_debounce_0_3s(self):
        s, clock, post = self.mk(False)
        a = self.agent(s, clock)
        a.report("thinking")
        clock.t += 0.2
        a.report("working", tool="llm")
        s.tick()
        self.assertEqual(post.batches, [])
        clock.t += 0.11
        s.tick()
        self.assertEqual([u["state"] for u in post.flat], ["working"])
        clock.t += 1
        a.report("thinking")                      # local: no 30 s throttle
        clock.t += 0.31
        s.tick()
        self.assertEqual([u["state"] for u in post.flat], ["working", "thinking"])

    def test_cloud_groups_and_throttle(self):
        s, clock, post = self.mk(True)
        a = self.agent(s, clock)
        a.report("thinking")
        clock.t += 1.0
        s.tick()
        self.assertEqual(len(post.flat), 1)
        for i in range(10):                         # tight loop inside the active group
            clock.t += 2.5
            a.report("working" if i % 2 else "thinking", tool="llm" if i % 2 else None)
            s.tick()
        self.assertEqual(len(post.flat), 1)        # 25 s < 30 s: nothing yet
        clock.t += 5.1
        s.tick()
        self.assertEqual(len(post.flat), 2)        # 30 s after the last send: newest state
        a.report("waiting_for_user", label="Onay bekliyor")
        s.tick()
        self.assertEqual(len(post.flat), 2)        # group change: 1 s debounce
        clock.t += 1.0
        s.tick()
        self.assertEqual(post.flat[-1]["state"], "waiting_for_user")
        a.report("idle")
        clock.t += 1.0
        s.tick()
        self.assertEqual(post.flat[-1]["state"], "idle")

    def test_cloud_hour_budget(self):
        s, clock, post = self.mk(True)
        a = self.agent(s, clock)
        for i in range(1440):                       # one LLM call every 2.5 s for an hour
            a.report("thinking" if i % 2 == 0 else "working", tool=None if i % 2 == 0 else "search")
            clock.t += 2.5
            s.tick()
        self.assertLessEqual(len(post.flat), 2 + 121)

    def test_dedupe_keepalive_and_offline(self):
        s, clock, post = self.mk(False)
        a = self.agent(s, clock)
        a.report("idle")
        clock.t += 1
        s.tick()
        a.report("idle")
        clock.t += 1
        s.tick()
        self.assertEqual(len(post.flat), 1)
        clock.t += 300
        s.tick()
        self.assertEqual(len(post.flat), 2)        # keep-alive
        a.report("offline")
        s.flush(1.0)
        self.assertEqual(post.flat[-1]["state"], "offline")
        clock.t += 1000
        s.tick()
        self.assertEqual(len(post.flat), 3)        # no keep-alive after offline

    def test_batching(self):
        s, clock, post = self.mk(True)
        agents = [self.agent(s, clock, name="bot%d" % i) for i in range(12)]
        for a in agents:
            a.report("working", tool="t")
        clock.t += 1
        s.tick()
        self.assertEqual([len(b) for b in post.batches], [10, 2])
        self.assertEqual(len({u["session"] for u in post.flat}), 12)

    def test_429_and_errors_are_dropped(self):
        s, clock, post = self.mk(False)
        post.status = 429
        a = self.agent(s, clock)
        a.report("working")
        clock.t += 1
        s.tick()
        clock.t += 1
        s.tick()
        self.assertEqual(len(post.flat), 1)

    def test_payload_fields_and_ages(self):
        s, clock, post = self.mk(False)
        a = self.agent(s, clock, name="Kâtip Bot", project="/home/u/notes", owner="Ayşe")
        clock.t += 10
        a.report("working", tool="web search!", label="x" * 60)
        clock.t += 0.5
        s.tick()
        u = post.flat[0]
        self.assertEqual((u["session"], u["name"], u["project"], u["owner"], u["tool"], u["source"]),
                         ("Katip-Bot", "Kâtip Bot", "notes", "Ayşe", "websearch", "custom"))
        self.assertEqual(len(u["label"]), 40)
        self.assertEqual((u["stateAgeMs"], u["startedAgeMs"]), (500, 10500))
        self.assertNotIn("final", u)
        self.assertNotIn("_since", u)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.post = Post()
        self.s = pa.Sender({"url": "http://x", "room": None, "key": None, "owner": "T", "ownerId": None},
                           post=self.post, clock=self.clock, start_thread=False)
        self.a = pa.Agent("bot", sender=self.s, clock=self.clock, register_atexit=False, url="http://x")
        self.a.sender = self.s

    def sent(self):
        self.clock.t += 1
        self.s.tick()
        return self.post.flat[-1]

    def test_working_context_manager(self):
        with self.a.working(tool="llm", label="Özetliyor"):
            self.assertEqual(self.sent()["state"], "working")
        self.assertEqual(self.sent()["state"], "idle")
        with self.assertRaises(ValueError):
            with self.a.working(tool="llm"):
                raise ValueError("secret " + CANARY)
        u = self.sent()
        self.assertEqual((u["state"], u["label"]), ("idle", "Hata"))
        self.assertNotIn(CANARY, json.dumps(self.post.batches))

    def test_task_decorator_and_done(self):
        @self.a.task(tool="embed")
        def f(x):
            return x * 2
        self.assertEqual(f(21), 42)
        self.assertEqual(self.sent()["state"], "idle")
        self.a.waiting("Onay bekliyor")
        self.assertEqual(self.sent()["state"], "waiting_for_user")
        self.a.done()
        u = self.sent()
        self.assertEqual((u["state"], u["final"]), ("sleeping", True))
        self.assertTrue(self.a.finished)

    def test_never_raises(self):
        self.a.report("bogus")
        self.a.report(None)
        broken = pa.Agent("x", sender=object(), register_atexit=False, url="http://x")
        broken.report("working")
        broken.offline()
        with broken.working():
            pass

    def test_target_resolution(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        Path(tmp, "config.json").write_text(json.dumps({"server": "https://c.example", "room": "r" * 26,
                                                        "key": "k" * 32, "name": "Timur", "ownerId": "o_abcdef"}))
        with mock.patch.dict(os.environ, {"PIXEL_OFFICE_HOME": tmp}, clear=False):
            for k in ("PIXEL_OFFICE_URL", "PIXEL_OFFICE_ROOM", "PIXEL_OFFICE_KEY", "PIXEL_OFFICE_OWNER"):
                os.environ.pop(k, None)
            t = pa.resolve_target()
            self.assertEqual((t["url"], t["room"], t["owner"], t["ownerId"]),
                             ("https://c.example", "r" * 26, "Timur", "o_abcdef"))
            with mock.patch.dict(os.environ, {"PIXEL_OFFICE_URL": "http://127.0.0.1:9999/"}):
                t = pa.resolve_target()
                self.assertEqual((t["url"], t["room"], t["key"]), ("http://127.0.0.1:9999", None, None))
            t = pa.resolve_target(url="http://l:1", owner="Ece")
            self.assertEqual((t["url"], t["room"], t["owner"], t["ownerId"]), ("http://l:1", None, "Ece", None))
            self.assertEqual(pa.status_url({"url": "https://c", "room": "abc"}), "https://c/api/status?room=abc")
        with mock.patch.dict(os.environ, {"PIXEL_OFFICE_HOME": tmp + "/none"}):
            for k in ("PIXEL_OFFICE_URL", "PIXEL_OFFICE_OWNER"):
                os.environ.pop(k, None)
            self.assertEqual(pa.resolve_target()["url"], pa.DEFAULT_URL)


class BackendParityTests(unittest.TestCase):
    """The same Agent code reports to the local server and to the cloud, only the base URL (+room/key) differs."""

    def test_local_and_cloud(self):
        from dev_server import DevServer
        from pixel_cloud.app import App, Request
        from pixel_cloud.storage import MemoryStorage
        from pixel_office.httpd import make_server
        from pixel_office.reported import ReportedAgents
        from pixel_office.store import AgentStore
        store = AgentStore("live")
        reported = ReportedAgents(store)
        local = make_server("127.0.0.1", 0, store, reported=reported)
        app = App(storage=MemoryStorage(), env={"PIXEL_SECRET": "x" * 40})
        cloud = DevServer(("127.0.0.1", 0), app, ROOT / "web")
        for srv in (local, cloud):
            threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": POLL}, daemon=True).start()
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
        room = json.loads(app.dispatch(Request("POST", "/api/rooms", {}, body=b"{}", client_ip="1.1.1.1")).body)
        lu = "http://127.0.0.1:%d" % local.server_address[1]
        cu = "http://127.0.0.1:%d" % cloud.server_address[1]
        for url, rm, key in ((lu, None, None), (cu, room["roomId"], room["writeKey"])):
            s = pa.Sender(pa.resolve_target(url, rm, key, "Timur"))
            a = pa.Agent("Kâtip", project="notes", sender=s, register_atexit=False, url=url, room=rm, key=key,
                         owner="Timur")
            a.report("working", tool="llm", label="Özetliyor")
            a.flush(3.0)
            s.stop()
        c = http.client.HTTPConnection("127.0.0.1", local.server_address[1], timeout=5)
        c.request("GET", "/api/agents")
        la = json.loads(c.getresponse().read())["agents"]
        c.close()
        ca = json.loads(app.dispatch(Request("GET", "/api/state?room=" + room["roomId"], {})).body)["agents"]
        for lst in (la, ca):
            self.assertEqual(len(lst), 1)
            self.assertEqual((lst[0]["state"], lst[0]["tool"], lst[0]["detail"], lst[0]["name"]),
                             ("working", "llm", "Özetliyor", "Kâtip"))


# ---------------------------------------------------------------------------------------------------
CHILD = textwrap.dedent("""
    import os, signal, sys, time
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(int(os.environ.get("TERM_RC", "7"))))
    for i in range(4):
        print("tick %d CANARY-7f3a" % i, flush=True)
        time.sleep(0.1)
    if os.environ.get("WAIT_TERM"):
        print("ready", flush=True)
        time.sleep(30)
    def wait_for(flag, fallback):
        # With a flag file: wait (bounded) until the test's status sink has seen the state; else a fixed sleep.
        if not flag:
            time.sleep(fallback)
            return
        end = time.time() + 15
        while time.time() < end and not os.path.exists(flag):
            time.sleep(0.02)
    wait_for(os.environ.get("IDLE_FLAG"), float(os.environ.get("QUIET", "1.6")))
    print("again", flush=True)
    sys.stderr.write("err line\\n")
    wait_for(os.environ.get("AGAIN_FLAG"), float(os.environ.get("TAIL", "0.45")))
    sys.exit(int(os.environ.get("RC", "3")))
""")


class PixelRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.child = self.tmp / "child.py"
        self.child.write_text(CHILD)
        self.sink = StatusSink()
        self.addCleanup(self.sink.close)

    def wrapper(self, mode_flag, rc="3", extra_env=None, wait=True):
        env = dict(os.environ, RC=rc, PIXEL_OFFICE_HOME=str(self.tmp / "home"), **(extra_env or {}))
        args = [sys.executable, str(AGENT_PY), "run", "--name", "nightly", "--project", "proj", "--idle-after", "0.6",
                "--url", self.sink.url]
        if mode_flag:
            args.append(mode_flag)
        args += ["--", sys.executable, str(self.child), "--secret-arg", CANARY]
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=env)
        if not wait:
            return p
        out, err = p.communicate(timeout=30)
        return p.returncode, out, err

    def check_cycle(self, rc, out):
        self.assertEqual(rc, 3)
        self.assertIn(b"tick 0", out)
        self.assertIn(b"again", out)
        ups = self.sink.updates()
        states = [u["state"] for u in ups]
        self.assertEqual(states[0], "working")
        self.assertIn("idle", states)
        i = states.index("idle")
        self.assertIn("working", states[i:])
        last = ups[-1]
        self.assertEqual((last["state"], last.get("final"), last.get("label")), ("sleeping", True, "Hata: kod 3"))
        self.assertEqual({u["session"] for u in ups}, {"nightly"})
        self.assertEqual(ups[0]["tool"], "python3" if Path(sys.executable).name == "python3" else
                         pa.clean_tool(Path(sys.executable).name))
        blob = json.dumps(self.sink.updates())
        self.assertNotIn(CANARY, blob)                  # neither output nor arguments are sent
        self.assertNotIn("tick", blob)

    def cycle_env(self):
        idle, again = self.tmp / "seen-idle", self.tmp / "seen-again"
        self.sink.watch_cycle(idle, again)
        return {"IDLE_FLAG": str(idle), "AGAIN_FLAG": str(again)}

    def test_pipe_mode(self):
        rc, out, err = self.wrapper("--pipe", extra_env=self.cycle_env())
        self.check_cycle(rc, out)
        self.assertIn(b"err line", err)

    @unittest.skipUnless(os.name == "posix", "pty is POSIX only")
    def test_pty_mode(self):
        rc, out, err = self.wrapper("--pty", extra_env=self.cycle_env())
        self.check_cycle(rc, out)
        self.assertIn(b"err line", out)                 # stderr goes through the pty too

    def test_exit_zero_and_offline(self):
        rc, out, err = self.wrapper("--pipe", rc="0", extra_env={"QUIET": "0", "TAIL": "0"})
        self.assertEqual(rc, 0)
        self.assertEqual(self.sink.updates()[-1].get("label"), "Bitti")

    @unittest.skipUnless(os.name == "posix", "signals")
    def test_signal_is_forwarded_and_exit_code_passed_through(self):
        for flag in ("--pipe", "--pty"):
            with self.subTest(flag):
                p = self.wrapper(flag, extra_env={"WAIT_TERM": "1", "TERM_RC": "42"}, wait=False)
                buf = b""
                deadline = time.time() + 15
                while b"ready" not in buf and time.time() < deadline:
                    buf += os.read(p.stdout.fileno(), 4096)
                p.send_signal(signal.SIGTERM)
                p.communicate(timeout=15)
                self.assertEqual(p.returncode, 42)

    def mute_fds(self):
        """The wrapper relays to fd 1/2; keep the test output clean."""
        saved = [os.dup(1), os.dup(2)]
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.dup2(null, 2)
        os.close(null)

        def restore():
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            for fd in saved:
                os.close(fd)
        self.addCleanup(restore)

    def test_run_command_in_process_states(self):
        self.mute_fds()
        fake = FakeAgent()
        rc = pa.run_command([sys.executable, "-c", "import time,sys; print('a',flush=True); time.sleep(0.9); "
                                                   "print('b',flush=True); sys.exit(5)"],
                            fake, idle_after=0.4, mode="pipe", poll=0.05)
        self.assertEqual(rc, 5)
        self.assertEqual(fake.states[:3], ["working", "idle", "working"])
        self.assertEqual(fake.reports[-1], ("sleeping", None, "Hata: kod 5", True))
        fake2 = FakeAgent()
        rc = pa.run_command([sys.executable, "-c", "pass"], fake2, on_exit="offline", mode="pipe")
        self.assertEqual((rc, fake2.states[-1]), (0, "offline"))
        fake3 = FakeAgent()
        self.assertEqual(pa.run_command(["/nonexistent/cmd-xyz"], fake3, mode="pipe"), 127)
        self.assertEqual(fake3.reports[-1][2], "Hata: kod 127")


# ---------------------------------------------------------------------------------------------------
TOOL_SSE = [
    b'data: {"id":"c1","choices":[{"index":0,"delta":{"role":"assistant","content":null}}]}\n\n',
    b'data: {"id":"c1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function",',
    b'"function":{"name":"get_weather","arguments":""}}]}}]}\n\n',
    b'data: {"id":"c1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"city\\": \\"CANARY-7f3a\\"}"}}]}}]}\n\n',
    b'data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n',
    b"data: [DONE]\n\n",
]
TEXT_SSE = [
    b'data: {"choices":[{"index":0,"delta":{"content":"Mer"}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"content":"haba CANARY-7f3a"}}]}\n\n',
    b"data: [DONE]\n\n",
]
OLLAMA_TOOL = [
    b'{"model":"qwen","message":{"role":"assistant","content":""},"done":false}\n',
    b'{"model":"qwen","message":{"role":"assistant","content":"","tool_calls":[{"function":{"name":"run_shell",',
    b'"arguments":{"cmd":"CANARY-7f3a"}}}]},"done":false}\n',
    b'{"model":"qwen","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop"}\n',
]
OLLAMA_TEXT = [b'{"message":{"role":"assistant","content":"hi CANARY-7f3a"},"done":false}\n',
               b'{"message":{"role":"assistant","content":""},"done":true}\n']
NONSTREAM_TOOL = json.dumps({"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
    "tool_calls": [{"id": "c", "type": "function", "function": {"name": "search_docs",
                                                                 "arguments": "{\"q\": \"CANARY-7f3a\"}"}}]},
    "finish_reason": "tool_calls"}]}, indent=2).encode()
NONSTREAM_TEXT = json.dumps({"choices": [{"message": {"role": "assistant", "content": "CANARY-7f3a"}}]}).encode()
RESPONSES_TOOL = json.dumps({"output": [{"type": "function_call", "name": "lookup", "arguments": "{}"}]}).encode()


class FakeUpstream:
    def __init__(self):
        self.seen = []
        self.gate = threading.Event()
        up = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def chunks(self, parts, ctype, gated=False):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("X-Upstream", "yes")
                self.end_headers()
                for i, p in enumerate(parts):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(p), p))
                    self.wfile.flush()
                    if gated and i == 0:
                        up.gate.wait(10)        # the proxy must deliver chunk 0 before we continue
                    time.sleep(0.01)
                self.wfile.write(b"0\r\n\r\n")

            def fixed(self, body, status=200, ctype="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                up.seen.append(("GET", self.path, dict(self.headers), b""))
                self.fixed(b'{"models":[{"name":"qwen"}]}')

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                up.seen.append(("POST", self.path, dict(self.headers), body))
                req = json.loads(body or b"{}")
                path = self.path
                if path.endswith("/fail"):
                    return self.fixed(b'{"error":"boom"}', 500)
                if path in ("/v1/chat/completions", "/chat/completions"):
                    if req.get("stream"):
                        return self.chunks(TOOL_SSE if req.get("tools") else TEXT_SSE, "text/event-stream",
                                           gated=req.get("gated", False))
                    if req.get("error"):
                        return self.fixed(b'{"error":{"message":"bad"}}', 400)
                    return self.fixed(NONSTREAM_TOOL if req.get("tools") else NONSTREAM_TEXT)
                if path == "/api/chat":
                    if req.get("stream", True):
                        return self.chunks(OLLAMA_TOOL if req.get("tools") else OLLAMA_TEXT, "application/x-ndjson")
                    return self.fixed(b'{"message":{"tool_calls":[{"function":{"name":"x_y"}}]},"done":true}')
                if path == "/v1/responses":
                    return self.fixed(RESPONSES_TOOL)
                return self.fixed(b"{}")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": POLL}, daemon=True).start()
        self.port = self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def raw_request(port, method, path, body=None, headers=None):
    """Returns (status, headers dict, raw decoded body bytes)."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    hd = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hd, out


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.up = FakeUpstream()
        self.addCleanup(self.up.close)
        self.agents = {}

        def factory(name):
            a = self.agents[name] = FakeAgent(name)
            return a
        self.proxy = pa.make_proxy("127.0.0.1", 0, "http://127.0.0.1:%d" % self.up.port, factory, "local-llm",
                                   send_model=False)
        threading.Thread(target=self.proxy.serve_forever, kwargs={"poll_interval": POLL}, daemon=True).start()
        self.addCleanup(self.proxy.server_close)
        self.addCleanup(self.proxy.shutdown)
        self.pport = self.proxy.server_address[1]

    def both(self, method, path, body=None, headers=None, via_path=None):
        direct = raw_request(self.up.port, method, path, body)
        proxied = raw_request(self.pport, method, via_path or path, body, headers)
        self.settle()
        return direct, proxied

    def settle(self):
        deadline = time.time() + 3
        while time.time() < deadline and any(self.proxy.tracker.inflight.values()):
            time.sleep(0.005)

    def assert_same(self, direct, proxied):
        self.assertEqual(direct[0], proxied[0])
        self.assertEqual(direct[2], proxied[2])                 # byte-identical body
        self.assertEqual(direct[1].get("content-type"), proxied[1].get("content-type"))

    def test_nonstream_with_tool_calls(self):
        d, p = self.both("POST", "/v1/chat/completions", {"model": "m", "tools": [{}], "messages": []})
        self.assert_same(d, p)
        self.assertEqual(self.agents["local-llm"].reports,
                         [("thinking", "LLM", None, False), ("working", "search_docs", None, False)])

    def test_nonstream_text_goes_idle(self):
        d, p = self.both("POST", "/v1/chat/completions", {"model": "m", "messages": []})
        self.assert_same(d, p)
        self.assertEqual(self.agents["local-llm"].states, ["thinking", "idle"])

    def test_openai_sse_stream_tool_calls(self):
        d, p = self.both("POST", "/v1/chat/completions", {"stream": True, "tools": [{}]})
        self.assert_same(d, p)
        self.assertEqual(p[2], b"".join(TOOL_SSE))
        self.assertEqual(p[1].get("transfer-encoding"), "chunked")
        self.assertEqual(p[1].get("x-upstream"), "yes")
        self.assertEqual(self.agents["local-llm"].reports[-1], ("working", "get_weather", None, False))

    def test_openai_sse_text(self):
        d, p = self.both("POST", "/v1/chat/completions", {"stream": True})
        self.assert_same(d, p)
        self.assertEqual(self.agents["local-llm"].states, ["thinking", "idle"])

    def test_ollama_ndjson_stream_tool_calls(self):
        d, p = self.both("POST", "/api/chat", {"model": "qwen", "tools": [{}]})
        self.assert_same(d, p)
        self.assertEqual(p[2], b"".join(OLLAMA_TOOL))
        self.assertEqual(self.agents["local-llm"].reports[-1][:2], ("working", "run_shell"))
        d, p = self.both("POST", "/api/chat", {"model": "qwen"})
        self.assert_same(d, p)
        self.assertEqual(self.agents["local-llm"].states[-1], "idle")

    def test_ollama_nonstream_and_responses_api(self):
        self.both("POST", "/api/chat", {"stream": False})
        self.assertEqual(self.agents["local-llm"].reports[-1][:2], ("working", "x_y"))
        self.both("POST", "/v1/responses", {})
        self.assertEqual(self.agents["local-llm"].reports[-1][:2], ("working", "lookup"))

    def test_streaming_is_relayed_as_it_arrives(self):
        c = http.client.HTTPConnection("127.0.0.1", self.pport, timeout=10)
        c.request("POST", "/v1/chat/completions", body=json.dumps({"stream": True, "gated": True}),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        first = r.read1(65536)                      # arrives while upstream is still blocked
        self.assertEqual(first, TEXT_SSE[0])
        self.assertEqual(self.agents["local-llm"].states, ["thinking"])
        self.up.gate.set()
        rest = r.read()
        self.assertEqual(first + rest, b"".join(TEXT_SSE))
        c.close()

    def test_client_disconnect_mid_stream(self):
        """Phase 10: the client hangs up while the upstream is still streaming → the agent ends idle (not
        stuck on "thinking") and the proxy keeps serving."""
        c = http.client.HTTPConnection("127.0.0.1", self.pport, timeout=10)
        c.request("POST", "/v1/chat/completions", body=json.dumps({"stream": True, "gated": True}),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        self.assertEqual(r.read1(65536), TEXT_SSE[0])
        c.sock.shutdown(socket.SHUT_RDWR)
        c.close()
        self.up.gate.set()
        deadline = time.time() + 5
        while time.time() < deadline and self.agents["local-llm"].states[-1] != "idle":
            time.sleep(0.01)
        self.assertEqual(self.agents["local-llm"].states[-1], "idle")
        d, p = self.both("POST", "/v1/chat/completions", {"model": "m", "messages": []})
        self.assert_same(d, p)

    def test_identity_header_prefix_default(self):
        self.both("POST", "/v1/chat/completions", {}, headers={"X-Pixel-Agent": "aider"})
        self.assertIn("aider", self.agents)
        self.assertNotIn("x-pixel-agent", {k.lower() for k in self.up.seen[-1][2]})
        self.both("POST", "/v1/chat/completions", {}, via_path="/agent/interp/v1/chat/completions")
        self.assertEqual(self.up.seen[-1][1], "/v1/chat/completions")    # prefix stripped
        self.assertIn("interp", self.agents)
        self.both("POST", "/v1/chat/completions", {}, headers={"X-Pixel-Agent": "bad/name<>"})
        self.assertEqual(self.agents["local-llm"].states[-1], "idle")    # invalid → --name

    def test_non_generation_paths_do_not_touch_state(self):
        d, p = self.both("GET", "/api/tags?x=1")
        self.assert_same(d, p)
        self.assertEqual(self.up.seen[-1][1], "/api/tags?x=1")
        self.both("POST", "/v1/embeddings", {"input": "x"})
        self.assertEqual(self.agents, {})

    def test_upstream_error_status(self):
        d, p = self.both("POST", "/v1/chat/completions", {"error": True})
        self.assert_same(d, p)
        self.assertEqual(self.agents["local-llm"].reports[-1], ("idle", None, "LLM hatası", False))

    def test_headers_hop_by_hop_and_auth(self):
        raw_request(self.pport, "POST", "/v1/chat/completions", {},
                    {"Authorization": "Bearer sk-" + CANARY, "Connection": "keep-alive, X-Drop", "X-Drop": "1",
                     "Keep-Alive": "timeout=5", "X-Keep": "1"})
        h = {k.lower(): v for k, v in self.up.seen[-1][2].items()}
        self.assertEqual(h["authorization"], "Bearer sk-" + CANARY)      # passed through, never reported
        self.assertEqual(h["x-keep"], "1")
        self.assertNotIn("x-drop", h)
        self.assertNotIn("keep-alive", h)
        self.assertEqual(h["host"], "127.0.0.1:%d" % self.up.port)

    def test_upstream_unreachable(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
        s.close()
        px = pa.make_proxy("127.0.0.1", 0, "http://127.0.0.1:%d" % dead, lambda n: self.agents.setdefault(n, FakeAgent(n)))
        threading.Thread(target=px.serve_forever, kwargs={"poll_interval": POLL}, daemon=True).start()
        self.addCleanup(px.server_close)
        self.addCleanup(px.shutdown)
        st, _, body = raw_request(px.server_address[1], "POST", "/v1/chat/completions", {})
        deadline = time.time() + 5
        while time.time() < deadline and not (self.agents.get("local-llm") and self.agents["local-llm"].reports
                                              and self.agents["local-llm"].reports[-1][2] == "LLM hatası"):
            time.sleep(0.005)
        self.assertEqual((st, json.loads(body)), (502, {"error": "upstream_unreachable"}))
        self.assertEqual(self.agents["local-llm"].reports[-1][2], "LLM hatası")

    def test_send_model_and_privacy(self):
        self.proxy.send_model = True
        self.both("POST", "/v1/chat/completions", {"model": "qwen2.5-coder:7b", "stream": True, "tools": [{}],
                                                     "messages": [{"role": "user", "content": CANARY}]})
        a = self.agents["local-llm"]
        self.assertEqual(a.reports[0], ("thinking", "LLM", "qwen2.5-coder:7b", False))
        self.assertNotIn(CANARY, json.dumps(a.reports))
        self.proxy.send_model = False
        self.both("POST", "/v1/chat/completions", {"model": "secret-model"})
        self.assertNotIn("secret-model", json.dumps(a.reports))

    def test_concurrent_requests_stay_thinking(self):
        tr = pa.StateTracker(lambda n: self.agents.setdefault(n, FakeAgent(n)))
        tr.start("a")
        tr.start("a")
        tr.finish("a", has_tool=True, tool="x")
        self.assertEqual(self.agents["a"].states, ["thinking", "thinking"])
        tr.finish("a")
        self.assertEqual(self.agents["a"].states[-1], "idle")

    def test_scanner_limits(self):
        sc = pa.ToolScanner("text/event-stream")
        sc.feed(b"data: " + b"x" * (pa.SCAN_LINE_MAX + 10))
        sc.feed(b'\ndata: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"ok"}}]}}]}\n')
        self.assertEqual(sc.close(), (True, "ok"))
        import gzip
        sc = pa.ToolScanner("application/json", "gzip")
        z = gzip.compress(NONSTREAM_TOOL)
        sc.feed(z[:10])
        sc.feed(z[10:])
        self.assertEqual(sc.close(), (True, "search_docs"))


if __name__ == "__main__":
    unittest.main()
