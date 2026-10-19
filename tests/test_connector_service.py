"""Phase 13: service definitions (V3.md §12, text generation + dry runs only, no real systemctl/launchctl/schtasks)
and the offline CLI commands (setup, config-check, install-service --dry-run, reset-keys, peers/agents/usage)."""

import io
import json
import os
import plistlib
import stat
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import __main__ as CLI
from pixel_connector import config as CFG
from pixel_connector import service as S

ROOM = "abcdefghijklmnopqrstuvwxyz"


class ServiceTextTests(unittest.TestCase):
    def test_systemd_unit(self):
        t = S.systemd_unit("/usr/bin/python3", "%h/.pixel-office/connector/pixel-connector.pyz")
        self.assertIn("ExecStart=/usr/bin/python3 %h/.pixel-office/connector/pixel-connector.pyz run\n", t)
        for line in ("[Unit]", "Description=Pixel Office bağlayıcı", "After=network-online.target",
                     "Wants=network-online.target", "Type=simple", "Restart=on-failure", "RestartSec=5", "UMask=0077",
                     "NoNewPrivileges=yes", "Environment=PYTHONUNBUFFERED=1", "WantedBy=default.target"):
            self.assertIn(line + "\n", t)
        self.assertNotIn("PIXEL_OFFICE_HOME", t)
        q = S.systemd_unit("/opt/my python/bin/python3", "/srv/a b/x.pyz", "/srv/pixel home")
        self.assertIn('ExecStart="/opt/my python/bin/python3" "/srv/a b/x.pyz" run', q)
        self.assertIn('Environment="PIXEL_OFFICE_HOME=/srv/pixel home"', q)
        self.assertIn("%%", S.systemd_unit("/usr/bin/python3", "/x/100%.pyz"))

    def test_launchd_and_schtasks(self):
        p = plistlib.loads(S.launchd_plist("/usr/bin/python3", "/Users/u/.pixel-office/connector/pixel-connector.pyz",
                                           "/Users/u/.pixel-office/connector"))
        self.assertEqual(p["Label"], "tr.pixeloffice.connector")
        self.assertEqual(p["ProgramArguments"][-1], "run")
        self.assertTrue(p["RunAtLoad"])
        self.assertEqual(p["KeepAlive"], {"SuccessfulExit": False})
        self.assertTrue(p["StandardErrorPath"].endswith("connector.err.log"))
        argv = S.schtasks_create_argv(r"C:\Py\pythonw.exe", r"C:\Users\u\.pixel-office\connector\pixel-connector.pyz")
        self.assertEqual(argv[:9], ["schtasks", "/Create", "/TN", "PixelOffice\\Connector", "/SC", "ONLOGON", "/RL",
                                    "LIMITED", "/F"])
        self.assertEqual(argv[-1], '"C:\\Py\\pythonw.exe" "C:\\Users\\u\\.pixel-office\\connector\\pixel-connector.pyz" run')

    def test_install_dry_run_never_runs_anything(self):
        with tempfile.TemporaryDirectory() as h:
            ran, logs = [], []
            conn = os.path.join(h, ".pixel-office", "connector")
            for plat in ("linux", "darwin", "win32", "sunos5"):
                rc, path = S.install(conn, dry_run=True, log=logs.append, platform=plat, user_home=h,
                                     python="/usr/bin/python3", runner=lambda *a, **k: ran.append(a))
                self.assertEqual(rc, 0 if plat != "sunos5" else 2)
            self.assertEqual(ran, [])
            self.assertFalse(os.path.exists(os.path.join(h, ".config")))
            text = "\n".join(logs)
            self.assertIn("ExecStart=/usr/bin/python3 %h/.pixel-office/connector/pixel-connector.pyz run", text)
            self.assertIn("$ systemctl --user enable pixel-connector", text)
            self.assertIn("$ systemctl --user restart pixel-connector", text)
            self.assertIn("$ schtasks /Create", text)
            self.assertIn("launchctl bootstrap", text)
            self.assertEqual(S.uninstall(dry_run=True, log=logs.append, platform="linux", user_home=h,
                                         runner=lambda *a, **k: ran.append(a)), 0)
            self.assertEqual(ran, [])

    def test_install_writes_unit_with_fake_runner(self):
        with tempfile.TemporaryDirectory() as h:
            calls = []

            class R:
                returncode = 0
            conn = os.path.join(h, ".pixel-office", "connector")
            rc, path = S.install(conn, platform="linux", user_home=h, python="/usr/bin/python3", log=lambda s: None,
                                 runner=lambda argv, **k: calls.append(argv) or R())
            self.assertEqual(rc, 0)
            self.assertEqual(path, os.path.join(h, ".config", "systemd", "user", "pixel-connector.service"))
            self.assertIn("%h/.pixel-office/connector/pixel-connector.pyz", Path(path).read_text())
            self.assertEqual(calls, [["systemctl", "--user", "daemon-reload"],
                                     ["systemctl", "--user", "enable", "pixel-connector"],
                                     ["systemctl", "--user", "restart", "pixel-connector"]])
            S.uninstall(platform="linux", user_home=h, log=lambda s: None, runner=lambda argv, **k: calls.append(argv) or R())
            self.assertFalse(os.path.exists(path))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "conn")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main(["--home", self.home] + list(args))
        return rc, out.getvalue() + err.getvalue()

    def test_setup_config_check_and_files(self):
        rc, out = self.cli("setup", "--server", "https://pixel.example.app", "--room", ROOM, "--key", "k" * 40,
                           "--name", "Timur", "--device-name", "Masaüstü")
        self.assertEqual(rc, 0, out)
        self.assertIn("Cihaz kimliği:", out)
        for name in ("connector.json", "keys.json"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.home, name)).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.home).st_mode), 0o700)
        keys = json.loads(Path(os.path.join(self.home, "keys.json")).read_text())
        self.assertEqual(set(keys), {"v", "priv", "pub", "deviceId", "created"})
        rc, out = self.cli("config-check")
        self.assertEqual(rc, 0, out)
        self.assertIn("connector.json geçerli", out)
        cfg = json.loads(Path(os.path.join(self.home, "connector.json")).read_text())
        cfg["profiles"]["dev"]["allow"].append("bypassPermissions")
        CFG.write_json(os.path.join(self.home, "connector.json"), cfg)
        rc, out = self.cli("config-check")
        self.assertEqual(rc, 1)
        self.assertIn("can never be configured", out)
        rc, out = self.cli("setup", "--server", "https://x", "--room", "short", "--key", "k" * 40, "--name", "T")
        self.assertEqual(rc, 1)

    def test_reset_keys_and_offline_commands(self):
        self.cli("setup", "--server", "https://pixel.example.app", "--room", ROOM, "--key", "k" * 40, "--name", "T")
        old = json.loads(Path(os.path.join(self.home, "keys.json")).read_text())["deviceId"]
        CFG.write_json(os.path.join(self.home, "peers.json"), {"v": 1, "peers": [
            {"bid": "aaaaaaaaaaaaaaaa", "pub": "x", "label": "Telefon", "pairedAt": 1, "lastSeen": 2, "revoked": False}]})
        rc, out = self.cli("peers")
        self.assertIn("Telefon", out)
        rc, out = self.cli("peers", "revoke", "aaaaaaaaaaaaaaaa")      # service down → peers.json directly
        self.assertEqual(rc, 0, out)
        self.assertTrue(json.loads(Path(os.path.join(self.home, "peers.json")).read_text())["peers"][0]["revoked"])
        self.assertIn("peer.revoke", Path(os.path.join(self.home, "audit.log")).read_text())
        rc, out = self.cli("reset-keys", "--yes")
        self.assertEqual(rc, 0)
        self.assertNotEqual(json.loads(Path(os.path.join(self.home, "keys.json")).read_text())["deviceId"], old)
        self.assertFalse(os.path.exists(os.path.join(self.home, "peers.json")))
        rc, out = self.cli("usage", "--period", "week")
        self.assertIn("Bu hafta: 0 görev", out)
        rc, out = self.cli("agents")
        self.assertIn("Ajan yok", out)
        rc, out = self.cli("install-service", "--dry-run")
        self.assertEqual(rc, 0, out)
        rc, out = self.cli("approve", "p123")
        self.assertEqual(rc, 1)
        self.assertIn("Bağlayıcı çalışmıyor", out)
        rc, out = self.cli("runtimes", "start")
        self.assertEqual(rc, 1)



class DoctorTests(unittest.TestCase):
    def test_doctor_against_dev_server(self):
        import _v3_env as E
        from unittest import mock
        env = E.V3Env()
        try:
            home = env.connector_home()
            out = io.StringIO()
            with mock.patch("pixel_connector.service.status", return_value="inactive"), redirect_stdout(out):
                rc = CLI.main(["--home", home, "doctor"])
            text = out.getvalue()
            self.assertEqual(rc, 0, text)
            self.assertIn("connector.json: ✓", text)
            self.assertIn("röle: %s" % env.relay_url, text)
            self.assertIn("Saat farkı", text)
            self.assertIn("Servis: inactive", text)
            self.assertIn("Çalışan bağlayıcı: yok", text)
        finally:
            env.stop()


if __name__ == "__main__":
    unittest.main()
