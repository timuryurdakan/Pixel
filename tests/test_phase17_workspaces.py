"""Phase 17: workspace setup fix (PROJECT.md Aşama 17, SECURITY_AUDIT.md §5).

- `setup` creates ~/PixelOfficeIsler (0700) and adds it as the default workspace when the list is empty; idempotent,
  never touches a non-empty list, refuses a symlink / file / existing repository, `--no-default-workspace` opts out.
- `pixel-connector workspace add|list|remove`: same deny + ancestor + realpath rules as config-check, atomic 0600
  writes, rejects /, ~, /home, ~/.ssh and missing paths. Local CLI only: no E2E command and no /ctl route.
- `doctor`: clock skew from the no-store /api/health (+ Age), not from a CDN copy's Date; warns on empty workspaces.

Every test runs with a temporary HOME; nothing touches ~/.pixel-office, ~/.claude or ~/PixelOfficeIsler.
"""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from email.utils import formatdate
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import __main__ as CLI
from pixel_connector import config as CFG
from pixel_connector import workspaces as W

ROOM = "abcdefghijklmnopqrstuvwxyz"
ROOT = Path(__file__).resolve().parent.parent


class _TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user = os.path.join(self.tmp.name, "home", "ali")
        os.makedirs(self.user)
        self.conn = os.path.join(self.tmp.name, "conn")
        self.set_home(self.user)

    def tearDown(self):
        self.tmp.cleanup()

    def set_home(self, home):
        p = mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home})
        p.start()
        self.addCleanup(p.stop)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main(["--home", self.conn] + list(args))
        return rc, out.getvalue() + err.getvalue()

    def setup_cli(self, *extra):
        return self.cli("setup", "--server", "https://pixel.example.app", "--room", ROOM, "--key", "k" * 40,
                        "--name", "Ali", *extra)

    def cfg(self):
        return json.loads(Path(self.conn, "connector.json").read_text(encoding="utf-8"))

    def write_cfg(self, d):
        CFG.write_json(os.path.join(self.conn, "connector.json"), d)


class DefaultWorkspace(_TempHome):
    def test_setup_creates_the_default_folder(self):
        rc, out = self.setup_cli()
        self.assertEqual(rc, 0, out)
        d = os.path.join(self.user, "PixelOfficeIsler")
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        self.assertEqual(self.cfg()["workspaces"], [W.DEFAULT_ENTRY])
        self.assertEqual(W.DEFAULT_ENTRY, {"id": "isler", "name": "Pixel Office İşleri", "path": "~/PixelOfficeIsler",
                                           "children": True})
        self.assertIn("Varsayılan çalışma klasörü", out)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.conn, "connector.json")).st_mode), 0o600)
        rc, out = self.cli("config-check")
        self.assertEqual(rc, 0, out)
        self.assertIn("isler", out)
        cfg = CFG.load_config(self.conn)
        self.assertEqual(cfg.data["workspaces"][0]["real"], os.path.realpath(d))
        self.assertEqual(W.Workspaces(cfg.data["workspaces"]).resolve("isler"), os.path.realpath(d))

    def test_rerun_is_idempotent_and_keeps_user_workspaces(self):
        self.setup_cli()
        rc, out = self.setup_cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cfg()["workspaces"], [W.DEFAULT_ENTRY])
        os.makedirs(os.path.join(self.user, "proj"))
        own = [{"id": "proj", "name": "Projem", "path": "~/proj", "children": False}]
        c = self.cfg()
        c["workspaces"] = own
        self.write_cfg(c)
        rc, out = self.setup_cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cfg()["workspaces"], own)               # not overwritten, no default added
        self.assertIn("değiştirilmedi", out)

    def test_existing_empty_list_gets_the_default_on_rerun(self):
        self.setup_cli("--no-default-workspace")
        self.assertEqual(self.cfg()["workspaces"], [])
        os.makedirs(os.path.join(self.user, "PixelOfficeIsler"))   # an existing plain folder is reused
        rc, out = self.setup_cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cfg()["workspaces"], [W.DEFAULT_ENTRY])

    def test_opt_out(self):
        rc, out = self.setup_cli("--no-default-workspace")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.lexists(os.path.join(self.user, "PixelOfficeIsler")))
        self.assertEqual(self.cfg()["workspaces"], [])
        self.assertIn("--no-default-workspace", out)

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_symlink_is_refused(self):
        target = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(target)
        os.symlink(target, os.path.join(self.user, "PixelOfficeIsler"))
        rc, out = self.setup_cli()
        self.assertEqual(rc, 0, out)                                # setup itself still succeeds
        self.assertEqual(self.cfg()["workspaces"], [])
        self.assertIn("symbolic link", out)
        self.assertIn("workspace add", out)
        with self.assertRaises(W.WorkspaceDenied):
            W.ensure_default_dir(self.user)
        self.assertTrue(os.path.islink(os.path.join(self.user, "PixelOfficeIsler")))   # left alone

    def test_file_and_repository_are_refused(self):
        p = os.path.join(self.user, "PixelOfficeIsler")
        Path(p).write_text("x")
        with self.assertRaisesRegex(W.WorkspaceDenied, "not a directory"):
            W.ensure_default_dir(self.user)
        os.unlink(p)
        os.makedirs(os.path.join(p, ".git"))
        with self.assertRaisesRegex(W.WorkspaceDenied, "repository"):
            W.ensure_default_dir(self.user)
        rc, out = self.setup_cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cfg()["workspaces"], [])

    def test_installers_pass_the_opt_out(self):
        sh = (ROOT / "web" / "connector" / "install.sh").read_text(encoding="utf-8")
        ps1 = (ROOT / "web" / "connector" / "install.ps1").read_text(encoding="ascii")
        self.assertIn("--no-default-workspace", sh)
        self.assertIn("[switch]$NoDefaultWorkspace", ps1)
        self.assertIn('$setup += @("--no-default-workspace")', ps1)


