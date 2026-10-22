"""Phase 18: `pixel_antigravity.py` (also `pixel_agent.py antigravity`) — tails Antigravity IDE transcripts and
mirrors each conversation to one office agent. Synthetic transcripts in temp dirs only; nothing touches ~/.gemini,
~/.pixel-office or ~/.config."""

import ast
import calendar
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_cloud import validate

ROOT = Path(__file__).resolve().parent.parent
AGENT_PY = ROOT / "web" / "hook" / "pixel_agent.py"
AG_PY = ROOT / "web" / "hook" / "pixel_antigravity.py"
CANARY = "CANARY-ag18"
T0 = 1_800_000_000.0
CID = "11111111-2222-4333-8444-555555555555"
CID2 = "66666666-7777-4888-9999-aaaaaaaaaaaa"

spec = importlib.util.spec_from_file_location("pixel_antigravity_under_test", AG_PY)
ag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ag)
pa = ag.pixel_agent


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


# --- synthetic transcript lines -----------------------------------------------------------------------
def step(i, source="MODEL", typ="PLANNER_RESPONSE", status="DONE", tools=None, **extra):
    d = {"step_index": i, "source": source, "type": typ, "status": status}     # created_at: filled by Brain.write
    if tools:
        d["tool_calls"] = tools
    d.update(extra)
    return d


def call(name, **args):
    return {"name": name, "args": args}


def user(i):
    return step(i, "USER_EXPLICIT", "USER_INPUT", content="please " + CANARY)


def plan(i, tool=None, cwd=None):
    if tool is None:
        return step(i, content="answer " + CANARY, thinking="thought " + CANARY)
    args = {"toolSummary": "sum " + CANARY, "CommandLine": "echo " + CANARY,
            "AbsolutePath": "/home/u/" + CANARY + "/file.py"}
    if cwd:
        args["Cwd"] = json.dumps(cwd)
    return step(i, tools=[call(tool, **args)], thinking="why " + CANARY)


def result(i, typ="RUN_COMMAND", status="DONE"):
    return step(i, typ=typ, status=status, content="output " + CANARY, exit_code=0)


def line(d):
    return (json.dumps(d) + "\n").encode()


class FakeClock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class FakeAgent:
    def __init__(self, name, session):
        self.name, self.session, self.project = name, session, None
        self.reports = []
        self.flushed = False
        self.finished = False

    def report(self, state, tool=None, label=None, final=False):
        self.reports.append((state, tool, label))

    def flush(self, timeout=2.0):
        self.flushed = True

    @property
    def last(self):
        return self.reports[-1] if self.reports else None


class Brain:
    """A temp `brain` folder with conversation transcripts."""

    def __init__(self, clock=None):
        self.clock = clock
        self.tmp = tempfile.mkdtemp(prefix="ag-brain-")
        self.root = os.path.join(self.tmp, "brain")
        os.makedirs(self.root)

    def path(self, cid):
        return os.path.join(self.root, cid, ".system_generated", "logs", "transcript.jsonl")

    def write(self, cid, steps, mtime=None, mode="ab"):
        p = self.path(cid)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if mtime is None and self.clock is not None:
            mtime = self.clock()                                         # files "written now" on the fake clock
        with open(p, mode) as f:
            for s in steps:
                if isinstance(s, dict) and "created_at" not in s and mtime is not None:
                    s = dict(s, created_at=iso(mtime))                   # steps start when the file is written
                f.write(s if isinstance(s, bytes) else line(s))
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class WatcherCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.brain = Brain(self.clock)
        self.addCleanup(self.brain.close)
        self.agents = []

    def factory(self, name, session):
        a = FakeAgent(name, session)
        self.agents.append(a)
        return a

    def watcher(self, **kw):
        kw.setdefault("clock", self.clock)
        kw.setdefault("scan_every", 0.0)
        return ag.AntigravityWatcher(self.factory, [self.brain.root], **kw)

    def by_name(self, name):
        found = [a for a in self.agents if a.name == name]
        self.assertTrue(found, "no agent %r in %r" % (name, [a.name for a in self.agents]))
        return found[-1]


