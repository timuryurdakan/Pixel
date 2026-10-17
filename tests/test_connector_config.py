"""Phase 13: connector.json validation (V3.md §9.2) and the workspace allow-list (§6.4)."""

import copy
import json
import os
import stat
import tempfile
import unittest

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import config as CFG
from pixel_connector import workspaces as WS

ROOM = "abcdefghijklmnopqrstuvwxyz"


def fake_which(name):
    return {"sleep": "/bin/sleep", "true": "/bin/true", "systemctl": "/usr/bin/systemctl"}.get(name)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.realpath(self.tmp.name)       # plays the user's home directory
        self.proj = os.path.join(self.home, "projeler", "pixel")
        os.makedirs(os.path.join(self.proj, "src"))
        os.makedirs(os.path.join(self.home, ".ssh"))
        os.makedirs(os.path.join(self.home, "outside"))

    def tearDown(self):
        self.tmp.cleanup()

    def base(self, **kw):
        c = CFG.initial_config("https://pixel.example.app", ROOM, "k" * 40, "Timur", "Masaüstü", None)
        c["workspaces"] = [{"id": "pixel", "name": "Pixel", "path": "~/projeler/pixel", "children": True}]
        c.update(kw)
        return c

    def check(self, raw):
        return CFG.validate(raw, self.home, fake_which)


class ConfigTests(Base):
    def test_valid_defaults(self):
        d, errors, warnings = self.check(self.base())
        self.assertEqual(errors, [])
        self.assertEqual(d["workspaces"][0]["real"], self.proj)
        self.assertEqual(d["confirm"]["task"], "never")
        self.assertEqual(d["limits"]["maxConcurrent"], 2)
        self.assertFalse(d["profiles"]["auto"]["enabled"])
        self.assertEqual(d["profiles"]["dev"]["allow"], ["Bash(git status *)", "Bash(git diff *)"])
        self.assertEqual(d["approvalPort"], 8766)
        self.assertFalse(d["audit"]["storePrompts"])

    def test_bypass_permissions_anywhere_is_an_error(self):
        for mutate in (lambda c: c["profiles"]["dev"]["allow"].append("bypassPermissions"),
                       lambda c: c["profiles"].update(dev={"allow": ["Bash(x --dangerously-skip-permissions)"]}),
                       lambda c: c.update(permissionMode="bypassPermissions"),
                       lambda c: c["engines"]["claude"].update(extraArgs=["--dangerously-skip-permissions"]),
                       lambda c: c["runtimes"].append({"id": "x", "start": ["true", "--allow-dangerously-skip-permissions"]})):
            c = self.base()
            mutate(c)
            with self.subTest(c=json.dumps(c)[:80]):
                _, errors, _ = self.check(c)
                self.assertTrue(any("can never be configured" in e for e in errors), errors)

    def test_runtime_argv_rules(self):
        c = self.base(runtimes=[
            {"id": "sleeper", "name": "Uyku", "start": ["sleep", "0"], "stop": ["true"]},
            {"id": "abs", "start": ["/opt/x/bin/server", "--port", "1"]},
            {"id": "bad1", "start": "sleep 1"},
            {"id": "bad2", "start": []},
            {"id": "bad3", "start": ["nosuchprog"]},
            {"id": "bad4", "start": ["./relative"]},
            {"id": "bad5", "start": ["sleep", "a\x00b"]},
            {"id": "Bad ID", "start": ["true"]},
            {"id": "sleeper", "start": ["true"]}])
        d, errors, _ = self.check(c)
        self.assertEqual(d["runtimes"][0]["start"], ["/bin/sleep", "0"])         # resolved and stored
        self.assertEqual(d["runtimes"][1]["start"], ["/opt/x/bin/server", "--port", "1"])
        for frag in ("runtimes[2].start", "runtimes[3].start", "runtimes[4].start: program 'nosuchprog'",
                     "runtimes[5].start", "runtimes[6].start", "runtimes[7]: id", "runtimes[8]: duplicate"):
            self.assertTrue(any(e.startswith(frag) for e in errors), (frag, errors))

    def test_loopback_rules(self):
        c = self.base(runtimes=[{"id": "o", "kind": "ollama", "url": "http://10.0.0.5:11434",
                                 "status": {"http": "http://example.com/api/version"}},
                                {"id": "o2", "kind": "ollama", "url": "http://10.0.0.5:11434", "allowRemote": True}])
        c["engines"]["ollama"]["url"] = "http://192.168.1.2:11434"
        c["engines"]["openai"] = [{"id": "lm", "url": "http://[::1]:1234/v1"}, {"id": "rem", "url": "https://api.x.com/v1"}]
        d, errors, _ = self.check(c)
        for frag in ("runtimes[0].url", "runtimes[0].status.http", "engines.ollama.url", "engines.openai[1].url"):
            self.assertTrue(any(e.startswith(frag) for e in errors), (frag, errors))
        self.assertEqual(d["runtimes"][1]["url"], "http://10.0.0.5:11434")
        self.assertEqual(d["engines"]["openai"][0]["url"], "http://[::1]:1234/v1")

    def test_server_room_key(self):
        _, errors, _ = self.check(self.base(server="http://evil.example", room="short", key="a b"))
        self.assertEqual(len([e for e in errors if e.split(":")[0] in ("server", "room", "key")]), 3)
        _, errors, _ = self.check(self.base(server="http://127.0.0.1:8787"))
        self.assertEqual(errors, [])

    def test_profiles_models_budgets(self):
        c = self.base(budgets=[{"scope": "device", "period": "day", "unit": "usd", "limit": 10, "action": "block"},
                               {"scope": "galaxy", "period": "day", "unit": "usd", "limit": 1},
                               {"scope": "engine:ollama", "period": "year", "unit": "tokens", "limit": 5}])
        c["profiles"]["dev"]["allow"] = ["Bash(npm test *)", "Bash", "rm -rf /"]
        c["engines"]["claude"]["defaultModel"] = "--model"
        d, errors, warnings = self.check(c)
        self.assertEqual(d["budgets"], [{"scope": "device", "period": "day", "unit": "usd", "limit": 10.0,
                                         "action": "block", "locked": True}])
        self.assertTrue(any(e.startswith("budgets[1]") for e in errors))
        self.assertTrue(any(e.startswith("budgets[2]") for e in errors))
        self.assertTrue(any(e.startswith("profiles.dev.allow[2]") for e in errors))
        self.assertTrue(any("bare \"Bash\"" in w for w in warnings))
        self.assertTrue(any(e.startswith("engines.claude.defaultModel") for e in errors))
        self.assertIsNone(CFG.MODEL_RE.match("-x"))
        self.assertIsNotNone(CFG.MODEL_RE.match("qwen2.5-coder:7b"))
        self.assertIsNotNone(CFG.MODEL_RE.match("hf.co/org/model:Q4_K_M"))

    def test_workspace_deny_list(self):
        for path in ("~", "/", "~/.ssh", "~/.ssh/keys", "~/.config/x", "~/.pixel-office", "~/.claude", "/etc",
                     "/usr/lib", "relative/path"):
            c = self.base(workspaces=[{"id": "w", "name": "W", "path": path}])
            with self.subTest(path=path):
                _, errors, _ = self.check(c)
                self.assertTrue(errors, path)

    def test_symlinked_workspace_into_denied_dir(self):
        os.symlink(os.path.join(self.home, ".ssh"), os.path.join(self.home, "innocent"))
        _, errors, _ = self.check(self.base(workspaces=[{"id": "w", "name": "W", "path": "~/innocent"}]))
        self.assertTrue(any("not allowed" in e for e in errors), errors)

    def test_env_vars_not_expanded(self):
        os.environ["PIXEL_TEST_WS"] = self.proj
        try:
            _, errors, _ = self.check(self.base(workspaces=[{"id": "w", "name": "W", "path": "$PIXEL_TEST_WS"}]))
        finally:
            del os.environ["PIXEL_TEST_WS"]
        self.assertTrue(errors)

    def test_load_files_and_modes(self):
        home = os.path.join(self.home, "conn")
        CFG.ensure_dir(home)
        CFG.write_json(os.path.join(home, CFG.CONFIG_FILE), self.base())
        cfg = CFG.load_config(home, fake_which, self.home)
        self.assertTrue(cfg.ok, cfg.errors)
        self.assertEqual(len(cfg.hash), 64)
        self.assertEqual(stat.S_IMODE(os.stat(home).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(home, CFG.CONFIG_FILE)).st_mode), 0o600)
        with open(os.path.join(home, CFG.CONFIG_FILE), "w") as f:
            f.write("{nope")
        self.assertFalse(CFG.load_config(home).ok)
        self.assertFalse(CFG.load_config(os.path.join(self.home, "missing")).ok)


