"""Phase 19 (Package 5): remote approvals — Claude Code PermissionRequest hook → local connector → E2E → paired browser.

Unit tests cover the request/answer validation (allow-lists, sizes, invisible characters), first-answer-wins, replay,
expiry and withdrawal in `approvals.py`, the hook's summary/decision mapping and the settings entry. Integration tests
run the real connector (dev_relay + dev server, temp homes, `start_ctl`) with FakeBrowsers and the real
`web/hook/pixel_hook.py hook --decide` as a subprocess: allow/deny/question answers come back in Claude Code's
PermissionRequest output format; timeout, feature off, a vanished hook and anything unexpected give no output.
Nothing touches ~/.claude or ~/.pixel-office (PIXEL_OFFICE_HOME and PIXEL_OFFICE_CLAUDE_SETTINGS point to temp dirs).
"""

import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.parse import urlsplit

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E

from pixel_connector import __main__ as CLI
from pixel_connector import approvals as A
from pixel_connector import config as CFG
from pixel_connector.approvals import ctl_request

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "web" / "hook" / "pixel_hook.py"
sys.path.insert(0, str(HOOK.parent))
import pixel_hook as PH  # noqa: E402

ITER = 1000
NO_OLLAMA = {"ollama": {"url": "http://127.0.0.1:9", "enabled": False}}     # never probe a real local Ollama
SECRET_CMD = "rm -rf /home/x/gizli-proje/build && echo CANARY-7f3a"
QUESTION_INPUT = {"questions": [
    {"question": "Hangi çatı?", "header": "Çatı", "multiSelect": False,
     "options": [{"label": "React", "description": "Bileşen kütüphanesi"}, {"label": "Vue", "description": "Aşamalı"}]},
    {"question": "Hangi araçlar?", "header": "Araçlar", "multiSelect": True,
     "options": [{"label": "ESLint"}, {"label": "Prettier"}, {"label": "Vitest"}]}]}


def tool_req(**kw):
    d = {"ask": "tool", "tool": "Bash", "summary": "npm test", "agent": "Claude Code", "project": "pixel",
         "session": "a" * 20, "match": "b" * 32}
    d.update(kw)
    return d


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


# ----------------------------------------------------------------------------------------------------------------------
# approvals.py
# ----------------------------------------------------------------------------------------------------------------------

class RequestValidation(unittest.TestCase):
    def test_tool_request_is_normalized(self):
        r = A.validate_tool_request(tool_req(summary="ls\x1b[31m -la\u202e\u200b  x\n y", agent=None, project=None))
        self.assertEqual(r["summary"], "ls [31m -la x y")
        self.assertEqual(r["agent"], "Claude Code")
        self.assertIsNone(r["project"])
        self.assertEqual(len(A.validate_tool_request(tool_req(summary="x" * 4000))["summary"]), A.SUMMARY_MAX)

    def test_negatives(self):
        bad = [tool_req(tool="Bash; rm"), tool_req(tool=""), tool_req(tool="x" * 65), tool_req(ask="plan"),
               tool_req(summary="x" * 5000), tool_req(summary=5), tool_req(session="A" * 20), tool_req(session="a" * 19),
               tool_req(match="zz"), tool_req(questions=[]), tool_req(agent=["x"]), "not a dict",
               tool_req(ask="question"), tool_req(ask="question", questions=[{}]),
               tool_req(ask="question", questions=[QUESTION_INPUT["questions"][0]] * 5),
               tool_req(ask="question", questions=[{"question": "q", "options": [{"label": "a"}] * 7}]),
               tool_req(ask="question", questions=[{"question": "q", "options": [{"label": " \u200b "}]}]),
               tool_req(ask="question", questions=[{"question": "", "options": [{"label": "a"}]}]),
               tool_req(ask="question", questions=[{"question": "q", "multiSelect": "yes", "options": [{"label": "a"}]}])]
        for b in bad:
            with self.subTest(b=str(b)[:80]):
                with self.assertRaises(ValueError):
                    A.validate_tool_request(b)

    def test_answers_are_option_indices(self):
        qs = A.validate_tool_request(tool_req(ask="question", questions=QUESTION_INPUT["questions"],
                                              summary=""))["questions"]
        self.assertEqual(A.check_answers(qs, [[1], [2, 0]]), [[1], [0, 2]])
        for bad in ([[0, 1], [0]], [[0]], [[0], []], [[2], [0]], [[0], [0, 0]], [[True], [0]], [["0"], [0]],
                    [[0], [3]], None, {"Hangi çatı?": "React"}, [[0], [0], [0]]):
            with self.subTest(bad=bad):
                self.assertIsNone(A.check_answers(qs, bad))


