"""Phase 20 / Package 2 (v2.md): connector robustness. One class per item.

E1 version 3.1.0 + build id (+ update check), H4 run.lock wait / pair refusal / retry window, H5 single I/O thread for
the relay socket, H6/E5 relay backoff and logged reasons, H9 claude path refresh + service PATH, H11 clean shutdown,
H24 unit text, H25 new_session only for Claude, H26 local delete clears history, H27 audit order, H28 no traceback for
hang-ups, H29 doctor/pair error handling, H32 engine writes through the agent store.
No network: fake sockets, fake openers, temp homes.
"""

import io
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

import pixel_connector
from pixel_connector import __main__ as CLI
from pixel_connector import config as CFG
from pixel_connector import relay as R
from pixel_connector import service as S
from pixel_connector import ws as W
from pixel_connector.agents import AgentStore
from pixel_connector.app import Connector
from pixel_connector.approvals import Approvals, ControlServer
from pixel_connector.engines.base import empty_usage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_connector  # noqa: E402

ROOM = "abcdefghijklmnopqrstuvwxyz"
KEY = "k" * 40
PYZ = ROOT / "web" / "connector" / "pixel-connector.pyz"


# ------------------------------------------------------------------------------------------------------------------
# fakes
# ------------------------------------------------------------------------------------------------------------------

class FakeRelay:
    def __init__(self, **kw):
        self.status = "live"
        self.connected = threading.Event()
        self.connected.set()
        self.last_error = "ticket:401"
        self.last_error_at = 1700000000.5
        self.sent = []

    def start(self):
        pass

    def stop(self):
        self.status = "stopped"

    def send(self, frame):
        self.sent.append(frame)
        return True


class FakeSender:
    def submit(self, u):
        pass

    def flush(self, timeout):
        pass

    def stop(self):
        pass


class FakeRun:
    def __init__(self, events, end_on_cancel=True, end_delay=0.0):
        self.events = events
        self.end_on_cancel = end_on_cancel
        self.end_delay = end_delay
        self.cancelled = False
        self.killed = False

    def cancel(self):
        self.cancelled = True
        if self.end_on_cancel:
            t = threading.Timer(self.end_delay, lambda: self.events.end("cancelled", None, empty_usage(), None,
                                                                        "unknown"))
            t.daemon = True
            t.start()

    def kill(self):
        self.killed = True


class FakeEngine:
    def __init__(self, end_on_cancel=True, end_delay=0.0):
        self.runs = []
        self.ctxs = []
        self.end_on_cancel, self.end_delay = end_on_cancel, end_delay

    def start(self, ctx, events):
        self.ctxs.append(ctx)
        r = FakeRun(events, self.end_on_cancel, self.end_delay)
        self.runs.append(r)
        return r

    def available(self, force=False):
        return {"available": True, "version": "1.0.0", "auth": "subscription", "models": []}


def fake_peer(bid="b1"):
    return SimpleNamespace(bid=bid, label="Telefon", conn="c1", ch="x", watching=set(),
                           channel=SimpleNamespace(closed=True))