# --- parsing and mapping ------------------------------------------------------------------------------
class ParseTests(unittest.TestCase):
    def test_only_whitelisted_fields_survive(self):
        p = ag.parse_antigravity_step(line(dict(plan(3, "run_command", cwd="/home/u/secret-" + CANARY + "/myproj"),
                                                       created_at="2026-10-19T15:25:15Z")))
        self.assertEqual(set(p), {"source", "type", "status", "tools", "project", "at"})
        self.assertEqual(p["at"], calendar.timegm((2026, 10, 19, 15, 25, 15, 0, 0, 0)))
        self.assertEqual(p["tools"], ["run_command"])
        self.assertEqual(p["project"], "myproj")
        self.assertNotIn(CANARY, json.dumps(p))

    def test_cwd_quoted_and_plain_and_windows(self):
        for cwd, want in (('"/a/b/proj"', "proj"), ("/a/b/proj2/", "proj2"), ('"C:\\\\Users\\\\x\\\\winproj"', "winproj")):
            d = step(1, tools=[call("run_command", Cwd=cwd)])
            self.assertEqual(ag.parse_antigravity_step(line(d))["project"], want, cwd)

    def test_malformed_lines(self):
        for raw in (b"{not json", b"[1,2]", b'"str"', b"null", b"[" * 100000 + b"]" * 100000, b"\xff\xfe"):
            self.assertIsNone(ag.parse_antigravity_step(raw))
        p = ag.parse_antigravity_step(b'{"type": 5, "source": null, "tool_calls": [1, {"name": 7}, {"args": "x"}]}')
        self.assertEqual(p, {"source": None, "type": None, "status": None, "tools": [], "project": None, "at": None})
        self.assertIsNone(ag.antigravity_state(p))                       # a step that says nothing is ignored

    def test_state_mapping(self):
        def st(d):
            return ag.antigravity_state(ag.parse_antigravity_step(line(d)))
        self.assertEqual(st(user(0)), ("thinking", None, None))
        self.assertEqual(st(step(1, "SYSTEM", "CHECKPOINT")), ("thinking", None, None))
        self.assertEqual(st(step(1, "SYSTEM", "ERROR_MESSAGE")), ("thinking", None, "Hata"))
        self.assertEqual(st(plan(2, "run_command")), ("working", "Bash", None))
        self.assertEqual(st(plan(2, "view_file")), ("working", "Read", None))
        self.assertEqual(st(plan(2, "replace_file_content")), ("working", "Edit", None))
        self.assertEqual(st(plan(2, "write_to_file")), ("working", "Write", None))
        self.assertEqual(st(plan(2, "grep_search")), ("working", "Grep", None))
        self.assertEqual(st(plan(2, "browser_click_element")), ("working", "browser_click_element", None))
        self.assertEqual(st(plan(2, "bad name!/$")), ("working", "badname", None))
        self.assertEqual(st(result(3, "VIEW_FILE")), ("working", "Read", None))
        self.assertEqual(st(result(3, "RUN_COMMAND", "RUNNING")), ("working", "Bash", None))
        self.assertEqual(st(result(3, "GENERIC")), ("working", None, None))
        self.assertEqual(st(plan(4)), ("idle", None, None))                               # turn over
        self.assertEqual(st(step(4, status="RUNNING")), ("thinking", None, None))

    def test_roots(self):
        with mock.patch.dict(os.environ, {"HOME": "/h", "PIXEL_ANTIGRAVITY_DIR": ""}):
            r = ag.antigravity_roots()
            self.assertEqual(r, [os.path.abspath(os.path.expanduser(x)) for x in ag.AG_DEFAULT_ROOTS])
            self.assertTrue(any("antigravity-ide" in x for x in r) and any(x.endswith(os.path.join("antigravity", "brain"))
                                                                          for x in r))
        with mock.patch.dict(os.environ, {"PIXEL_ANTIGRAVITY_DIR": os.pathsep.join(["/x/a", "/x/b", "/x/a"])}):
            self.assertEqual(ag.antigravity_roots(), ["/x/a", "/x/b"])
            self.assertEqual(ag.antigravity_roots(["/y"]), ["/y"])


