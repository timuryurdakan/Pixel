"""Claude Code hook client (CLOUD.md §7): mapping vectors, privacy fuzz, coalescing, installer, cc_hook."""

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_cloud.app import App, Request
from pixel_cloud.storage import MemoryStorage

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "web" / "hook" / "pixel_hook.py"
HOOK_PS1 = ROOT / "web" / "hook" / "pixel_hook.ps1"
VECTORS = json.loads((Path(__file__).parent / "hook_vectors.json").read_text(encoding="utf-8"))
CANARY = "CANARY-7f3a"


def load_hook():
    spec = importlib.util.spec_from_file_location("pixel_hook_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ph = load_hook()


def _h(salt, raw):
    return hashlib.sha256((salt + raw).encode()).hexdigest()[:20]


def vector_case(vec):
    cfg = dict(VECTORS["cfg"])
    cfg.update(vec.get("cfg") or {})
    cfg = {k: v for k, v in cfg.items() if v is not None}
    salt = cfg["salt"]
    ev = vec["event"]
    subs = {"$S": _h(salt, ev.get("session_id", "")) if isinstance(ev.get("session_id"), str) else _h(salt, ""),
            "$A": _h(salt, ev["agent_id"]) if isinstance(ev.get("agent_id"), str) else None,
            "$S0": _h(salt, "")}
    expected = []
    for item in vec["expect"]:
        d = dict(VECTORS["base"])
        if cfg.get("color") is None:
            d.pop("color", None)
        d.update(item)
        d = {k: subs.get(v, v) if isinstance(v, str) else v for k, v in d.items()}
        expected.append({k: v for k, v in d.items() if v is not None})
    return ev, cfg, vec.get("last"), expected


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t
        self.on_sleep = []          # callbacks run while "sleeping" (simulates a concurrent hook process)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.on_sleep:
            self.on_sleep.pop(0)()


class Recorder:
    def __init__(self, status=200):
        self.calls = []
        self.status = status

    def __call__(self, updates, timeout, retries):
        self.calls.append((copy.deepcopy(updates), timeout, retries))
        return self.status

    @property
    def states(self):
        return [[u["state"] for u in c[0]] for c in self.calls]


def ev(name, **kw):
    d = {"hook_event_name": name, "session_id": "sess-1", "cwd": "/home/u/proj", "transcript_path": "/x.jsonl"}
    d.update(kw)
    return d


class VectorTests(unittest.TestCase):
    def test_at_least_40_vectors(self):
        self.assertGreaterEqual(len(VECTORS["vectors"]), 40)

    def test_every_event_is_covered(self):
        names = {v["event"].get("hook_event_name") for v in VECTORS["vectors"]}
        for e in ph.KNOWN_EVENTS:
            self.assertIn(e, names)

    def test_vectors(self):
        for vec in VECTORS["vectors"]:
            with self.subTest(vec["name"]):
                event, cfg, last, expected = vector_case(vec)
                self.assertEqual(ph.map_event(event, cfg, last), expected)

    def test_vectors_through_map_cli(self):
        vec = VECTORS["vectors"][12]
        event, cfg, last, expected = vector_case(vec)
        out = subprocess.run([sys.executable, str(HOOK), "map"], input=json.dumps(
            {"event": event, "cfg": cfg, "last": last}), capture_output=True, text=True, timeout=20)
        self.assertEqual(json.loads(out.stdout), expected)

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed: PowerShell client not executed")
    def test_vectors_powershell(self):    # pragma: no cover - depends on pwsh
        for vec in VECTORS["vectors"]:
            with self.subTest(vec["name"]):
                event, cfg, last, expected = vector_case(vec)
                out = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(HOOK_PS1), "map"],
                                     input=json.dumps({"event": event, "cfg": cfg, "last": last}),
                                     capture_output=True, text=True, timeout=60)
                self.assertEqual(json.loads(out.stdout), expected, out.stderr)

    def test_ps1_is_ascii(self):
        HOOK_PS1.read_bytes().decode("ascii")
        (ROOT / "web" / "hook" / "install.ps1").read_bytes().decode("ascii")

    def test_python38_syntax(self):
        import ast
        for f in ("pixel_hook.py", "pixel_agent.py"):
            ast.parse((ROOT / "web" / "hook" / f).read_text(encoding="utf-8"), feature_version=(3, 8))