def write_exe(path, text="#!/bin/sh\nexit 0\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o755)
    return path


class ConnectorCase(unittest.TestCase):
    """A real Connector with fake relay, fake office sender and fake engines (no network)."""
    confirm_task = "never"
    start_ctl = False
    stop_wait_s = 2.0
    engine_kw = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = []
        self.conn = None

    def make(self, port=0, start=True):
        t = self.tmp.name
        home = os.path.join(t, "connector")
        CFG.ensure_dir(home)
        ws = os.path.join(t, "proj")
        os.makedirs(ws, exist_ok=True)
        claude = write_exe(os.path.join(t, "bin", "claude"))
        cfg = CFG.initial_config("https://pixel.example.app", ROOM, KEY, "Timur", "Test Cihazı", claude)
        cfg["workspaces"] = [{"id": "pixel", "name": "Pixel", "path": ws, "children": False}]
        cfg["approvalPort"] = port
        cfg["confirm"] = dict(cfg["confirm"], task=self.confirm_task)
        CFG.write_json(os.path.join(home, CFG.CONFIG_FILE), cfg)
        c = CFG.load_config(home, user_home=t)
        self.assertTrue(c.ok, c.errors)
        self.home = home
        self.claude_eng = FakeEngine(**self.engine_kw)
        self.ollama_eng = FakeEngine(**self.engine_kw)
        self.conn = Connector(home, c, start_ctl=self.start_ctl, user_home=t, relay_factory=FakeRelay,
                              office_sender=FakeSender(), log=self.logs.append, stop_wait_s=self.stop_wait_s,
                              engines={"claude": self.claude_eng, "ollama": self.ollama_eng, "openai": {}})
        self.conn.approvals.notify_fn = lambda name: None
        if start:
            self.conn.start()
            self.addCleanup(self._stop)
        return self.conn

    def _stop(self):
        if self.conn is not None and not self.conn._stop.is_set():
            self.conn.stop()

    def agent(self, engine="ollama"):
        return self.conn.agents.create("Ajan", engine, None if engine == "claude" else "qwen2.5-coder:7b",
                                       {"id": "pixel", "sub": None}, "edit", None)

    def start_task(self, a, prompt="merhaba", fresh=False):
        return self.conn.h_task_start(fake_peer(), {"type": "task.start", "agent": a["key"], "prompt": prompt,
                                                    "fresh": fresh})

    def audit(self):
        return self.conn.audit.read()


# ------------------------------------------------------------------------------------------------------------------
# E1 — version 3.1.0, build id, update check
# ------------------------------------------------------------------------------------------------------------------

class VersionAndBuild(unittest.TestCase):
    def test_version_and_dev_build_from_source(self):
        self.assertEqual(pixel_connector.__version__, "3.13.0")
        self.assertEqual(pixel_connector.__build__, "dev")
        self.assertEqual(pixel_connector.version_string(), "3.13.0 (build dev)")
        self.assertFalse((ROOT / "pixel_connector" / "_build.py").exists(), "_build.py is generated, never committed")

    def test_cli_version_flag(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            CLI.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), "pixel-connector 3.13.0 (build dev)")

    def test_build_id_is_deterministic_and_tracks_content(self):
        files = build_connector.sources()
        bid = build_connector.build_id(files)
        self.assertRegex(bid, r"^[0-9a-f]{12}$")
        self.assertEqual(bid, build_connector.build_id())
        changed = [(n, d + b"\n" if n == "pixel_connector/relay.py" else d) for n, d in files]
        self.assertNotEqual(build_connector.build_id(changed), bid)
        renamed = [("x/" + n if n == "pixel_agent.py" else n, d) for n, d in files]
        self.assertNotEqual(build_connector.build_id(renamed), bid)
        self.assertNotIn(build_connector.BUILD_MODULE, [n for n, _ in files])

    def test_pyz_embeds_build_id_and_reports_it(self):
        bid = build_connector.build_id()
        with zipfile.ZipFile(PYZ) as z:
            mod = z.read(build_connector.BUILD_MODULE).decode()
        self.assertIn('BUILD = "%s"' % bid, mod)
        r = subprocess.run([sys.executable, str(PYZ), "--version"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "pixel-connector 3.13.0 (build %s)" % bid)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import pixel_connector.app as a; "
                "print(a.running_pyz_sha256())")
        r = subprocess.run([sys.executable, "-I", "-c", code, str(PYZ)], capture_output=True, text=True, timeout=60)
        import hashlib
        self.assertEqual(r.stdout.strip(), hashlib.sha256(PYZ.read_bytes()).hexdigest(), r.stderr)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class UpdateCheck(unittest.TestCase):
    OWN = "a" * 64
    OTHER = "b" * 64

    def opener(self, text=None, exc=None):
        calls = []

        def fn(url, timeout):
            calls.append((url, timeout))
            if exc is not None:
                raise exc
            return FakeResponse(text.encode())
        fn.calls = calls
        return fn

    def sums(self, pyz_sha):
        return "%s  install.ps1\n%s  install.sh\n%s  pixel-connector.pyz\n" % ("1" * 64, "2" * 64, pyz_sha)

    def test_same_build(self):
        op = self.opener(self.sums(self.OWN))
        line = CLI.update_line("https://pixel.example.app/", self.OWN, op)
        self.assertIn("✓", line)
        self.assertEqual(op.calls, [("https://pixel.example.app/connector/SHA256SUMS", 10.0)])

    def test_newer_build_published(self):
        line = CLI.update_line("https://pixel.example.app", self.OWN, self.opener(self.sums(self.OTHER)))
        self.assertTrue(line.startswith("Güncelleme var: pixel-connector update komutunu çalıştırın"), line)
        self.assertIn(self.OTHER[:12], line)

    def test_failures_are_not_fatal(self):
        for op in (self.opener(exc=OSError("offline")), self.opener("garbage\n"),
                   self.opener("%s  pixel-connector.pyz\n" % ("Z" * 64))):
            with self.subTest():
                line = CLI.update_line("https://pixel.example.app", self.OWN, op)
                self.assertTrue(line.startswith("Güncelleme: kontrol edilemedi"), line)

    def test_source_checkout_does_not_fetch(self):
        op = self.opener(exc=AssertionError("must not fetch"))
        self.assertIn("kaynak koddan", CLI.update_line("https://pixel.example.app", None, op))
        self.assertEqual(op.calls, [])


class VersionOnTheWire(ConnectorCase):
    start_ctl = True

    def test_device_info_and_ctl_status(self):
        conn = self.make()
        info = conn.device_info()
        self.assertEqual((info["connector"], info["build"], info["pyz"]), ("3.13.0", "dev", None))
        st, d = conn.ctl_routes()[("GET", "/ctl/status")]({}, {})
        self.assertEqual(st, 200)
        self.assertEqual((d["version"], d["build"], d["relayError"], d["relayErrorAt"]),
                         ("3.13.0", "dev", "ticket:401", 1700000000500))
        line = CLI.running_status_line(self.home)                        # through the real control server
        self.assertIn("3.13.0 (build dev)", line)
        self.assertIn("Son röle hatası: ticket:401", line)


# ------------------------------------------------------------------------------------------------------------------
# H4 — run.lock wait, pair refusal while the service runs, retry window
# ------------------------------------------------------------------------------------------------------------------

class FakeLock:
    def __init__(self, results):
        self.results = list(results)

    def acquire(self):
        return self.results.pop(0) if self.results else False


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, d):
        self.sleeps.append(d)
        self.t += d


class RunLockWait(unittest.TestCase):
    def test_acquires_after_retries_logging_once(self):
        clk, logs = FakeClock(), []
        ok = CLI.wait_for_lock(FakeLock([False, False, False, True]), total=150, sleep=clk.sleep, now=clk.now,
                               log=logs.append)
        self.assertTrue(ok)
        self.assertEqual(clk.sleeps, [0.5, 1, 2])
        self.assertEqual(len(logs), 2)
        self.assertIn("run.lock", logs[0])

    def test_gives_up_after_total(self):
        clk = FakeClock()
        self.assertFalse(CLI.wait_for_lock(FakeLock([]), total=30, sleep=clk.sleep, now=clk.now))
        self.assertAlmostEqual(sum(clk.sleeps), 30)
        self.assertLessEqual(max(clk.sleeps), 10)

    def test_free_lock_no_wait(self):
        clk, logs = FakeClock(), []
        self.assertTrue(CLI.wait_for_lock(FakeLock([True]), sleep=clk.sleep, now=clk.now, log=logs.append))
        self.assertEqual((clk.sleeps, logs), ([], []))

    def test_real_lock_released_by_other_holder(self):
        with tempfile.TemporaryDirectory() as h:
            path = os.path.join(h, "run.lock")
            held = CLI.InstanceLock(path)
            self.assertTrue(held.acquire())
            threading.Timer(0.3, held.release).start()
            mine = CLI.InstanceLock(path)
            self.assertFalse(mine.acquire())
            self.assertTrue(CLI.wait_for_lock(mine, total=5))
            mine.release()

    def test_cmd_run_exits_when_lock_stays_taken(self):
        with tempfile.TemporaryDirectory() as h:
            home = os.path.join(h, "connector")
            CFG.ensure_dir(home)
            CFG.write_json(os.path.join(home, CFG.CONFIG_FILE),
                           CFG.initial_config("https://pixel.example.app", ROOM, KEY, "Timur", "PC", None))
            held = CLI.InstanceLock(os.path.join(home, CFG.LOCK_FILE))
            self.assertTrue(held.acquire())
            err = io.StringIO()
            try:
                with mock.patch.object(CLI, "LOCK_WAIT_S", 0.3), redirect_stderr(err):
                    rc = CLI.main(["--home", home, "run"])
            finally:
                held.release()
            self.assertEqual(rc, 1)
            self.assertIn("run.lock", err.getvalue())