class WorkspaceCli(_TempHome):
    def setUp(self):
        super().setUp()
        self.setup_cli("--no-default-workspace")

    def test_add_list_remove(self):
        proj = os.path.join(self.user, "Projem Ş")
        os.makedirs(os.path.join(proj, "web"))
        rc, out = self.cli("workspace", "add", "~/Projem Ş", "--children")
        self.assertEqual(rc, 0, out)
        self.assertIn("Eklendi: projem-s", out)
        self.assertIn("yeniden başla", out)
        ws = self.cfg()["workspaces"]
        self.assertEqual(ws, [{"id": "projem-s", "name": "Projem Ş", "path": os.path.realpath(proj), "children": True}])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.conn, "connector.json")).st_mode), 0o600)
        self.assertEqual(self.cfg()["room"], ROOM)                    # the rest of the file is kept
        rc, out = self.cli("workspace", "add", proj, "--id", "p2")
        self.assertEqual(rc, 1)
        self.assertIn("zaten ekli", out)
        other = os.path.join(self.tmp.name, "data", "x")
        os.makedirs(other)
        rc, out = self.cli("workspace", "add", other, "--id", "veri", "--name", "Veri klasörü")
        self.assertEqual(rc, 0, out)
        rc, out = self.cli("workspace", "add", other + "/..", "--id", "veri")
        self.assertEqual(rc, 1)
        self.assertIn("kimlik zaten", out)
        rc, out = self.cli("workspace", "list")
        self.assertEqual(rc, 0, out)
        self.assertIn("projem-s", out)
        self.assertIn("Veri klasörü", out)
        self.assertIn("açık", out)
        self.assertIn("kapalı", out)
        rc, out = self.cli("config-check")
        self.assertEqual(rc, 0, out)
        rc, out = self.cli("workspace", "remove", "projem-s")
        self.assertEqual(rc, 0, out)
        self.assertEqual([w["id"] for w in self.cfg()["workspaces"]], ["veri"])
        rc, out = self.cli("workspace", "remove", "projem-s")
        self.assertEqual(rc, 1)
        self.assertIn("böyle bir çalışma klasörü yok", out)
        rc, out = self.cli("workspace", "remove")
        self.assertEqual(rc, 1)

    def test_list_empty(self):
        rc, out = self.cli("workspace")
        self.assertEqual(rc, 0, out)
        self.assertIn("pixel-connector workspace add", out)

    def test_rejections(self):
        os.makedirs(os.path.join(self.user, ".ssh", "keys"))
        os.makedirs(os.path.join(self.user, ".config"))
        for path, why in (("/", "kökü"), ("~", "ev dizininin kendisi"), (self.user, "ev dizininin kendisi"),
                          (os.path.dirname(self.user), "üst klasör"), (self.tmp.name, "üst klasör"),
                          ("~/.ssh", "yasaklı yol"), (os.path.join(self.user, ".ssh", "keys"), "yasaklı yol"),
                          ("~/.config", "yasaklı yol"), ("/etc", "yasaklı yol"), ("~/yok", "klasör yok")):
            rc, out = self.cli("workspace", "add", path)
            self.assertEqual(rc, 1, path)
            self.assertIn(why, out, path)
        Path(self.user, "dosya.txt").write_text("x")
        rc, out = self.cli("workspace", "add", "~/dosya.txt")
        self.assertEqual(rc, 1)
        self.assertIn("klasör değil", out)
        os.makedirs(os.path.join(self.user, "ok"))
        for bad in ("Büyük", "a" * 25, "x/../y", "a b"):
            rc, out = self.cli("workspace", "add", "~/ok", "--id", bad)
            self.assertEqual(rc, 1, bad)
        self.assertEqual(self.cfg()["workspaces"], [])

    @unittest.skipUnless(os.path.isdir("/home") and sys.platform.startswith("linux"), "Linux /home layout")
    def test_rejects_slash_home(self):
        self.set_home("/home/pixel-office-test-no-such-user")           # never created
        rc, out = self.cli("workspace", "add", "/home")
        self.assertEqual(rc, 1)
        self.assertIn("üst klasör", out)
        rc, out = self.cli("workspace", "add", "~")
        self.assertEqual(rc, 1)
        self.assertIn("ev dizininin kendisi", out)
        self.assertFalse(os.path.exists("/home/pixel-office-test-no-such-user"))

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_symlink_is_resolved_and_denied_targets_stay_denied(self):
        real = os.path.join(self.tmp.name, "real-proj")
        os.makedirs(real)
        os.symlink(real, os.path.join(self.user, "kisayol"))
        rc, out = self.cli("workspace", "add", "~/kisayol")
        self.assertEqual(rc, 0, out)
        self.assertIn("sembolik bağ", out)
        self.assertEqual(self.cfg()["workspaces"][0]["path"], os.path.realpath(real))   # the target, not the link
        os.makedirs(os.path.join(self.user, ".ssh"))
        os.symlink(os.path.join(self.user, ".ssh"), os.path.join(self.user, "gizli"))
        rc, out = self.cli("workspace", "add", "~/gizli")
        self.assertEqual(rc, 1)
        self.assertIn("yasaklı yol", out)

    def test_relative_path_uses_the_current_directory(self):
        os.makedirs(os.path.join(self.user, "rel", "sub"))
        cwd = os.getcwd()
        os.chdir(os.path.join(self.user, "rel"))
        try:
            rc, out = self.cli("workspace", "add", "sub")
        finally:
            os.chdir(cwd)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cfg()["workspaces"][0]["path"], os.path.realpath(os.path.join(self.user, "rel", "sub")))

    def test_needs_setup_first(self):
        os.unlink(os.path.join(self.conn, "connector.json"))
        rc, out = self.cli("workspace", "add", "~")
        self.assertEqual(rc, 1)
        self.assertIn("setup", out)

    def test_local_only(self):
        """No remote path can widen the allow-list: no E2E message type, no /ctl route, no config write."""
        from pixel_connector import app, approvals
        app_src = Path(app.__file__).read_text(encoding="utf-8")
        ctl_src = Path(approvals.__file__).read_text(encoding="utf-8")
        for src in (app_src, ctl_src):
            for needle in ("workspace.add", "workspace.remove", "/ctl/workspace", "cmd_workspace", "CONFIG_FILE"):
                self.assertNotIn(needle, src)
        self.assertIn('"workspace.list"', app_src)          # the only remote workspace message is read-only

    def test_restart_hint_per_platform(self):
        self.assertIn("systemctl --user restart pixel-connector", CLI.restart_hint("linux"))
        self.assertIn("launchctl kickstart", CLI.restart_hint("darwin"))
        self.assertIn("schtasks /Run", CLI.restart_hint("win32"))


