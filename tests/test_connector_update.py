"""`pixel-connector update` (connector 3.5.0, V3.md §5.4 "Güncelleme").

A local fake HTTP server on 127.0.0.1 plays the site (`/connector/SHA256SUMS`, `/connector/pixel-connector.pyz`,
`/hook/*.py`). Covered: up to date, outdated (atomic replace, mode 0600, version from the archive, service restart),
`--check`, SHA-256 mismatch, download failure, non-https server, size limits, refused downgrade redirect, hook files
(only installed ones that have a line), the Antigravity watcher restart, Windows stop → replace → start order, macOS
kickstart, a failed restart, the CLI wiring, SHA256SUMS hook lines and the installers still reading only the .pyz line,
and that nothing in the connector can start an update except the CLI. No real network, no real service, nothing under
~/.pixel-office or ~/.claude.
"""

import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import __main__ as CLI
from pixel_connector import config as CFG
from pixel_connector import service as S
from pixel_connector import update as U

ROOT = Path(__file__).resolve().parent.parent
CONN_DIR = ROOT / "web" / "connector"
HOOK_DIR = ROOT / "web" / "hook"
ROOM = "abcdefghijklmnopqrstuvwxyz"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fake_pyz(version: str, build: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pixel_connector/__init__.py", '__version__ = "%s"\n' % version)
        z.writestr("pixel_connector/_build.py", 'BUILD = "%s"\n' % build)
        z.writestr("__main__.py", "raise SystemExit(0)\n")
    return b"#!/usr/bin/env python3\n" + buf.getvalue()


OLD_PYZ = fake_pyz("3.4.1", "aaaaaaaaaaaa")
NEW_PYZ = fake_pyz("3.5.0", "bbbbbbbbbbbb")


class FakeSite:
    """Serves `files` {path: bytes}; `redirects` {path: location}; records every requested path."""

    def __init__(self):
        self.files = {}
        self.redirects = {}
        self.requests = []
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                site.requests.append(self.path)
                if self.path in site.redirects:
                    self.send_response(302)
                    self.send_header("Location", site.redirects[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = site.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def publish(self, pyz: bytes, hooks=None, sums_override=None):
        hooks = hooks or {}
        self.files["/connector/pixel-connector.pyz"] = pyz
        lines = {"pixel-connector.pyz": sha(pyz), "install.sh": "1" * 64, "install.ps1": "2" * 64}
        for name, data in hooks.items():
            self.files["/hook/" + name] = data
            lines["../hook/" + name] = sha(data)
        lines.update(sums_override or {})
        self.files["/connector/SHA256SUMS"] = "".join(
            "%s  %s\n" % (h, n) for n, h in sorted(lines.items()) if h is not None).encode()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


class FakeRunner:
    """Records argv (and the target file's digest at that moment); returns the configured exit codes."""

    def __init__(self, watch=None, codes=None):
        self.calls = []
        self.watch = watch
        self.codes = codes or {}

    def __call__(self, argv, **kw):
        digest = sha(Path(self.watch).read_bytes()) if self.watch else None
        self.calls.append((list(argv), digest))
        rc = 0
        for prefix, code in self.codes.items():
            if " ".join(argv).startswith(prefix):
                rc = code
        return subprocess.CompletedProcess(argv, rc)

    @property
    def argvs(self):
        return [a for a, _ in self.calls]


class UpdateBase(unittest.TestCase):
    platform = "linux"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pixel-update-")
        self.home = os.path.join(self.tmp, "home")
        self.office = os.path.join(self.home, ".pixel-office")
        self.conn = os.path.join(self.office, "connector")
        os.makedirs(self.conn, mode=0o700)
        self.pyz = os.path.join(self.conn, "pixel-connector.pyz")
        Path(self.pyz).write_bytes(OLD_PYZ)
        os.chmod(self.pyz, 0o600)
        self.site = FakeSite()
        self.runner = FakeRunner(watch=self.pyz)
        self.out = []

    def tearDown(self):
        self.site.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install_unit(self, name=S.UNIT_NAME):
        d = os.path.join(self.home, ".config", "systemd", "user")
        os.makedirs(d, exist_ok=True)
        Path(d, name).write_text("[Unit]\n")

    def update(self, check=False, server=None, opener=U.default_opener, platform=None):
        services = U.Services(platform=platform or self.platform, user_home=self.home, runner=self.runner,
                              log=self.out.append, environ={})
        return U.run_update(self.site.base if server is None else server, self.pyz, self.office, check_only=check, opener=opener,
                            services=services, out=self.out.append)

    def text(self):
        return "\n".join(self.out)

    def leftovers(self):
        return [n for d in (self.conn, self.office) for n in os.listdir(d) if n.endswith(".yeni")]


class UpdateFlowTests(UpdateBase):
    def test_up_to_date_downloads_nothing_else_and_changes_nothing(self):
        self.install_unit()
        self.site.publish(OLD_PYZ)
        before = os.stat(self.pyz)
        self.assertEqual(self.update(), U.EXIT_OK)
        self.assertIn("Zaten güncel", self.text())
        self.assertEqual(self.site.requests, ["/connector/SHA256SUMS"])
        self.assertEqual(os.stat(self.pyz).st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.runner.calls, [])

    def test_outdated_is_replaced_atomically_and_the_service_restarted(self):
        self.install_unit()
        self.site.publish(NEW_PYZ)
        self.assertEqual(self.update(), U.EXIT_OK, self.text())
        self.assertEqual(Path(self.pyz).read_bytes(), NEW_PYZ)
        self.assertEqual(stat.S_IMODE(os.stat(self.pyz).st_mode), 0o600)
        self.assertEqual(self.leftovers(), [])
        self.assertIn("Bağlayıcı güncellendi: 3.5.0 (build bbbbbbbbbbbb)", self.text())
        self.assertEqual(self.runner.calls, [(["systemctl", "--user", "restart", "pixel-connector"], sha(NEW_PYZ))])
        self.assertEqual(sorted(os.listdir(self.conn)), ["pixel-connector.pyz"])     # settings etc. untouched

    def test_without_an_installed_service_nothing_is_restarted(self):
        self.site.publish(NEW_PYZ)
        self.assertEqual(self.update(), U.EXIT_OK)
        self.assertEqual(self.runner.calls, [])
        self.assertIn("Servis kurulu değil", self.text())

    def test_check_only_changes_nothing(self):
        self.install_unit()
        self.site.publish(NEW_PYZ)
        self.assertEqual(self.update(check=True), U.EXIT_AVAILABLE)
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)
        self.assertEqual(self.site.requests, ["/connector/SHA256SUMS"])
        self.assertEqual(self.runner.calls, [])
        self.assertIn("Güncelleme var: pixel-connector.pyz", self.text())
        self.site.publish(OLD_PYZ)
        self.assertEqual(self.update(check=True), U.EXIT_OK)

    def test_sha_mismatch_fails_closed(self):
        self.install_unit()
        self.site.publish(NEW_PYZ, sums_override={"pixel-connector.pyz": sha(b"something else")})
        self.assertEqual(self.update(), U.EXIT_ERROR)
        self.assertIn("SHA-256 doğrulanamadı", self.text())
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)
        self.assertEqual((self.runner.calls, self.leftovers()), ([], []))

    def test_download_failure_fails_closed(self):
        self.install_unit()
        self.site.publish(NEW_PYZ)
        del self.site.files["/connector/pixel-connector.pyz"]
        self.assertEqual(self.update(), U.EXIT_ERROR)
        self.assertIn("indirilemedi (HTTPError)", self.text())
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)
        self.assertEqual(self.runner.calls, [])
        self.site.files.clear()                                      # no SHA256SUMS at all
        self.out.clear()
        self.assertEqual(self.update(), U.EXIT_ERROR)
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)

    def test_sums_without_a_pyz_line_is_refused(self):
        self.site.publish(NEW_PYZ, sums_override={"pixel-connector.pyz": None})
        self.assertEqual(self.update(), U.EXIT_ERROR)
        self.assertIn("pixel-connector.pyz satırı yok", self.text())
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)

    def test_non_https_server_is_refused_before_any_request(self):
        def no_network(url, timeout):
            raise AssertionError("must not fetch " + url)
        for server in ("http://pixel.example.app", "ftp://pixel.example.app", "file:///etc", "", "https://",
                       "http://192.168.1.5:8000", "https://user:pw@pixel.example.app"):
            with self.subTest(server=server):
                self.out.clear()
                self.assertEqual(self.update(server=server, opener=no_network), U.EXIT_ERROR)
                self.assertTrue(self.text().startswith("Hata: sunucu"), self.text())
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)

    def test_size_limits(self):
        self.install_unit()
        self.site.publish(NEW_PYZ)
        with mock.patch.object(U, "MAX_PYZ_BYTES", len(NEW_PYZ) - 1):
            self.assertEqual(self.update(), U.EXIT_ERROR)
        self.assertIn("çok büyük", self.text())
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)

        class NoLength:                                # a body larger than the limit without Content-Length
            headers = {}

            def __init__(self, data):
                self.data = data

            def read(self, n):
                return self.data[:n]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with self.assertRaises(U.UpdateError):
            U.fetch("https://x.example/a", 10, lambda url, timeout: NoLength(b"x" * 11))
        self.assertEqual(U.fetch("https://x.example/a", 10, lambda url, timeout: NoLength(b"x" * 10)), b"x" * 10)
        self.assertLessEqual(U.MAX_PYZ_BYTES, 16 * 1024 * 1024)
        self.assertGreater(U.MAX_PYZ_BYTES, (CONN_DIR / "pixel-connector.pyz").stat().st_size * 4)
        self.assertGreater(U.MAX_HOOK_BYTES, max(p.stat().st_size for p in HOOK_DIR.glob("*.py")) * 4)

    def test_timeout_is_passed_to_every_request(self):
        seen = []

        def opener(url, timeout):
            seen.append(timeout)
            return U.default_opener(url, timeout)
        self.site.publish(NEW_PYZ)
        self.assertEqual(self.update(opener=opener), U.EXIT_OK)
        self.assertEqual(seen, [U.FETCH_TIMEOUT_S, U.FETCH_TIMEOUT_S])

    def test_downgrade_redirect_is_refused(self):
        self.site.publish(NEW_PYZ)
        self.site.redirects["/connector/pixel-connector.pyz"] = "http://198.51.100.7/pixel-connector.pyz"
        self.assertEqual(self.update(), U.EXIT_ERROR)
        self.assertIn("indirilemedi", self.text())
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)
        self.assertTrue(U.redirect_allowed("https://a.example/x", "https://b.example/y"))
        self.assertTrue(U.redirect_allowed("http://127.0.0.1:8000/x", "/y"))
        self.assertFalse(U.redirect_allowed("https://a.example/x", "http://a.example/y"))
        self.assertFalse(U.redirect_allowed("http://127.0.0.1:8000/x", "http://evil.example/y"))
        self.assertFalse(U.redirect_allowed("https://a.example/x", "file:///etc/passwd"))

    def test_failed_restart_is_reported(self):
        self.install_unit()
        self.site.publish(NEW_PYZ)
        self.runner.codes = {"systemctl --user restart": 5}
        self.assertEqual(self.update(), U.EXIT_ERROR)
        self.assertEqual(Path(self.pyz).read_bytes(), NEW_PYZ)
        self.assertIn("servis yeniden başlatılamadı (çıkış kodu 5)", self.text())

    def test_source_checkout_is_refused(self):
        self.site.publish(NEW_PYZ)
        self.pyz = None
        self.assertEqual(self.update(), U.EXIT_ERROR)
        self.assertIn("kaynak koddan", self.text())
        self.assertEqual(self.site.requests, [])


