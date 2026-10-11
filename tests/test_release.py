"""Phase 10 release checks: Vercel function detection and config, release files, installer symlink safety,
fast first appearance (CLOUD.md §13.2, §17.10)."""

import ast
import fnmatch
import http.client
import importlib.util
import json
import os
import re
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_cloud import app as app_mod
from pixel_cloud import derive
from pixel_cloud import settings as S
from pixel_cloud.app import App
from pixel_cloud.storage import MemoryStorage
from pixel_cloud.vercel import ApiHandler

ROOT = Path(__file__).resolve().parent.parent
API_FILES = sorted((ROOT / "api").glob("*.py"))


def vercel_entrypoint_name(source: str):
    """Python port of Vercel's `find_app_or_handler_impl` (vercel/vercel packages/python-analysis
    crates/vercel_python_analysis/src/entrypoint.rs, read 2026-10-11): only a top-level assignment / def /
    `from … import` of `app`/`application`, or a top-level *class* named `handler` (case-insensitive),
    makes a file under api/ a function. `handler = make_handler()` is NOT recognised."""
    for st in ast.parse(source).body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name) and t.id in ("app", "application"):
                    return t.id
        elif isinstance(st, ast.AnnAssign):
            if isinstance(st.target, ast.Name) and st.target.id in ("app", "application"):
                return st.target.id
        elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)) and st.name in ("app", "application"):
            return st.name
        elif isinstance(st, ast.ImportFrom):
            for a in st.names:
                if (a.asname or a.name) in ("app", "application"):
                    return a.asname or a.name
        elif isinstance(st, ast.ClassDef) and st.name.lower() == "handler":
            return st.name
    return None