class PairWhileServiceStarts(unittest.TestCase):
    def test_inprocess_refused_while_service_active_or_starting(self):
        with tempfile.TemporaryDirectory() as h:
            for state in ("active", "activating"):
                err = io.StringIO()
                with redirect_stderr(err):
                    rc = CLI._pair_inprocess(h, lambda q: True, service_status=lambda: state)
                self.assertEqual(rc, 1)
                self.assertIn("çalışıyor ya da başlıyor (%s)" % state, err.getvalue())
                lock = CLI.InstanceLock(os.path.join(h, CFG.LOCK_FILE))
                self.assertTrue(lock.acquire(), "the refused pair must not keep the run lock")
                lock.release()

    def test_inprocess_allowed_when_service_inactive(self):
        with tempfile.TemporaryDirectory() as h:
            err = io.StringIO()
            with redirect_stderr(err):
                rc = CLI._pair_inprocess(h, lambda q: True, service_status=lambda: "inactive")
            self.assertEqual(rc, 2)                      # got past the check: no connector.json in this home
            self.assertNotIn("başlıyor", err.getvalue())

    def test_retry_window_covers_restart_sec(self):
        unit = S.systemd_unit("/usr/bin/python3", "%h/x.pyz")
        restart = float([ln.split("=", 1)[1] for ln in unit.splitlines() if ln.startswith("RestartSec=")][0])
        window = (CLI.PAIR_CTL_ATTEMPTS - 1) * CLI.PAIR_CTL_SLEEP_S
        self.assertGreaterEqual(window, 8.0)
        self.assertGreater(window, restart + 2)


# ------------------------------------------------------------------------------------------------------------------
# H5 — one I/O thread: writes are queued, never under the 1 s read timeout, never concurrent with reads
# ------------------------------------------------------------------------------------------------------------------

class SpySock:
    """Client end of a socketpair that records, for every write, the socket timeout in effect and the calling thread,
    flags any overlap of two socket operations, can make large writes slow and can fail writes after the first."""

    def __init__(self, sock, slow_s=0.0, fail_after=None):
        self._s = sock
        self.slow_s, self.fail_after = slow_s, fail_after
        self.writes = []
        self.threads = set()
        self.overlap = False
        self._active = 0
        self._lock = threading.Lock()

    def _enter(self):
        with self._lock:
            self._active += 1
            if self._active > 1:
                self.overlap = True
            self.threads.add(threading.get_ident())

    def _exit(self):
        with self._lock:
            self._active -= 1

    def sendall(self, data):
        self._enter()
        try:
            self.writes.append((self._s.gettimeout(), len(data)))
            if self.fail_after is not None and len(self.writes) > self.fail_after:
                raise BrokenPipeError(32, "Broken pipe")
            if self.slow_s and len(data) > 4096:
                time.sleep(self.slow_s)
            self._s.sendall(data)
        finally:
            self._exit()

    def recv(self, n):
        self._enter()
        try:
            return self._s.recv(n)
        finally:
            self._exit()

    def __getattr__(self, name):
        return getattr(self._s, name)


class RelayServerSide(threading.Thread):
    """Answers hello with welcome, then collects every text message the client sends."""

    def __init__(self, sock):
        super().__init__(daemon=True)
        self.ws = W.WebSocket(sock, client=False, max_message=1 << 20)
        self.messages = []
        self.got = threading.Event()

    def run(self):
        try:
            hello = json.loads(self.ws.recv(timeout=5))
            assert hello["t"] == "hello"
            self.ws.send_text(json.dumps({"t": "welcome", "v": 1, "serverTime": 1}))
            while True:
                m = self.ws.recv(timeout=10)
                if isinstance(m, str):
                    self.messages.append(json.loads(m))
                    self.got.set()
        except (W.ConnectionClosed, OSError, ValueError):
            pass
        finally:
            self.ws._gone()


