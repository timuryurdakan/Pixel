"""v2.md H38: a stale engines.claude.path (VS Code updated the Claude Code extension and deleted the old version folder)
is replaced at run time with the setup discovery, persisted atomically (0600) and audited as `engine.path_refresh`.

Temp HOME with fake editor extension folders; the fake `claude` is a shell script that prints its version. No network.
"""

import io
import json
import os
import stat
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import __main__ as CLI
from pixel_connector import config as CFG
from pixel_connector.app import Connector
from pixel_connector.audit import AuditLog
from pixel_connector.engines import claude as CL
from pixel_connector.engines.base import TaskContext

ROOM = "abcdefghijklmnopqrstuvwxyz"
KEY = "k" * 43
POSIX = os.name != "nt"


def nowhere(name, path=None):
    return None


def write_fake_claude(path, version):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("#!/bin/sh\necho '%s (Claude Code)'\n" % version)
    os.chmod(path, 0o755)
    return path


def ext_binary(home, version, editor=".vscode", platform="linux-x64"):
    return os.path.join(home, editor, "extensions", "anthropic.claude-code-%s-%s" % (version, platform), "resources",
                        "native-binary", "claude")


class Events:
    def __init__(self):
        self.end_args = None
        self.done = threading.Event()

    def part(self, p):
        pass

    def live(self, **u):
        pass

    def office(self, state, tool=None, label=None):
        pass

    def end(self, status, result, usage, cost_usd, cost_basis, turns=None, error=None, denials=0):
        self.end_args = {"status": status, "error": error}
        self.done.set()


@unittest.skipUnless(POSIX, "POSIX permissions")
class Discovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = self.tmp.name

    def test_newest_extension_across_editors_numeric_order(self):
        for v in ("2.1.99", "2.1.306", "2.1.304"):
            write_fake_claude(ext_binary(self.h, v), v)
        write_fake_claude(ext_binary(self.h, "2.1.305", ".cursor"), "2.1.305")
        write_fake_claude(ext_binary(self.h, "2.1.300", ".antigravity"), "2.1.300")
        self.assertEqual(CFG.find_claude(self.h, which=nowhere), ext_binary(self.h, "2.1.306"))
        os.remove(ext_binary(self.h, "2.1.306"))
        self.assertEqual(CFG.find_claude(self.h, which=nowhere), ext_binary(self.h, "2.1.305", ".cursor"))

    def test_insiders_extension_found(self):
        write_fake_claude(ext_binary(self.h, "2.1.310", ".vscode-insiders", "darwin-arm64"), "2.1.310")
        self.assertEqual(CFG.find_claude(self.h, which=nowhere),
                         ext_binary(self.h, "2.1.310", ".vscode-insiders", "darwin-arm64"))

    def test_world_writable_candidate_rejected(self):
        old = write_fake_claude(ext_binary(self.h, "2.1.99"), "2.1.99")
        new = write_fake_claude(ext_binary(self.h, "2.1.306"), "2.1.306")
        os.chmod(new, 0o777)
        self.assertFalse(CFG.trusted_binary(new))
        self.assertEqual(CFG.find_claude(self.h, which=nowhere), old)
        os.chmod(new, 0o755)
        os.chmod(os.path.dirname(new), 0o777)                    # the folder: anyone could swap the file
        self.assertFalse(CFG.trusted_binary(new))
        self.assertEqual(CFG.find_claude(self.h, which=nowhere), old)
        os.chmod(os.path.dirname(new), 0o755)
        self.assertEqual(CFG.find_claude(self.h, which=nowhere), new)

    def test_symlink_into_world_writable_dir_rejected(self):
        shared = os.path.join(self.h, "shared")
        target = write_fake_claude(os.path.join(shared, "claude"), "2.1.306")
        os.chmod(shared, 0o777)
        link = os.path.join(self.h, ".local", "bin", "claude")
        os.makedirs(os.path.dirname(link))
        os.symlink(target, link)
        self.assertFalse(CFG.trusted_binary(link))
        self.assertIsNone(CFG.find_claude(self.h, which=nowhere))
        os.chmod(shared, 0o755)
        self.assertEqual(CFG.find_claude(self.h, which=nowhere), link)

    def test_foreign_owner_rejected(self):
        p = write_fake_claude(ext_binary(self.h, "2.1.306"), "2.1.306")
        self.assertTrue(CFG.trusted_binary(p))
        self.assertFalse(CFG.trusted_binary(p, uid=os.getuid() + 4242))     # owned by "someone else"

    def test_workspace_and_relative_path_entries_skipped(self):
        ws = os.path.join(self.h, "proj")
        in_ws = write_fake_claude(os.path.join(ws, "node_modules", ".bin", "claude"), "9.9.9")
        ext = write_fake_claude(ext_binary(self.h, "2.1.306"), "2.1.306")
        self.assertEqual(CFG.find_claude(self.h, which=lambda n, path=None: in_ws, exclude=[ws]), ext)
        self.assertEqual(CFG.find_claude(self.h, which=lambda n, path=None: "node_modules/.bin/claude"), ext)
        self.assertEqual(CFG.find_claude(self.h, which=lambda n, path=None: in_ws), in_ws)    # not a workspace

    def test_nothing_found(self):
        self.assertIsNone(CFG.find_claude(self.h, which=nowhere))

    def test_label_has_no_home_path(self):
        self.assertEqual(CFG.claude_path_label(ext_binary(self.h, "2.1.306")), "anthropic.claude-code-2.1.306-linux-x64")
        self.assertEqual(CFG.claude_path_label(os.path.join(self.h, ".local", "bin", "claude")), "claude")
        self.assertIsNone(CFG.claude_path_label(None))