# --- tailing ------------------------------------------------------------------------------------------
class TailTests(unittest.TestCase):
    def setUp(self):
        self.brain = Brain()
        self.addCleanup(self.brain.close)

    def test_partial_line_waits_for_completion(self):
        p = self.brain.write(CID, [user(0)])
        t = ag.TranscriptTail(p)
        self.assertEqual(len(t.read_new()), 1)
        raw = line(plan(1, "view_file"))
        with open(p, "ab") as f:
            f.write(raw[:20])
        self.assertEqual(t.read_new(), [])
        with open(p, "ab") as f:
            f.write(raw[20:])
        self.assertEqual([s["tools"] for s in t.read_new()], [["view_file"]])
        self.assertEqual(t.read_new(), [])

    def test_seek_end_skips_history_keeps_partial(self):
        raw = line(plan(9, "view_file"))
        p = self.brain.write(CID, [user(0), plan(1, "run_command"), result(2), raw[:10]])
        t = ag.TranscriptTail(p)
        steps = t.seek_end(os.stat(p))
        self.assertEqual([s["type"] for s in steps], ["USER_INPUT", "PLANNER_RESPONSE", "RUN_COMMAND"])
        self.assertEqual(t.read_new(), [])
        with open(p, "ab") as f:
            f.write(raw[10:])
        self.assertEqual([s["tools"] for s in t.read_new()], [["view_file"]])

    def test_truncation_and_replacement_start_over(self):
        p = self.brain.write(CID, [user(0), plan(1, "run_command"), result(2)])
        t = ag.TranscriptTail(p)
        self.assertEqual(len(t.read_new()), 3)
        with open(p, "wb") as f:                                     # truncated and rewritten shorter
            f.write(line(plan(0)))
        self.assertEqual([s["type"] for s in t.read_new()], ["PLANNER_RESPONSE"])
        tmp = p + ".new"                                               # replaced (new inode), same size or larger
        with open(tmp, "wb") as f:
            f.write(line(user(0)) + line(plan(1, "grep_search")))
        os.replace(tmp, p)
        self.assertEqual([s["type"] for s in t.read_new()], ["USER_INPUT", "PLANNER_RESPONSE"])

    def test_huge_and_malformed_lines_are_skipped(self):
        p = self.brain.write(CID, [])
        t = ag.TranscriptTail(p)
        with mock.patch.object(ag, "AG_LINE_MAX", 1000), mock.patch.object(ag, "AG_READ_MAX", 4000):
            with open(p, "ab") as f:
                f.write(b'{"type": "' + b"x" * 5000)                # huge line, end not written yet
            self.assertEqual(t.read_new(), [])
            self.assertTrue(t.skipping)
            with open(p, "ab") as f:
                f.write(b"y" * 3000 + b'"}\n' + b"{broken\n" + line(user(1)) + b"x" * 1500 + b"\n" + line(plan(2)))
            got = []
            for _ in range(5):
                got += t.read_new()
            self.assertEqual([s["type"] for s in got], ["USER_INPUT", "PLANNER_RESPONSE"])

    def test_read_is_bounded_per_call(self):
        p = self.brain.write(CID, [result(i, "VIEW_FILE") for i in range(200)])
        t = ag.TranscriptTail(p)
        with mock.patch.object(ag, "AG_READ_MAX", 2048), mock.patch.object(ag, "AG_LINE_MAX", 1024):
            first = t.read_new()
            self.assertTrue(0 < len(first) < 200)
            total = len(first)
            for _ in range(100):
                total += len(t.read_new())
        self.assertEqual(total, 200)

    def test_missing_file(self):
        t = ag.TranscriptTail(os.path.join(self.brain.root, "nope.jsonl"))
        self.assertEqual(t.read_new(), [])