class RelayWriter(unittest.TestCase):
    def client(self, spy, logs):
        socks = [spy]

        def connect(url, **kw):
            if socks:
                return W.WebSocket(socks.pop(), client=True, max_message=200 * 1024)
            raise ConnectionRefusedError(111, "Connection refused")

        def ticket():
            if not socks:
                raise R.TicketError(404)               # one connection per test, then a long wait (stop() ends it)
            return {"url": "wss://relay.example/v1/rooms/%s/ws" % ROOM, "ticket": "t"}
        return R.RelayClient("https://pixel.example.app", ROOM, KEY, "dev1", on_frame=lambda f: None,
                             ticket_fn=ticket, connect_fn=connect, log=logs.append)

    def test_slow_write_is_queued_and_written_with_its_own_deadline(self):
        a, b = socket.socketpair()
        spy = SpySock(a, slow_s=1.5)
        srv = RelayServerSide(b)
        srv.start()
        logs = []
        c = self.client(spy, logs)
        c.start()
        try:
            self.assertTrue(c.connected.wait(5), logs)
            big = {"t": "send", "to": "x", "b": "y" * 60000}
            t0 = time.monotonic()
            self.assertTrue(c.send(big))
            self.assertTrue(c.send({"t": "send", "to": "x", "b": "small"}))
            self.assertLess(time.monotonic() - t0, 0.5, "send() must not wait for the socket")
            deadline = time.monotonic() + 8
            while len(srv.messages) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(srv.messages, [big, {"t": "send", "to": "x", "b": "small"}])
            big_writes = [w for w in spy.writes if w[1] > 4096]
            self.assertEqual([w[0] for w in big_writes], [R.WRITE_TIMEOUT_S])     # not the 1 s read slice
            self.assertNotIn(threading.get_ident(), spy.threads)
            self.assertEqual(len(spy.threads), 1, "one I/O thread touches the socket")
            self.assertFalse(spy.overlap, "a read and a write ran at the same time")
            self.assertEqual(c.lost_frames, 0)
        finally:
            c.stop()

    def test_lost_frames_are_counted_and_logged(self):
        a, b = socket.socketpair()
        spy = SpySock(a, fail_after=1)                  # hello goes out, every later write fails
        srv = RelayServerSide(b)
        srv.start()
        logs = []
        c = self.client(spy, logs)
        c.start()
        try:
            self.assertTrue(c.connected.wait(5), logs)
            accepted = sum(1 for i in range(3) if c.send({"t": "send", "to": "x", "b": str(i)}))
            deadline = time.monotonic() + 5
            while c.lost_frames < accepted and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(accepted, 1)
            self.assertEqual(c.lost_frames, accepted)
            self.assertTrue(any("mesaj gönderilemedi" in ln for ln in logs), logs)
            self.assertTrue(c.last_error.startswith("write: BrokenPipeError"), c.last_error)
            self.assertFalse(c.send({"t": "send"}), "no queueing while disconnected")
        finally:
            c.stop()

    def test_full_outbox_refuses_and_counts(self):
        logs = []
        c = R.RelayClient("https://pixel.example.app", ROOM, KEY, "dev1", on_frame=lambda f: None, log=logs.append)
        c.ws = SimpleNamespace(closed=False)
        c.connected.set()
        with mock.patch.object(R, "MAX_OUTBOX_BYTES", 100):
            self.assertTrue(c.send({"b": "x" * 40}))
            self.assertFalse(c.send({"b": "x" * 80}))
        self.assertEqual((c.lost_frames, c.queued()), (1, 1))
        self.assertIn("kuyruğu dolu", logs[-1])
        c.stop()