@unittest.skipUnless(POSIX, "POSIX permissions + shell-script fake binary")
class EngineRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = self.tmp.name
        self.home = CFG.ensure_dir(os.path.join(self.h, "connector"))
        self.stale = ext_binary(self.h, "2.1.301")
        cfg = CFG.initial_config("https://pixel.example.app", ROOM, KEY, "Timur", "Test", self.stale)
        CFG.write_json(os.path.join(self.home, CFG.CONFIG_FILE), cfg)
        self.audit = AuditLog(os.path.join(self.home, CFG.AUDIT_FILE))

    def engine(self, path, discover=None):
        cfg = dict(CFG.DEFAULT_CLAUDE, path=path)
        refresh = lambda old, new, v: CFG.claude_path_refreshed(self.home, self.audit, old, new, v)  # noqa: E731
        return CL.ClaudeEngine(cfg, {}, discover=discover or (lambda: CFG.find_claude(self.h, which=nowhere)),
                               on_path_refresh=refresh)

    def stored_path(self):
        return CFG.read_json(os.path.join(self.home, CFG.CONFIG_FILE))["engines"]["claude"]["path"]

    def test_stale_path_newest_picked_persisted_and_audited(self):
        for v in ("2.1.99", "2.1.304", "2.1.306"):
            write_fake_claude(ext_binary(self.h, v), v)
        eng = self.engine(self.stale)
        av = eng.available()
        new = ext_binary(self.h, "2.1.306")
        self.assertEqual((av["available"], av["version"]), (True, "2.1.306"))
        self.assertEqual(eng.cfg["path"], new)
        self.assertEqual(self.stored_path(), new)
        cfg_file = os.path.join(self.home, CFG.CONFIG_FILE)
        self.assertEqual(stat.S_IMODE(os.stat(cfg_file).st_mode), 0o600)
        self.assertEqual(CFG.read_json(cfg_file)["room"], ROOM)               # the rest of the file is kept
        recs = [r for r in self.audit.read() if r["ev"] == "engine.path_refresh"]
        self.assertEqual(len(recs), 1)
        self.assertEqual({k: recs[0][k] for k in ("engine", "old", "new", "version", "persisted")},
                         {"engine": "claude", "old": "anthropic.claude-code-2.1.301-linux-x64",
                          "new": "anthropic.claude-code-2.1.306-linux-x64", "version": "2.1.306", "persisted": True})
        self.assertNotIn(self.h, json.dumps(recs[0]))                        # labels only, never the home path
        eng.available()                                                      # a working path: no second refresh
        self.assertEqual(len([r for r in self.audit.read() if r["ev"] == "engine.path_refresh"]), 1)

    def test_working_path_untouched(self):
        mine = write_fake_claude(os.path.join(self.h, "opt", "claude"), "2.1.200")
        write_fake_claude(ext_binary(self.h, "2.1.306"), "2.1.306")
        calls = []
        eng = self.engine(mine, discover=lambda: calls.append(1) or ext_binary(self.h, "2.1.306"))
        self.assertEqual(eng.available()["version"], "2.1.200")
        self.assertEqual((eng.cfg["path"], calls, self.stored_path()), (mine, [], self.stale))
        self.assertEqual(self.audit.read(), [])

    def test_not_executable_path_replaced(self):
        cur = write_fake_claude(ext_binary(self.h, "2.1.304"), "2.1.304")
        os.chmod(cur, 0o644)
        write_fake_claude(ext_binary(self.h, "2.1.306"), "2.1.306")
        eng = self.engine(cur)
        self.assertEqual(eng.version(), (2, 1, 306))

    def test_world_writable_replacement_not_used(self):
        new = write_fake_claude(ext_binary(self.h, "2.1.306"), "2.1.306")
        os.chmod(new, 0o777)
        eng = self.engine(self.stale)
        self.assertFalse(eng.available()["available"])
        self.assertEqual((eng.cfg["path"], self.stored_path()), (self.stale, self.stale))

    def test_nothing_found_clear_turkish_error(self):
        eng = self.engine(self.stale)
        self.assertFalse(eng.available()["available"])
        self.assertEqual(self.stored_path(), self.stale)
        self.assertEqual(self.audit.read(), [])
        ws = os.path.join(self.h, "proj")
        os.makedirs(ws)
        agent = {"key": "a1", "name": "Ajan", "engine": "claude", "claudeSession": None,
                 "claudeSessionStarted": False, "claudeTotals": None}
        ev = Events()
        eng.start(TaskContext("t_0123456789", agent, "merhaba", ws, None, "edit", False, None,
                              save_agent=lambda a: None), ev)
        self.assertTrue(ev.done.wait(20))
        self.assertEqual(ev.end_args["status"], "error")
        self.assertEqual(ev.end_args["error"]["code"], "engine_unavailable")
        self.assertIn("Claude Code bulunamadı", ev.end_args["error"]["message"])

    def test_refresh_before_task_after_cached_version(self):
        cur = write_fake_claude(ext_binary(self.h, "2.1.304"), "2.1.304")
        eng = self.engine(cur)
        self.assertEqual(eng.version(), (2, 1, 304))                         # cached for an hour
        os.remove(cur)                                                        # VS Code updated the extension
        write_fake_claude(ext_binary(self.h, "2.1.306"), "2.1.306")
        self.assertEqual(eng.version(), (2, 1, 306))
        self.assertEqual(self.stored_path(), ext_binary(self.h, "2.1.306"))

    def test_without_discover_no_op(self):
        eng = CL.ClaudeEngine(dict(CFG.DEFAULT_CLAUDE, path=self.stale), {})
        self.assertEqual(eng.ensure_path(), self.stale)