class HookFileTests(UpdateBase):
    def setUp(self):
        super().setUp()
        Path(self.office, "pixel_hook.py").write_text("# eski kanca\n")
        Path(self.office, "pixel_antigravity.py").write_text("# eski izleyici\n")
        self.new_hooks = {"pixel_hook.py": b"# yeni kanca\n", "pixel_agent.py": b"# yeni ajan\n",
                          "pixel_antigravity.py": b"# yeni izleyici\n"}

    def test_installed_hook_files_are_refreshed_missing_ones_not_created(self):
        self.install_unit()
        self.install_unit(S.AG_UNIT_NAME)
        self.site.publish(NEW_PYZ, self.new_hooks)
        self.assertEqual(self.update(), U.EXIT_OK, self.text())
        self.assertEqual(Path(self.office, "pixel_hook.py").read_bytes(), b"# yeni kanca\n")
        self.assertEqual(Path(self.office, "pixel_antigravity.py").read_bytes(), b"# yeni izleyici\n")
        self.assertFalse(os.path.exists(os.path.join(self.office, "pixel_agent.py")))    # not installed here
        for n in ("pixel_hook.py", "pixel_antigravity.py"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.office, n)).st_mode), 0o600)
        self.assertNotIn("/hook/pixel_agent.py", self.site.requests)
        self.assertEqual(self.runner.argvs, [["systemctl", "--user", "restart", "pixel-connector"],
                                             ["systemctl", "--user", "restart", S.AG_UNIT_NAME]])
        self.assertEqual(self.leftovers(), [])

    def test_a_file_without_a_sums_line_is_skipped(self):
        self.site.publish(OLD_PYZ, self.new_hooks, sums_override={"../hook/pixel_hook.py": None})
        self.assertEqual(self.update(), U.EXIT_OK, self.text())
        self.assertEqual(Path(self.office, "pixel_hook.py").read_bytes(), b"# eski kanca\n")
        self.assertEqual(Path(self.office, "pixel_antigravity.py").read_bytes(), b"# yeni izleyici\n")
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)

    def test_only_the_hook_changed_no_connector_restart(self):
        self.install_unit()
        self.install_unit(S.AG_UNIT_NAME)
        hooks = dict(self.new_hooks, **{"pixel_antigravity.py": b"# eski izleyici\n"})
        self.site.publish(OLD_PYZ, hooks)
        self.assertEqual(self.update(), U.EXIT_OK, self.text())
        self.assertEqual(Path(self.office, "pixel_hook.py").read_bytes(), b"# yeni kanca\n")
        self.assertEqual(self.runner.calls, [])           # neither the connector nor the watcher files changed

    def test_a_bad_hook_file_leaves_everything_unchanged(self):
        self.install_unit()
        self.site.publish(NEW_PYZ, self.new_hooks, sums_override={"../hook/pixel_antigravity.py": sha(b"x")})
        self.assertEqual(self.update(), U.EXIT_ERROR)
        self.assertEqual(Path(self.pyz).read_bytes(), OLD_PYZ)
        self.assertEqual(Path(self.office, "pixel_hook.py").read_bytes(), b"# eski kanca\n")
        self.assertEqual((self.runner.calls, self.leftovers()), ([], []))

    def test_unknown_or_malformed_sums_lines_are_ignored(self):
        text = "\n".join([
            "%s  pixel-connector.pyz" % ("a" * 64),
            "%s  ../hook/pixel_hook.py" % ("b" * 64),
            "%s  ../../etc/passwd" % ("c" * 64),
            "%s  ../hook/evil.py" % ("d" * 64),
            "%s  pixel-connector.pyz" % ("Z" * 64),
            "garbage"])
        self.assertEqual(U.parse_sums(text), {"pixel-connector.pyz": "a" * 64, "../hook/pixel_hook.py": "b" * 64})