# --- the watcher --------------------------------------------------------------------------------------
class WatcherTests(WatcherCase):
    def test_lifecycle_working_idle_sleeping_offline(self):
        p = self.brain.write(CID, [], mtime=T0)
        w = self.watcher(idle_after=60, sleep_after=180, gone_after=2700)
        w.poll()
        self.assertEqual(self.agents, [])                               # empty transcript: nothing to show yet
        self.brain.write(CID, [user(0)])
        self.clock.t += 1
        w.poll()
        a = self.by_name("Antigravity")
        self.assertEqual(a.last, ("thinking", None, None))
        self.assertTrue(a.session.startswith("ag-") and CID[:8] not in a.session and len(a.session) == 13)
        self.brain.write(CID, [plan(1, "run_command", cwd="/home/u/work/pixel-app"), result(2)])
        self.clock.t += 1
        w.poll()
        self.assertEqual(a.last, ("working", "Bash", None))
        self.assertEqual(a.project, "pixel-app")
        self.clock.t += 59
        w.poll()
        self.assertEqual(a.last[0], "working")
        self.clock.t += 1
        w.poll()
        self.assertEqual(a.last, ("idle", None, None))
        self.clock.t += 120
        w.poll()
        self.assertEqual(a.last, ("sleeping", None, None))
        n = len(a.reports)
        self.clock.t += 600
        w.poll()
        self.assertEqual(len(a.reports), n)                             # no repeats
        self.clock.t += 2700
        w.poll()
        self.assertEqual(a.last, ("offline", None, None))
        self.brain.write(CID, [user(3)])                                # the conversation comes back
        self.clock.t += 1
        w.poll()
        self.assertEqual(len(self.agents), 2)
        self.assertEqual(self.agents[-1].name, "Antigravity")
        self.assertEqual(self.agents[-1].last, ("thinking", None, None))
        self.assertTrue(os.path.exists(p))

    def test_turn_end_is_idle_at_once_and_generic_keeps_tool(self):
        self.brain.write(CID, [], mtime=T0)
        w = self.watcher()
        w.poll()
        self.brain.write(CID, [user(0), plan(1, "manage_task"), result(2, "GENERIC")])
        w.poll()
        a = self.by_name("Antigravity")
        self.assertEqual(a.last, ("working", "TodoWrite", None))
        self.brain.write(CID, [plan(3)])
        self.clock.t += 1
        w.poll()
        self.assertEqual(a.last, ("idle", None, None))
        self.clock.t += 179
        w.poll()
        self.assertEqual(a.last, ("idle", None, None))
        self.clock.t += 1
        w.poll()
        self.assertEqual(a.last, ("sleeping", None, None))

    def test_startup_shows_current_state_without_replaying_history(self):
        hist = [user(0)] + [s for i in range(1, 40, 2) for s in (plan(i, "view_file", cwd="/p/projA"), result(i + 1, "VIEW_FILE"))]
        self.brain.write(CID, hist + [plan(99, "run_command", cwd="/p/projB")], mtime=T0 - 5)    # active now
        self.brain.write(CID2, hist + [plan(99)], mtime=T0 - 600)                               # answered 10 min ago
        self.brain.write("33333333-0000-4000-8000-000000000000", hist, mtime=T0 - 3 * 3600)     # long untouched
        w = self.watcher()
        w.poll()
        self.assertEqual(len(self.agents), 2)
        active = self.by_name("Antigravity")                            # the most recent one gets the plain name
        self.assertEqual(active.reports, [("working", "Bash", None)])
        self.assertEqual(active.project, "projB")
        old = self.by_name("Antigravity 2")
        self.assertEqual(old.reports, [("sleeping", None, None)])
        self.assertEqual(old.project, "projA")

    def test_new_conversation_after_start_is_read_from_the_beginning_and_numbered(self):
        self.brain.write(CID, [user(0)], mtime=T0)
        w = self.watcher()
        w.poll()
        self.brain.write(CID2, [user(0), plan(1, "grep_search")])
        self.clock.t += 1
        w.poll()
        second = self.by_name("Antigravity 2")
        self.assertEqual(second.reports, [("working", "Grep", None)])
        # the first one leaves; its number is free again, the second keeps its name
        self.clock.t += 2700
        os.utime(self.brain.path(CID2), (self.clock.t, self.clock.t))
        self.brain.write(CID2, [plan(2, "grep_search")])
        w.poll()
        self.assertEqual(self.by_name("Antigravity").last, ("offline", None, None))
        self.assertEqual(second.last[0], "working")
        third = "77777777-0000-4000-8000-000000000000"
        self.brain.write(third, [user(0)])
        self.clock.t += 1
        w.poll()
        self.assertEqual(self.agents[-1].name, "Antigravity")

    def test_old_conversation_discovered_later_stays_quiet(self):
        w = self.watcher()
        w.poll()
        self.brain.write(CID, [user(0), plan(1, "run_command")], mtime=T0 - 4 * 3600)
        w.poll()
        self.assertEqual(self.agents, [])
        self.brain.write(CID, [plan(2)])
        self.clock.t += 1
        w.poll()
        self.assertEqual(self.by_name("Antigravity").reports, [("idle", None, None)])

    def test_custom_name_project_and_deleted_conversation(self):
        self.brain.write(CID, [user(0)], mtime=T0)
        self.brain.write(CID2, [user(0)], mtime=T0 - 1)
        w = self.watcher(name="Gemini Ajanı Uzun Bir İsim Burada", project="/x/fixed")
        w.poll()
        names = sorted(a.name for a in self.agents)
        self.assertEqual(names, ["Gemini Ajanı Uzun Bir 2", "Gemini Ajanı Uzun Bir İs"])
        self.assertTrue(all(len(n) <= 24 for n in names))
        self.brain.write(CID, [plan(1, "run_command", cwd="/elsewhere/other")])
        self.clock.t += 1
        w.poll()
        self.assertEqual(self.agents[0].project, "fixed")
        shutil.rmtree(os.path.join(self.brain.root, CID))
        w.poll()
        self.assertEqual(self.agents[0].last, ("offline", None, None))
        self.assertEqual(len(w.convs), 1)

    def test_shutdown_sends_offline_and_flushes(self):
        self.brain.write(CID, [user(0)], mtime=T0)
        self.brain.write(CID2, [plan(0)], mtime=T0)
        w = self.watcher()
        w.poll()
        w.shutdown(0.5)
        self.assertEqual(len(self.agents), 2)
        for a in self.agents:
            self.assertEqual(a.last, ("offline", None, None))
            self.assertTrue(a.flushed and a.finished)

    def test_both_roots_are_watched_and_files_stay_unchanged(self):
        other = Brain(self.clock)
        self.addCleanup(other.close)
        p1 = self.brain.write(CID, [user(0)], mtime=T0)
        p2 = other.write(CID2, [plan(0, "view_file")], mtime=T0)
        before = {p: (Path(p).read_bytes(), os.stat(p).st_mtime) for p in (p1, p2)}
        w = ag.AntigravityWatcher(self.factory, [self.brain.root, other.root, "/nonexistent/brain"], clock=self.clock)
        w.poll()
        self.assertEqual(len(self.agents), 2)
        for p, (data, mt) in before.items():
            self.assertEqual(Path(p).read_bytes(), data)
            self.assertEqual(os.stat(p).st_mtime, mt)


