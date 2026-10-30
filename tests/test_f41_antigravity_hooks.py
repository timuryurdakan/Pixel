"""F4-1: Antigravity hooks (`pixel_antigravity.py hook <Event>`, `--install-hooks`), docs/ARASTIRMA2_ORTAK.md.

The hook must answer Antigravity at once with a valid reply (PreToolUse and Stop need a `decision`), whatever the
input, even without pixel_agent.py next to it; it keeps only state-like fields (a canary in arguments, paths and
errors never reaches its record); the watcher prefers the records over the lagging transcript, never shows an open
tool as idle, and shows "Onay bekliyor olabilir" for a long-open tool that may ask; installing adds only our entry to
the user's hooks.json, keeps everything else, backs it up once and refuses a broken file.
"""

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

import _helpers  # noqa: F401  (sys.path)
from test_phase18_antigravity import AG_PY, CANARY, CID, T0, Brain, FakeClock, WatcherCase, ag, plan, user


def event(**kw):
    d = {"conversationId": CID, "workspacePaths": ["/home/u/projeler/" + CANARY + "/site"],
         "transcriptPath": "/home/u/.gemini/x/" + CANARY, "artifactDirectoryPath": "/tmp/" + CANARY,
         "modelName": "gemini-x"}
    d.update(kw)
    return d


def load(path):
    with open(path) as f:
        return json.load(f)


def read(path):
    with open(path) as f:
        return f.read()


def run_hook(name, payload, home):
    out = io.StringIO()
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    rc = ag.hook_main([name], stdin=io.BytesIO(raw), stdout=out, home=home, clock=lambda: T0)
    return rc, out.getvalue()


class HookReplyTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ag-home-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def record(self):
        d = ag.hook_dir(self.home)
        files = os.listdir(d)
        self.assertEqual(len(files), 1, files)
        return load(os.path.join(d, files[0])), os.path.join(d, files[0])

    def test_each_event_gets_its_required_reply(self):
        want = {"PreToolUse": {"decision": "allow"}, "Stop": {"decision": "stop"}, "SessionStart": {},
                "PreInvocation": {}, "PostInvocation": {}, "PostToolUse": {}}
        for name, reply in want.items():
            rc, out = run_hook(name, event(toolCall={"name": "run_command", "args": {}}), self.home)
            self.assertEqual((rc, json.loads(out)), (0, reply), name)
        self.assertNotEqual(ag.AG_HOOK_REPLY["Stop"]["decision"], "continue")      # never keeps the agent going

    def test_bad_input_still_gets_a_valid_reply_and_no_record(self):
        for payload in (b"", b"{", b"null", b"[]", b"\xff\xfe", json.dumps({"x": 1}).encode(),
                        json.dumps(event(conversationId="")).encode(),
                        json.dumps(event(conversationId="x" * 201)).encode(),
                        b"{" + b" " * (ag.AG_HOOK_STDIN_MAX + 1) + b"}"):
            rc, out = run_hook("PreToolUse", payload, self.home)
            self.assertEqual((rc, json.loads(out)), (0, {"decision": "allow"}), payload[:20])
        self.assertFalse(os.path.exists(ag.hook_dir(self.home)))
        for name in ("", "Unknown", "preToolUse"):
            rc, out = run_hook(name, event(), self.home)
            self.assertEqual((rc, json.loads(out)), (0, {}))

    def test_record_keeps_only_state_fields(self):
        run_hook("PreToolUse", event(toolCall={"name": "run_command",
                                               "args": {"CommandLine": "echo " + CANARY}}, stepIdx=7), self.home)
        rec, path = self.record()
        self.assertEqual(set(rec), {"v", "id", "ev", "at", "tool", "err", "folder"})
        self.assertEqual(rec["folder"], "site")
        self.assertEqual(rec["ev"], "PreToolUse")
        self.assertEqual(rec["tool"], "run_command")
        self.assertNotIn(CANARY, json.dumps(rec))
        self.assertEqual(ag._hook_short("run_command; rm -rf /\n<x>", 48), "run_commandrm-rfx")      # sanitised
        self.assertNotIn(CID, json.dumps(rec))
        self.assertEqual(rec["id"], ag.hashlib.sha256(CID.encode()).hexdigest()[:16])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        run_hook("Stop", event(error="boom " + CANARY, terminationReason=CANARY, fullyIdle=True), self.home)
        rec, _ = self.record()
        self.assertEqual((rec["ev"], rec["err"], rec["tool"]), ("Stop", True, None))
        self.assertNotIn(CANARY, json.dumps(rec))

    def test_the_script_answers_alone_and_fast(self):
        d = tempfile.mkdtemp(prefix="ag-alone-")
        self.addCleanup(shutil.rmtree, d, True)
        script = shutil.copy(AG_PY, os.path.join(d, "pixel_antigravity.py"))       # no pixel_agent.py next to it
        env = dict(os.environ, HOME=self.home)
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, script, "hook", "PreToolUse"], input=json.dumps(event()).encode(),
                           capture_output=True, env=env, timeout=20)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual((r.returncode, json.loads(r.stdout)), (0, {"decision": "allow"}), r.stderr)
        r = subprocess.run([sys.executable, script, "hook", "Stop"], input=b"garbage", capture_output=True, env=env,
                           timeout=20)
        self.assertEqual((r.returncode, json.loads(r.stdout)), (0, {"decision": "stop"}))


