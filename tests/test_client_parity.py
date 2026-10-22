"""v2.md F4: the hook client (web/hook/pixel_hook.py), the library/CLI (web/hook/pixel_agent.py) and the connector are
downloaded and run separately, so they can't import each other and some helpers are copied on purpose. These tests
fail as soon as a copy drifts: the same constants, byte-identical helper functions (compared as ASTs), the same
behaviour where the signatures differ, and the PowerShell client's state list and regexes (by text parsing)."""

import ast
import importlib.util
import os
import re
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import config as CFG
from pixel_connector import localhttp

ROOT = Path(__file__).resolve().parent.parent
HOOK_PY = ROOT / "web" / "hook" / "pixel_hook.py"
AGENT_PY = ROOT / "web" / "hook" / "pixel_agent.py"
HOOK_PS1 = ROOT / "web" / "hook" / "pixel_hook.ps1"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ph = load("pixel_hook_parity", HOOK_PY)
pa = load("pixel_agent_parity", AGENT_PY)


def function_ast(path, name):
    """The function's AST without its docstring, so only the code is compared."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body = body[1:]
            return ast.dump(ast.Module(body=body, type_ignores=[])) + ast.dump(node.args)
    raise AssertionError("%s not found in %s" % (name, path.name))


LOOPBACK_VECTORS = ["http://127.0.0.1:8765/api/status", "http://localhost:8765/x", "http://LOCALHOST./x",
                    "http://app.localhost/x", "http://[::1]:9/x", "http://127.9.9.9/x", "https://pixel.example/x",
                    "http://10.0.0.1/x", "http://localhost.evil.example/x", "http://0.0.0.0/x", "http:///x"]


class ConstantsParity(unittest.TestCase):
    def test_states_and_regexes_match(self):
        self.assertEqual(ph.STATES, pa.STATES)
        for name in ("TOOL_RE", "OWNER_ID_RE"):
            a, b = getattr(ph, name), getattr(pa, name)
            self.assertEqual((a.pattern, a.flags), (b.pattern, b.flags), name)

    def test_agent_groups_cover_every_state(self):
        self.assertEqual(set(pa.GROUPS), set(pa.STATES))


class HelperParity(unittest.TestCase):
    def test_identical_helpers_in_both_clients(self):
        for name in ("office_home", "_is_loopback_url", "_urlopen"):
            self.assertEqual(function_ast(HOOK_PY, name), function_ast(AGENT_PY, name), name)

    def test_connector_office_home_is_the_same_code(self):
        self.assertEqual(function_ast(HOOK_PY, "office_home"),
                         function_ast(ROOT / "pixel_connector" / "config.py", "office_home"))

    def test_office_home_semantics(self):
        for env in ({}, {"PIXEL_OFFICE_HOME": "~/elsewhere"}, {"PIXEL_OFFICE_HOME": "/tmp/po-home"}):
            with mock.patch.dict(os.environ, env, clear=False):
                if not env:
                    os.environ.pop("PIXEL_OFFICE_HOME", None)
                self.assertEqual(ph.office_home(), pa.office_home(), env)
                self.assertEqual(ph.office_home(), CFG.office_home(), env)

    def test_loopback_detection_agrees_with_the_connector(self):
        for url in LOOPBACK_VECTORS:
            want = localhttp.is_loopback_url(url)
            self.assertEqual(ph._is_loopback_url(url), want, url)
            self.assertEqual(pa._is_loopback_url(url), want, url)

    def test_loopback_opener_bypasses_the_proxy_everywhere(self):
        """All three send loopback requests through an opener without proxies, everything else through urlopen."""
        for mod, fn in ((ph, ph._urlopen), (pa, pa._urlopen), (localhttp, localhttp.urlopen)):
            built = []

            class Opener:
                def open(self, req, timeout):
                    return ("direct", req)

            def build_opener(*handlers):
                built.append(handlers)
                return Opener()
            with mock.patch.object(urllib.request, "build_opener", build_opener), \
                    mock.patch.object(urllib.request, "urlopen", lambda req, timeout: ("default", req)):
                if mod is localhttp:
                    with mock.patch.object(localhttp, "_DIRECT", Opener()):
                        self.assertEqual(fn("http://127.0.0.1:1/x", 1)[0], "direct")
                else:
                    self.assertEqual(fn("http://127.0.0.1:1/x", 1)[0], "direct")
                    handlers = built[-1]
                    self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {}
                                        for h in handlers), mod.__name__)
                self.assertEqual(fn("https://pixel.example/x", 1)[0], "default")

    def test_status_url_semantics(self):
        """Different signatures (hook: config dict with `server`; agent: resolved target with `url`), same URL."""
        for base, room in (("http://127.0.0.1:8765", None), ("https://pixel.example", "abcdefghijklmnopqrstuvwxyz"),
                           ("https://pixel.example", "a b/c?d")):
            hook = ph.status_url({"server": base + "/", "room": room})
            agent = pa.status_url({"url": base, "room": room})
            self.assertEqual(hook, agent, (base, room))
        self.assertEqual(ph.status_url({}), pa.status_url({"url": pa.DEFAULT_URL}))


class PowerShellParity(unittest.TestCase):
    """pixel_hook.ps1 is not refactored (v2.md F4); its copies of the shared lists/regexes are checked by text."""

    @classmethod
    def setUpClass(cls):
        cls.ps1 = HOOK_PS1.read_text(encoding="utf-8")

    def test_states(self):
        m = re.search(r"^\$STATES = @\(([^)]*)\)", self.ps1, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(tuple(re.findall(r"'([^']*)'", m.group(1))), ph.STATES)

    def test_regexes(self):
        patterns = set(re.findall(r"-cmatch '([^']*)'", self.ps1))
        for rx in (ph.TOOL_RE, ph.OWNER_ID_RE, ph.SESSION_RE):
            self.assertIn(rx.pattern, patterns, rx.pattern)

    def test_office_home_and_status_url(self):
        self.assertIn("if ($env:PIXEL_OFFICE_HOME) { return $env:PIXEL_OFFICE_HOME }", self.ps1)
        self.assertIn("(Join-Path $h '.pixel-office')", self.ps1)
        self.assertIn("$base = 'http://127.0.0.1:8765'", self.ps1)
        self.assertIn("$url = $base.TrimEnd('/') + '/api/status'", self.ps1)
        self.assertIn("$url += '?room=' + [Uri]::EscapeDataString($r)", self.ps1)
        self.assertEqual(pa.DEFAULT_URL, "http://127.0.0.1:8765")


if __name__ == "__main__":
    unittest.main()