def canary_event(name, **extra):
    c = CANARY
    e = {"hook_event_name": name, "session_id": c + "-session", "prompt_id": c, "transcript_path": "/t/" + c,
         "agent_transcript_path": "/t/" + c, "cwd": "/home/" + c + "/" + c + "/proj", "permission_mode": c,
         "effort": c, "prompt": "secret prompt " + c, "tool_input": {"command": c, "nested": [{"deep": c}]},
         "tool_response": {"stdout": c}, "tool_use_id": c, "last_assistant_message": c, "message": c, "title": c,
         "session_title": c, "scratchpad_dir": "/s/" + c, "error_details": c, "model": c,
         "background_tasks": [c], "session_crons": [c], "reason": c, "trigger": c, "custom_instructions": c,
         "source": c, "notification_type": c, "error": c, "agent_type": c, "tool_name": "Bash"}
    e.update(extra)
    return e


def privacy_events():
    out = []
    for name in ph.KNOWN_EVENTS:
        out.append(canary_event(name))
        out.append(canary_event(name, agent_id=CANARY + "-agent"))
    out += [canary_event("SessionStart", source="startup"),
            canary_event("PreToolUse", tool_name="mcp__" + CANARY + "__tool"),
            canary_event("PermissionRequest", tool_name="mcp__" + CANARY),
            canary_event("Notification", notification_type="permission_prompt"),
            canary_event("Notification", notification_type="idle_prompt"),
            canary_event("PostToolUseFailure", is_interrupt=True),
            canary_event("StopFailure", error="rate_limit"),
            canary_event("SubagentStart", agent_id=CANARY, agent_type="Explore"),
            canary_event("SessionEnd")]
    return out


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_canary_never_leaves_the_machine(self):
        cfg = {"server": "http://x", "name": "Timur", "ownerId": "o_testowner1", "salt": "s" * 32}
        rec = Recorder()
        clock = FakeClock()
        r = ph.HookRunner(cfg, self.tmp, clock=clock, sleep=clock.sleep, post=rec)
        for e in privacy_events():
            for last in (None, "waiting_for_user", "working"):
                self.assertNotIn(CANARY, json.dumps(ph.map_event(e, cfg, last), ensure_ascii=False))
            r.handle(e)
            clock.t += 400
        self.assertTrue(rec.calls)
        sent = json.dumps([c[0] for c in rec.calls], ensure_ascii=False)
        self.assertNotIn(CANARY, sent)
        self.assertNotIn("secret prompt", sent)
        self.assertIn("proj", sent)                                 # only the basename of cwd
        # nothing on disk either
        for p in Path(self.tmp).rglob("*"):
            if p.is_file():
                self.assertNotIn(CANARY, p.read_text(encoding="utf-8", errors="replace"), str(p))
            self.assertNotIn(CANARY, p.name)

    def test_canary_through_real_http_and_log(self):
        """End to end: hook subprocess → real cloud dispatch over HTTP. Bodies and hook.log are canary-free."""
        received = []
        app = App(storage=MemoryStorage(), env={"PIXEL_SECRET": "x" * 40}, mapper="auto")
        from dev_server import DevServer
        srv = DevServer(("127.0.0.1", 0), app, ROOT / "web")
        orig = app.dispatch

        def spy(req):
            if req.method == "POST" and req.path.startswith("/api/status"):
                body = req.rfile.read(int(req.headers.get("Content-Length") or 0))
                received.append(body)
                import io
                req.rfile = io.BytesIO(body)
            return orig(req)
        app.dispatch = spy
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        room = json.loads(app.dispatch(Request("POST", "/api/rooms", {}, body=b"{}", client_ip="1.1.1.1")).body)
        home = Path(self.tmp) / "home"
        home.mkdir()
        (home / "config.json").write_text(json.dumps({"server": base, "room": room["roomId"], "key": room["writeKey"],
                                                      "name": "Timur", "ownerId": "o_testowner1",
                                                      "salt": "a" * 32}))
        env = dict(os.environ, PIXEL_OFFICE_HOME=str(home), HOME=str(Path(self.tmp) / "fakehome"))
        for e in [canary_event("UserPromptSubmit"), canary_event("PermissionRequest"),
                  canary_event("PostToolUse"), canary_event("SubagentStart", agent_id="x", agent_type=CANARY),
                  canary_event("SessionEnd")]:
            p = subprocess.run([sys.executable, str(HOOK), "hook"], input=json.dumps(e), capture_output=True,
                               text=True, env=env, timeout=30)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        self.assertGreaterEqual(len(received), 4)
        blob = b"".join(received).decode("utf-8")
        self.assertNotIn(CANARY, blob)
        self.assertNotIn(CANARY, (home / "hook.log").read_text())
        self.assertIn('"state":"offline"', blob)
        stored = json.dumps(app.storage.read_room(room["roomId"]), ensure_ascii=False)
        self.assertNotIn(CANARY, stored)


class CoalescingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = {"server": "http://x", "name": "Timur", "ownerId": "o_testowner1", "salt": "s" * 32}
        self.clock = FakeClock()
        self.rec = Recorder()

    def runner(self, rec=None):
        return ph.HookRunner(self.cfg, self.tmp, clock=self.clock, sleep=self.clock.sleep, post=rec or self.rec)

    def test_state_change_is_debounced_and_superseded(self):
        r = self.runner()
        self.clock.on_sleep.append(lambda: self.assertEqual(r.handle(ev("PreToolUse", tool_name="Bash")), "sent:200"))
        self.assertEqual(r.handle(ev("UserPromptSubmit")), "superseded")
        self.assertEqual(self.rec.states, [["working"]])
        self.assertEqual(self.rec.calls[0][0][0]["tool"], "Bash")

    def test_debounce_sleeps_one_second(self):
        r = self.runner()
        t0 = self.clock.t
        r.handle(ev("UserPromptSubmit"))
        self.assertEqual(self.clock.t - t0, 1.0)
        self.assertEqual(self.rec.calls[0][1:], (4.0, 1))

    def test_dedupe_and_keepalive(self):
        r = self.runner()
        self.assertEqual(r.handle(ev("Stop")), "sent:200")
        self.clock.t += 100
        self.assertEqual(r.handle(ev("Stop")), "dedupe")
        self.clock.t += 250
        self.assertEqual(r.handle(ev("Stop")), "sent:200")          # > 300 s since the last send
        self.assertEqual(len(self.rec.calls), 2)

    def test_tool_only_throttle(self):
        r = self.runner()
        r.handle(ev("PreToolUse", tool_name="Bash"))
        self.clock.t += 5
        self.assertEqual(r.handle(ev("PreToolUse", tool_name="Read")), "throttled")
        self.clock.t += 10
        self.assertEqual(r.handle(ev("PreToolUse", tool_name="Grep")), "throttled")
        self.clock.t += 20                                            # 35 s after the last send
        self.assertEqual(r.handle(ev("PreToolUse", tool_name="Edit")), "sent:200")
        self.assertEqual([c[0][0]["tool"] for c in self.rec.calls], ["Bash", "Edit"])
        self.assertEqual(self.rec.calls[-1][0][0]["toolCount"], 4)

    def test_subagent_tool_interval_is_60s(self):
        r = self.runner()
        r.handle(ev("SubagentStart", agent_id="ag", agent_type="Explore"))
        r.handle(ev("PreToolUse", agent_id="ag", tool_name="Read"))      # no agent_type: remembered from the record
        n = len(self.rec.calls)
        self.clock.t += 40
        self.assertEqual(r.handle(ev("PreToolUse", agent_id="ag", tool_name="Grep")), "throttled")
        self.clock.t += 25
        self.assertEqual(r.handle(ev("PreToolUse", agent_id="ag", tool_name="Glob")), "sent:200")
        self.assertEqual(len(self.rec.calls), n + 1)
        self.assertEqual(self.rec.calls[-1][0][0]["agentType"], "Explore")

    def test_waiting_always_sends_and_posttooluse_leaves_it(self):
        r = self.runner()
        r.handle(ev("PreToolUse", tool_name="Bash"))
        self.clock.t += 2
        r.handle(ev("PermissionRequest", tool_name="Bash"))
        self.clock.t += 2
        self.assertEqual(r.handle(ev("Notification", notification_type="permission_prompt")), "none")
        self.clock.t += 5
        r.handle(ev("PostToolUse", tool_name="Bash"))
        self.clock.t += 2
        self.assertEqual(r.handle(ev("PostToolUse", tool_name="Bash")), "none")
        self.assertEqual(self.rec.states, [["working"], ["waiting_for_user"], ["thinking"]])

    def test_posttooluse_sees_pending_waiting(self):
        """A PostToolUse arriving inside the waiting debounce window supersedes it (no stuck '!')."""
        r = self.runner()
        r.handle(ev("PreToolUse", tool_name="Bash"))
        self.clock.on_sleep.append(lambda: r.handle(ev("PostToolUse", tool_name="Bash")))
        self.assertEqual(r.handle(ev("PermissionRequest", tool_name="Bash")), "superseded")
        self.assertEqual(self.rec.states, [["working"], ["thinking"]])

    def test_typical_turn_at_most_4_writes(self):
        r = self.runner()
        r.handle(ev("UserPromptSubmit"))
        for i in range(12):
            self.clock.t += 2
            r.handle(ev("PreToolUse", tool_name=["Read", "Grep", "Edit", "Bash"][i % 4]))
            self.clock.t += 1
            r.handle(ev("PostToolUse", tool_name="x"))
        self.clock.t += 3
        r.handle(ev("Stop"))
        self.assertLessEqual(len(self.rec.calls), 4)
        self.assertEqual(self.rec.states[0], ["thinking"])
        self.assertEqual(self.rec.states[-1], ["idle"])

    def test_ages(self):
        r = self.runner()
        r.handle(ev("SessionStart", source="startup"))
        self.clock.t += 100
        r.handle(ev("UserPromptSubmit"))
        u = self.rec.calls[-1][0][0]
        self.assertEqual(u["stateAgeMs"], 0)
        self.assertEqual(u["startedAgeMs"], 101000)    # 1 s debounce of the first event + 100 s

    def test_network_error_keeps_last_and_retries_next_time(self):
        bad = Recorder(status=0)
        r = self.runner(bad)
        r.handle(ev("Stop"))
        self.clock.t += 5
        self.assertNotEqual(r.handle(ev("Stop")), "dedupe")        # nothing was confirmed sent
        self.assertEqual(len(bad.calls), 2)

    def test_session_end_offlines_live_subagents_without_debounce(self):
        r = self.runner()
        r.handle(ev("UserPromptSubmit"))
        r.handle(ev("SubagentStart", agent_id="a1", agent_type="Explore"))
        r.handle(ev("SubagentStart", agent_id="a2", agent_type="Plan"))
        r.handle(ev("SubagentStop", agent_id="a2"))
        t0 = self.clock.t
        self.assertEqual(r.handle(ev("SessionEnd", reason="other")), "sent:200")
        self.assertEqual(self.clock.t, t0)                         # no debounce sleep
        updates, timeout, retries = self.rec.calls[-1]
        self.assertEqual([u["state"] for u in updates], ["offline", "offline", "offline"])
        self.assertEqual(sorted(u["sub"] for u in updates[1:]), sorted([_h("s" * 32, "a1"), _h("s" * 32, "a2")]))
        self.assertEqual((timeout, retries), (ph.END_TIMEOUT, 0))
        self.assertEqual([p for p in Path(self.tmp, "state").glob("*.json")], [])

    def test_ignore_file_and_disabled(self):
        proj = Path(self.tmp) / "secret" / "sub"
        proj.mkdir(parents=True)
        (Path(self.tmp) / "secret" / ".pixel-office-ignore").write_text("")
        r = self.runner()
        self.assertEqual(r.handle(ev("Stop", cwd=str(proj))), "none")
        self.cfg["enabled"] = False
        self.assertEqual(self.runner().handle(ev("Stop")), "none")
        self.assertEqual(self.rec.calls, [])

    def test_state_file_format(self):
        r = self.runner()
        r.handle(ev("Stop"))
        files = list(Path(self.tmp, "state").glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0].name, r"^[0-9a-f]{16}\.json$")
        rec = json.loads(files[0].read_text())
        for k in ("seq", "last", "pending", "since", "started", "tc"):
            self.assertIn(k, rec)
        self.assertEqual(set(rec["last"]), {"st", "tool", "lbl", "proj", "t"})
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)


