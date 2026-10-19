"""Phase 15 security review: one regression test per fix (V3.md §20 "Phase 15 findings").

F1 /onay page token is separate from the control token (cookies reach every 127.0.0.1 port).
F2 install.sh: nothing runs from a truncated `curl | sh` download; --server must be https (or loopback http);
   the shim quotes paths safely. install.ps1 is ASCII (Windows PowerShell 5.1 reads BOM-less files as ANSI).
F3 Service re-install restarts the running connector (updates take effect); `$` in unit paths is escaped.
F4 The connector only follows a relay URL that is wss:// (or the loopback dev relay) on the room's own path.
F5 Claude Code's environment never carries PIXEL_* / RELAY_SECRET / Upstash tokens.
F6 `pixel-connector approve` prints remote-supplied names/prompts without terminal control sequences.
Release checks: README publishes the current .pyz SHA-256 (T5); sw.js never intercepts /connector/* or /api/*.
"""

import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import relay as R
from pixel_connector import service as S
from pixel_connector.approvals import Approvals, ControlServer
from pixel_connector.engines import claude as CL

ROOT = Path(__file__).resolve().parent.parent
CONN_DIR = ROOT / "web" / "connector"
ROOM = "abcdefghijklmnopqrstuvwxyz"


class OnayPageToken(unittest.TestCase):
    def setUp(self):
        self.srv = ControlServer(0, {("GET", "/ctl/status"): lambda b, q: (200, {"ok": True})}, Approvals())
        self.srv.start()
        self.addCleanup(lambda: (self.srv.shutdown(), self.srv.server_close()))

    def get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        r.read()
        c.close()
        return r

    def test_page_token_never_unlocks_the_control_api(self):
        st = self.srv
        self.assertNotEqual(st.page_token, st.token)
        self.assertEqual(self.get("/ctl/status", {"X-Pixel-Ctl": st.token}).status, 200)
        self.assertEqual(self.get("/ctl/status", {"X-Pixel-Ctl": st.page_token}).status, 401)
        r = self.get("/onay?t=" + st.page_token)
        self.assertEqual(r.status, 303)
        cookie = r.getheader("Set-Cookie")
        self.assertIn("pixel_ctl=%s;" % st.page_token, cookie)
        self.assertNotIn(st.token, cookie)
        self.assertIn("Path=/onay", cookie)
        self.assertEqual(self.get("/onay", {"Cookie": "pixel_ctl=" + st.page_token}).status, 200)
        self.assertEqual(self.get("/onay", {"Cookie": "pixel_ctl=" + st.token}).status, 401)
        self.assertEqual(self.get("/onay?t=" + st.token).status, 401)
        self.assertIn("/onay?t=" + st.page_token, st.page_link())