class LateDiscoveryTests(WatcherCase):
    """v2.md §7: a conversation found after startup (brain folder created later, or a new folder) must not replay
    its history as "active now"; its state comes from the last step's created_at."""

    def hist(self, at):
        steps = [user(0)] + [s for i in range(1, 30, 2) for s in (plan(i, "run_command", cwd="/p/projL"),
                                                                    result(i + 1))]
        return [dict(x, created_at=iso(at)) for x in steps]

    def test_brain_folder_appearing_later_uses_step_age_not_mtime(self):
        shutil.rmtree(self.brain.root)
        w = self.watcher()
        w.poll()
        self.assertEqual(self.agents, [])
        os.makedirs(self.brain.root)
        self.brain.write(CID, self.hist(T0 - 600))                       # file mtime = now, last step 10 min ago
        self.brain.write(CID2, self.hist(T0 - 90))                        # 90 s ago: past idle, before sleep
        self.brain.write("88888888-0000-4000-8000-000000000000", self.hist(T0 - 4 * 3600))   # 4 h ago: quiet
        self.clock.t += 1
        w.poll()
        self.assertEqual(sorted(a.name for a in self.agents), ["Antigravity", "Antigravity 2"])
        self.assertEqual(self.by_name("Antigravity").reports, [("idle", None, None)])        # the more recent one
        self.assertEqual(self.by_name("Antigravity 2").reports, [("sleeping", None, None)])
        for a in self.agents:
            self.assertNotIn("working", [r[0] for r in a.reports])
        self.assertEqual(self.by_name("Antigravity").project, "projL")

    def test_late_conversation_only_new_bytes_are_live(self):
        w = self.watcher()
        w.poll()
        self.brain.write(CID, self.hist(T0 - 3600 * 2))                   # discovered later, long untouched
        self.clock.t += 1
        w.poll()
        self.assertEqual(self.agents, [])
        self.brain.write(CID, [user(40)])                                # the user writes again: live
        self.clock.t += 1
        w.poll()
        a = self.by_name("Antigravity")
        self.assertEqual(a.reports, [("thinking", None, None)])
        self.brain.write(CID, [plan(41, "view_file")])
        self.clock.t += 1
        w.poll()
        self.assertEqual(a.last, ("working", "Read", None))

    def test_future_or_missing_timestamps(self):
        w = self.watcher()
        w.poll()
        self.brain.write(CID, [dict(plan(1, "grep_search"), created_at=iso(T0 + 3600))])   # clock skew: clamp to now
        self.brain.write(CID2, [dict(plan(1, "view_file"), created_at="yesterday")], mtime=T0 - 30)   # → mtime
        self.clock.t += 1
        w.poll()
        self.assertEqual(self.by_name("Antigravity").reports, [("working", "Grep", None)])
        self.assertEqual(self.by_name("Antigravity 2").reports, [("working", "Read", None)])

    def test_unknown_types_and_missing_fields_are_tolerated(self):
        self.brain.write(CID, [], mtime=T0)
        w = self.watcher()
        w.poll()
        self.brain.write(CID, [user(0), {"step_index": 1}, {"source": "MODEL", "type": "BRAND_NEW_STEP", "status": "X"},
                               {"source": "SOMETHING", "type": None}, b"\n", b"{}\n"])
        self.clock.t += 1
        w.poll()
        self.assertEqual(self.by_name("Antigravity").last, ("working", None, None))