class HookWatcherTests(WatcherCase):
    def setUp(self):
        super().setUp()
        self.home = tempfile.mkdtemp(prefix="ag-home-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def hook(self, name, **kw):
        rec = ag.hook_record(name, event(**kw), self.clock())
        ag.write_hook_record(rec, self.home)
        p = os.path.join(ag.hook_dir(self.home), rec["id"] + ".json")
        os.utime(p, (self.clock(), self.clock() + len(name) * 0.001 + kw.get("stepIdx", 0) * 0.01))

    def watcher(self, **kw):
        return super().watcher(hook_home=self.home, **kw)

    def test_hooks_alone_drive_one_agent_with_the_transcript_session(self):
        w = self.watcher()
        self.hook("PreInvocation")
        w.poll()
        a = self.by_name("Antigravity")
        self.assertEqual(a.session, "ag-" + ag.hashlib.sha256(CID.encode()).hexdigest()[:10])
        self.assertEqual(a.last, ("thinking", None, None))
        self.assertEqual(a.project, "site")
        self.clock.t += 1
        self.hook("PreToolUse", toolCall={"name": "view_file", "args": {}}, stepIdx=1)
        w.poll()
        self.assertEqual(a.last, ("working", "Read", None))
        self.clock.t += 1
        self.hook("Stop", fullyIdle=True, stepIdx=2)
        w.poll()
        self.assertEqual(a.last, ("idle", None, None))
        self.brain.write(CID, [user(0), plan(1)])                           # the transcript appears later
        self.clock.t += 1
        w.poll()
        self.assertEqual(len(self.agents), 1)                                # same character, no duplicate
        self.assertEqual(a.last, ("idle", None, None))

    def test_open_tool_is_never_idle_and_may_be_waiting(self):
        w = self.watcher()
        self.hook("PreToolUse", toolCall={"name": "run_command", "args": {}})
        w.poll()
        a = self.by_name("Antigravity")
        self.assertEqual(a.last, ("working", "Bash", None))
        self.clock.t += ag.AG_MAYBE_WAIT - 1
        w.poll()
        self.assertEqual(a.last, ("working", "Bash", None))
        self.clock.t += 2
        w.poll()
        self.assertEqual(a.last, ("waiting_for_user", "Bash", ag.L_MAYBE_WAIT))
        self.clock.t += ag.AG_IDLE_AFTER * 3                                 # still open: not idle
        w.poll()
        self.assertEqual(a.last[0], "waiting_for_user")
        self.hook("PostToolUse", toolCall={"name": "run_command", "args": {}}, stepIdx=1)
        w.poll()
        self.assertEqual(a.last, ("thinking", None, None))

    def test_a_read_tool_never_looks_like_waiting(self):
        w = self.watcher()
        self.hook("PreToolUse", toolCall={"name": "view_file", "args": {}})
        w.poll()
        self.clock.t += ag.AG_MAYBE_WAIT * 5
        w.poll()
        self.assertEqual(self.by_name("Antigravity").last, ("working", "Read", None))

    def test_fresh_hooks_win_over_the_transcript_then_it_takes_over_again(self):
        self.brain.write(CID, [user(0)])
        w = self.watcher()
        w.poll()
        a = self.by_name("Antigravity")
        self.hook("Stop", fullyIdle=True)
        w.poll()
        self.assertEqual(a.last, ("idle", None, None))
        self.clock.t += 1
        self.brain.write(CID, [plan(1, tool="run_command")])                 # lagging transcript says "working"
        w.poll()
        self.assertEqual(a.last, ("idle", None, None))
        self.clock.t += ag.AG_HOOK_FRESH + 1
        self.brain.write(CID, [plan(2, tool="view_file")])
        w.poll()
        self.assertEqual(a.last, ("working", "Read", None))

    def test_broken_foreign_and_stale_records_are_ignored(self):
        d = ag.hook_dir(self.home)
        os.makedirs(d, mode=0o700)
        for name, body in (("0123456789abcdef.json", "{"), ("aaaaaaaaaaaaaaaa.json", json.dumps(
                {"v": 1, "id": "bbbbbbbbbbbbbbbb", "ev": "Stop", "at": T0})), ("x.json", "[]"),
                           ("cccccccccccccccc.json", json.dumps({"v": 1, "id": "cccccccccccccccc", "ev": "Rm",
                                                                 "at": T0})),
                           ("dddddddddddddddd.json", "x" * (ag.AG_HOOK_FILE_MAX + 1))):
            with open(os.path.join(d, name), "w") as f:
                f.write(body)
        w = self.watcher()
        w.poll()
        self.assertEqual(self.agents, [])
        self.hook("Stop", fullyIdle=True)
        self.clock.t += ag.AG_GONE_AFTER + 1
        w2 = self.watcher()
        w2.poll()
        self.assertFalse(os.path.exists(os.path.join(d, ag.hook_record("Stop", event(), T0)["id"] + ".json")))


class InstallHooksTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ag-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.path = os.path.join(self.home, ".gemini", "config", "hooks.json")
        self.logs = []

    def install(self, **kw):
        kw.setdefault("dry_run", True)                  # dry run: no copy into the real ~/.pixel-office
        return ag.install_antigravity_hooks(log=self.logs.append, home=self.home, python="/usr/bin/py thon", **kw)

    def write(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_entry_has_every_event_with_a_quoted_command(self):
        e = ag.hooks_entry("/usr/bin/py thon", "/home/u/.pixel-office/pixel_antigravity.py")
        self.assertEqual(set(e), set(ag.AG_HOOK_EVENTS))
        for ev, handlers in e.items():
            h = handlers[0]["hooks"][0] if ev in ag.AG_TOOL_EVENTS else handlers[0]
            if ev in ag.AG_TOOL_EVENTS:
                self.assertEqual(handlers[0]["matcher"], "*")
            self.assertEqual((h["type"], h["timeout"]), ("command", ag.AG_HOOK_TIMEOUT))
            self.assertTrue(h["command"].startswith("'/usr/bin/py thon' "), h["command"])
            self.assertTrue(h["command"].endswith(" hook " + ev))

    def test_merge_keeps_other_hooks_and_is_idempotent_and_removable(self):
        other = {"my-linter": {"PostToolUse": [{"matcher": "run_command", "hooks": [{"command": "./lint.sh"}]}]}}
        entry = ag.hooks_entry("p", "s")
        once = ag.merge_hooks(json.dumps(other), entry)
        self.assertEqual(json.loads(once), dict(other, **{"pixel-office": entry}))
        self.assertEqual(ag.merge_hooks(once, entry), once)
        self.assertEqual(json.loads(ag.merge_hooks(once, None)), other)
        self.assertEqual(json.loads(ag.merge_hooks(None, entry)), {"pixel-office": entry})
        for bad in ("{", "[]", "3", '"x"'):
            with self.assertRaises(ValueError):
                ag.merge_hooks(bad, entry)

    def test_install_writes_backup_once_refuses_broken_file_and_uninstalls(self):
        self.write({"mine": {"Stop": [{"command": "x"}]}})
        self.assertEqual(self.install(dry_run=True), 0)
        self.assertEqual(load(self.path), {"mine": {"Stop": [{"command": "x"}]}})   # dry run: unchanged
        script_dir = os.path.join(self.home, ".pixel-office")
        with unittest.mock.patch.object(ag, "install_files", lambda: os.path.join(script_dir, ag.AG_SCRIPT)):
            self.assertEqual(self.install(dry_run=False), 0)
            data = load(self.path)
            self.assertEqual(set(data), {"mine", "pixel-office"})
            self.assertEqual(load(self.path + ".pixel-backup"), {"mine": {"Stop": [{"command": "x"}]}})
            self.assertEqual(self.install(dry_run=False), 0)
            self.assertIn("Değişiklik yok", self.logs[-1])
        self.assertEqual(self.install(dry_run=False, uninstall=True), 0)
        self.assertEqual(load(self.path), {"mine": {"Stop": [{"command": "x"}]}})
        self.write("{ bozuk")
        self.assertEqual(self.install(dry_run=False, uninstall=True), 1)
        self.assertEqual(read(self.path), "{ bozuk")

if __name__ == "__main__":
    unittest.main()