class PlatformTests(UpdateBase):
    def test_windows_stops_the_task_before_replacing_and_starts_it_after(self):
        self.site.publish(NEW_PYZ)
        self.assertEqual(self.update(platform="win32"), U.EXIT_OK, self.text())
        calls = self.runner.calls
        self.assertEqual([a for a, _ in calls], [["schtasks", "/Query", "/TN", S.SCHTASK_NAME],
                                                 ["schtasks", "/End", "/TN", S.SCHTASK_NAME],
                                                 ["schtasks", "/Run", "/TN", S.SCHTASK_NAME]])
        self.assertEqual(calls[1][1], sha(OLD_PYZ))     # stopped while the old file is still there
        self.assertEqual(calls[2][1], sha(NEW_PYZ))     # started after the replace

    def test_windows_task_missing_no_stop_no_start(self):
        self.site.publish(NEW_PYZ)
        self.runner.codes = {"schtasks /Query": 1}
        self.assertEqual(self.update(platform="win32"), U.EXIT_OK)
        self.assertEqual(self.runner.argvs, [["schtasks", "/Query", "/TN", S.SCHTASK_NAME]])

    def test_windows_failed_download_never_stops_the_task(self):
        self.site.publish(NEW_PYZ, sums_override={"pixel-connector.pyz": sha(b"x")})
        self.assertEqual(self.update(platform="win32"), U.EXIT_ERROR)
        self.assertEqual(self.runner.calls, [])

    def test_macos_kickstart(self):
        d = os.path.join(self.home, "Library", "LaunchAgents")
        os.makedirs(d)
        Path(d, S.LAUNCHD_LABEL + ".plist").write_bytes(b"")
        self.site.publish(NEW_PYZ)
        self.assertEqual(self.update(platform="darwin"), U.EXIT_OK)
        uid = os.getuid() if hasattr(os, "getuid") else 0
        self.assertEqual(self.runner.argvs, [["launchctl", "kickstart", "-k", "gui/%d/%s" % (uid, S.LAUNCHD_LABEL)]])

    def test_antigravity_service_names_match_the_watcher(self):
        src = (HOOK_DIR / "pixel_antigravity.py").read_text(encoding="utf-8")
        self.assertIn('AG_SERVICE_NAME = "%s"' % S.AG_UNIT_NAME, src)
        self.assertIn('AG_LAUNCHD_LABEL = "%s"' % S.AG_LAUNCHD_LABEL, src)
        self.assertIn('AG_SCHTASK_NAME = "%s"' % S.AG_SCHTASK_NAME.replace("\\", "\\\\"), src)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pixel-update-cli-")
        self.home = os.path.join(self.tmp, "connector")
        os.makedirs(self.home, mode=0o700)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main(["--home", self.home] + list(argv))
        return rc, out.getvalue() + err.getvalue()

    def test_update_from_a_source_checkout_is_refused_without_network(self):
        CFG.write_json(os.path.join(self.home, CFG.CONFIG_FILE),
                       CFG.initial_config("https://pixel.example.app", ROOM, "k" * 32, "Ali", "pc", None))
        with mock.patch.object(U, "default_opener", side_effect=AssertionError("network")):
            rc, text = self.run_cli("update")
        self.assertEqual(rc, U.EXIT_ERROR)
        self.assertIn("kaynak koddan", text)
        rc, text = self.run_cli("update", "--check")
        self.assertEqual(rc, U.EXIT_ERROR)

    def test_update_without_settings(self):
        rc, text = self.run_cli("update")
        self.assertEqual(rc, 2)

    def test_help_lists_update(self):
        self.assertIn("update", CLI.__doc__)
        p = CLI.build_parser()
        self.assertTrue(p.parse_args(["update", "--check"]).check)


