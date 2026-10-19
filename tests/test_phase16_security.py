"""Phase 16 security audit: one regression test per fix (docs/SECURITY_AUDIT.md).

S16-01 Claude Code always gets deny rules for files where an edit becomes code execution (.git, .claude settings,
       .mcp.json, ~/.claude, shell start-up files, ~/.config, ~/.local/bin) and for more credential files.
S16-02 The v1 local server rejects foreign Host headers (DNS rebinding could read prompts from /api/agents, /events);
       PIXEL_OFFICE_ALLOWED_HOSTS adds names such as a Tailscale MagicDNS name.
S16-03 Unauthenticated /api/state is rate-limited per IP before any storage command; /api/health reuses its PING.
S16-04 Server-side id/format regexes reject a trailing newline (`$` matched before "\n").
S16-05 The v2 hook installer runs nothing from a truncated download and refuses plain http to public hosts.
S16-06 A workspace may not be an ancestor of the home directory (/home, /Users, C:\\Users).
S16-10 vercel.json/dev_server send object-src 'none', form-action 'self' and a Permissions-Policy.
"""

import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

import dev_server
from pixel_cloud import ids
from pixel_cloud import settings as S
from pixel_cloud.app import App, Request
from pixel_cloud.storage import MemoryStorage
from pixel_cloud.validate import ValidationError, parse_cloud_rename, parse_update
from pixel_connector import workspaces as W
from pixel_connector.engines import claude as CL
from pixel_office.httpd import host_allowed, make_server
from pixel_office.store import AgentStore

ROOT = Path(__file__).resolve().parent.parent
ROOM = "abcdefghijklmnopqrstuvwxyz"


# -- S16-01 ---------------------------------------------------------------------------------------------------

class ClaudeExecSensitiveDeny(unittest.TestCase):
    PROFILES = {"dev": {"allow": ["Bash(git status *)"], "deny": []}, "auto": {"enabled": True}}

    def argv(self, profile):
        return CL.build_argv("/opt/claude", profile, self.PROFILES, "11111111-2222-4333-8444-555555555555",
                             False, None, 50, None, (2, 1, 300))

    def test_every_profile_denies_edits_that_lead_to_code_execution(self):
        must = ["Edit(//**/.git/**)", "Edit(**/.git/**)", "Edit(**/.claude/settings.json)",
                "Edit(**/.claude/settings.local.json)", "Edit(**/.mcp.json)", "Edit(**/.vscode/**)", "Edit(**/.envrc)", "Edit(~/.claude/**)",
                "Edit(~/.claude.json)", "Edit(~/.bashrc)", "Edit(~/.profile)", "Edit(~/.zshrc)", "Edit(~/.config/**)",
                "Edit(~/.local/bin/**)", "Edit(~/.local/share/**)", "Edit(~/.vscode/**)", "Edit(~/.gitconfig)", "Read(~/.config/gh/**)", "Read(~/.git-credentials)"]
        for prof in ("readonly", "edit", "dev", "auto"):
            a = self.argv(prof)
            j = a.index("--disallowedTools")
            deny = a[j + 1:j + 1 + len(CL.BUILTIN_DENY)]
            with self.subTest(profile=prof):
                for rule in must:
                    self.assertIn(rule, deny)
                self.assertEqual(deny[:7], ["Bash(sudo *)", "Read(~/.ssh/**)", "Read(~/.gnupg/**)", "Read(~/.aws/**)",
                                            "Read(~/.pixel-office/**)", "Edit(~/.pixel-office/**)",
                                            "Read(~/.claude/.credentials.json)"])

    def test_rules_are_well_formed_and_unique(self):
        self.assertEqual(len(CL.BUILTIN_DENY), len(set(CL.BUILTIN_DENY)))
        for r in CL.BUILTIN_DENY:
            self.assertRegex(r, r"^(Bash|Read|Edit)\([^()\s]+( \*)?\)\Z")
            self.assertNotIn("dangerously", r)


# -- S16-02 ---------------------------------------------------------------------------------------------------

