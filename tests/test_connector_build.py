"""Phase 13: distribution (V3.md §5.4, §17.2): the committed zipapp is a reproducible build, the connector imports
only the standard library (ast scan) and parses as Python 3.8, the pyz runs, and install.sh verifies SHA-256 (a
tampered download is refused) and installs into a temp HOME."""

import ast
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

import dev_server
from pixel_cloud.app import App
from pixel_cloud.storage import MemoryStorage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_connector  # noqa: E402

CONN_DIR = ROOT / "web" / "connector"
ROOM = "abcdefghijklmnopqrstuvwxyz"
STDLIB_FALLBACK = {"__future__", "argparse", "ast", "atexit", "base64", "binascii", "collections", "contextlib",
                   "copy", "datetime", "email", "fcntl", "functools", "hashlib", "hmac", "html", "http", "importlib",
                   "io", "ipaddress", "json", "math", "msvcrt", "os", "platform", "plistlib", "queue", "random", "re",
                   "secrets", "select", "shutil", "signal", "socket", "sqlite3", "ssl", "stat", "string", "struct",
                   "subprocess", "sys", "tempfile", "termios", "threading", "time", "typing", "unicodedata", "urllib",
                   "uuid", "zipfile", "pty", "tty", "errno", "getpass", "traceback", "types", "logging", "itertools"}


def stdlib_names():
    return set(getattr(sys, "stdlib_module_names", ())) or STDLIB_FALLBACK


class BuildTests(unittest.TestCase):
    def test_committed_build_is_current_and_reproducible(self):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            self.assertEqual(build_connector.main(["--check"]), 0)
        a, b = build_connector.build_bytes(), build_connector.build_bytes()
        self.assertEqual(a, b)
        sums = (CONN_DIR / "SHA256SUMS").read_text()
        self.assertIn("%s  pixel-connector.pyz\n" % hashlib.sha256(a).hexdigest(), sums)
        for name in ("install.sh", "install.ps1"):
            self.assertIn("%s  %s\n" % (hashlib.sha256((CONN_DIR / name).read_bytes()).hexdigest(), name), sums)

    def test_zip_contents(self):
        with zipfile.ZipFile(CONN_DIR / "pixel-connector.pyz") as z:
            names = z.namelist()
            self.assertEqual(names, sorted(names))
            self.assertIn("__main__.py", names)
            self.assertIn("pixel_agent.py", names)
            for mod in ("crypto", "session", "relay", "ws", "app", "tasks", "agents", "approvals", "audit", "config",
                        "peers", "runtimes", "service", "usage", "workspaces", "__main__", "__init__"):
                self.assertIn("pixel_connector/%s.py" % mod, names)
            for eng in ("base", "claude", "ollama", "openai_compat", "__init__"):
                self.assertIn("pixel_connector/engines/%s.py" % eng, names)
            self.assertFalse([n for n in names if "pycache" in n or n.endswith(".pyc") or "test" in n])
        self.assertTrue((CONN_DIR / "pixel-connector.pyz").read_bytes().startswith(b"#!/usr/bin/env python3\n"))

    def test_stdlib_only_and_python38_syntax(self):
        std = stdlib_names()
        files = sorted((ROOT / "pixel_connector").rglob("*.py")) + [ROOT / "web" / "hook" / "pixel_agent.py"]
        for p in files:
            src = p.read_text(encoding="utf-8")
            with self.subTest(file=p.name):
                tree = ast.parse(src, feature_version=(3, 8))
                for n in ast.walk(tree):
                    if isinstance(n, ast.Import):
                        mods = [a.name.split(".")[0] for a in n.names]
                    elif isinstance(n, ast.ImportFrom) and n.level == 0:
                        mods = [(n.module or "").split(".")[0]]
                    else:
                        continue
                    for m in mods:
                        self.assertTrue(m in std or m in ("pixel_connector", "pixel_agent"), "%s imports %s" % (p, m))
                        self.assertNotIn(m, ("zoneinfo", "graphlib", "tomllib"), "%s: 3.9+ module" % p.name)
                for bad in (".removeprefix(", ".removesuffix(", "is_relative_to(", "functools.cache(", "math.lcm("):
                    self.assertFalse(bad in src, "%s uses the 3.9+ API %s" % (p.name, bad))
                if "| None" in src or "list[" in src or "dict[" in src:
                    self.assertIn("from __future__ import annotations", src, p.name)

    def test_pyz_runs_setup_and_config_check(self):
        with tempfile.TemporaryDirectory() as h:
            env = dict(os.environ, HOME=h, PIXEL_OFFICE_HOME=os.path.join(h, ".pixel-office"))
            pyz = str(CONN_DIR / "pixel-connector.pyz")
            r = subprocess.run([sys.executable, pyz, "setup", "--server", "https://pixel.example.app", "--room", ROOM,
                                "--key", "k" * 40, "--name", "Timur"], env=env, capture_output=True, text=True,
                               timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Cihaz kimliği", r.stdout)
            r = subprocess.run([sys.executable, pyz, "config-check"], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.exists(os.path.join(h, ".pixel-office", "connector", "keys.json")))


@unittest.skipUnless(shutil.which("sh"), "no POSIX shell")
class InstallScriptTests(unittest.TestCase):
    def serve(self, web_root):
        app = App(storage=MemoryStorage(), env={"PIXEL_SECRET": "d" * 40}, mapper=None)
        srv = dev_server.DevServer(("127.0.0.1", 0), app, web_root)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def install(self, base, h):
        env = dict(os.environ, HOME=h, PIXEL_OFFICE_HOME=os.path.join(h, ".pixel-office"))
        return subprocess.run(["sh", str(CONN_DIR / "install.sh"), "--server", base, "--room", ROOM, "--key", "k" * 40,
                               "--name", "Timur Y", "--no-service"], env=env, capture_output=True, text=True,
                              timeout=120)

    def test_install_end_to_end(self):
        base = self.serve(dev_server.WEB_ROOT)
        with tempfile.TemporaryDirectory() as h:
            r = self.install(base, h)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            office = os.path.join(h, ".pixel-office")
            pyz = os.path.join(office, "connector", "pixel-connector.pyz")
            self.assertEqual(Path(pyz).read_bytes(), (CONN_DIR / "pixel-connector.pyz").read_bytes())
            self.assertIn("SHA-256: " + hashlib.sha256(Path(pyz).read_bytes()).hexdigest(), r.stdout)
            self.assertIn("pixel-connector pair", r.stdout)
            self.assertEqual(os.stat(os.path.join(office, "connector")).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(pyz).st_mode & 0o777, 0o600)
            shim = os.path.join(office, "bin", "pixel-connector")
            r2 = subprocess.run([shim, "config-check"], env=dict(os.environ, HOME=h, PIXEL_OFFICE_HOME=office),
                                capture_output=True, text=True, timeout=60)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("Sunucu: %s" % base, r2.stdout)

    def test_tampered_download_is_refused(self):
        with tempfile.TemporaryDirectory() as web, tempfile.TemporaryDirectory() as h:
            shutil.copytree(CONN_DIR, os.path.join(web, "connector"))
            with open(os.path.join(web, "connector", "pixel-connector.pyz"), "ab") as f:
                f.write(b"\n# injected\n")
            base = self.serve(Path(web))
            r = self.install(base, h)
            self.assertEqual(r.returncode, 1)
            self.assertIn("SHA-256 doğrulanamadı", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(h, ".pixel-office", "connector", "pixel-connector.pyz")))
            self.assertFalse(os.path.exists(os.path.join(h, ".pixel-office", "connector", "connector.json")))


if __name__ == "__main__":
    unittest.main()