class WorkspaceTests(Base):
    def ws(self, children=True):
        return WS.Workspaces([{"id": "pixel", "name": "Pixel", "path": self.proj, "children": children}], self.home)

    def test_resolve_and_subs(self):
        w = self.ws()
        self.assertEqual(w.resolve("pixel"), self.proj)
        self.assertEqual(w.resolve("pixel", "src"), os.path.join(self.proj, "src"))
        self.assertEqual(w.resolve("pixel", "./src/"), os.path.join(self.proj, "src"))
        for bad in ("../outside", "src/../../outside", "/etc", "C:\\Windows", "\\\\server\\share", "a\x00b",
                    "x" * 201, "nope", ".."):
            with self.subTest(sub=bad):
                with self.assertRaises(WS.WorkspaceDenied):
                    w.resolve("pixel", bad)
        with self.assertRaises(WS.WorkspaceDenied):
            w.resolve("other")
        with self.assertRaises(WS.WorkspaceDenied):
            self.ws(children=False).resolve("pixel", "src")

    def test_symlink_escape(self):
        os.symlink(os.path.join(self.home, "outside"), os.path.join(self.proj, "link"))
        os.symlink(os.path.join(self.home, ".ssh"), os.path.join(self.proj, "keys"))
        os.symlink(os.path.join(self.proj, "src"), os.path.join(self.proj, "inside"))
        w = self.ws()
        with self.assertRaises(WS.WorkspaceDenied):
            w.resolve("pixel", "link")
        with self.assertRaises(WS.WorkspaceDenied):
            w.resolve("pixel", "keys")
        self.assertEqual(w.resolve("pixel", "inside"), os.path.join(self.proj, "src"))   # symlink staying inside
        self.assertEqual(w.children("pixel"), ["inside", "src"])

    def test_reresolve_catches_swap(self):
        w = self.ws()
        sub = os.path.join(self.proj, "swap")
        os.makedirs(sub)
        self.assertEqual(w.resolve("pixel", "swap"), sub)
        os.rmdir(sub)
        os.symlink(os.path.join(self.home, "outside"), sub)
        with self.assertRaises(WS.WorkspaceDenied):
            w.resolve("pixel", "swap")

    def test_public_list_has_no_paths(self):
        for i in range(60):
            os.makedirs(os.path.join(self.proj, "d%02d" % i))
        os.makedirs(os.path.join(self.proj, ".git"))
        lst = self.ws().public_list()
        self.assertEqual(lst[0]["id"], "pixel")
        self.assertEqual(len(lst[0]["children"]), 50)
        self.assertNotIn(".git", lst[0]["children"])
        self.assertNotIn(self.home, json.dumps(lst))


if __name__ == "__main__":
    unittest.main()