@unittest.skipUnless(POSIX, "POSIX permissions + shell-script fake binary")
class ConnectorAndDoctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = self.tmp.name
        self.home = CFG.ensure_dir(os.path.join(self.h, "connector"))
        self.ws = os.path.join(self.h, "proj")
        os.makedirs(self.ws)
        self.stale = ext_binary(self.h, "2.1.301")
        cfg = CFG.initial_config("https://pixel.example.app", ROOM, KEY, "Timur", "Test", self.stale)
        cfg["workspaces"] = [{"id": "pixel", "name": "Pixel", "path": self.ws, "children": False}]
        CFG.write_json(os.path.join(self.home, CFG.CONFIG_FILE), cfg)
        # PATH points into the workspace (an agent could have written that `claude`): never picked.
        ws_bin = os.path.dirname(write_fake_claude(os.path.join(self.ws, "bin", "claude"), "9.9.9"))
        env = mock.patch.dict(os.environ, {"HOME": self.h, "PATH": ws_bin})
        env.start()
        self.addCleanup(env.stop)
        write_fake_claude(ext_binary(self.h, "2.1.306"), "2.1.306")

    def test_device_info_refreshes_and_persists(self):
        c = CFG.load_config(self.home, user_home=self.h)
        self.assertTrue(c.ok, c.errors)
        logs = []
        conn = Connector(self.home, c, start_ctl=False, user_home=self.h, relay_factory=lambda **kw: mock.Mock(),
                         office_sender=lambda *a, **kw: None, log=logs.append, engines={"openai": {}})
        info = conn.device_info()
        self.assertEqual(info["engines"]["claude"]["version"], "2.1.306")
        self.assertEqual(conn.cfg["engines"]["claude"]["path"], ext_binary(self.h, "2.1.306"))
        stored = CFG.read_json(os.path.join(self.home, CFG.CONFIG_FILE))["engines"]["claude"]["path"]
        self.assertEqual(stored, ext_binary(self.h, "2.1.306"))
        self.assertTrue(any("Claude Code yolu yenilendi" in ln for ln in logs))
        self.assertEqual([r["ev"] for r in conn.audit.read()], ["engine.path_refresh"])

    def test_doctor_engine_refreshes(self):
        c = CFG.load_config(self.home, user_home=self.h)
        out = io.StringIO()
        with redirect_stdout(out):
            av = CLI.doctor_claude_engine(self.home, c).available()
        self.assertEqual(av["version"], "2.1.306")
        self.assertIn("Claude Code yolu güncellendi", out.getvalue())
        stored = CFG.read_json(os.path.join(self.home, CFG.CONFIG_FILE))["engines"]["claude"]["path"]
        self.assertEqual(stored, ext_binary(self.h, "2.1.306"))


if __name__ == "__main__":
    unittest.main()