class LocalServerHostCheck(unittest.TestCase):
    def test_host_allowed(self):
        for ok in (None, "127.0.0.1:8765", "localhost:8765", "LOCALHOST", "[::1]:8765", "192.168.1.20:8765",
                   "10.0.0.5", "app.localhost:8765"):
            self.assertTrue(host_allowed(ok), ok)
        for bad in ("evil.example:8765", "127.0.0.1.nip.io:8765", "rebind.attacker.test", "", "[]:80"):
            self.assertFalse(host_allowed(bad), bad)
        self.assertTrue(host_allowed("office.lan:8765", ("office.lan",)))

    def test_extra_names_from_env(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = os.environ.get("PIXEL_OFFICE_ALLOWED_HOSTS")
        os.environ["PIXEL_OFFICE_ALLOWED_HOSTS"] = " pc.tail1234.ts.net , other.lan"
        try:
            srv = make_server("127.0.0.1", 0, AgentStore("demo"), Path(tmp.name))
        finally:
            if old is None:
                os.environ.pop("PIXEL_OFFICE_ALLOWED_HOSTS", None)
            else:
                os.environ["PIXEL_OFFICE_ALLOWED_HOSTS"] = old
        self.addCleanup(srv.server_close)
        self.assertEqual(srv.host_names, ("127.0.0.1", "pc.tail1234.ts.net", "other.lan"))
        self.assertTrue(host_allowed("pc.tail1234.ts.net:8765", srv.host_names))
        self.assertFalse(host_allowed("evil.ts.net:8765", srv.host_names))

    def test_rebinding_request_cannot_read_agents_or_events_or_write(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "index.html").write_text("<!doctype html><title>x</title>")
        srv = make_server("127.0.0.1", 0, AgentStore("demo"), root)
        port = srv.server_address[1]
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        self.addCleanup(lambda: (srv.shutting_down.set(), srv.shutdown(), srv.server_close()))

        def req(method, path, host, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.putrequest(method, path, skip_host=True)
            c.putheader("Host", host)
            if body is not None:
                c.putheader("Content-Type", "application/json")
                c.putheader("Content-Length", str(len(body)))
            c.endheaders(body)
            r = c.getresponse()
            data = r.read() if path != "/events" or r.status != 200 else b""
            c.close()
            return r.status, data

        evil = "attacker.example:%d" % port
        for path in ("/api/agents", "/events", "/", "/api/health"):
            st, data = req("GET", path, evil)
            self.assertEqual(st, 421, path)
            self.assertNotIn(b"lastPrompt", data)
        self.assertEqual(req("POST", "/api/status", evil, b"{}")[0], 421)
        st, data = req("GET", "/api/agents", "127.0.0.1:%d" % port)
        self.assertEqual(st, 200)
        self.assertEqual(req("GET", "/api/health", "localhost:%d" % port)[0], 200)


# -- S16-03 / S16-04 ----------------------------------------------------------------------------------------------

class CountingStorage(MemoryStorage):
    def __init__(self, clock):
        super().__init__(clock)
        self.reads = 0
        self.pings = 0

    def read_room(self, room):
        self.reads += 1
        return super().read_room(room)

    def ping(self):
        self.pings += 1
        return super().ping()


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class CloudQuotaAndFormats(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.storage = CountingStorage(self.clock)
        self.app = App(storage=self.storage, env={"PIXEL_SECRET": "x" * 48}, clock=self.clock, mapper=None)

    def get(self, path, ip="203.0.113.7"):
        return self.app.dispatch(Request("GET", path, {}, body=None, client_ip=ip))

    def test_state_is_rate_limited_per_ip_before_storage(self):
        burst = S.RATE_IP_STATE[1]
        statuses = []
        for i in range(burst + 30):
            # a different (well-formed) room every time: every request would be a CDN miss and an Upstash read
            room = ("a" * 20) + format(i, "06d").translate(str.maketrans("0123456789", "abcdefghij"))
            statuses.append(self.get("/api/state?room=" + room).status)
        self.assertEqual(statuses[:burst], [200] * burst)
        self.assertEqual(set(statuses[burst:]), {429})
        self.assertEqual(self.storage.reads, burst)
        r = self.get("/api/state?room=" + ROOM)
        self.assertEqual(r.status, 429)
        self.assertTrue(r.header("Retry-After"))
        self.assertEqual(self.get("/api/state?room=" + ROOM, ip="198.51.100.9").status, 200)   # other IPs unaffected
        self.clock.t += 5
        self.assertEqual(self.get("/api/state?room=" + ROOM).status, 200)                      # refills at 2/s

    def test_health_reuses_the_ping(self):
        for _ in range(50):
            self.assertEqual(self.get("/api/health").status, 200)
        self.assertEqual(self.storage.pings, 1)
        self.clock.t += S.HEALTH_PING_TTL + 1
        self.get("/api/health")
        self.assertEqual(self.storage.pings, 2)

    def test_trailing_newline_is_rejected(self):
        self.assertFalse(ids.valid_room_id(ROOM + "\n"))
        self.assertEqual(self.get("/api/state?room=" + ROOM + "%0A").status, 404)
        for rx, good in ((ids.AGENT_ID_RE, "a0123456789abcdef"), (ids.OWNER_ID_RE, "n_abcdef"),
                         (ids.SESSION_RE, "sess-1"), (ids.WRITE_KEY_RE, "k" * 32)):
            self.assertTrue(rx.match(good))
            self.assertIsNone(rx.match(good + "\n"))
        with self.assertRaises(ValidationError):
            parse_cloud_rename(json.dumps({"agentId": "a0123456789abcdef\n", "name": "x"}).encode())
        for field, value in (("session", "s1\n"), ("tool", "Bash\n"), ("source", "api\n")):
            obj = {"session": "s1", "owner": "Ali", "state": "working", field: value}
            with self.assertRaises(ValidationError, msg=field):
                parse_update(obj)


# -- S16-05 ---------------------------------------------------------------------------------------------------

@unittest.skipUnless(shutil.which("sh"), "no POSIX shell")
class HookInstallerHardening(unittest.TestCase):
    SCRIPT = ROOT / "web" / "hook" / "install.sh"

    def run_sh(self, script, *args, home):
        env = dict(os.environ, HOME=home, PIXEL_OFFICE_HOME=os.path.join(home, ".pixel-office"))
        return subprocess.run(["sh", str(script)] + list(args), env=env, capture_output=True, text=True, timeout=60)

    def test_truncated_download_runs_nothing(self):
        text = self.SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(text.rstrip().endswith('main "$@"'))
        with tempfile.TemporaryDirectory() as h:
            for cut in (len(text) // 4, len(text) // 2, len(text) - len('main "$@"\n') - 1):
                part = Path(h) / "part.sh"
                part.write_text(text[:cut], encoding="utf-8")
                self.run_sh(part, "--server", "https://x.example", "--room", ROOM, "--key", "k" * 32, home=h)
                self.assertFalse(os.path.exists(os.path.join(h, ".pixel-office")), cut)

    def test_plain_http_only_for_local_servers(self):
        with tempfile.TemporaryDirectory() as h:
            for bad in ("http://pixel.example.app", "http://203.0.113.5.nip.io:8765", "ftp://x", "pixel.example.app"):
                r = self.run_sh(self.SCRIPT, "--server", bad, "--room", ROOM, "--key", "k" * 32, home=h)
                self.assertEqual(r.returncode, 2, bad)
                self.assertIn("https://", r.stderr)
                self.assertFalse(os.path.exists(os.path.join(h, ".pixel-office")), bad)
            # loopback (closed port): passes the scheme check and fails later at the download, quickly
            r = self.run_sh(self.SCRIPT, "--server", "http://127.0.0.1:9", "--room", ROOM, "--key", "k" * 32, home=h)
            self.assertNotEqual(r.returncode, 2)
            self.assertNotIn("https://", r.stderr)

    def test_windows_hook_installer_scheme_check(self):
        import re
        text = (ROOT / "web" / "hook" / "install.ps1").read_text(encoding="utf-8")
        self.assertTrue(text.isascii())
        local = re.search(r"^\$LocalHttp = '([^']+)'$", text, re.M).group(1)
        self.assertIn("-match '^https://[^/]' -or $Server -match $LocalHttp", text)

        def ok(u):                      # PowerShell -match is case-insensitive
            return bool(re.match(r"^https://[^/]", u, re.I) or re.match(local, u, re.I))
        for good in ("https://pixel.example.app", "http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765",
                     "http://192.168.1.20:8765", "http://10.0.0.5", "http://172.20.1.1:80", "http://office.local:8765",
                     "http://pc.lan", "http://pc:8765"):
            self.assertTrue(ok(good), good)
        for bad in ("http://pixel.example.app", "http://192.168.1.5.nip.io", "http://172.32.0.1", "ftp://x",
                    "pixel.example.app", "https:///x", "http://8.8.8.8"):
            self.assertFalse(ok(bad), bad)

    def test_pwsh_path_is_quoted(self):
        self.assertIn("PS1_PATH=", self.SCRIPT.read_text(encoding="utf-8"))


# -- S16-06 ---------------------------------------------------------------------------------------------------

class WorkspaceAncestors(unittest.TestCase):
    def test_ancestors_of_home_are_denied(self):
        with tempfile.TemporaryDirectory() as t:
            t = os.path.realpath(t)
            home = os.path.join(t, "home", "ali")
            proj = os.path.join(home, "proj")
            os.makedirs(proj)
            self.assertIn("ancestor", W.check_denied(os.path.join(t, "home"), home))
            self.assertIn("ancestor", W.check_denied(t, home))
            self.assertIsNone(W.check_denied(proj, home))
            self.assertIsNone(W.check_denied(os.path.join(t, "elsewhere"), home))
            ws = W.Workspaces([{"id": "all", "name": "All", "path": os.path.join(t, "home"), "children": True}], home)
            with self.assertRaises(W.WorkspaceDenied):
                ws.resolve("all")


# -- S16-10 ---------------------------------------------------------------------------------------------------

class HardeningHeaders(unittest.TestCase):
    def test_vercel_and_dev_server_headers(self):
        cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        h = {x["key"]: x["value"] for e in cfg["headers"] if e["source"] == "/(.*)" for x in e["headers"]}
        dev = dict(dev_server.SECURITY_HEADERS)
        for headers in (h, dev):
            csp = headers["Content-Security-Policy"]
            self.assertIn("object-src 'none'", csp)
            self.assertIn("form-action 'self'", csp)
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertIn("camera=()", headers["Permissions-Policy"])
        self.assertEqual(h["Permissions-Policy"], dev["Permissions-Policy"])


if __name__ == "__main__":
    unittest.main()