class WsHelpers(unittest.TestCase):
    def test_pending_only_for_a_whole_buffered_frame(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        frame = bytes([0x81, 5]) + b"hello"
        self.assertTrue(W.WebSocket(a, client=True, buffered=frame).pending())
        self.assertFalse(W.WebSocket(a, client=True, buffered=frame[:4]).pending())
        self.assertFalse(W.WebSocket(a, client=True, buffered=bytes([0x81, 126, 0])).pending())
        long_head = bytes([0x81, 126]) + (300).to_bytes(2, "big")
        self.assertFalse(W.WebSocket(a, client=True, buffered=long_head + b"x" * 299).pending())
        self.assertTrue(W.WebSocket(a, client=True, buffered=long_head + b"x" * 300).pending())
        self.assertFalse(W.WebSocket(a, client=True).pending())

    def test_write_deadline_drops_a_stuck_socket(self):
        a, b = socket.socketpair()
        self.addCleanup(b.close)
        ws = W.WebSocket(a, client=True)
        t0 = time.monotonic()
        with self.assertRaises(socket.timeout):
            ws.send_text("x" * (32 * 1024 * 1024), timeout=0.2)        # nobody reads b
        self.assertLess(time.monotonic() - t0, 5)
        self.assertTrue(ws.closed, "a half-written frame leaves the stream unusable")


# ------------------------------------------------------------------------------------------------------------------
# H6 / E5 — backoff and reasons
# ------------------------------------------------------------------------------------------------------------------

class FakeStop:
    def __init__(self, n):
        self.waits = []
        self.n = n
        self.flag = False

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def wait(self, d=None):
        self.waits.append(d)
        if len(self.waits) >= self.n:
            self.flag = True
        return self.flag


class DummyWS:
    closed = False

    def close(self, *a):
        self.closed = True

    def _gone(self):
        pass


class RelayBackoff(unittest.TestCase):
    def run_client(self, errors, n, session=None, rand=lambda a, b: 0.0):
        seq = list(errors)
        logs, statuses = [], []

        def ticket():
            e = seq.pop(0) if seq else errors[-1]
            if e is None:
                return {"url": "wss://relay.example/v1/rooms/%s/ws" % ROOM, "ticket": "t"}
            raise e
        c = R.RelayClient("https://pixel.example.app", ROOM, KEY, "dev1", on_frame=lambda f: None,
                          on_status=statuses.append, ticket_fn=ticket, connect_fn=lambda url, **kw: DummyWS(),
                          log=logs.append, rand=rand)
        if session is not None:
            c._session = session
        c._stop = FakeStop(n)
        c._run()
        c.stop()
        return c, c._stop.waits, logs, statuses

    def test_network_errors_cap_at_60s(self):
        c, waits, logs, _ = self.run_client([OSError(101, "Network is unreachable")], 9)
        self.assertEqual(waits, [1, 2, 4, 8, 16, 32, 60, 60, 60])
        self.assertEqual(c.last_error, "OSError: [Errno 101] Network is unreachable")
        self.assertIsNotNone(c.last_error_at)

    def test_jitter_stays_within_bounds(self):
        import random
        _, waits, _, _ = self.run_client([OSError("x")], 12, rand=random.uniform)
        self.assertLessEqual(max(waits), 60 * 1.2)
        self.assertGreaterEqual(min(waits), 0.8)

    def test_long_wait_only_for_ticket_auth_and_not_found(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                _, waits, _, statuses = self.run_client([R.TicketError(status)], 3)
                self.assertEqual(waits, [300.0] * 3)
                self.assertIn("error:ticket:%d" % status, statuses)
        for err in (R.TicketError(500), R.TicketError(400), R.TicketError(429), R.TicketError(0, "bad ticket answer"),
                    W.HandshakeError(503, "", {}, b"")):
            with self.subTest(err=err):
                _, waits, _, _ = self.run_client([err], 3)
                self.assertEqual(waits, [1, 2, 4])

    def test_long_wait_is_not_sticky(self):
        _, waits, _, _ = self.run_client([R.TicketError(401), OSError("dns"), OSError("dns")], 3)
        self.assertEqual(waits, [300.0, 2, 4])

    def test_stable_connection_resets_backoff(self):
        _, waits, _, _ = self.run_client([None], 4, session=lambda ws: R.STABLE_S + 1)
        self.assertEqual(waits, [1, 1, 1, 1])
        _, waits, _, _ = self.run_client([None], 4, session=lambda ws: 5.0)       # live but flapping
        self.assertEqual(waits, [1, 2, 4, 8])

    def test_reason_logged_once_per_change_without_secrets(self):
        _, _, logs, _ = self.run_client([R.TicketError(401)], 3)
        self.assertEqual(logs[0], "Röle: connecting")
        self.assertEqual(logs[1], "Röle: reconnecting (sebep: ticket:401; 300 sn sonra yeniden denenecek)")
        self.assertEqual(sum(1 for ln in logs if "reconnecting" in ln), 1)
        self.assertEqual(logs[-1], "Röle: stopped")
        c, _, logs, _ = self.run_client([OSError("auth failed for Bearer " + KEY)], 2)
        self.assertNotIn(KEY, c.last_error)
        self.assertIn("***", c.last_error)
        self.assertFalse([ln for ln in logs if KEY in ln])


# ------------------------------------------------------------------------------------------------------------------
# H9 — claude path refresh, service PATH
# ------------------------------------------------------------------------------------------------------------------

class ClaudeDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = self.tmp.name

    def nowhere(self, name, path=None):
        return None

    def test_newest_nvm_node(self):
        for v in ("v9.0.0", "v18.2.0", "v20.11.1"):
            write_exe(os.path.join(self.h, ".nvm", "versions", "node", v, "bin", "claude"))
        self.assertEqual(CFG.find_claude(self.h, which=self.nowhere),
                         os.path.join(self.h, ".nvm", "versions", "node", "v20.11.1", "bin", "claude"))

    def test_newest_vscode_extension_numeric_order(self):
        base = os.path.join(self.h, ".vscode", "extensions")
        for v in ("2.1.9", "2.1.301", "2.1.30"):
            write_exe(os.path.join(base, "anthropic.claude-code-%s-linux-x64" % v, "resources", "native-binary",
                                   "claude"))
        self.assertIn("anthropic.claude-code-2.1.301-", CFG.find_claude(self.h, which=self.nowhere))

    def test_preference_and_executable_bit(self):
        write_exe(os.path.join(self.h, ".nvm", "versions", "node", "v20.0.0", "bin", "claude"))
        local = os.path.join(self.h, ".local", "bin", "claude")
        os.makedirs(os.path.dirname(local))
        Path(local).write_text("not executable")
        self.assertIn(".nvm", CFG.find_claude(self.h, which=self.nowhere))
        os.chmod(local, 0o755)
        self.assertEqual(CFG.find_claude(self.h, which=self.nowhere), local)
        on_path = write_exe(os.path.join(self.h, "p", "claude"))
        self.assertEqual(CFG.find_claude(self.h, which=lambda n, path=None: on_path), on_path)

    def test_nothing_found(self):
        self.assertIsNone(CFG.find_claude(self.h, which=self.nowhere))

    def test_refresh_keeps_working_user_path(self):
        mine = write_exe(os.path.join(self.h, "opt", "claude"))
        found = write_exe(os.path.join(self.h, "other", "claude"))
        data = {"engines": {"claude": {"path": mine}}}
        self.assertEqual(CLI.refresh_claude_path(data, found), (mine, None))
        self.assertEqual(data["engines"]["claude"]["path"], mine)

    def test_refresh_fills_null_and_replaces_broken(self):
        found = write_exe(os.path.join(self.h, "other", "claude"))
        data = {"engines": {"claude": {"path": None, "enabled": True}}}
        path, note = CLI.refresh_claude_path(data, found)
        self.assertEqual((path, data["engines"]["claude"]["path"]), (found, found))
        self.assertIn("bulundu", note)
        data = {"engines": {"claude": {"path": os.path.join(self.h, "gone", "claude")}}}
        path, note = CLI.refresh_claude_path(data, found)
        self.assertEqual(data["engines"]["claude"]["path"], found)
        self.assertIn("güncellendi", note)
        data = {}
        self.assertEqual(CLI.refresh_claude_path(data, found)[0], found)
        self.assertEqual(data["engines"]["claude"]["path"], found)

    def test_refresh_without_candidate_changes_nothing(self):
        data = {"engines": {"claude": {"path": None}}}
        self.assertEqual(CLI.refresh_claude_path(data, None), (None, None))
        self.assertIsNone(data["engines"]["claude"]["path"])

    def test_setup_rerun_fills_path(self):
        home = os.path.join(self.h, "connector")
        empty = os.path.join(self.h, "empty")
        os.makedirs(empty)
        args = ["--home", home, "setup", "--server", "https://pixel.example.app", "--room", ROOM, "--key", KEY,
                "--name", "Timur", "--no-default-workspace"]
        with mock.patch.dict(os.environ, {"HOME": self.h, "PATH": empty}), redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(args), 0)
            self.assertIsNone(CFG.read_json(os.path.join(home, CFG.CONFIG_FILE))["engines"]["claude"]["path"])
            claude = write_exe(os.path.join(self.h, ".local", "bin", "claude"))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(CLI.main(args), 0)
        self.assertEqual(CFG.read_json(os.path.join(home, CFG.CONFIG_FILE))["engines"]["claude"]["path"], claude)
        self.assertIn("Claude Code bulundu", out.getvalue())


class ServicePath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = self.tmp.name

    def d(self, *p):
        path = os.path.join(self.h, *p)
        os.makedirs(path, exist_ok=True)
        return path

    def test_sanitised_user_path(self):
        a, b = self.d("a"), self.d("b")
        user = os.pathsep.join(["", "relative/bin", a, os.path.join(self.h, "missing"), b, a, self.h + "/x\ny", "."])
        p = S.service_path(user).split(os.pathsep)
        self.assertEqual(p[:2], [a, b])
        self.assertNotIn("relative/bin", p)
        self.assertNotIn(".", p)
        self.assertFalse([x for x in p if "\n" in x or not os.path.isabs(x)])
        self.assertEqual(len(p), len(set(p)))
        self.assertIn("/usr/bin", p)

    def test_node_script_brings_node_dir(self):
        nvm_bin = self.d("nvm", "bin")
        claude = write_exe(os.path.join(nvm_bin, "claude"), "#!/usr/bin/env node\nconsole.log(1)\n")
        node_dir = self.d("node", "bin")
        node = write_exe(os.path.join(node_dir, "node"))
        p = S.service_path("/usr/bin", claude, which=lambda n, path=None: node if n == "node" else None)
        self.assertEqual(p.split(os.pathsep)[:2], [nvm_bin, node_dir])
        native = write_exe(os.path.join(self.d("native"), "claude"), "\x7fELF....")
        p2 = S.service_path("/usr/bin", native, which=lambda n, path=None: self.fail("no node lookup for a binary"))
        self.assertEqual(p2.split(os.pathsep)[0], os.path.dirname(native))

    def test_unit_and_plist_record_path(self):
        p = os.pathsep.join([self.d("with space"), "/usr/bin"])
        unit = S.systemd_unit("/usr/bin/python3", "%h/x.pyz", None, p)
        self.assertIn('Environment="PATH=%s"' % p, unit)
        self.assertNotIn("network-online", unit)
        import plistlib
        pl = plistlib.loads(S.launchd_plist("/usr/bin/python3", "/x.pyz", "/tmp", p))
        self.assertEqual(pl["EnvironmentVariables"], {"PATH": p})
        self.assertNotIn("EnvironmentVariables", plistlib.loads(S.launchd_plist("/usr/bin/python3", "/x.pyz", "/t")))

    def test_dry_run_install_writes_nothing_and_shows_path(self):
        logs, calls = [], []
        rc, path = S.install(os.path.join(self.h, ".pixel-office", "connector"), dry_run=True, log=logs.append,
                             platform="linux", user_home=self.h, runner=lambda *a, **k: calls.append(a),
                             path_env="/opt/bin:/usr/bin")
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(calls, [])
        self.assertIn("Environment=PATH=/opt/bin:/usr/bin", "\n".join(logs))


# ------------------------------------------------------------------------------------------------------------------
# H11 — clean shutdown
# ------------------------------------------------------------------------------------------------------------------

class CleanShutdown(ConnectorCase):
    engine_kw = {"end_delay": 0.3}

    def test_stop_waits_for_cancelled_runs_then_closes_ledger(self):
        conn = self.make()
        ack = self.start_task(self.agent())
        self.assertEqual(ack["confirm"], None)
        conn.stop()
        run = self.ollama_eng.runs[0]
        self.assertTrue(run.cancelled)
        self.assertFalse(run.killed)
        db = sqlite3.connect(os.path.join(self.home, CFG.LEDGER_FILE))
        try:
            status = db.execute("SELECT status FROM tasks WHERE task=?", (ack["task"],)).fetchone()[0]
        finally:
            db.close()
        self.assertEqual(status, "cancelled")
        evs = [r["ev"] for r in self.audit()]
        self.assertLess(evs.index("task.end"), evs.index("service.stop"))
        self.assertTrue(conn._ledger_closed)


class ShutdownWithStuckRun(ConnectorCase):
    engine_kw = {"end_on_cancel": False}
    stop_wait_s = 0.3

    def test_bounded_wait_then_kill_and_no_ledger_write_after_close(self):
        conn = self.make()
        self.start_task(self.agent())
        t0 = time.monotonic()
        conn.stop()
        self.assertLess(time.monotonic() - t0, 3)
        run = self.ollama_eng.runs[0]
        self.assertTrue(run.killed)
        self.assertTrue(any("zorla sonlandırıldı" in ln for ln in self.logs), self.logs)
        run.events.end("cancelled", None, empty_usage(), None, "unknown")      # the engine ends late
        self.assertTrue(any("kapanış sonrası görev sonu kaydedilmedi" in ln for ln in self.logs), self.logs)


# ------------------------------------------------------------------------------------------------------------------
# H25 / H26 / H27 / H32 — connector handlers
# ------------------------------------------------------------------------------------------------------------------

class NewSessionConfirmOnlyForClaude(ConnectorCase):
    confirm_task = "new_session"

    def test_ollama_never_asks_claude_asks_for_a_new_session(self):
        self.make()
        self.assertIsNone(self.start_task(self.agent("ollama"))["confirm"])
        self.assertEqual(self.start_task(self.agent("claude"))["confirm"], "pending")


class LocalDeleteClearsHistory(ConnectorCase):
    def test_ctl_delete_removes_history(self):
        conn = self.make()
        a = self.agent()
        hist = os.path.join(self.home, "history", a["key"] + ".jsonl")
        os.makedirs(os.path.dirname(hist), exist_ok=True)
        Path(hist).write_text('{"role":"user","content":"x"}\n')
        st, d = conn.ctl_routes()[("POST", "/ctl/agents/delete")]({"agent": a["key"]}, {})
        self.assertEqual((st, d), (200, {"deleted": a["key"]}))
        self.assertFalse(os.path.exists(hist))
        st, _ = conn.ctl_routes()[("POST", "/ctl/agents/delete")]({"agent": a["key"]}, {})
        self.assertEqual(st, 404)


class AuditServiceStart(ConnectorCase):
    start_ctl = True

    def test_service_start_after_control_port_bound(self):
        self.make()
        evs = [(r["ev"], r.get("build")) for r in self.audit()]
        self.assertEqual(evs[:2], [("config.load", None), ("service.start", "dev")])

    def test_no_service_start_when_port_busy(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        self.addCleanup(busy.close)
        conn = self.make(port=busy.getsockname()[1], start=False)
        with self.assertRaises(OSError):
            conn.start()
        conn.stop()
        self.assertNotIn("service.start", [r["ev"] for r in self.audit()])


class AuditTaskConfirm(ConnectorCase):
    confirm_task = "always"

    def test_task_start_only_after_approval_denied_is_task_denied(self):
        conn = self.make()
        a = self.agent()
        ack = self.start_task(a, "reddedilecek")
        self.assertEqual(ack["confirm"], "pending")
        self.assertNotIn("task.start", [r["ev"] for r in self.audit()])
        pid = conn.scheduler.runs[ack["task"]].pending_approval.id
        self.assertTrue(conn.approvals.resolve(pid, False))
        denied = [r for r in self.audit() if r["ev"] == "task.denied"]
        self.assertEqual([(r["task"], r["result"]) for r in denied], [(ack["task"], "denied")])
        self.assertNotIn("task.start", [r["ev"] for r in self.audit()])
        ack2 = self.start_task(a, "onaylı")
        conn.approvals.resolve(conn.scheduler.runs[ack2["task"]].pending_approval.id, True)
        starts = [r for r in self.audit() if r["ev"] == "task.start"]
        self.assertEqual([(r["task"], r["result"]) for r in starts], [(ack2["task"], "ok")])
        self.assertNotIn("reddedilecek", json.dumps(self.audit()))          # prompts stay hashed


class AuditTaskNoConfirm(ConnectorCase):
    def test_task_start_logged_immediately(self):
        self.make()
        ack = self.start_task(self.agent())
        starts = [r for r in self.audit() if r["ev"] == "task.start"]
        self.assertEqual([(r["task"], r["result"]) for r in starts], [(ack["task"], "ok")])


class EngineAgentWrites(ConnectorCase):
    def test_engine_gets_a_copy_and_reset_wins(self):
        conn = self.make()
        a = self.agent("claude")
        self.start_task(a)
        ctx = self.claude_eng.ctxs[0]
        self.assertIsNot(ctx.agent, conn.agents.agents[a["key"]])
        ctx.agent.update(claudeSession="s-1", claudeSessionStarted=False)
        self.assertTrue(ctx.save_agent(ctx.agent))
        self.assertEqual(conn.agents.get(a["key"])["claudeSession"], "s-1")
        conn.agents.update(a["key"], {"name": "Yeni Ad"})               # rename during the run
        ctx.agent["claudeSessionStarted"] = True
        self.assertTrue(ctx.save_agent(ctx.agent))
        stored = conn.agents.get(a["key"])
        self.assertEqual((stored["name"], stored["claudeSessionStarted"]), ("Yeni Ad", True))
        conn.h_agent_reset(fake_peer(), {"type": "agent.reset", "agent": a["key"]})
        ctx.agent["claudeTotals"] = {"costUsd": 1.0}
        self.assertFalse(ctx.save_agent(ctx.agent), "a run ending after agent.reset must not restore its session")
        stored = conn.agents.get(a["key"])
        self.assertEqual((stored["claudeSession"], stored["claudeSessionStarted"], stored["claudeTotals"]),
                         (None, False, None))
        on_disk = CFG.read_json(os.path.join(self.home, CFG.AGENTS_FILE))["agents"][0]
        self.assertEqual((on_disk["name"], on_disk["claudeSession"]), ("Yeni Ad", None))


class AgentStoreConcurrency(unittest.TestCase):
    def test_session_writes_race_with_saves(self):
        with tempfile.TemporaryDirectory() as h:
            st = AgentStore(h)
            a = st.create("A", "claude", None, {"id": "w", "sub": None}, "edit", None)
            epoch = st.session_epoch(a["key"])
            errors = []

            def writer():
                try:
                    for i in range(300):
                        st.save_session(a["key"], {"claudeSession": "s%d" % i, "claudeTotals": {"n": i}}, epoch)
                except Exception as e:
                    errors.append(e)

            def saver():
                try:
                    for _ in range(300):
                        st.save()
                except Exception as e:
                    errors.append(e)
            ths = [threading.Thread(target=writer), threading.Thread(target=saver)]
            for t in ths:
                t.start()
            for t in ths:
                t.join(30)
            self.assertEqual(errors, [])
            self.assertEqual(st.get(a["key"])["claudeSession"], "s299")
            self.assertFalse(st.save_session("nope", {"claudeSession": "x"}, 0))


# ------------------------------------------------------------------------------------------------------------------
# H28 / H29 — quiet hang-ups, doctor and pair error handling
# ------------------------------------------------------------------------------------------------------------------

class QuietHangups(unittest.TestCase):
    def test_handle_error(self):
        srv = ControlServer(0, {}, Approvals(10))
        self.addCleanup(srv.server_close)
        for exc in (BrokenPipeError(32, "Broken pipe"), ConnectionResetError(104, "reset")):
            err = io.StringIO()
            try:
                raise exc
            except OSError:
                with redirect_stderr(err):
                    srv.handle_error(None, ("127.0.0.1", 1))
            self.assertEqual(err.getvalue(), "")
        err = io.StringIO()
        try:
            raise KeyError("secret-preview")
        except KeyError:
            with redirect_stderr(err):
                srv.handle_error(None, ("127.0.0.1", 1))
        self.assertEqual(err.getvalue(), "ctl: istek işlenemedi (KeyError)\n")


class DoctorRunningLine(unittest.TestCase):
    def test_answers(self):
        def req(answer):
            def fn(home, method, path, timeout):
                if isinstance(answer, BaseException):
                    raise answer
                return answer
            return fn
        self.assertEqual(CLI.running_status_line("/x", req(ConnectionRefusedError())), "Çalışan bağlayıcı: yok")
        line = CLI.running_status_line("/x", req((401, {"error": "unauthorized"})))
        self.assertIn("denetim jetonu geçersiz", line)
        self.assertIn("restart", line)
        self.assertIn("HTTP 500", CLI.running_status_line("/x", req((500, None))))
        self.assertIn("HTTP 200", CLI.running_status_line("/x", req((200, ["not", "a", "dict"]))))
        line = CLI.running_status_line("/x", req((200, {"version": "3.1.0", "build": "abc", "relay": "live"})))
        self.assertIn("röle live, ? kanal", line)
        self.assertNotIn("Son röle hatası", line)

    def test_doctor_with_stale_token_does_not_crash(self):
        import _v3_env as E
        env = E.V3Env()
        try:
            home = env.connector_home()
            CFG.write_json(os.path.join(home, CFG.CONTROL_FILE), {"port": 1, "token": "stale"})
            out = io.StringIO()
            with mock.patch("pixel_connector.service.status", return_value="inactive"), \
                    mock.patch.object(CLI, "ctl_request", return_value=(401, {"error": "unauthorized"})), \
                    redirect_stdout(out):
                rc = CLI.main(["--home", home, "doctor"])
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn("denetim jetonu geçersiz", out.getvalue())
            self.assertIn("Güncelleme: kaynak koddan", out.getvalue())
        finally:
            env.stop()


class PairErrors(unittest.TestCase):
    START = (200, {"state": "open", "code": "1234-5678"})

    def run_pair(self, answers):
        seq = list(answers)

        def ctl(home, method, path, body=None, timeout=10.0):
            a = seq.pop(0)
            if isinstance(a, BaseException):
                raise a
            return a
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(CLI, "ctl_request", side_effect=ctl), redirect_stdout(out), redirect_stderr(err):
            rc = CLI.cmd_pair(SimpleNamespace(home="/tmp/x"), ask=lambda q: True)
        return rc, out.getvalue(), err.getvalue()

    def test_oserror_mid_poll(self):
        rc, out, err = self.run_pair([self.START, ConnectionResetError(104, "reset")])
        self.assertEqual(rc, 1)
        self.assertIn("1234-5678", out)
        self.assertIn("bağlantı koptu", err)

    def test_oserror_on_confirm(self):
        rc, _, err = self.run_pair([self.START, (200, {"state": "confirm", "sas": "123 456"}), OSError("gone")])
        self.assertEqual(rc, 1)
        self.assertIn("bağlantı koptu", err)

    def test_stale_token_and_odd_answers(self):
        rc, _, err = self.run_pair([(401, {"error": "unauthorized"})])
        self.assertEqual(rc, 1)
        self.assertIn("denetim jetonu geçersiz", err)
        rc, _, err = self.run_pair([self.START, (200, None)])
        self.assertEqual(rc, 1)
        self.assertIn("yanıt vermedi", err)



class WriteLog:
    """A text stream recording each write() call, shared by "stdout" and "stderr" like the journal under systemd."""

    def __init__(self, calls):
        self.calls = calls

    def write(self, s):
        self.calls.append(s)
        return len(s)

    def flush(self):
        pass


class RunLogLines(unittest.TestCase):
    """H37: the relay status line and the startup banner each end up on their own line (one write per line)."""

    def test_cmd_run_writes_whole_lines(self):
        calls = []
        started = threading.Event()

        class FakeConnector:
            def __init__(self, home, cfg, log):
                self.log = log
                self.key = SimpleNamespace(device_id="dev12345")

            def start(self):
                # the relay thread announces its status while the main thread prints the banner
                t = threading.Thread(target=lambda: (self.log("Röle: connecting"), started.set()))
                t.start()
                t.join()

            def stop(self):
                self.log("Röle: stopped")

        with tempfile.TemporaryDirectory() as h:
            home = os.path.join(h, "connector")
            CFG.ensure_dir(home)
            CFG.write_json(os.path.join(home, CFG.CONFIG_FILE),
                           CFG.initial_config("https://pixel.example.app", ROOM, KEY, "Timur", "PC", None))
            out = WriteLog(calls)
            with mock.patch("pixel_connector.app.Connector", FakeConnector), \
                    mock.patch.object(CLI.signal, "signal", side_effect=lambda sig, handler: handler(sig, None)), \
                    mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", out):
                rc = CLI.main(["--home", home, "run"])
        self.assertEqual(rc, 0)
        self.assertTrue(started.is_set())
        for c in calls:
            self.assertTrue(c.endswith("\n") and c.count("\n") == 1, calls)
        lines = "".join(calls).splitlines()
        self.assertEqual(lines[0], "Röle: connecting")
        self.assertTrue(lines[1].startswith("Pixel Office bağlayıcı %s" % pixel_connector.__version__), lines)
        self.assertEqual(lines[2:], ["Kapatılıyor…", "Röle: stopped"])

    def test_concurrent_lines_never_mix_on_one_descriptor(self):
        r, w = os.pipe()
        a = io.TextIOWrapper(os.fdopen(w, "wb", buffering=0, closefd=False), encoding="utf-8", write_through=True)
        b = io.TextIOWrapper(os.fdopen(w, "wb", buffering=0, closefd=False), encoding="utf-8", write_through=True)
        msgs = {"Röle: connecting", "Pixel Office bağlayıcı çalışıyor"}

        def writer(stream, msg):
            for _ in range(200):
                CLI.log_line(msg, stream)
        ts = [threading.Thread(target=writer, args=(a, "Röle: connecting")),
              threading.Thread(target=writer, args=(b, "Pixel Office bağlayıcı çalışıyor"))]
        chunks = []
        reader = threading.Thread(target=lambda: chunks.extend(iter(lambda: os.read(r, 65536), b"")))
        reader.start()
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        os.close(w)
        reader.join(10)
        os.close(r)
        lines = b"".join(chunks).decode("utf-8").splitlines()
        self.assertEqual(len(lines), 400)
        self.assertEqual(set(lines), msgs)

    def test_closed_stream_is_ignored(self):
        s = io.StringIO()
        s.close()
        CLI.log_line("x", s)                       # no exception: a closed journal must not kill the service


if __name__ == "__main__":
    unittest.main()