class RunHookTests(unittest.TestCase):
    def test_always_exit_0_and_silent(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        env = dict(os.environ, PIXEL_OFFICE_HOME=tmp, HOME=tmp)
        for stdin in ("", "not json", "[1,2]", json.dumps(ev("Stop")), "\x00\xff"):
            p = subprocess.run([sys.executable, str(HOOK), "hook"], input=stdin, capture_output=True, text=True,
                               env=env, timeout=20)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        # configured but unreachable server: still 0, silent, logged without content
        Path(tmp, "config.json").write_text(json.dumps({"server": "http://127.0.0.1:9", "name": "T", "salt": "x" * 32}))
        p = subprocess.run([sys.executable, str(HOOK), "hook"], input=json.dumps(ev("Stop")), capture_output=True,
                           text=True, env=env, timeout=30)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        self.assertIn("Stop neterr", Path(tmp, "hook.log").read_text())
        env["PIXEL_OFFICE_DISABLE"] = "1"
        p = subprocess.run([sys.executable, str(HOOK), "hook"], input=json.dumps(ev("UserPromptSubmit")),
                           capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(Path(tmp, "hook.log").read_text().count("\n"), 1)

    def test_post_urls_cloud_and_local(self):
        cfg = {"server": "https://h.example/", "room": "abc", "key": "K" * 32}
        self.assertEqual(ph.status_url(cfg), "https://h.example/api/status?room=abc")
        self.assertEqual(ph.status_url({"server": "http://127.0.0.1:8765"}), "http://127.0.0.1:8765/api/status")


class InstallerTests(unittest.TestCase):
    FOREIGN = {"theme": "dark", "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/audit.sh"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo 'İstem'"}]}]}}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = self.tmp / "claude" / "settings.json"
        self.settings.parent.mkdir()

    def write(self, obj):
        self.settings.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def read(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_merge_preserves_foreign_and_is_idempotent(self):
        self.write(self.FOREIGN)
        ph.install_hooks(str(self.settings), "/usr/bin/python3", ["/h/.pixel-office/pixel_hook.py", "hook"])
        first = self.settings.read_text(encoding="utf-8")
        ph.install_hooks(str(self.settings), "/usr/bin/python3", ["/h/.pixel-office/pixel_hook.py", "hook"])
        self.assertEqual(self.settings.read_text(encoding="utf-8"), first)
        data = self.read()
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["hooks"]["PreToolUse"][0], self.FOREIGN["hooks"]["PreToolUse"][0])
        self.assertEqual(data["hooks"]["Stop"][0], self.FOREIGN["hooks"]["Stop"][0])
        self.assertIn("İstem", first)                                # ensure_ascii=False
        self.assertIn('\n  "hooks"', first)                          # 2-space indent
        for e in ph.KNOWN_EVENTS:
            ours = [g for g in data["hooks"][e] if any(ph._is_ours(h) for h in g["hooks"])]
            self.assertEqual(len(ours), 1, e)
            h = ours[0]["hooks"][0]
            self.assertEqual((h["type"], h["command"], h["args"]),
                             ("command", "/usr/bin/python3", ["/h/.pixel-office/pixel_hook.py", "hook"]))
            if e == "SessionEnd":
                self.assertEqual(h.get("timeout"), 5)
                self.assertNotIn("async", h)
            else:
                self.assertIs(h["async"], True)
            self.assertEqual(ours[0].get("matcher"), "*" if e in ph.MATCHER_EVENTS else None)
        backup = Path(str(self.settings) + ".pixel-backup")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), self.FOREIGN)   # backed up once

    def test_invalid_json_aborts_without_writing(self):
        self.settings.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(ph.SettingsError):
            ph.install_hooks(str(self.settings), "py", ["pixel_hook.py", "hook"])
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{ not json")
        self.assertFalse(Path(str(self.settings) + ".pixel-backup").exists())
        self.write({"hooks": []})
        with self.assertRaises(ph.SettingsError):
            ph.install_hooks(str(self.settings), "py", ["pixel_hook.py", "hook"])

    def test_missing_file_is_created(self):
        res = ph.install_hooks(str(self.settings), "py", ["/x/pixel_hook.py", "hook"])
        self.assertIsNone(res["backup"])
        self.assertEqual(len(self.read()["hooks"]), len(ph.KNOWN_EVENTS))

    def test_uninstall_restores_foreign_only(self):
        mixed = copy.deepcopy(self.FOREIGN)
        mixed["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "py", "args": ["/old/pixel_hook.py"]})
        self.write(mixed)
        ph.install_hooks(str(self.settings), "py", ["/x/pixel_hook.py", "hook"])
        n = ph.uninstall_hooks(str(self.settings))
        self.assertEqual(n, len(ph.KNOWN_EVENTS))                   # the old mixed entry was replaced on install
        self.assertEqual(self.read(), self.FOREIGN)
        self.assertEqual(ph.uninstall_hooks(str(self.settings)), 0)

    def test_uninstall_removes_empty_hooks_object(self):
        ph.install_hooks(str(self.settings), "py", ["/x/pixel_hook.py", "hook"])
        ph.uninstall_hooks(str(self.settings))
        self.assertEqual(self.read(), {})

    def test_shell_command_style(self):
        cmd, args = ph.hook_command("/home/a b/.pixel-office/pixel_hook.py", "shell")
        self.assertIsNone(args)
        self.assertIn("pixel_hook.py", cmd)
        self.assertIn("'/home/a b/.pixel-office/pixel_hook.py'", cmd)

    def test_install_cli_with_temp_home(self):
        home = self.tmp / "office"
        fake_home = self.tmp / "fakehome"
        fake_home.mkdir()
        self.write(self.FOREIGN)
        env = dict(os.environ, PIXEL_OFFICE_HOME=str(home), PIXEL_OFFICE_CLAUDE_SETTINGS=str(self.settings),
                   HOME=str(fake_home), USERPROFILE=str(fake_home))
        args = [sys.executable, str(HOOK), "install", "--server", "http://127.0.0.1:9", "--room",
                "a" * 26, "--key", "K" * 32, "--name", "Timur", "--color", "4", "--no-test"]
        for _ in range(2):
            p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
        cfg = json.loads((home / "config.json").read_text())
        self.assertEqual((cfg["room"], cfg["name"], cfg["color"], cfg["client"]), ("a" * 26, "Timur", 4, "python"))
        self.assertRegex(cfg["ownerId"], r"^o_[A-Za-z0-9_-]{12}$")
        self.assertEqual(len(cfg["salt"]), 32)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE((home / "config.json").stat().st_mode), 0o600)
        self.assertEqual((home / "pixel_hook.py").read_bytes(), HOOK.read_bytes())
        data = self.read()
        h = data["hooks"]["Stop"][-1]["hooks"][0]
        self.assertEqual(h["args"], [str(home / "pixel_hook.py"), "hook"])
        self.assertEqual(h["command"], sys.executable)
        self.assertFalse((fake_home / ".claude").exists())
        # bad args → usage error, nothing written
        p = subprocess.run([sys.executable, str(HOOK), "install", "--server", "ftp://x", "--name", "T"],
                           capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(p.returncode, 2)
        p = subprocess.run([sys.executable, str(HOOK), "uninstall"], capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.read(), self.FOREIGN)


class CcHookEndpointTests(unittest.TestCase):
    """api/cc_hook with the real mapper from web/hook/pixel_hook.py (no longer 503)."""

    def test_real_mapper(self):
        app = App(storage=MemoryStorage(), env={"PIXEL_SECRET": "x" * 40}, mapper="auto")
        room = json.loads(app.dispatch(Request("POST", "/api/rooms", {}, body=b"{}", client_ip="1.1.1.1")).body)
        h = {"Authorization": "Bearer " + room["writeKey"], "X-Pixel-Owner": "Ay%C5%9Fe"}

        def hook(e):
            return app.dispatch(Request("POST", "/api/cc_hook?room=" + room["roomId"], h,
                                        body=json.dumps(e).encode(), client_ip="1.1.1.1"))
        r = hook(canary_event("PreToolUse", tool_name="Bash"))
        self.assertEqual((r.status, json.loads(r.body)), (200, {}))
        hook(canary_event("SubagentStart", agent_id="x", agent_type="Explore"))
        state = json.loads(app.dispatch(Request("GET", "/api/state?room=" + room["roomId"], {})).body)
        agents = sorted(state["agents"], key=lambda a: a["isSubagent"])
        self.assertEqual([(a["state"], a["tool"], a["owner"], a["source"]) for a in agents],
                         [("working", "Bash", "Ayşe", "claude-code"), ("working", None, "Ayşe", "claude-code")])
        self.assertEqual(agents[1]["agentType"], "Explore")
        self.assertNotIn(CANARY, json.dumps(app.storage.read_room(room["roomId"])))
        r = hook(canary_event("SessionEnd"))
        self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