def load_api(path: Path):
    spec = importlib.util.spec_from_file_location("vercel_api_" + path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class VercelFunctionDetectionTests(unittest.TestCase):
    def test_every_api_file_is_detected_as_a_function(self):
        self.assertEqual({p.stem for p in API_FILES},
                         {"config", "health", "rooms", "state", "status", "cc_hook", "rename"})
        for p in API_FILES:
            with self.subTest(file=p.name):
                self.assertEqual(vercel_entrypoint_name(p.read_text(encoding="utf-8")), "handler")

    def test_detector_port_rejects_the_old_assignment_form(self):
        self.assertIsNone(vercel_entrypoint_name("from x import make_handler\nhandler = make_handler()\n"))
        self.assertEqual(vercel_entrypoint_name("class handler(Base):\n    pass\n"), "handler")

    def test_api_handler_class_and_route(self):
        routes = set(App(storage=MemoryStorage(), env={}).routes)
        for p in API_FILES:
            with self.subTest(file=p.name):
                h = load_api(p).handler
                self.assertTrue(issubclass(h, ApiHandler) and issubclass(h, BaseHTTPRequestHandler))
                self.assertIn("/api/" + p.stem, routes)     # Vercel serves api/<stem>.py at /api/<stem>

    def test_api_handler_serves_over_a_real_socket(self):
        """What the Vercel runtime does with a BaseHTTPRequestHandler subclass: run it behind an HTTP server."""
        test_app = App(storage=MemoryStorage(), env={})
        with mock.patch.object(app_mod, "_default", test_app):
            h = load_api(ROOT / "api" / "config.py").handler
            srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
                c.request("GET", "/api/config")
                r = c.getresponse()
                body = json.loads(r.read())
                c.close()
            finally:
                srv.shutdown()
                srv.server_close()
        self.assertEqual(r.status, 200)
        self.assertEqual(body["mode"], "cloud")


class VercelConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))

    def test_static_output_and_no_framework(self):
        self.assertIsNone(self.cfg["framework"])           # "Other": no Python framework preset hides api/
        self.assertEqual(self.cfg["outputDirectory"], "web")
        self.assertNotIn("builds", self.cfg)                # `functions` can't be combined with `builds`
        self.assertTrue((ROOT / "web" / "index.html").is_file())

    def test_functions_glob_matches_every_api_file(self):
        (pattern, conf), = self.cfg["functions"].items()
        for p in API_FILES:
            self.assertTrue(fnmatch.fnmatch("api/" + p.name, pattern), p.name)
        self.assertLessEqual(conf["maxDuration"], 300)
        inc = conf["includeFiles"]
        self.assertIn("pixel_cloud/**", inc)
        self.assertIn("web/hook/pixel_hook.py", inc)       # /api/cc_hook imports map_event from it

    def test_python_version_supported(self):
        self.assertIn((ROOT / ".python-version").read_text().strip(), ("3.12", "3.13", "3.14"))

    def test_security_headers(self):
        all_h = {h["key"]: h["value"] for e in self.cfg["headers"] if e["source"] == "/(.*)" for h in e["headers"]}
        self.assertEqual(all_h["Referrer-Policy"], "no-referrer")
        self.assertEqual(all_h["X-Content-Type-Options"], "nosniff")
        self.assertIn("connect-src 'self'", all_h["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", all_h["Content-Security-Policy"])

    def test_vercelignore(self):
        lines = {ln.strip() for ln in (ROOT / ".vercelignore").read_text().splitlines()}
        for need in ("/server.py", "/pixel_office/", "/tests/", "/names.json", "/dev_server.py", "**/__pycache__/"):
            self.assertIn(need, lines)
        for keep in ("/api/", "/pixel_cloud/", "/web/", "/vercel.json", "/.python-version"):
            self.assertNotIn(keep, lines)

    def test_no_python_entrypoint_left_in_the_deployment(self):
        """Vercel also looks for app.py/index.py/server.py/main.py/wsgi.py/asgi.py at the root and in src/, app/."""
        ignored = {ln.strip().strip("/") for ln in (ROOT / ".vercelignore").read_text().splitlines()}
        for d in ("", "src/", "app/"):
            for n in ("app.py", "index.py", "server.py", "main.py", "wsgi.py", "asgi.py"):
                p = ROOT / (d + n)
                if p.exists():
                    self.assertIn(d + n, ignored)


class ReleaseFilesTests(unittest.TestCase):
    def test_gitignore(self):
        lines = (ROOT / ".gitignore").read_text().splitlines()
        for need in ("__pycache__/", "/names.json", ".vercel/", ".env", ".env.*", ".pixel-office/"):
            self.assertIn(need, lines)

    def test_license(self):
        text = (ROOT / "LICENSE").read_text()
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 Timur Yurdakan", text)

    def test_no_personal_absolute_paths(self):
        home = str(Path.home())
        if not re.match(r"^/(home|Users)/[^/]+$", home):
            self.skipTest("no personal home directory on this machine")
        bad = re.compile(re.escape(home + "/") + "|" + re.escape(home.replace("/", "-") + "-"))
        for p in ROOT.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix in (".png", ".pyc") or p.name == Path(__file__).name:
                continue
            if p.name == "names.json":
                continue
            with self.subTest(file=str(p.relative_to(ROOT))):
                self.assertIsNone(bad.search(p.read_text(encoding="utf-8", errors="replace")))

    def test_service_worker_never_touches_api(self):
        sw = (ROOT / "web" / "sw.js").read_text()
        self.assertRegex(sw, r"const VERSION = 'po-v[0-9.]+';")
        i_api = sw.index("url.pathname.startsWith('/api/')")
        self.assertLess(i_api, sw.index("event.respondWith"))
        self.assertNotIn("'/api", sw.split("const SHELL")[1].split("];")[0])


class InstallerSymlinkTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "symlinks")
    def test_install_and_uninstall_keep_a_symlinked_settings_file(self):
        from test_hook_client import ph
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        real = tmp / "dotfiles" / "claude-settings.json"
        real.parent.mkdir()
        real.write_text('{"theme": "dark"}', encoding="utf-8")
        link = tmp / "claude" / "settings.json"
        link.parent.mkdir()
        link.symlink_to(real)
        res = ph.install_hooks(str(link), "/usr/bin/python3", ["/h/.pixel-office/pixel_hook.py", "hook"])
        self.assertTrue(link.is_symlink())
        data = json.loads(real.read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "dark")
        self.assertIn("SessionStart", data["hooks"])
        self.assertTrue(Path(res["backup"]).is_file())
        self.assertEqual(json.loads(Path(res["backup"]).read_text()), {"theme": "dark"})
        self.assertGreater(ph.uninstall_hooks(str(link)), 0)
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(real.read_text(encoding="utf-8")), {"theme": "dark"})


class FastFirstAppearanceTests(unittest.TestCase):
    def test_empty_room_cdn_ttl_is_short(self):
        # The client polls every 5 s while boosting (§17.10); a longer empty-room CDN TTL would hide a
        # newly connected first agent for up to that long.
        self.assertLessEqual(S.CDN_TTL_EMPTY, 5)
        self.assertEqual(derive.cache_control("empty"), f"public, max-age=0, s-maxage={S.CDN_TTL_EMPTY}")
        self.assertEqual(derive.poll_ms("empty"), S.POLL_EMPTY_MS)          # non-boosting viewers stay slow

    def test_client_boost_constants(self):
        tr = (ROOT / "web" / "js" / "transport.js").read_text()
        self.assertIn("export const POLL_FAST_MS = 5000;", tr)
        main = (ROOT / "web" / "js" / "main.js").read_text()
        self.assertIn("transport.boost({ emptyMs: BOOST_MS })", main)


if __name__ == "__main__":
    unittest.main()