class TimeParseTests(unittest.TestCase):
    def test_formats(self):
        base = calendar.timegm((2026, 10, 19, 15, 25, 15, 0, 0, 0))
        self.assertEqual(ag.parse_step_time("2026-10-19T15:25:15Z"), base)
        self.assertEqual(ag.parse_step_time("2026-10-19T15:25:15.250Z"), base + 0.25)
        self.assertEqual(ag.parse_step_time("2026-10-19T15:25:15.123456789Z"), base + 0.123456789)
        self.assertEqual(ag.parse_step_time("2026-10-19T18:25:15+03:00"), base)
        self.assertEqual(ag.parse_step_time("2026-10-19T15:25:15"), base)
        for bad in (None, 5, "", "2026-13-01T00:00:00Z", "2026-10-19", "x2026-10-19T15:25:15Z", "2026-10-19T25:00:00Z"):
            self.assertIsNone(ag.parse_step_time(bad), bad)


# --- privacy: everything that reaches the wire --------------------------------------------------------
class PrivacyTests(WatcherCase):
    ALLOWED = {"session", "owner", "ownerId", "name", "project", "state", "tool", "label", "source", "toolCount",
               "stateAgeMs", "startedAgeMs", "final"}

    def test_no_content_thinking_command_or_path_is_ever_posted(self):
        tmp_home = tempfile.mkdtemp(prefix="po-home-")
        self.addCleanup(shutil.rmtree, tmp_home, True)
        posted = []

        def post(updates, timeout):
            posted.append(json.loads(json.dumps(updates)))
            return 200
        for cloud in (False, True):
            with self.subTest(cloud=cloud), mock.patch.dict(os.environ, {"PIXEL_OFFICE_HOME": tmp_home}):
                posted.clear()
                sender = pa.Sender({"url": "http://x", "room": "r" if cloud else None, "key": None}, post=post,
                                   clock=self.clock, start_thread=False)

                def factory(name, session):
                    return pa.Agent(name, session=session, url="http://x", owner="Timur", source=ag.AG_SOURCE,
                                    sender=sender, clock=self.clock, register_atexit=False)
                cwd = "/home/u/" + CANARY + "/Pixel-antigravity"
                self.brain.write(CID, [user(0), plan(1, "run_command", cwd=cwd), result(2),
                                       step(3, "SYSTEM", "ERROR_MESSAGE", error=CANARY, content=CANARY),
                                       plan(4, "write_to_file", cwd=cwd), result(5, "CODE_ACTION"),
                                       plan(6, "search_web"), plan(7)], mtime=self.clock.t)
                w = ag.AntigravityWatcher(factory, [self.brain.root], clock=self.clock)
                for _ in range(6):
                    w.poll()
                    sender.tick(force=True)
                    self.clock.t += 61
                w.shutdown(0.5)
                sender.tick(force=True)
                flat = [u for b in posted for u in b]
                self.assertTrue(flat)
                self.assertEqual(flat[-1]["state"], "offline")
                for u in flat:
                    blob = json.dumps(u, ensure_ascii=False)
                    self.assertNotIn(CANARY, blob)
                    self.assertNotIn("/", blob)
                    self.assertNotIn("\\", blob)
                    self.assertNotIn(CID[:8], blob)
                    self.assertLessEqual(set(u), self.ALLOWED, u)
                    self.assertEqual(u["source"], "antigravity")
                    if "label" in u:
                        self.assertIn(u["label"], ("Hata",))
                    if "project" in u:
                        self.assertEqual(u["project"], "Pixel-antigravity")
                    parsed = validate.parse_update(u)             # the server accepts it as is
                    self.assertEqual(parsed.source, "antigravity")
                self.brain.close()
                self.brain = Brain(self.clock)