class NoRemoteUpdateTests(unittest.TestCase):
    """The update runs only from the CLI: no E2E message, no /ctl route, no background thread starts one."""

    def test_nothing_but_the_cli_imports_the_update_module(self):
        users = [p.name for p in (ROOT / "pixel_connector").rglob("*.py")
                 if p.name != "update.py" and ("from . import update" in p.read_text(encoding="utf-8")
                                               or "from .update" in p.read_text(encoding="utf-8")
                                               or "import update" in p.read_text(encoding="utf-8"))]
        self.assertEqual(users, ["__main__.py"])

    def test_no_handler_or_control_route_for_updates(self):
        from pixel_connector.app import Connector
        names = [n for n in dir(Connector) if n.startswith("h_") and any(w in n for w in ("update", "upgrade", "install"))]
        self.assertEqual(names, ["h_agent_update"])          # editing an agent, not the connector
        src = (ROOT / "pixel_connector" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("/ctl/update", src)


class SumsFileTests(unittest.TestCase):
    def sums(self):
        return (CONN_DIR / "SHA256SUMS").read_text()

    def test_hook_lines_match_the_served_files(self):
        parsed = U.parse_sums(self.sums())
        for name in U.HOOK_FILES:
            self.assertEqual(parsed["../hook/" + name], sha((HOOK_DIR / name).read_bytes()), name)
        self.assertEqual(parsed["pixel-connector.pyz"], sha((CONN_DIR / "pixel-connector.pyz").read_bytes()))

    def test_hook_files_are_listed_not_bundled(self):
        with zipfile.ZipFile(CONN_DIR / "pixel-connector.pyz") as z:
            names = z.namelist()
        self.assertNotIn("pixel_antigravity.py", names)
        self.assertNotIn("pixel_hook.py", names)
        self.assertIn("pixel_agent.py", names)          # bundled for office presence since Phase 13

    @unittest.skipIf(os.name == "nt", "POSIX shell")
    def test_install_sh_still_reads_only_the_pyz_line(self):
        sums = os.path.join(tempfile.mkdtemp(prefix="pixel-sums-"), "SHA256SUMS")
        try:
            Path(sums).write_text(self.sums())
            script = (CONN_DIR / "install.sh").read_text()
            pipeline = [l.strip() for l in script.splitlines() if l.strip().startswith("EXPECTED=")][0]
            self.assertEqual(pipeline, '''EXPECTED="$(grep ' pixel-connector.pyz$' "$TMP/SHA256SUMS" | cut -d' ' -f1)"''')
            r = subprocess.run(["sh", "-c", pipeline + '; printf %s "$EXPECTED"'], env=dict(os.environ, TMP=os.path.dirname(sums)),
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(r.stdout, sha((CONN_DIR / "pixel-connector.pyz").read_bytes()))
        finally:
            shutil.rmtree(os.path.dirname(sums), ignore_errors=True)

    def test_install_ps1_still_reads_only_the_pyz_line(self):
        import re
        script = (CONN_DIR / "install.ps1").read_text()
        self.assertIn('Where-Object { $_ -match " pixel-connector\\.pyz$" }', script)
        hits = [l.split(" ")[0] for l in self.sums().splitlines() if re.search(r" pixel-connector\.pyz$", l)]
        self.assertEqual(hits, [sha((CONN_DIR / "pixel-connector.pyz").read_bytes())])

    def test_doctor_line_suggests_update(self):
        line = CLI.update_line("https://pixel.example.app", "a" * 64,
                               lambda url, timeout: io.BytesIO(("%s  pixel-connector.pyz\n" % ("b" * 64)).encode()))
        self.assertIn("pixel-connector update", line)


if __name__ == "__main__":
    unittest.main()