class _Resp:
    def __init__(self, headers):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ClockSkew(unittest.TestCase):
    NOW = 1_800_000_000.0

    def opener(self, table):
        self.asked = []

        def op(req, timeout=10):
            path = req.full_url.split("example.app", 1)[1]
            self.asked.append((path, req.get_header("Cache-control")))
            h = table.get(path)
            if h is None:
                raise OSError("down")
            return _Resp(h)
        return op

    def skew(self, table):
        return CLI.server_clock_skew("https://pixel.example.app", opener=self.opener(table), now=lambda: self.NOW)

    def test_uses_the_no_store_health_endpoint(self):
        stale = {"Date": formatdate(self.NOW - 259, usegmt=True)}      # a CDN copy of /api/config, no Age
        sk, ep = self.skew({"/api/health": {"Date": formatdate(self.NOW, usegmt=True)}, "/api/config": stale})
        self.assertEqual(ep, "/api/health")
        self.assertLess(abs(sk), 1)
        self.assertEqual(self.asked, [("/api/health", "no-cache")])

    def test_age_is_added_back_on_a_cached_fallback(self):
        sk, ep = self.skew({"/api/config": {"Date": formatdate(self.NOW - 259, usegmt=True), "Age": "259"}})
        self.assertEqual(ep, "/api/config")
        self.assertLess(abs(sk), 1)

    def test_real_skew_is_still_reported(self):
        sk, _ = self.skew({"/api/health": {"Date": formatdate(self.NOW + 120, usegmt=True)}})
        self.assertAlmostEqual(sk, 120, delta=1)
        sk, _ = self.skew({"/api/health": {"Date": formatdate(self.NOW - 90, usegmt=True), "Age": "bogus"}})
        self.assertAlmostEqual(sk, -90, delta=1)

    def test_no_date(self):
        self.assertIsNone(self.skew({"/api/health": {}}))
        self.assertIsNone(self.skew({}))

    def test_health_is_no_store_on_the_server(self):
        from pixel_cloud.app import App, Request
        from pixel_cloud.storage import MemoryStorage
        app = App(storage=MemoryStorage(), env={"PIXEL_SECRET": "d" * 40}, mapper=None)
        r = app.dispatch(Request("GET", "/api/health", {}, body=None, client_ip="10.0.0.1"))
        self.assertEqual(r.status, 200)
        self.assertIn(("Cache-Control", "no-store"), list(r.headers))


class DoctorPhase17(unittest.TestCase):
    def run_doctor(self, workspaces):
        import _v3_env as E
        env = E.V3Env()
        try:
            home = env.connector_home(workspaces=workspaces)
            out = io.StringIO()
            with mock.patch("pixel_connector.service.status", return_value="inactive"), redirect_stdout(out):
                rc = CLI.main(["--home", home, "doctor"])
            return rc, out.getvalue()
        finally:
            env.stop()

    def test_empty_workspaces_warn_with_the_fix(self):
        rc, text = self.run_doctor([])
        self.assertIn("Çalışma klasörleri: ✗ yok", text)
        self.assertIn("pixel-connector workspace add ~/projem", text)
        self.assertRegex(text, r"Saat farkı: \d+ sn ✓")

    def test_workspaces_ok(self):
        rc, text = self.run_doctor(None)
        self.assertEqual(rc, 0, text)
        self.assertIn("Çalışma klasörleri: ✓ 1", text)


if __name__ == "__main__":
    unittest.main()
