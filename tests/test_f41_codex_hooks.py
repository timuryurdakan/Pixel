"""F4-1: OpenAI Codex CLI hooks through the same client (`pixel_hook.py hook --codex`, `install --target codex`).

Codex sends Claude Code's JSON shape (hook_event_name, session_id, cwd, tool_name …) to the command on stdin; exit 0
with no output means "continue", so the client must print nothing for Codex (no remote --decide), mark updates as
source "codex", map Interrupt to idle, and the installer must add only our entries to ~/.codex/hooks.json (all async,
SessionEnd / Interrupt within Codex's 3 s cap), keep the user's own hooks, and remove only ours.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

import _helpers  # noqa: F401  (sys.path)
from test_hook_client import HOOK, load_hook

CANARY = "CANARY-codex-f41"


def ev(name, **kw):
    d = {"hook_event_name": name, "session_id": "019a-codex-session", "cwd": "/home/u/projeler/site",
         "transcript_path": None, "model": "gpt-x", "permission_mode": "default"}
    d.update(kw)
    return d


class CodexMappingTests(unittest.TestCase):
    def setUp(self):
        self.ph = load_hook()
        self.cfg = {"name": "Timur", "salt": "s" * 32, "ownerId": "o_abcdefgh"}

    def codex(self):
        self.ph.SOURCE = self.ph.CODEX_SOURCE

    def test_events_map_like_claude_with_source_codex(self):
        self.codex()
        cases = [("SessionStart", {"source": "startup"}, "idle"), ("UserPromptSubmit", {"prompt": CANARY}, "thinking"),
                 ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "echo " + CANARY}}, "working"),
                 ("PermissionRequest", {"tool_name": "apply_patch", "tool_input": {"command": CANARY}},
                  "waiting_for_user"),
                 ("Stop", {"last_assistant_message": CANARY, "stop_hook_active": False}, "idle"),
                 ("Interrupt", {"turn_id": "t1"}, "idle"), ("SessionEnd", {"reason": "other"}, "offline")]
        for name, extra, state in cases:
            ups = self.ph.map_event(ev(name, **extra), self.cfg)
            self.assertEqual(len(ups), 1, name)
            self.assertEqual((ups[0]["state"], ups[0]["source"]), (state, "codex"), name)
            self.assertNotIn(CANARY, json.dumps(ups), name)
        self.assertEqual(self.ph.map_event(ev("Interrupt"), self.cfg)[0]["label"], self.ph.L_INTERRUPTED)
        self.assertEqual(self.ph.map_event(ev("PreToolUse", tool_name="Bash"), self.cfg)[0]["tool"], "Bash")

    def test_interrupt_is_codex_only(self):
        self.ph.SOURCE = "claude-code"
        self.assertEqual(self.ph.map_event(ev("Interrupt"), self.cfg), [])

    def test_codex_never_answers_on_stdout(self):
        self.codex()
        home = tempfile.mkdtemp(prefix="ph-home-")
        self.addCleanup(shutil.rmtree, home, True)
        with open(os.path.join(home, "config.json"), "w") as f:
            json.dump({"server": "http://127.0.0.1:9", "room": None, "key": None, "name": "T", "salt": "s" * 32,
                       "enabled": True}, f)
        out = io.StringIO()
        raw = json.dumps(ev("PermissionRequest", tool_name="Bash")).encode()
        called = []
        self.ph.decide_permission = lambda *a: called.append(a) or {"decision": "allow"}
        self.ph._watchdog = lambda seconds: None      # in-process: never arm the hook's SIGALRM exit
        self.ph.HookRunner.handle = lambda self_, e: "none"
        rc = self.ph.run_hook(stdin=io.BytesIO(raw), home=home, decide=True, out=out)
        self.assertEqual((rc, out.getvalue(), called), (0, "", []))     # no remote --decide path for Codex


class CodexEntriesTests(unittest.TestCase):
    def setUp(self):
        self.ph = load_hook()

    def test_every_codex_event_once_async_and_flagged(self):
        e = self.ph.build_codex_entries("/usr/bin/python3 /h/.pixel-office/pixel_hook.py hook")
        self.assertEqual(set(e), set(self.ph.CODEX_EVENTS))
        for name, groups in e.items():
            self.assertEqual(len(groups), 1)
            h = groups[0]["hooks"][0]
            self.assertTrue(h["command"].endswith(" hook --codex"), h)
            self.assertIn(self.ph.MARKER, h["command"])
            if name in ("SessionEnd", "Interrupt"):
                self.assertNotIn("async", h)
                self.assertLessEqual(h["timeout"], 3)
            else:
                self.assertIs(h["async"], True)
            self.assertEqual("matcher" in groups[0], name in self.ph.MATCHER_EVENTS)

    def test_install_keeps_foreign_hooks_and_uninstall_removes_only_ours(self):
        d = tempfile.mkdtemp(prefix="codex-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "hooks.json")
        mine = {"description": "mine", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "./policy.py"}]}]}}
        with open(path, "w") as f:
            json.dump(mine, f)
        data = self.ph.read_settings(path)
        self.ph.merge_settings(data, self.ph.build_codex_entries("py /x/pixel_hook.py hook"))
        self.ph.write_settings(path, data)
        with open(path) as f:
            got = json.load(f)
        self.assertEqual(got["description"], "mine")
        self.assertEqual(got["hooks"]["PreToolUse"][0], mine["hooks"]["PreToolUse"][0])
        self.assertEqual(len(got["hooks"]["PreToolUse"]), 2)
        n = self.ph.uninstall_hooks(path)
        self.assertEqual(n, len(self.ph.CODEX_EVENTS))
        with open(path) as f:
            self.assertEqual(json.load(f)["hooks"]["PreToolUse"], mine["hooks"]["PreToolUse"])

    def test_cli_install_and_uninstall_target_codex(self):
        home = tempfile.mkdtemp(prefix="ph-cli-")
        self.addCleanup(shutil.rmtree, home, True)
        hooks = os.path.join(home, ".codex", "hooks.json")
        env = dict(os.environ, HOME=home, PIXEL_OFFICE_HOME=os.path.join(home, ".pixel-office"))
        r = subprocess.run([sys.executable, str(HOOK), "install", "--server", "http://127.0.0.1:9", "--name", "Timur",
                            "--target", "codex", "--settings-path", hooks, "--no-test"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(hooks) as f:
            self.assertEqual(set(json.load(f)["hooks"]), set(self.ph.CODEX_EVENTS))
        self.assertFalse(os.path.exists(os.path.join(home, ".claude", "settings.json")))   # Claude untouched
        r = subprocess.run([sys.executable, str(HOOK), "install", "--server", "http://127.0.0.1:9", "--name", "T",
                            "--target", "codex", "--force-powershell", "--settings-path", hooks, "--no-test"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, str(HOOK), "uninstall", "--target", "codex", "--settings-path", hooks],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(hooks) as f:
            text = f.read()
        self.assertNotIn(self.ph.MARKER, text)                 # nothing of ours is left
        self.assertFalse(json.loads(text).get("hooks"))

    def test_hook_codex_flag_prints_nothing(self):
        home = tempfile.mkdtemp(prefix="ph-run-")
        self.addCleanup(shutil.rmtree, home, True)
        env = dict(os.environ, HOME=home, PIXEL_OFFICE_HOME=os.path.join(home, ".pixel-office"))
        for name in ("Stop", "PermissionRequest", "Interrupt"):
            r = subprocess.run([sys.executable, str(HOOK), "hook", "--codex"], input=json.dumps(ev(name)).encode(),
                               capture_output=True, env=env, timeout=30)
            self.assertEqual((r.returncode, r.stdout), (0, b""), name)


if __name__ == "__main__":
    unittest.main()


# -- Cursor -----------------------------------------------------------------------------------------------------------
def cursor_ev(name, **kw):
    d = {"hook_event_name": name, "conversation_id": "conv-123", "generation_id": "g1", "model": "x",
         "cursor_version": "2.0", "workspace_roots": ["/home/u/projeler/site"], "user_email": "a@b.example",
         "transcript_path": None}
    d.update(kw)
    return d


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.ph = load_hook()

    def test_only_non_permission_events_are_registered(self):
        permission = {"preToolUse", "beforeShellExecution", "beforeMCPExecution", "beforeReadFile",
                      "beforeTabFileRead", "subagentStart"}
        e = self.ph.build_cursor_entries("py /x/pixel_hook.py hook")
        self.assertEqual(set(e), set(self.ph.CURSOR_EVENTS))
        self.assertEqual(set(e) & permission, set())
        for lst in e.values():
            self.assertTrue(lst[0]["command"].endswith(" hook --cursor"))
            self.assertLessEqual(lst[0]["timeout"], 10)

    def test_payload_is_reduced_to_state_fields(self):
        cases = {"beforeSubmitPrompt": ("UserPromptSubmit", None), "postToolUse": ("PreToolUse", "Shell"),
                 "afterShellExecution": ("PreToolUse", "Bash"), "afterFileEdit": ("PreToolUse", "Edit"),
                 "stop": ("Stop", None), "sessionStart": ("SessionStart", None), "sessionEnd": ("SessionEnd", None)}
        for name, (mapped, tool) in cases.items():
            ev = self.ph.cursor_event(cursor_ev(name, prompt=CANARY, command="rm " + CANARY, file_path="/" + CANARY,
                                                tool_name="Shell", tool_input={"c": CANARY}, text=CANARY))
            self.assertEqual(ev["hook_event_name"], mapped, name)
            self.assertEqual(ev.get("tool_name"), tool, name)
            self.assertEqual(ev["session_id"], "conv-123")
            self.assertEqual(ev["cwd"], "/home/u/projeler/site")
            self.assertNotIn(CANARY, json.dumps(ev), name)
            self.assertNotIn("a@b.example", json.dumps(ev), name)
        for bad in (cursor_ev("preToolUse"), cursor_ev("stop", conversation_id="x y"), cursor_ev("stop",
                    conversation_id=None), {"hook_event_name": "stop"}, [], None):
            self.assertIsNone(self.ph.cursor_event(bad))

    def test_hook_replies_valid_json_at_once_and_spawns_only_state(self):
        spawned = []
        for name, reply in (("beforeSubmitPrompt", {"continue": True}), ("stop", {}), ("postToolUse", {}),
                            ("preToolUse", {}), ("garbage", {})):
            out = io.StringIO()
            rc = self.ph.run_cursor_hook(stdin=io.BytesIO(json.dumps(cursor_ev(name, prompt=CANARY)).encode()),
                                         out=out, spawn=spawned.append)
            self.assertEqual((rc, json.loads(out.getvalue())), (0, reply), name)
        out = io.StringIO()
        self.assertEqual(self.ph.run_cursor_hook(stdin=io.BytesIO(b"{bozuk"), out=out, spawn=spawned.append), 0)
        self.assertEqual(json.loads(out.getvalue()), {})
        self.assertEqual(len(spawned), 3)                       # preToolUse / garbage / broken: nothing spawned
        for argv in spawned:
            self.assertEqual(argv[2], self.ph.CURSOR_POST)
            self.assertNotIn(CANARY, " ".join(argv))

    def test_merge_keeps_foreign_entries_and_removes_only_ours(self):
        data = {"version": 1, "hooks": {"afterFileEdit": [{"command": ".cursor/hooks/format.sh"}]}}
        self.ph.merge_cursor(data, self.ph.build_cursor_entries("py /x/pixel_hook.py hook"))
        self.assertEqual(data["hooks"]["afterFileEdit"][0], {"command": ".cursor/hooks/format.sh"})
        self.assertEqual(len(data["hooks"]["afterFileEdit"]), 2)
        self.ph.merge_cursor(data, self.ph.build_cursor_entries("py /x/pixel_hook.py hook"))   # idempotent
        self.assertEqual(len(data["hooks"]["afterFileEdit"]), 2)
        self.assertEqual(self.ph.merge_cursor(data, None), len(self.ph.CURSOR_EVENTS))
        self.assertEqual(data, {"version": 1, "hooks": {"afterFileEdit": [{"command": ".cursor/hooks/format.sh"}]}})

    def test_cli_install_target_cursor_and_script_reply(self):
        home = tempfile.mkdtemp(prefix="ph-cur-")
        self.addCleanup(shutil.rmtree, home, True)
        hooks = os.path.join(home, ".cursor", "hooks.json")
        env = dict(os.environ, HOME=home, PIXEL_OFFICE_HOME=os.path.join(home, ".pixel-office"),
                   PIXEL_OFFICE_DISABLE="1")
        r = subprocess.run([sys.executable, str(HOOK), "install", "--server", "http://127.0.0.1:9", "--name", "T",
                            "--target", "cursor", "--settings-path", hooks, "--no-test"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(hooks) as f:
            data = json.load(f)
        self.assertEqual((data["version"], set(data["hooks"])), (1, set(self.ph.CURSOR_EVENTS)))
        r = subprocess.run([sys.executable, str(HOOK), "hook", "--cursor"],
                           input=json.dumps(cursor_ev("beforeSubmitPrompt")).encode(), capture_output=True, env=env,
                           timeout=30)
        self.assertEqual((r.returncode, json.loads(r.stdout)), (0, {"continue": True}))
        r = subprocess.run([sys.executable, str(HOOK), "uninstall", "--target", "cursor", "--settings-path", hooks],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(hooks) as f:
            self.assertNotIn(self.ph.MARKER, f.read())

    def test_post_half_marks_source_cursor(self):
        home = tempfile.mkdtemp(prefix="ph-post-")
        self.addCleanup(shutil.rmtree, home, True)
        with open(os.path.join(home, "config.json"), "w") as f:
            json.dump({"server": "http://127.0.0.1:9", "name": "T", "salt": "s" * 32, "enabled": True}, f)
        seen = []
        self.ph.HookRunner.handle = lambda self_, e: seen.append((e, self.ph.SOURCE))
        self.ph.cmd_cursor_post(json.dumps({"hook_event_name": "Stop", "session_id": "conv-1"}), home=home)
        self.assertEqual(seen, [({"hook_event_name": "Stop", "session_id": "conv-1"}, "cursor")])
        self.ph.cmd_cursor_post("{bozuk", home=home)
        self.ph.cmd_cursor_post(json.dumps({"hook_event_name": "rm -rf"}), home=home)
        self.assertEqual(len(seen), 1)