class ApprovalsCore(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.ap = A.Approvals(120, notify_fn=lambda n: None, clock=self.clock)
        self.done = []

    def req(self, **kw):
        return self.ap.request_tool(A.validate_tool_request(tool_req(**kw)), 60,
                                    lambda p, res: self.done.append((p.id, res)))

    def test_single_use_first_answer_wins(self):
        p = self.req()
        self.assertRegex(p.id, A.TOOL_ID_RE)
        self.assertEqual(self.ap.answer(p.id, True, by="b1"), "ok")
        self.assertEqual(self.ap.answer(p.id, False, by="b2"), "unknown")      # replay / second device
        self.assertEqual(self.done, [(p.id, "ok")])
        self.assertEqual((p.outcome()["decision"], p.by), ("allow", "b1"))
        self.assertTrue(p.done.is_set())

    def test_ids_are_random_and_distinct(self):
        ids = {self.req().id for _ in range(A.MAX_TOOL_PENDING)}
        self.assertEqual(len(ids), A.MAX_TOOL_PENDING)
        with self.assertRaises(A.ToolLimit):
            self.req()

    def test_question_needs_valid_answers(self):
        p = self.req(ask="question", questions=QUESTION_INPUT["questions"], summary="")
        self.assertEqual(self.ap.answer(p.id, True), "bad_answer")             # CLI / page can't approve a question
        self.assertEqual(self.ap.answer(p.id, True, [[5], [0]]), "bad_answer")
        self.assertFalse(self.ap.resolve(p.id, True))
        self.assertEqual(self.ap.answer(p.id, True, [[1], [0, 2]]), "ok")
        self.assertEqual(p.outcome(), {"decision": "allow", "id": p.id, "answers": [[1], [0, 2]]})

    def test_answers_on_a_tool_request_are_refused(self):
        p = self.req()
        self.assertEqual(self.ap.answer(p.id, True, [[0]]), "bad_answer")
        self.assertEqual(self.ap.answer(p.id, False, [[0]]), "bad_answer")
        self.assertEqual(self.ap.answer(p.id, False), "ok")
        self.assertEqual(p.outcome()["decision"], "deny")

    def test_remote_can_not_answer_local_confirmations(self):
        local = self.ap.request("agentCreate", "Yeni ajan", None, "x", lambda r: self.done.append(("local", r)))
        self.assertEqual(self.ap.answer(local.id, True, by="bid", tool_only=True), "unknown")
        self.assertEqual(self.ap.answer(local.id, True, by="cli"), "ok")
        self.assertEqual(self.done, [("local", "ok")])

    def test_expiry(self):
        p = self.req()
        self.clock.t += 59
        self.ap.tick()
        self.assertFalse(p.done.is_set())
        self.clock.t += 2
        self.ap.tick()
        self.assertEqual(p.outcome()["decision"], "timeout")
        self.assertEqual(self.ap.answer(p.id, True), "unknown")

    def test_withdraw_by_session_and_match(self):
        p1, p2 = self.req(match="c" * 32), self.req(match="d" * 32)
        other = self.req(session="e" * 20)
        self.assertEqual(self.ap.withdraw("a" * 20, "c" * 32), [p1.id])
        self.assertEqual(self.ap.withdraw("a" * 20), [p2.id])
        self.assertEqual(p1.outcome()["decision"], "withdrawn")
        self.assertFalse(other.done.is_set())
        self.assertEqual(self.ap.withdraw(pid=other.id), [other.id])

    def test_list_shows_kind_and_wire_fields(self):
        p = self.req()
        row = self.ap.list()[0]
        self.assertEqual((row["kind"], row["id"], row["tool"], row["summary"]), ("toolApproval", p.id, "Bash", "npm test"))
        self.assertEqual(row["title"], "İzin: Bash")


class ConfigAndCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        cfg = CFG.initial_config("https://x.example", "a" * 26, "k" * 20, "Timur")
        CFG.write_json(os.path.join(self.home, CFG.CONFIG_FILE), cfg)
        self.settings = os.path.join(self.home, "settings.json")
        os.environ["PIXEL_OFFICE_CLAUDE_SETTINGS"] = self.settings

    def tearDown(self):
        os.environ.pop("PIXEL_OFFICE_CLAUDE_SETTINGS", None)
        self.tmp.cleanup()

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main(["--home", self.home] + list(argv))
        return rc, out.getvalue() + err.getvalue()

    def test_default_off_and_on_off_status(self):
        self.assertEqual(CFG.remote_approvals_state(self.home), {"enabled": False, "timeoutS": 240})
        rc, out = self.cli("remote-approvals")
        self.assertEqual(rc, 0)
        self.assertIn("Uzaktan onay: kapalı", out)
        rc, out = self.cli("remote-approvals", "on")
        self.assertIn("açıldı", out)
        self.assertIn("Kancayı yeniden kurun", out)                 # no settings.json entry yet
        self.assertTrue(CFG.remote_approvals_state(self.home)["enabled"])
        self.assertTrue(CFG.load_config(self.home).ok)
        self.cli("remote-approvals", "off")
        self.assertFalse(CFG.remote_approvals_state(self.home)["enabled"])

    def test_only_literal_true_enables_and_bounds(self):
        for raw, exp in (({"enabled": "true"}, False), ({"enabled": 1}, False), ({"enabled": True}, True)):
            self.assertEqual(CFG.remote_approvals_from({"remoteApprovals": raw})["enabled"], exp)
        self.assertEqual(CFG.remote_approvals_from({"remoteApprovals": {"timeoutS": 9999}})["timeoutS"], 280)    # clamped
        with open(os.path.join(self.home, CFG.CONFIG_FILE), "w") as f:
            f.write("{broken")
        self.assertFalse(CFG.remote_approvals_state(self.home)["enabled"])

    def test_hook_entry_state(self):
        self.assertEqual(CLI.hook_entry_state(self.settings), "missing")
        PH.install_hooks(self.settings, sys.executable, ["/h/.pixel-office/pixel_hook.py", "hook"], decide=False)
        self.assertEqual(CLI.hook_entry_state(self.settings), "old")
        PH.install_hooks(self.settings, sys.executable, ["/h/.pixel-office/pixel_hook.py", "hook"])
        self.assertEqual(CLI.hook_entry_state(self.settings), "ok")
        self.assertEqual(CLI.hook_entry_state(self.settings, wait_s=300), "old")     # timeout must exceed the wait
        PH.install_hooks(self.settings, "/usr/bin/python3 /h/.pixel-office/pixel_hook.py hook", None)
        self.assertEqual(CLI.hook_entry_state(self.settings), "ok")                  # shell-string form
        with open(self.settings, "w") as f:
            f.write("{nope")
        self.assertEqual(CLI.hook_entry_state(self.settings), "unreadable")

    def test_switch_is_not_reachable_remotely(self):
        """Neither an E2E handler nor a /ctl route can change remoteApprovals (local CLI only)."""
        from pixel_connector.app import Connector
        handlers = [n for n in dir(Connector) if n.startswith("h_")]
        self.assertFalse([n for n in handlers if "remote" in n or "config" in n], handlers)
        src = (ROOT / "pixel_connector" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn('"remoteApprovals"', src)
        self.assertNotIn("write_json", src)


# ----------------------------------------------------------------------------------------------------------------------
# pixel_hook.py (pure parts)
# ----------------------------------------------------------------------------------------------------------------------

def perm_event(tool="Bash", tool_input=None, **kw):
    e = {"hook_event_name": "PermissionRequest", "session_id": "sess-1", "cwd": "/home/x/gizli-proje",
         "tool_name": tool, "tool_input": {"command": SECRET_CMD} if tool_input is None else tool_input,
         "permission_mode": "default"}
    e.update(kw)
    return e


class HookMapping(unittest.TestCase):
    CFG = {"salt": "s" * 32, "name": "Timur", "enabled": True}

    def test_summaries(self):
        self.assertEqual(PH.tool_summary("Edit", {"file_path": "/home/x/p/src/app.py"}), "app.py")
        self.assertEqual(PH.tool_summary("Write", {"file_path": "C:\\Users\\x\\a.txt"}), "a.txt")
        self.assertEqual(PH.tool_summary("WebFetch", {"url": "https://docs.example.com/a?b=c"}), "docs.example.com")
        self.assertEqual(len(PH.tool_summary("Bash", {"command": "x" * 900})), PH.SUMMARY_MAX)
        self.assertEqual(PH.tool_summary("Bash", {"command": "a\x1b\x07b"}), "ab")
        self.assertEqual(PH.tool_summary("mcp__srv__do", {"secret": "v"}), "")

    def test_build_request(self):
        b = PH.build_request(perm_event(), self.CFG)
        self.assertEqual((b["ask"], b["tool"], b["agent"], b["project"]), ("tool", "Bash", "Claude Code", "gizli-proje"))
        self.assertEqual(b["session"], PH.hash_id(self.CFG["salt"], "sess-1"))
        rest = json.dumps({k: v for k, v in b.items() if k != "summary"})
        self.assertNotIn("sess-1", rest)
        self.assertNotIn("/home/x", rest)                            # cwd → basename only
        self.assertEqual(A.validate_tool_request(b)["summary"], SECRET_CMD)
        self.assertIsNone(PH.build_request(perm_event("ExitPlanMode", {"plan": "x"}), self.CFG))
        self.assertIsNone(PH.build_request(perm_event("Bad Tool!"), self.CFG))
        q = PH.build_request(perm_event("AskUserQuestion", QUESTION_INPUT), self.CFG)
        self.assertEqual(q["ask"], "question")
        self.assertEqual(A.validate_tool_request(q)["questions"][1]["options"][2]["label"], "Vitest")
        self.assertIsNone(PH.build_request(perm_event("AskUserQuestion", {"questions": "x"}), self.CFG))
        sub = PH.build_request(perm_event(agent_id="ag1", agent_type="Explore"), self.CFG)
        self.assertEqual(sub["agent"], "Claude Code · Explore")
        hidden = PH.build_request(perm_event(), dict(self.CFG, hide_projects=True))
        self.assertEqual(hidden["project"], PH.L_HIDDEN_PROJECT)

    def test_decision_output_matches_claude_code_schema(self):
        ev = perm_event()
        self.assertEqual(PH.decision_output(ev, {"decision": "allow"}),
                         {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}})
        deny = PH.decision_output(ev, {"decision": "deny"})["hookSpecificOutput"]["decision"]
        self.assertEqual(deny["behavior"], "deny")
        self.assertIn("reddetti", deny["message"])
        for r in ({"decision": "timeout"}, {"decision": "withdrawn"}, {"decision": "off"}, {"decision": "ALLOW"},
                  {}, None, "allow", {"decision": ["allow"]}):
            self.assertIsNone(PH.decision_output(ev, r), r)

    def test_question_answers_are_rechecked_against_the_original(self):
        ev = perm_event("AskUserQuestion", QUESTION_INPUT)
        out = PH.decision_output(ev, {"decision": "allow", "answers": [[1], [0, 2]]})
        upd = out["hookSpecificOutput"]["decision"]["updatedInput"]
        self.assertEqual(upd["questions"], QUESTION_INPUT["questions"])
        self.assertEqual(upd["answers"], {"Hangi çatı?": "Vue", "Hangi araçlar?": "ESLint, Vitest"})
        for bad in ([[1]], [[0, 1], [0]], [[9], [0]], [[0], [0, 0]], None, [[True], [0]]):
            self.assertIsNone(PH.decision_output(ev, {"decision": "allow", "answers": bad}), bad)
        self.assertIn("cevaplamadan", json.dumps(PH.decision_output(ev, {"decision": "deny"}), ensure_ascii=False))

    def test_remote_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as home:
            ch = os.path.join(home, "connector")
            os.makedirs(ch)
            self.assertIsNone(PH.remote_state(home))
            CFG.write_json(os.path.join(ch, "connector.json"), {"remoteApprovals": {"enabled": "true"}})
            CFG.write_json(os.path.join(ch, "control.json"), {"port": 1234, "token": "t"})
            self.assertIsNone(PH.remote_state(home))
            CFG.write_json(os.path.join(ch, "connector.json"), {"remoteApprovals": {"enabled": True, "timeoutS": 30}})
            self.assertEqual(PH.remote_state(home), {"port": 1234, "token": "t", "wait": 30})
            CFG.write_json(os.path.join(ch, "connector.json"), {"remoteApprovals": {"enabled": True, "timeoutS": 999}})
            self.assertEqual(PH.remote_state(home)["wait"], 280)    # same clamp as the connector
            CFG.write_json(os.path.join(ch, "control.json"), {"port": "1234", "token": "t"})
            self.assertIsNone(PH.remote_state(home))
            os.unlink(os.path.join(ch, "control.json"))
            self.assertIsNone(PH.remote_state(home))

    def test_remote_decision_never_raises_and_never_allows_on_its_own(self):
        with tempfile.TemporaryDirectory() as home:
            ch = os.path.join(home, "connector")
            os.makedirs(ch)
            CFG.write_json(os.path.join(ch, "connector.json"), {"remoteApprovals": {"enabled": True}})
            CFG.write_json(os.path.join(ch, "control.json"), {"port": 9, "token": "t"})
            seen = []

            def boom(*a):
                seen.append(a)
                raise OSError("down")
            self.assertIsNone(PH.remote_decision(perm_event(), self.CFG, home, post=boom))
            self.assertEqual(seen[0][1], "/ctl/approvals/tool")
            self.assertEqual(seen[0][3], 240 + PH.APPROVAL_HTTP_SLACK)
            for reply in (None, {"decision": "off"}, {"decision": "timeout"}, {"weird": 1}):
                self.assertIsNone(PH.remote_decision(perm_event(), self.CFG, home, post=lambda *a, r=reply: r))
            self.assertIsNone(PH.remote_decision(perm_event(), self.CFG, home, post=lambda *a: None))
            # the real loopback client against a closed port: no output, no exception
            self.assertIsNone(PH.remote_decision(perm_event(), self.CFG, home))


# ----------------------------------------------------------------------------------------------------------------------
# Full stack: hook subprocess → connector (/ctl) → relay → FakeBrowser
# ----------------------------------------------------------------------------------------------------------------------

class FullStack(unittest.TestCase):
    TIMEOUT_S = 240

    def setUp(self):
        self.env = E.V3Env()
        self.home = self.env.connector_home("dev", remoteApprovals={"enabled": True, "timeoutS": self.TIMEOUT_S},
                                            engines=NO_OLLAMA)
        self.office = os.path.dirname(self.home)
        self.conn = self.env.connector(self.home, start_ctl=True)
        PH.write_json_atomic(os.path.join(self.office, "config.json"),
                             {"server": self.env.base, "room": self.env.room, "key": self.env.key, "name": "Timur",
                              "salt": "s" * 32, "ownerId": "o_testtesttest", "enabled": True}, mode=0o600)
        self.br = E.FakeBrowser(self.env, "Telefon")
        self.dev, _, p4 = E.pair(self.env, self.conn, self.br, iterations=ITER)
        self.assertTrue(p4["ok"])
        self.br.connect_device(self.dev)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
            for f in (p.stdout, p.stderr):
                if f is not None:
                    f.close()
        self.br.close()
        self.conn.stop()
        self.env.stop()

    # -- helpers ---------------------------------------------------------------------------------------
    def hook_env(self):
        env = dict(os.environ, PIXEL_OFFICE_HOME=self.office,
                   PIXEL_OFFICE_CLAUDE_SETTINGS=os.path.join(self.office, "settings.json"))
        env.pop("PIXEL_OFFICE_DISABLE", None)
        return env

    def start_hook(self, event, decide=True):
        argv = [sys.executable, str(HOOK), "hook"] + (["--decide"] if decide else [])
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=self.hook_env())
        p.stdin.write(json.dumps(event).encode())
        p.stdin.close()
        self.procs.append(p)
        return p

    def hook_output(self, p, timeout=30):
        out = p.stdout.read()
        p.wait(timeout)
        self.assertEqual(p.returncode, 0)
        return json.loads(out) if out.strip() else None

    def wait_request(self, br=None, timeout=15):
        return (br or self.br).next_msg(lambda m: m.get("type") == "approval.request", timeout)["approval"]

    def answer(self, aid, decision, answers=None, br=None):
        msg = {"type": "approval.answer", "approval": aid, "decision": decision}
        if answers is not None:
            msg["answers"] = answers
        return (br or self.br).request(self.dev, msg)

    def audit(self):
        return self.conn.audit.read()

    # -- tests -----------------------------------------------------------------------------------------
    def test_allow_from_phone(self):
        p = self.start_hook(perm_event())
        a = self.wait_request()
        self.assertEqual((a["ask"], a["tool"], a["summary"], a["project"], a["agent"]),
                         ("tool", "Bash", SECRET_CMD, "gizli-proje", "Claude Code"))
        self.assertGreater(a["expiresAt"], time.time() * 1000 + (self.TIMEOUT_S - 10) * 1000)
        r = self.answer(a["id"], "allow")
        self.assertEqual(r, {"type": "approval.answered", "approval": a["id"], "decision": "allow", "re": r["re"]})
        self.assertEqual(self.hook_output(p),
                         {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}})
        res = self.br.next_msg(lambda m: m.get("type") == "approval.resolved")
        self.assertEqual((res["approval"], res["result"]), (a["id"], "allow"))
        evs = [(r["ev"], r.get("tool"), r.get("by")) for r in self.audit() if r["ev"].startswith("approval.")]
        self.assertEqual(evs, [("approval.request", "Bash", None), ("approval.approve", "Bash", self.br.bid)])
        audit_text = Path(self.conn.audit.path).read_text(encoding="utf-8")
        self.assertNotIn("CANARY-7f3a", audit_text)                  # never the command, only a short hash
        self.assertIn(A.summary_hash(SECRET_CMD), audit_text)
        # E2E only: the relay and the office server never see the command or the project path
        self.assertFalse(any("CANARY-7f3a" in f for f in self.env.relay_frames))
        self.assertFalse(any(b"CANARY-7f3a" in b for b in self.env.status_bodies))
        # the office still shows waiting_for_user (status report runs next to the wait)
        states = [json.loads(b).get("state") for b in self.env.status_bodies if b]
        self.assertIn("waiting_for_user", states)

    def test_deny_from_phone(self):
        p = self.start_hook(perm_event())
        a = self.wait_request()
        self.answer(a["id"], "deny")
        out = self.hook_output(p)["hookSpecificOutput"]["decision"]
        self.assertEqual(out["behavior"], "deny")
        self.assertIn("Pixel Office", out["message"])
        self.assertIn("approval.deny", [r["ev"] for r in self.audit()])

    def test_question_answered_from_phone(self):
        p = self.start_hook(perm_event("AskUserQuestion", QUESTION_INPUT))
        a = self.wait_request()
        self.assertEqual(a["ask"], "question")
        self.assertEqual([o["label"] for o in a["questions"][0]["options"]], ["React", "Vue"])
        bad = self.answer(a["id"], "allow", [[0, 1], [0]])           # single choice with two answers
        self.assertEqual((bad["type"], bad["code"]), ("error", "bad_request"))
        bad = self.answer(a["id"], "allow")                           # a question needs answers
        self.assertEqual(bad["code"], "bad_request")
        self.answer(a["id"], "allow", [[0], [2, 1]])
        out = self.hook_output(p)["hookSpecificOutput"]["decision"]
        self.assertEqual(out["behavior"], "allow")
        self.assertEqual(out["updatedInput"]["answers"], {"Hangi çatı?": "React", "Hangi araçlar?": "Prettier, Vitest"})
        self.assertEqual(out["updatedInput"]["questions"], QUESTION_INPUT["questions"])

    def test_second_device_sees_resolution_and_can_not_replay(self):
        b2 = E.FakeBrowser(self.env, "Tablet")
        try:
            E.pair(self.env, self.conn, b2, iterations=ITER)
            b2.connect_device(self.dev)
            p = self.start_hook(perm_event())
            a = self.wait_request()
            self.assertEqual(self.wait_request(b2)["id"], a["id"])
            self.answer(a["id"], "deny")
            res = b2.next_msg(lambda m: m.get("type") == "approval.resolved")
            self.assertEqual(res["result"], "deny")
            late = self.answer(a["id"], "allow", br=b2)
            self.assertEqual((late["type"], late["code"]), ("error", "unknown_approval"))
            self.assertEqual(self.hook_output(p)["hookSpecificOutput"]["decision"]["behavior"], "deny")
            again = self.answer(a["id"], "allow")                     # the same device replays its answer
            self.assertEqual(again["code"], "unknown_approval")
        finally:
            b2.close()

    def test_unpaired_and_revoked_browsers_see_nothing(self):
        stranger = E.FakeBrowser(self.env, "Yabancı")               # in the room, never paired
        b2 = E.FakeBrowser(self.env, "Eski telefon")
        try:
            E.pair(self.env, self.conn, b2, iterations=ITER)
            b2.connect_device(self.dev)
            self.conn.revoke(b2.bid)
            p = self.start_hook(perm_event())
            a = self.wait_request()
            with self.assertRaises(AssertionError):
                b2.next_msg(lambda m: m.get("type") == "approval.request", timeout=1.5)
            with self.assertRaises(AssertionError):
                stranger.wait_frame(lambda f: f.get("t") == "msg", timeout=1.0)
            # a crafted answer from the unpaired browser can't even reach the handler (no channel)
            stranger.send(self.dev, "e", b="x" * 40, ch="c" * 22, seq=0)
            time.sleep(0.5)
            self.assertIn(a["id"], [x["id"] for x in self.conn.approvals.list()])
            self.answer(a["id"], "allow")
            self.hook_output(p)
        finally:
            stranger.close()
            b2.close()

    def test_late_device_gets_pending_after_hello(self):
        p = self.start_hook(perm_event())
        a = self.wait_request()
        b2 = E.FakeBrowser(self.env, "Sonradan açılan")
        try:
            E.pair(self.env, self.conn, b2, iterations=ITER)
            b2.connect_device(self.dev)
            self.assertEqual(self.wait_request(b2)["id"], a["id"])
            lst = b2.request(self.dev, {"type": "approval.list"})
            self.assertEqual([x["id"] for x in lst["approvals"]], [a["id"]])
            self.answer(a["id"], "allow", br=b2)
            self.assertEqual(self.hook_output(p)["hookSpecificOutput"]["decision"]["behavior"], "allow")
        finally:
            b2.close()

    def test_answer_validation_at_the_boundary(self):
        p = self.start_hook(perm_event())
        a = self.wait_request()
        for msg, code in (({"approval": "x"}, "bad_request"), ({"approval": a["id"], "decision": "yes"}, "bad_request"),
                          ({"approval": a["id"], "decision": "allow", "answers": [[0]]}, "bad_request"),
                          ({"approval": a["id"], "decision": "deny", "answers": [[0]]}, "bad_request"),
                          ({"approval": "a" + "Q" * 16, "decision": "allow"}, "unknown_approval"),
                          ({"approval": a["id"] + "x", "decision": "allow"}, "bad_request")):
            r = self.br.request(self.dev, dict(msg, type="approval.answer"))
            self.assertEqual((r["type"], r["code"]), ("error", code), msg)
        self.assertIn(a["id"], [x["id"] for x in self.conn.approvals.list()])
        self.answer(a["id"], "deny")
        self.hook_output(p)

    def test_cli_approve_and_deny(self):
        p = self.start_hook(perm_event())
        a = self.wait_request()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(CLI.main(["--home", self.home, "approve"]), 0)
        self.assertIn(a["id"], out.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(["--home", self.home, "approve", a["id"]]), 0)
        self.assertEqual(self.hook_output(p)["hookSpecificOutput"]["decision"]["behavior"], "allow")
        self.assertEqual([r.get("by") for r in self.audit() if r["ev"] == "approval.approve"], ["cli"])
        q = self.start_hook(perm_event("AskUserQuestion", QUESTION_INPUT))
        a = self.wait_request()
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(["--home", self.home, "approve", a["id"]]), 1)
        self.assertIn("bu bir soru", err.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(["--home", self.home, "deny", a["id"]]), 0)
        self.assertEqual(self.hook_output(q)["hookSpecificOutput"]["decision"]["behavior"], "deny")

    def test_onay_page_answers_too(self):
        p = self.start_hook(perm_event())
        a = self.wait_request()
        port, ctl = self.conn.ctl.port, self.conn.ctl
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/onay")                                   # no cookie → 401, nothing listed
        r = c.getresponse()
        self.assertEqual(r.status, 401)
        self.assertNotIn(b"CANARY", r.read())
        cookie = "pixel_ctl=" + ctl.page_token
        c.request("GET", "/onay", headers={"Cookie": cookie})
        r = c.getresponse()
        page = r.read().decode()
        self.assertIn(a["id"], page)
        self.assertIn("rm -rf", page)
        body = "csrf=bad&id=%s&karar=onayla" % a["id"]
        c.request("POST", "/onay/karar", body=body, headers={"Cookie": cookie,
                                                              "Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 403)                              # CSRF
        self.assertIn(a["id"], [x["id"] for x in self.conn.approvals.list()])
        body = "csrf=%s&id=%s&karar=onayla" % (ctl.csrf, a["id"])
        c.request("POST", "/onay/karar", body=body, headers={"Cookie": cookie,
                                                              "Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        r.read()
        c.close()
        self.assertEqual(self.hook_output(p)["hookSpecificOutput"]["decision"]["behavior"], "allow")
        self.assertEqual([r.get("by") for r in self.audit() if r["ev"] == "approval.approve"], ["page"])
        late = self.answer(a["id"], "deny")
        self.assertEqual(late["code"], "unknown_approval")

    def test_vanished_hook_withdraws_the_card(self):
        """Claude Code answered locally and ended the hook process: the card disappears from the phones."""
        p = self.start_hook(perm_event())
        a = self.wait_request()
        p.kill()
        p.wait()
        res = self.br.next_msg(lambda m: m.get("type") == "approval.resolved", timeout=10)
        self.assertEqual((res["approval"], res["result"]), (a["id"], "withdrawn"))
        self.assertIn("approval.withdraw", [r["ev"] for r in self.audit()])
        self.assertEqual(self.answer(a["id"], "allow")["code"], "unknown_approval")

    def test_post_tool_use_withdraws_the_matching_card(self):
        p = self.start_hook(perm_event())
        a = self.wait_request()
        other = perm_event(tool_input={"command": "something else"})
        other["hook_event_name"] = "PostToolUse"
        self.start_hook(other, decide=False).wait(30)
        time.sleep(0.5)
        self.assertIn(a["id"], [x["id"] for x in self.conn.approvals.list()])
        ran = perm_event()
        ran["hook_event_name"] = "PostToolUse"
        self.start_hook(ran, decide=False).wait(30)
        res = self.br.next_msg(lambda m: m.get("type") == "approval.resolved", timeout=10)
        self.assertEqual(res["result"], "withdrawn")
        self.assertIsNone(self.hook_output(p))                       # Claude Code already decided: nothing printed

    def test_feature_off_means_old_behaviour(self):
        cfg = CFG.read_json(os.path.join(self.home, CFG.CONFIG_FILE))
        cfg["remoteApprovals"]["enabled"] = False
        CFG.write_json(os.path.join(self.home, CFG.CONFIG_FILE), cfg)
        self.assertFalse(self.conn.device_info()["approvals"]["remote"])
        t0 = time.time()
        p = self.start_hook(perm_event())
        self.assertIsNone(self.hook_output(p))
        self.assertLess(time.time() - t0, 15)
        with self.assertRaises(AssertionError):
            self.wait_request(timeout=1.0)
        # even a direct call (e.g. a hook written before the switch) is refused while off
        st, d = ctl_request(self.home, "POST", "/ctl/approvals/tool", tool_req())
        self.assertEqual((st, d), (200, {"decision": "off"}))
        self.assertFalse([r for r in self.audit() if r["ev"].startswith("approval.")])

    def test_old_async_entry_never_asks_the_phone(self):
        """A hook installed before Phase 19 (no --decide) only reports the state, whatever the connector says."""
        p = self.start_hook(perm_event(), decide=False)
        self.assertIsNone(self.hook_output(p))
        with self.assertRaises(AssertionError):
            self.wait_request(timeout=1.0)

    def test_ctl_requires_token_and_valid_body(self):
        st, d = ctl_request(self.home, "POST", "/ctl/approvals/tool", tool_req(tool="rm -rf"))
        self.assertEqual((st, d["error"], d["field"]), (400, "bad_request", "tool"))
        st, d = ctl_request(self.home, "POST", "/ctl/approvals/withdraw", {"session": "nope"})
        self.assertEqual(st, 400)
        # a wrong token, and the /onay page token (cookie or header), never unlock the control API
        for headers in ({"X-Pixel-Ctl": "wrong"}, {"X-Pixel-Ctl": self.conn.ctl.page_token},
                        {"Cookie": "pixel_ctl=" + self.conn.ctl.page_token}):
            c = http.client.HTTPConnection("127.0.0.1", self.conn.ctl.port, timeout=5)
            c.request("POST", "/ctl/approvals/tool", body=json.dumps(tool_req()),
                      headers=dict({"Content-Type": "application/json"}, **headers))
            r = c.getresponse()
            self.assertEqual(r.status, 401, headers)
            r.read()
            c.close()
        self.assertEqual(self.conn.approvals.list(), [])

    def test_device_info_reports_the_switch(self):
        self.assertEqual(self.conn.device_info()["approvals"], {"remote": True})


class Expiry(unittest.TestCase):
    """A short connector wait: no answer → no hook output (Claude Code's own dialog stays), card resolved "expired"."""

    def test_timeout_gives_no_output(self):
        env = E.V3Env()
        try:
            home = env.connector_home("dev", remoteApprovals={"enabled": True, "timeoutS": 5}, engines=NO_OLLAMA)
            conn = env.connector(home, start_ctl=True)
            br = E.FakeBrowser(env, "Telefon")
            try:
                dev, _, _ = E.pair(env, conn, br, iterations=ITER)
                br.connect_device(dev)
                office = os.path.dirname(home)
                PH.write_json_atomic(os.path.join(office, "config.json"),
                                     {"server": env.base, "room": env.room, "key": env.key, "name": "Timur",
                                      "salt": "s" * 32, "enabled": True}, mode=0o600)
                envv = dict(os.environ, PIXEL_OFFICE_HOME=office)
                envv.pop("PIXEL_OFFICE_DISABLE", None)
                t0 = time.time()
                r = subprocess.run([sys.executable, str(HOOK), "hook", "--decide"], input=json.dumps(perm_event()),
                                   capture_output=True, text=True, env=envv, timeout=60)
                self.assertEqual((r.returncode, r.stdout), (0, ""))
                self.assertGreaterEqual(time.time() - t0, 4.5)
                res = br.next_msg(lambda m: m.get("type") == "approval.resolved")
                self.assertEqual(res["result"], "expired")
                self.assertIn("approval.expire", [x["ev"] for x in conn.audit.read()])
            finally:
                br.close()
                conn.stop()
        finally:
            env.stop()


class ClientGone(unittest.TestCase):
    def test_raw_disconnect_is_detected(self):
        """The long-poll notices a closed client socket (no hook process involved)."""
        env = E.V3Env()
        try:
            home = env.connector_home("dev", remoteApprovals={"enabled": True, "timeoutS": 60}, engines=NO_OLLAMA)
            conn = env.connector(home, start_ctl=True)
            try:
                c = CFG.read_json(os.path.join(home, "control.json"))
                body = json.dumps(tool_req()).encode()
                s = socket.create_connection(("127.0.0.1", c["port"]), timeout=5)
                s.sendall(b"POST /ctl/approvals/tool HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Pixel-Ctl: %s\r\n"
                          b"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n%s"
                          % (c["port"], c["token"].encode(), len(body), body))
                end = time.time() + 5
                while not conn.approvals.list() and time.time() < end:
                    time.sleep(0.05)
                self.assertEqual(len(conn.approvals.list()), 1)
                s.close()
                end = time.time() + 5
                evs = lambda: [x["ev"] for x in conn.audit.read()]   # noqa: E731
                while "approval.withdraw" not in evs() and time.time() < end:
                    time.sleep(0.05)
                self.assertEqual(conn.approvals.list(), [])
                self.assertIn("approval.withdraw", evs())
            finally:
                conn.stop()
        finally:
            env.stop()


if __name__ == "__main__":
    unittest.main()