@unittest.skipUnless(shutil.which("sh"), "no POSIX shell")
class InstallerHardening(unittest.TestCase):
    def run_sh(self, script, *args, home):
        env = dict(os.environ, HOME=home, PIXEL_OFFICE_HOME=os.path.join(home, ".pixel-office"))
        return subprocess.run(["sh", str(script)] + list(args), env=env, capture_output=True, text=True, timeout=60)

    def test_truncated_download_runs_nothing(self):
        text = (CONN_DIR / "install.sh").read_text(encoding="utf-8")
        self.assertTrue(text.rstrip().endswith('main "$@"'))
        with tempfile.TemporaryDirectory() as h:
            for cut in (len(text) // 3, len(text) // 2, len(text) - len('main "$@"\n') - 1):
                part = Path(h) / "part.sh"
                part.write_text(text[:cut], encoding="utf-8")
                self.run_sh(part, "--server", "https://x.example", "--room", ROOM, "--key", "k" * 40, home=h)
                self.assertFalse(os.path.exists(os.path.join(h, ".pixel-office")), cut)

    def test_server_must_be_https_or_loopback(self):
        with tempfile.TemporaryDirectory() as h:
            for bad in ("http://pixel.example.app", "ftp://x", "pixel.example.app", "file:///etc"):
                r = self.run_sh(CONN_DIR / "install.sh", "--server", bad, "--room", ROOM, "--key", "k" * 40,
                                "--no-service", home=h)
                self.assertEqual(r.returncode, 2, bad)
                self.assertIn("https://", r.stderr)
                self.assertFalse(os.path.exists(os.path.join(h, ".pixel-office")))

    def test_ps1_is_ascii_and_checks_the_server_scheme(self):
        src = (CONN_DIR / "install.ps1").read_bytes().decode("ascii")
        self.assertIn("'^https://'", src)
        self.assertNotIn("$args =", src)          # automatic variable, never assigned


class ServiceReinstall(unittest.TestCase):
    def test_linux_reinstall_restarts_and_dollar_is_escaped(self):
        with tempfile.TemporaryDirectory() as h:
            calls = []

            class Ok:
                returncode = 0
            conn = os.path.join(h, ".pixel-office", "connector")
            for _ in range(2):                    # idempotent: same file, same commands
                S.install(conn, platform="linux", user_home=h, python="/usr/bin/python3", log=lambda s: None,
                          runner=lambda argv, **k: calls.append(argv) or Ok())
            self.assertEqual(calls[:3], [["systemctl", "--user", "daemon-reload"],
                                         ["systemctl", "--user", "enable", "pixel-connector"],
                                         ["systemctl", "--user", "restart", "pixel-connector"]])
            self.assertEqual(calls[:3], calls[3:])
        unit = S.systemd_unit("/opt/py$HOME/python3", "/srv/a b/$X%/pixel-connector.pyz")
        self.assertIn('ExecStart="/opt/py$$HOME/python3" "/srv/a b/$$X%%/pixel-connector.pyz" run', unit)

    def test_launchd_and_schtasks_replace_a_running_instance(self):
        logs = []
        S.install("/Users/u/.pixel-office/connector", platform="darwin", user_home="/Users/u", python="/usr/bin/python3",
                  dry_run=True, log=logs.append)
        cmds = [x for x in logs if x.startswith("$ ")]
        self.assertTrue(cmds[0].startswith("$ launchctl bootout gui/"), cmds)
        self.assertTrue(cmds[1].startswith("$ launchctl bootstrap gui/"), cmds)
        logs.clear()
        S.install(r"C:\u\.pixel-office\connector", platform="win32", user_home=r"C:\u", python=r"C:\Py\python.exe",
                  dry_run=True, log=logs.append)
        cmds = [x.split()[1] for x in logs if x.startswith("$ ")]
        self.assertEqual(cmds, ["schtasks", "schtasks", "schtasks"])
        self.assertEqual([x.split()[2] for x in logs if x.startswith("$ ")], ["/Create", "/End", "/Run"])


class _TicketHandler(BaseHTTPRequestHandler):
    answer = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps(self.server.answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RelayUrlFromTicket(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _TicketHandler)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(lambda: (self.srv.shutdown(), self.srv.server_close()))
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def fetch(self, url):
        self.srv.answer = {"url": url, "ticket": "v1.p.m", "exp": 0}
        return R.fetch_ticket(self.base, ROOM, "k" * 32, "devaaaaaaaaaaaa2")

    def test_only_wss_or_loopback_ws_on_the_rooms_path(self):
        for ok in ("wss://pixel-relay.timur.workers.dev/v1/rooms/%s/ws" % ROOM,
                   "ws://127.0.0.1:8788/v1/rooms/%s/ws" % ROOM):
            self.assertEqual(self.fetch(ok)["url"], ok)
        for bad in ("ws://pixel-relay.timur.workers.dev/v1/rooms/%s/ws" % ROOM,      # ticket in clear text
                    "wss://evil.example/v1/rooms/%s/ws" % ("b" * 26),               # another room
                    "wss://evil.example/v1/rooms/%s/ws?tk=1" % ROOM,
                    "https://pixel-relay.timur.workers.dev/v1/rooms/%s/ws" % ROOM,
                    "ws://10.0.0.1/v1/rooms/%s/ws" % ROOM):
            with self.assertRaises(R.TicketError, msg=bad):
                self.fetch(bad)


class ClaudeChildEnv(unittest.TestCase):
    def test_server_secrets_are_stripped(self):
        env = CL.child_env({"PATH": "/bin", "HOME": "/h", "RELAY_SECRET": "x", "PIXEL_SECRET": "y",
                            "PIXEL_RELAY_URL": "wss://r", "UPSTASH_REDIS_REST_TOKEN": "z", "KV_REST_API_TOKEN": "w",
                            "PIXEL_OFFICE_HOME": "/p", "ANTHROPIC_MODEL": "m"})
        self.assertEqual(env, {"PATH": "/bin", "HOME": "/h", "ANTHROPIC_MODEL": "m", "PIXEL_OFFICE_DISABLE": "1"})


class ReleaseChecks(unittest.TestCase):
    def test_readme_publishes_the_current_connector_digest(self):
        sums = dict(reversed(line.split("  ")) for line in (CONN_DIR / "SHA256SUMS").read_text().splitlines())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("pixel-connector.pyz SHA-256: %s" % sums["pixel-connector.pyz"], readme)

    def test_service_worker_is_network_only_for_connector_api_and_hook(self):
        sw = (ROOT / "web" / "sw.js").read_text()
        guard = sw[sw.index("url.pathname.startsWith('/api/')"):sw.index("// network only")]
        for prefix in ("'/api/'", "'/hook/'", "'/connector/'"):
            self.assertIn("startsWith(%s)" % prefix, guard)
        self.assertLess(sw.index("// network only"), sw.index("event.respondWith"))
        shell = sw.split("const SHELL")[1].split("];")[0]
        self.assertNotIn("/connector", shell)
        self.assertNotIn("/api", shell)


class TerminalSafe(unittest.TestCase):
    def test_control_sequences_are_neutralised(self):
        from pixel_connector.__main__ import term_safe
        self.assertEqual(term_safe("a\x1b]0;pwned\x07b\r\nc"), "a ]0;pwned b  c")
        self.assertEqual(term_safe(None), "")
        self.assertEqual(len(term_safe("x" * 500)), 200)
        self.assertEqual(term_safe("Yeni ajan: Kâtip"), "Yeni ajan: Kâtip")


if __name__ == "__main__":
    unittest.main()