# --- autostart and shims --------------------------------------------------------------------------------
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ag-svc-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.office = os.path.join(self.tmp, "office")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        env = {"PIXEL_OFFICE_HOME": self.office, "HOME": self.home, "XDG_CONFIG_HOME": ""}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.log = []
        self.calls = []

    def runner(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    def args(self, *argv):
        return ag.build_parser().parse_args(list(argv))

    def install(self, extra=(), **kw):
        kw.setdefault("log", self.log.append)
        kw.setdefault("user_home", self.home)
        kw.setdefault("runner", self.runner)
        return ag.install_antigravity_service(list(extra), **kw)

    def test_service_args_never_carry_target_or_key(self):
        a = self.args("--name", "Ag", "--idle-after", "30", "--root", "/r/brain", "--url", "http://x", "--key", "SECRET",
                      "--room", "ROOM", "--owner", "me")
        extra = ag.antigravity_service_args(a)
        self.assertEqual(extra, ["--name", "Ag", "--idle-after", "30", "--root", os.path.abspath("/r/brain")])
        self.assertEqual(ag.antigravity_service_args(self.args()), [])

    def test_dry_run_writes_nothing_on_any_platform(self):
        for plat in ("linux", "darwin", "win32"):
            self.log.clear()
            rc = self.install(["--name", "A b"], dry_run=True, platform=plat, python="/usr/bin/python3", uid=501)
            self.assertEqual(rc, 0, plat)
            self.assertIn("A b", "\n".join(self.log))
        self.assertEqual(self.calls, [])
        self.assertEqual(os.listdir(self.home), [])
        self.assertFalse(os.path.exists(self.office))

    def test_linux_install_and_uninstall_in_temp_home(self):
        rc = self.install(platform="linux", python="/usr/bin/python3")
        self.assertEqual(rc, 0)
        unit = os.path.join(self.home, ".config", "systemd", "user", "pixel-antigravity.service")
        text = Path(unit).read_text()
        script = os.path.join(self.office, "pixel_antigravity.py")
        self.assertIn("ExecStart=/usr/bin/python3 %s\n" % script, text)
        self.assertIn("PIXEL_OFFICE_HOME=" + self.office, text)          # not the default home → passed on
        self.assertTrue(os.path.isfile(script))
        self.assertTrue(os.path.isfile(os.path.join(self.office, "pixel_agent.py")))   # the library it imports
        self.assertNotIn("--key", text)
        self.assertEqual(self.calls, [["systemctl", "--user", "daemon-reload"],
                                      ["systemctl", "--user", "enable", "pixel-antigravity.service"],
                                      ["systemctl", "--user", "restart", "pixel-antigravity.service"]])
        self.calls.clear()
        rc = self.install(uninstall=True, platform="linux")
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(unit))
        self.assertEqual(self.calls[0], ["systemctl", "--user", "disable", "--now", "pixel-antigravity.service"])

    def test_installed_copy_runs_from_office_home(self):
        self.install(platform="linux", python=sys.executable)
        r = subprocess.run([sys.executable, os.path.join(self.office, "pixel_antigravity.py"), "--help"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"--install-service", r.stdout)

    def test_unit_quoting(self):
        text = ag.antigravity_systemd_unit("/opt/py$HOME/python3", "/srv/a b/%x/pixel_antigravity.py",
                                           ["--name", 'A "q"'])
        self.assertIn('ExecStart="/opt/py$$HOME/python3" "/srv/a b/%%x/pixel_antigravity.py" --name "A \\"q\\""',
                      text)

    def test_quote_systemd_matches_the_connector_copy(self):
        from pixel_connector import service
        for v in ("/usr/bin/python3", "a b", 'q"q', "back\\slash", "%h/x", "$HOME", "", "ünï", "a,b=c:d@e+f"):
            self.assertEqual(ag._quote_systemd(v), service._quote_systemd(v), v)

    def test_macos_launchd_agent(self):
        import plistlib
        rc = self.install(["--name", "Gem"], platform="darwin", python="/usr/local/bin/python3", uid=501)
        self.assertEqual(rc, 0)
        plist = os.path.join(self.home, "Library", "LaunchAgents", "tr.pixeloffice.antigravity.plist")
        with open(plist, "rb") as f:
            d = plistlib.load(f)
        self.assertEqual(d["ProgramArguments"], ["/usr/local/bin/python3",
                                                 os.path.join(self.office, "pixel_antigravity.py"), "--name", "Gem"])
        self.assertTrue(d["RunAtLoad"])
        self.assertEqual(d["EnvironmentVariables"], {"PIXEL_OFFICE_HOME": self.office})
        self.assertEqual(self.calls, [["launchctl", "bootout", "gui/501/tr.pixeloffice.antigravity"],
                                      ["launchctl", "bootstrap", "gui/501", plist]])
        self.calls.clear()
        self.assertEqual(self.install(uninstall=True, platform="darwin", uid=501), 0)
        self.assertFalse(os.path.exists(plist))
        self.assertEqual(self.calls, [["launchctl", "bootout", "gui/501/tr.pixeloffice.antigravity"]])

    def test_windows_task_scheduler(self):
        rc = self.install(platform="win32", python="C:\\Py\\python.exe")
        self.assertEqual(rc, 0)
        create = self.calls[0]
        xml_path = os.path.join(self.office, "pixel-antigravity.task.xml")
        # Task Scheduler XML since Phase 20 / Package 7 (battery, no time limit, restart): tests/test_phase20_platform.py
        self.assertEqual(create, ["schtasks", "/Create", "/TN", "PixelOffice\\Antigravity", "/XML", xml_path, "/F"])
        xml = Path(xml_path).read_bytes().decode("utf-16")
        self.assertIn("<LogonTrigger>", xml)
        self.assertIn("<Command>C:\\Py\\pythonw.exe</Command>", xml)
        self.assertIn("pixel_antigravity.py", xml)
        self.assertEqual(self.calls[-1], ["schtasks", "/Run", "/TN", "PixelOffice\\Antigravity"])
        self.calls.clear()
        self.assertEqual(self.install(uninstall=True, platform="win32"), 0)
        self.assertEqual(self.calls[-1], ["schtasks", "/Delete", "/TN", "PixelOffice\\Antigravity", "/F"])

    def test_install_shims_adds_pixel_antigravity(self):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pa.cmd_install_shims(None), 0)
        names = os.listdir(os.path.join(self.office, "bin"))
        for shim in ("pixel-run", "pixel-proxy", "pixel-antigravity"):
            self.assertTrue(any(n.startswith(shim) for n in names), names)
        self.assertTrue(os.path.isfile(os.path.join(self.office, "pixel_antigravity.py")))
        if os.name != "nt":
            self.assertIn(" antigravity ", Path(self.office, "bin", "pixel-antigravity").read_text())

    def test_install_shims_without_the_watcher_file(self):
        alone = os.path.join(self.tmp, "alone")
        os.makedirs(alone)
        shutil.copyfile(AGENT_PY, os.path.join(alone, "pixel_agent.py"))
        r = subprocess.run([sys.executable, os.path.join(alone, "pixel_agent.py"), "install-shims"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = os.listdir(os.path.join(self.office, "bin"))
        self.assertTrue(any(n.startswith("pixel-run") for n in names))
        self.assertFalse(any(n.startswith("pixel-antigravity") for n in names), names)
        r = subprocess.run([sys.executable, os.path.join(alone, "pixel_agent.py"), "antigravity"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"pixel_antigravity.py", r.stderr)

    def test_bad_thresholds_are_rejected(self):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ag.cmd_antigravity(self.args("--idle-after", "0")), 2)


class SourceTests(unittest.TestCase):
    def test_python38_syntax_and_stdlib_only(self):
        tree = ast.parse(AG_PY.read_text(encoding="utf-8"), feature_version=(3, 8))
        allowed = set(getattr(sys, "stdlib_module_names", ())) or None
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0:
                mods = [(n.module or "").split(".")[0]]
            else:
                continue
            for m in mods:
                self.assertNotEqual(m, "pixel_connector")
                if allowed is not None and m != "__future__":
                    self.assertIn(m, allowed)

    def test_watcher_is_not_bundled_in_the_connector(self):
        src = (ROOT / "tools" / "build_connector.py").read_text(encoding="utf-8")
        self.assertNotIn("pixel_antigravity", src)


# --- the real CLI process: SIGTERM → offline ------------------------------------------------------------
class _Sink:
    def __init__(self):
        self.updates = []
        sink = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                sink.updates.extend(body["agents"] if "agents" in body else [body])
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@unittest.skipIf(os.name == "nt", "POSIX signals")
class CliTests(unittest.TestCase):
    def test_sigterm_sends_offline_via_pixel_agent(self):
        self._run([sys.executable, str(AGENT_PY), "antigravity"])

    def test_sigterm_sends_offline_via_own_script(self):
        self._run([sys.executable, str(AG_PY)])

    def _run(self, entry):
        brain = Brain()
        self.addCleanup(brain.close)
        sink = _Sink()
        self.addCleanup(sink.close)
        home = tempfile.mkdtemp(prefix="po-home-")
        self.addCleanup(shutil.rmtree, home, True)
        brain.write(CID, [user(0), plan(1, "run_command", cwd="/w/projX")])
        env = dict(os.environ, PIXEL_OFFICE_HOME=home, PIXEL_ANTIGRAVITY_DIR=brain.root)
        proc = subprocess.Popen(entry + ["--url", sink.url, "--owner", "T", "--interval", "0.2"], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not sink.updates:
                time.sleep(0.05)
            self.assertTrue(sink.updates, "no update arrived")
            self.assertEqual(sink.updates[0]["state"], "working")
            self.assertEqual(sink.updates[0]["tool"], "Bash")
            self.assertEqual(sink.updates[0]["project"], "projX")
            proc.send_signal(signal.SIGTERM)
            proc.wait(10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            proc.stderr.close()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(sink.updates[-1]["state"], "offline")
        self.assertEqual(sink.updates[-1]["source"], "antigravity")


if __name__ == "__main__":
    unittest.main()
