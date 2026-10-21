#!/usr/bin/env python3
"""Pixel Office — Antigravity IDE watcher (Phase 18). Python >= 3.8, stdlib only.

Antigravity has no hooks, so this tails the transcripts it writes and mirrors each conversation to one office agent:
    <root>/<conversation-id>/.system_generated/logs/transcript.jsonl      (one JSON step per line, append-only)

Needs `pixel_agent.py` in the same folder (it is the library that sends the updates):
    curl -fsSL <server>/hook/pixel_agent.py -o pixel_agent.py
    curl -fsSL <server>/hook/pixel_antigravity.py -o pixel_antigravity.py

CLI (either form):
    python3 pixel_antigravity.py [--name Antigravity] [--idle-after 60] [--sleep-after 180] [--gone-after 2700]
    python3 pixel_agent.py antigravity [same flags]                       (pixel-antigravity after install-shims)
    python3 pixel_antigravity.py --install-service [--dry-run]            # start at login: systemd / launchd / schtasks
    python3 pixel_antigravity.py --uninstall-service

Why a separate file: the watcher is only useful next to Antigravity, the connector (.pyz) bundles pixel_agent.py and
does not need it, and pixel_agent.py stays a small library (v2.md D6). This file imports pixel_agent from its own
folder; it never imports pixel_connector (that package is not on machines that only downloaded the hook files).

Privacy: only state, tool name (mapped / sanitised), the fixed label "Hata", the basename of the step's `Cwd`, the
agent name and owner leave this machine. Content, thinking, command lines, paths and tool arguments are dropped while
a line is parsed (parse_antigravity_step); nothing is written to disk. Transcripts are opened read-only.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_pixel_agent():
    """The `pixel_agent` already imported (pixel_agent.py's `antigravity` command registers itself), else the
    sibling file. Loaded by path, so a different `pixel_agent` elsewhere on sys.path is never picked up."""
    mod = sys.modules.get("pixel_agent")
    if mod is not None and hasattr(mod, "Agent"):
        return mod
    path = os.path.join(_HERE, "pixel_agent.py")
    if not os.path.isfile(path):
        raise ImportError("pixel_agent.py bulunamadı: %s klasörüne indirin (aynı sunucudan /hook/pixel_agent.py)"
                          % _HERE)
    spec = importlib.util.spec_from_file_location("pixel_agent", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pixel_agent"] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop("pixel_agent", None)
        raise
    return mod


pixel_agent = _load_pixel_agent()
Agent = pixel_agent.Agent
clean_project = pixel_agent.clean_project
clean_tool = pixel_agent.clean_tool
office_home = pixel_agent.office_home
L_ERROR = pixel_agent.L_ERROR
_text = pixel_agent._text
_debug = pixel_agent._debug

AG_SOURCE = "antigravity"
AG_DEFAULT_NAME = "Antigravity"
AG_ROOTS_ENV = "PIXEL_ANTIGRAVITY_DIR"
AG_DEFAULT_ROOTS = (os.path.join("~", ".gemini", "antigravity-ide", "brain"),
                    os.path.join("~", ".gemini", "antigravity", "brain"))
AG_TRANSCRIPT = os.path.join(".system_generated", "logs", "transcript.jsonl")
AG_IDLE_AFTER = 60.0        # s without a new step while working → idle (~2.5 % of in-turn pauses observed are longer)
AG_SLEEP_AFTER = 180.0      # s without a new step → sleeping (= the server's IDLE_TO_SLEEP; the 300 s keep-alive
#                             would otherwise flip a longer "idle" back and forth with the server's derived "sleeping")
AG_GONE_AFTER = 2700.0      # s without a new step → offline (= the server's REMOVE_AFTER for Claude Code sessions);
#                             conversations whose last step is older are not reported when they are discovered
AG_POLL = 1.0               # s
AG_SCAN_EVERY = 5.0         # s between directory scans for new conversations
AG_LINE_MAX = 1024 * 1024   # bytes; longer lines are skipped
AG_READ_MAX = 4 * 1024 * 1024   # bytes read per file per poll (the rest comes next poll)
AG_START_TAIL = 256 * 1024  # bytes read when a conversation is discovered, to find its current state
AG_MAX_TOOL_CALLS = 16
AG_SERVICE_NAME = "pixel-antigravity.service"
AG_LAUNCHD_LABEL = "tr.pixeloffice.antigravity"
AG_SCHTASK_NAME = "PixelOffice\\Antigravity"
AG_SCRIPT = "pixel_antigravity.py"
AG_TASK_XML_FILE = "pixel-antigravity.task.xml"
TASK_RESTART_COUNT = 3
TASK_RESTART_INTERVAL = "PT1M"
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
AG_ACTIVE = ("working", "thinking")
AG_KNOWN_SOURCES = ("MODEL", "USER_EXPLICIT", "SYSTEM")
# Antigravity tool names → the office's (Claude Code style) names, so the right bubble icon shows.
AG_TOOL_NAMES = {
    "run_command": "Bash", "send_command_input": "Bash", "read_terminal": "Bash", "command_status": "Bash",
    "view_file": "Read", "view_file_outline": "Read", "view_code_item": "Read", "list_dir": "LS",
    "replace_file_content": "Edit", "multi_replace_file_content": "Edit", "write_to_file": "Write",
    "grep_search": "Grep", "find_by_name": "Glob", "codebase_search": "Grep",
    "search_web": "WebSearch", "read_url_content": "WebFetch", "manage_task": "TodoWrite",
}
AG_STEP_TOOLS = {
    "RUN_COMMAND": "Bash", "VIEW_FILE": "Read", "LIST_DIRECTORY": "LS", "CODE_ACTION": "Edit", "GREP_SEARCH": "Grep",
    "SEARCH_WEB": "WebSearch", "READ_URL_CONTENT": "WebFetch",
}
_ISO_RE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(\.\d+)?(Z|[+-]\d\d:?\d\d)?$")


# --------------------------------------------------------------------------------------------------
# Parsing (the privacy boundary: nothing but the fields below survives a line)
# --------------------------------------------------------------------------------------------------

def antigravity_roots(roots: object = None) -> list:
    """--root values, else $PIXEL_ANTIGRAVITY_DIR (os.pathsep-separated), else both default install locations."""
    if roots:
        raw = list(roots)
    elif os.environ.get(AG_ROOTS_ENV):
        raw = [r for r in os.environ[AG_ROOTS_ENV].split(os.pathsep) if r]
    else:
        raw = list(AG_DEFAULT_ROOTS)
    out = []
    for r in raw:
        p = os.path.abspath(os.path.expanduser(r))
        if p not in out:
            out.append(p)
    return out


def _ag_unquote(v: str) -> str:
    """Antigravity writes some argument values JSON-quoted ('"/a/b"')."""
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        try:
            u = json.loads(v)
            return u if isinstance(u, str) else v
        except ValueError:
            return v
    return v


def parse_step_time(v: object) -> object:
    """`created_at` ("2026-10-19T15:25:15Z", fractions and offsets tolerated; no zone = UTC) → epoch s, else None."""
    if not isinstance(v, str):
        return None
    m = _ISO_RE.match(v.strip())
    if not m:
        return None
    try:
        y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
        t = calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))
        if not (1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 61):
            return None
    except (ValueError, OverflowError):
        return None
    frac, zone = m.group(7), m.group(8)
    if frac:
        t += float("0" + frac)
    if zone and zone != "Z":
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        t -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    return float(t)


def parse_antigravity_step(line: bytes) -> object:
    """One transcript line → {source, type, status, tools, project, at} or None. Nothing else survives this
    function: no content, thinking, error text, command line, path or other argument."""
    try:
        obj = json.loads(line.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None

    def s(k):
        v = obj.get(k)
        return v if isinstance(v, str) else None
    tools = []
    project = None
    calls = obj.get("tool_calls")
    if isinstance(calls, list):
        for c in calls[:AG_MAX_TOOL_CALLS]:
            if not isinstance(c, dict):
                continue
            if isinstance(c.get("name"), str):
                tools.append(c["name"])
            args = c.get("args")
            cwd = args.get("Cwd") if isinstance(args, dict) else None
            if isinstance(cwd, str) and cwd.strip():
                project = clean_project(_ag_unquote(cwd.strip())) or project
    return {"source": s("source"), "type": s("type"), "status": s("status"), "tools": tools, "project": project,
            "at": parse_step_time(obj.get("created_at"))}


def antigravity_tool(name: object) -> object:
    if not isinstance(name, str):
        return None
    return AG_TOOL_NAMES.get(name) or clean_tool(name)


def antigravity_state(step: dict) -> object:
    """A parsed step → (state, tool, label), or None for a step that says nothing (unknown source and no type).
    USER_INPUT and SYSTEM steps → thinking (the model is about to answer; "Hata" on ERROR_MESSAGE);
    a model step with tool calls → working (first tool); a tool-result step → working (tool from the step type);
    a finished model answer without tool calls → idle (the turn is over, the human is next; like Claude Code's Stop);
    a model step still RUNNING without tool calls → thinking. Unknown step types count as work."""
    src, typ, status = step.get("source"), step.get("type"), step.get("status")
    tools = step.get("tools") or []
    if src not in AG_KNOWN_SOURCES and typ is None and not tools:
        return None
    if src == "USER_EXPLICIT" or typ == "USER_INPUT":
        return "thinking", None, None
    if src == "SYSTEM":
        return "thinking", None, L_ERROR if typ == "ERROR_MESSAGE" else None
    if tools:
        return "working", antigravity_tool(tools[0]), None
    if typ == "PLANNER_RESPONSE":
        return ("thinking" if status == "RUNNING" else "idle"), None, None
    return "working", AG_STEP_TOOLS.get(typ or ""), None


# --------------------------------------------------------------------------------------------------
# Tailing
# --------------------------------------------------------------------------------------------------

def _parse_lines(body: bytes) -> list:
    return [p for p in (parse_antigravity_step(x) for x in body.split(b"\n")
                        if x.strip() and len(x) <= AG_LINE_MAX) if p is not None]


class TranscriptTail:
    """Reads the complete lines appended to one transcript since the last call. Read-only; survives truncation and
    replacement (starts over), partial last lines (left for the next call) and huge lines (skipped)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.offset = 0
        self.ident = None
        self.skipping = False

    def stat(self) -> object:
        try:
            return os.stat(self.path)
        except OSError:
            return None

    def seek_end(self, st) -> list:
        """Start after the current end. Returns the steps found in the last AG_START_TAIL bytes (to find the current
        state; history is never replayed as live activity). A trailing partial line is read once it is complete."""
        self.ident = (st.st_dev, st.st_ino)
        start = max(0, st.st_size - AG_START_TAIL)
        try:
            with open(self.path, "rb") as f:
                f.seek(start)
                data = f.read(st.st_size - start)
        except OSError:
            self.offset = st.st_size
            return []
        end = data.rfind(b"\n") + 1
        self.offset = start + end
        body = data[:end]
        if start > 0:
            body = body[body.find(b"\n") + 1:]            # the first line is probably cut
        return _parse_lines(body)

    def read_new(self, st=None) -> list:
        st = st if st is not None else self.stat()
        if st is None:
            return []
        ident = (st.st_dev, st.st_ino)
        if ident != self.ident or st.st_size < self.offset:
            self.ident, self.offset, self.skipping = ident, 0, False
        if st.st_size <= self.offset:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read(min(st.st_size - self.offset, AG_READ_MAX))
        except OSError:
            return []
        steps = []
        pos = 0
        while True:
            nl = data.find(b"\n", pos)
            if nl < 0:
                break
            line = data[pos:nl]
            if self.skipping:
                self.skipping = False
            elif line.strip() and len(line) <= AG_LINE_MAX:
                p = parse_antigravity_step(line)
                if p is not None:
                    steps.append(p)
            pos = nl + 1
        if len(data) - pos > AG_LINE_MAX:                 # a huge line still without its end: drop what we have
            self.skipping = True
            pos = len(data)
        self.offset += pos
        return steps


# --------------------------------------------------------------------------------------------------
# The watcher
# --------------------------------------------------------------------------------------------------

class _AgConv:
    __slots__ = ("key", "tail", "agent", "base", "last", "project", "shown")

    def __init__(self, key: str, path: str) -> None:
        self.key = key
        self.tail = TranscriptTail(path)
        self.agent = None
        self.base = None          # (state, tool, label) of the last step
        self.last = 0.0           # time of the last activity (clock time; a step's created_at at discovery)
        self.project = None
        self.shown = None         # what was last reported


class AntigravityWatcher:
    """Polls the transcript folders and mirrors each conversation to one office agent.

    Discovery (at startup, when a brain folder appears later, or for a new conversation) never replays history:
    only the tail of the file is parsed, the conversation's state comes from its last step, and the age of that
    step's `created_at` (file mtime when missing) runs through the same idle / sleeping / offline thresholds as live
    silence. Conversations older than `gone_after` stay quiet until they get a new step. After discovery only newly
    appended bytes are read.

    Naming: the most recent live conversation is `name`; the others get the lowest free number ("Antigravity 2", …),
    kept for the conversation's lifetime. Session: "ag-" + the first 10 hex digits of SHA-256(conversation id), so
    the raw id never leaves the machine."""

    def __init__(self, agent_factory, roots: list, name: str = AG_DEFAULT_NAME, idle_after: float = AG_IDLE_AFTER,
                 sleep_after: float = AG_SLEEP_AFTER, gone_after: float = AG_GONE_AFTER, project: object = None,
                 clock=time.time, scan_every: float = AG_SCAN_EVERY) -> None:
        self.agent_factory = agent_factory
        self.roots = list(roots)
        self.name = _text(name, 24) or AG_DEFAULT_NAME
        self.idle_after, self.sleep_after, self.gone_after = idle_after, sleep_after, gone_after
        self.fixed_project = clean_project(project)
        self.clock = clock
        self.scan_every = scan_every
        self.convs = {}
        self.numbers = {}         # conversation key → name number (1 = plain name)
        self._scanned_at = None

    # -- discovery ---------------------------------------------------------------------------------
    def _discover(self) -> list:
        found = []
        for root in self.roots:
            try:
                with os.scandir(root) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                path = os.path.join(e.path, AG_TRANSCRIPT)
                if os.path.isfile(path):
                    found.append((path, e.name))
        return found

    @staticmethod
    def _last_activity(steps: list, st, now: float) -> float:
        """The last step's created_at (the step's start, so a long step looks older: never falsely "active"), else
        the file's mtime; never in the future."""
        for step in reversed(steps):
            if step.get("at") is not None:
                return min(step["at"], now)
        return min(st.st_mtime, now)

    def scan(self, now: float) -> None:
        self._scanned_at = now
        fresh = []
        for path, cid in self._discover():
            if path in self.convs:
                continue
            conv = _AgConv(cid, path)
            st = conv.tail.stat()
            if st is None:
                continue
            steps = conv.tail.seek_end(st)
            fresh.append((self._last_activity(steps, st, now), path, conv, steps))
        fresh.sort(key=lambda x: -x[0])                   # the most recent conversation gets the plain name
        for at, path, conv, steps in fresh:
            self.convs[path] = conv
            if steps and now - at < self.gone_after:
                self._apply(conv, steps, at)

    # -- state -------------------------------------------------------------------------------------
    def _apply(self, conv: _AgConv, steps: list, at: float) -> None:
        changed = False
        for st in steps:
            mapped = antigravity_state(st)
            if mapped is None:
                continue
            if st.get("project"):
                conv.project = st["project"]
            state, tool, label = mapped
            if state == "working" and tool is None and conv.base is not None and conv.base[0] == "working":
                tool = conv.base[1]                     # GENERIC result steps keep the tool that produced them
            conv.base = (state, tool, label)
            changed = True
        if changed:
            conv.last = at

    def effective(self, conv: _AgConv, now: float) -> object:
        if conv.base is None:
            return None
        age = now - conv.last
        if age >= self.gone_after:
            return ("offline", None, None)
        state, tool, label = conv.base
        if state in AG_ACTIVE and age >= self.idle_after:
            state, tool, label = "idle", None, None
        if state == "idle" and age >= self.sleep_after:
            state, tool, label = "sleeping", None, None
        return state, tool, label

    def _number(self, key: str) -> int:
        used = set(self.numbers.values())
        n = 1
        while n in used:
            n += 1
        self.numbers[key] = n
        return n

    def _agent_for(self, conv: _AgConv):
        if conv.agent is None:
            n = self._number(conv.key)
            suffix = " %d" % n
            name = self.name if n == 1 else self.name[:24 - len(suffix)].rstrip() + suffix
            session = "ag-" + hashlib.sha256(conv.key.encode("utf-8", "replace")).hexdigest()[:10]
            conv.agent = self.agent_factory(name, session)
        return conv.agent

    def _show(self, conv: _AgConv, now: float) -> None:
        eff = self.effective(conv, now)
        if eff is None or eff == conv.shown:
            return
        if eff[0] == "offline":
            if conv.agent is not None:
                conv.agent.report("offline")
            self._forget(conv)
            return
        agent = self._agent_for(conv)
        try:
            agent.project = self.fixed_project or conv.project
        except Exception:
            pass
        agent.report(eff[0], tool=eff[1], label=eff[2])
        conv.shown = eff

    def _forget(self, conv: _AgConv) -> None:
        conv.agent = None
        conv.shown = None
        conv.base = None
        self.numbers.pop(conv.key, None)

    # -- loop --------------------------------------------------------------------------------------
    def poll(self) -> None:
        now = self.clock()
        if self._scanned_at is None or now - self._scanned_at >= self.scan_every:
            self.scan(now)
        for path, conv in list(self.convs.items()):
            st = conv.tail.stat()
            if st is None:                              # conversation deleted
                if conv.agent is not None:
                    conv.agent.report("offline")
                self._forget(conv)
                del self.convs[path]
                continue
            steps = conv.tail.read_new(st)
            if steps:
                self._apply(conv, steps, now)
            self._show(conv, now)

    def shutdown(self, timeout: float = 1.0) -> None:
        agents = [c.agent for c in self.convs.values() if c.agent is not None]
        for a in agents:
            try:
                a.report("offline")
            except Exception:
                pass
        for a in agents:
            try:
                a.flush(timeout)
                a.finished = True
            except Exception:
                pass
        for c in self.convs.values():
            self._forget(c)


# --------------------------------------------------------------------------------------------------
# Start at login (systemd user unit / launchd agent / Task Scheduler)
# --------------------------------------------------------------------------------------------------

def _quote_systemd(arg: str) -> str:
    """One ExecStart argument (systemd.syntax). A deliberate copy of pixel_connector/service.py:_quote_systemd: this
    file is downloaded on its own and cannot import pixel_connector (tests/test_phase18_antigravity.py checks that
    both give the same result)."""
    a = arg.replace("%", "%%").replace("$", "$$")
    if a and all(c.isalnum() or c in "/._-+:=@," for c in a):
        return a
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"'


def antigravity_service_args(a) -> list:
    """Watcher flags that go into the service definition. Target flags (--url/--room/--key/--owner) never do: the
    service reads ~/.pixel-office/config.json, so no key ends up in a unit file."""
    out = []
    if a.name != AG_DEFAULT_NAME:
        out += ["--name", a.name]
    for flag, v, d in (("--idle-after", a.idle_after, AG_IDLE_AFTER), ("--sleep-after", a.sleep_after, AG_SLEEP_AFTER),
                       ("--gone-after", a.gone_after, AG_GONE_AFTER)):
        if v != d:
            out += [flag, "%g" % v]
    if a.project:
        out += ["--project", a.project]
    for r in a.root or []:
        out += ["--root", os.path.abspath(os.path.expanduser(r))]
    return out


def antigravity_systemd_unit(python: str, script: str, extra: list, home_override: object = None) -> str:
    exec_start = " ".join(_quote_systemd(x) for x in [python, script] + list(extra))
    env = ["Environment=PYTHONUNBUFFERED=1"]
    if home_override:
        env.append("Environment=%s" % _quote_systemd("PIXEL_OFFICE_HOME=" + home_override))
    return "\n".join(["[Unit]", "Description=Pixel Office Antigravity izleyicisi", "", "[Service]", "Type=simple",
                      "ExecStart=" + exec_start, "Restart=on-failure", "RestartSec=10", "UMask=0077",
                      "NoNewPrivileges=yes"] + env + ["", "[Install]", "WantedBy=default.target", ""])


def antigravity_launchd_plist(python: str, script: str, extra: list, log_dir: str,
                              home_override: object = None) -> bytes:
    import plistlib
    d = {
        "Label": AG_LAUNCHD_LABEL, "ProgramArguments": [python, script] + list(extra),
        "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ProcessType": "Background",
        "StandardOutPath": os.path.join(log_dir, "antigravity.out.log"),
        "StandardErrorPath": os.path.join(log_dir, "antigravity.err.log"),
    }
    if home_override:
        d["EnvironmentVariables"] = {"PIXEL_OFFICE_HOME": home_override}
    return plistlib.dumps(d)


# Windows: a Task Scheduler XML instead of `schtasks /SC ONLOGON` defaults (v2.md H10). Deliberate copies of
# pixel_connector/service.py (this file can't import pixel_connector); tests/test_phase20_platform.py checks that both
# give the same result.

def windows_quote(arg: str) -> str:
    """One argument for a Windows command line (CommandLineToArgvW / the MSVC runtime rules, as
    subprocess.list2cmdline): always in double quotes, backslashes before a quote and at the end doubled."""
    out, slashes = [], 0
    for ch in arg:
        if ch == "\\":
            slashes += 1
            continue
        if ch == '"':
            out.append("\\" * (slashes * 2 + 1) + '"')
        else:
            out.append("\\" * slashes + ch)
        slashes = 0
    return '"' + "".join(out) + "\\" * (slashes * 2) + '"'


# Sets environment variables inside the started Python and then runs the target like `python <target> …` would.
# Used instead of a .cmd wrapper / `cmd /c set …`: cmd.exe would open a console window next to pythonw, and its
# quoting and code-page rules are easy to get wrong with paths such as C:\Users\Ömer Şahin. No double quotes inside,
# so it survives windows_quote() unchanged. argv: -c CODE <n> NAME1 VALUE1 … <target> <args…>
_WIN_ENV_LAUNCHER = ("import os,runpy,sys;a=sys.argv;n=int(a[1]);"
                     "os.environ.update(zip(a[2:2+2*n:2],a[3:3+2*n:2]));"
                     "sys.argv=a[2+2*n:];runpy.run_path(sys.argv[0],run_name='__main__')")


def windows_action(pythonw: str, target: str, args: list, env: object = None) -> tuple:
    """→ (Command, Arguments) of the task's Exec action: `pythonw <target> <args…>`, or with `env` the in-process
    launcher above (Task Scheduler has no per-task environment variables)."""
    argv = []
    if env:
        argv = ["-c", _WIN_ENV_LAUNCHER, str(len(env))]
        for k in sorted(env):
            argv += [k, env[k]]
    argv += [target] + list(args)
    return pythonw, " ".join(windows_quote(a) for a in argv)


def windows_user_id(environ: object = None) -> str:
    """DOMAIN\\user of the installing user (logon trigger + principal of the task)."""
    env = os.environ if environ is None else environ
    user = env.get("USERNAME") or ""
    if not user:
        import getpass
        user = getpass.getuser()
    dom = env.get("USERDOMAIN") or ""
    return dom + "\\" + user if dom else user


def task_scheduler_xml(command: str, arguments: str, user_id: str, description: str,
                       working_dir: object = None) -> str:
    """Task Scheduler 1.2 definition (v2.md H10): at logon of this user only, with the user's normal (limited)
    rights and an interactive token, so registering it needs no administrator and stores no password. Unlike the
    `schtasks /Create /SC ONLOGON` defaults it runs on battery and keeps running when the laptop unplugs, has no
    72-hour execution limit, ignores a second start while running and is restarted 3× one minute apart if it fails.
    The element order follows what Windows itself exports."""
    from xml.sax.saxutils import escape as e
    wd = "      <WorkingDirectory>%s</WorkingDirectory>\n" % e(working_dir) if working_dir else ""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="%s">\n'
        "  <RegistrationInfo>\n"
        "    <Description>%s</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        "      <UserId>%s</UserId>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        "      <UserId>%s</UserId>\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <IdleSettings>\n"
        "      <StopOnIdleEnd>false</StopOnIdleEnd>\n"
        "      <RestartOnIdle>false</RestartOnIdle>\n"
        "    </IdleSettings>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        "    <WakeToRun>false</WakeToRun>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>%s</Interval>\n"
        "      <Count>%d</Count>\n"
        "    </RestartOnFailure>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        "      <Command>%s</Command>\n"
        "      <Arguments>%s</Arguments>\n"
        "%s"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    ) % (TASK_NS, e(description), e(user_id), e(user_id), TASK_RESTART_INTERVAL, TASK_RESTART_COUNT, e(command),
         e(arguments), wd)


def task_xml_bytes(xml: str) -> bytes:
    """schtasks /XML reads UTF-16 with a byte order mark (the encoding Windows itself exports)."""
    return xml.encode("utf-16")


def pythonw_for(python: str) -> str:
    """pythonw.exe next to python.exe (no console window). Unchanged when the name isn't python.exe."""
    cut = max(python.rfind("\\"), python.rfind("/")) + 1
    return python[:cut] + "pythonw.exe" if python[cut:].lower() == "python.exe" else python


def antigravity_schtasks_argv(xml_path: str) -> list:
    return ["schtasks", "/Create", "/TN", AG_SCHTASK_NAME, "/XML", xml_path, "/F"]


def install_files() -> str:
    """Copy this file and pixel_agent.py to ~/.pixel-office/ (the stable paths services and shims point to)."""
    home = office_home()
    os.makedirs(home, exist_ok=True)
    import shutil
    for src, name in ((os.path.abspath(__file__), AG_SCRIPT),
                      (os.path.abspath(getattr(pixel_agent, "__file__", "") or ""), "pixel_agent.py")):
        dst = os.path.join(home, name)
        if os.path.isfile(src) and src != os.path.abspath(dst):
            shutil.copyfile(src, dst)
    return os.path.join(home, AG_SCRIPT)


def install_antigravity_service(extra: list, dry_run: bool = False, uninstall: bool = False, log=print,
                                platform: object = None, user_home: object = None, python: object = None,
                                runner=subprocess.run, uid: object = None, environ: object = None) -> int:
    """Start the watcher at login, or remove that. Linux: systemd user unit; macOS: launchd agent; Windows: a Task
    Scheduler task at logon (pythonw, no window). The same commands as pixel_connector/service.py."""
    platform = platform or sys.platform
    python = python or sys.executable or "python3"
    home = user_home or os.path.expanduser("~")
    script = os.path.join(office_home(), AG_SCRIPT)
    default_home = os.path.join(home, ".pixel-office")
    override = None if os.path.realpath(office_home()) == os.path.realpath(default_home) else office_home()

    def run(argv):
        log("$ " + " ".join(argv))
        if dry_run:
            return 0
        try:
            return runner(argv, stdin=subprocess.DEVNULL).returncode
        except OSError as e:
            log("  hata: %s" % e)
            return 127

    def remove(path):
        log("siliniyor: %s" % path)
        if not dry_run:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def write(path, data, show):
        if dry_run:
            log(show)
            return
        install_files()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    if platform.startswith("linux"):
        unit_dir = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "systemd", "user")
        path = os.path.join(unit_dir, AG_SERVICE_NAME)
        if uninstall:
            run(["systemctl", "--user", "disable", "--now", AG_SERVICE_NAME])
            remove(path)
            return run(["systemctl", "--user", "daemon-reload"])
        text = antigravity_systemd_unit(python, script, extra, override)
        log("systemd kullanıcı birimi: %s" % path)
        write(path, text.encode("utf-8"), text)
        rc = run(["systemctl", "--user", "daemon-reload"])
        if rc == 0:
            rc = run(["systemctl", "--user", "enable", AG_SERVICE_NAME])
        if rc == 0:
            rc = run(["systemctl", "--user", "restart", AG_SERVICE_NAME])
        return rc
    if platform == "darwin":
        path = os.path.join(home, "Library", "LaunchAgents", AG_LAUNCHD_LABEL + ".plist")
        uid = uid if uid is not None else (os.getuid() if hasattr(os, "getuid") else 0)
        if uninstall:
            rc = run(["launchctl", "bootout", "gui/%d/%s" % (uid, AG_LAUNCHD_LABEL)])
            remove(path)
            return rc
        data = antigravity_launchd_plist(python, script, extra, office_home(), override)
        log("launchd ajanı: %s" % path)
        write(path, data, data.decode("utf-8"))
        run(["launchctl", "bootout", "gui/%d/%s" % (uid, AG_LAUNCHD_LABEL)])     # unload an old one (ignored)
        return run(["launchctl", "bootstrap", "gui/%d" % uid, path])
    if platform == "win32":
        if uninstall:
            run(["schtasks", "/End", "/TN", AG_SCHTASK_NAME])
            return run(["schtasks", "/Delete", "/TN", AG_SCHTASK_NAME, "/F"])
        env = {"PIXEL_OFFICE_HOME": override} if override else None
        command, arguments = windows_action(pythonw_for(python), script, extra, env)
        xml = task_scheduler_xml(command, arguments, windows_user_id(environ), "Pixel Office Antigravity izleyicisi",
                                 office_home())
        path = os.path.join(office_home(), AG_TASK_XML_FILE)
        log("Görev Zamanlayıcı tanımı: %s" % path)
        write(path, task_xml_bytes(xml), xml)
        rc = run(antigravity_schtasks_argv(path))
        if rc == 0:
            run(["schtasks", "/End", "/TN", AG_SCHTASK_NAME])                     # stop an old instance (ignored)
            rc = run(["schtasks", "/Run", "/TN", AG_SCHTASK_NAME])
        return rc
    log("Bu işletim sistemi için servis kurulumu yok: %s" % platform)
    return 2


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------

def build_parser(prog: str = AG_SCRIPT) -> argparse.ArgumentParser:
    ag = argparse.ArgumentParser(prog=prog, description="pixel-antigravity: show Antigravity IDE conversations in "
                                                        "the office (reads its transcripts, sends only states)")
    ag.add_argument("--name", default=AG_DEFAULT_NAME, help="agent name (more conversations: \"<name> 2\", …)")
    ag.add_argument("--project", help="fixed project name (default: last folder of the agent's working directory)")
    ag.add_argument("--root", action="append",
                    help="brain folder to watch (repeatable; default: env %s, else ~/.gemini/antigravity-ide/brain "
                         "and ~/.gemini/antigravity/brain)" % AG_ROOTS_ENV)
    ag.add_argument("--idle-after", type=float, default=AG_IDLE_AFTER, help="s without steps while working → idle")
    ag.add_argument("--sleep-after", type=float, default=AG_SLEEP_AFTER, help="s without steps → sleeping")
    ag.add_argument("--gone-after", type=float, default=AG_GONE_AFTER, help="s without steps → leaves the office")
    ag.add_argument("--interval", type=float, default=AG_POLL, help="poll interval in s")
    ag.add_argument("--install-service", action="store_true",
                    help="start at login (Linux: systemd user unit, macOS: launchd, Windows: Task Scheduler)")
    ag.add_argument("--uninstall-service", action="store_true")
    ag.add_argument("--dry-run", action="store_true", help="with --install-service: only print what would be done")
    pixel_agent._add_target_args(ag)
    return ag


def cmd_antigravity(a) -> int:
    for flag, v in (("--idle-after", a.idle_after), ("--sleep-after", a.sleep_after), ("--gone-after", a.gone_after),
                    ("--interval", a.interval)):
        if not v > 0:
            sys.stderr.write("pixel-antigravity: %s sıfırdan büyük olmalı\n" % flag)
            return 2
    if a.install_service or a.uninstall_service:
        if a.url or a.room or a.key or a.owner:
            print("Not: servis --url/--room/--key/--owner kullanmaz; ~/.pixel-office/config.json dosyasını okur.")
        return install_antigravity_service(antigravity_service_args(a), dry_run=a.dry_run,
                                           uninstall=a.uninstall_service)
    roots = antigravity_roots(a.root)

    def factory(name, session):
        return Agent(name, session=session, url=a.url, room=a.room, key=a.key, owner=a.owner, source=AG_SOURCE,
                     register_atexit=False)
    watcher = AntigravityWatcher(factory, roots, name=a.name, idle_after=a.idle_after, sleep_after=a.sleep_after,
                                 gone_after=a.gone_after, project=a.project)
    if not any(os.path.isdir(r) for r in roots):
        print("pixel-antigravity: Antigravity klasörü henüz yok, bekleniyor: " + ", ".join(roots), flush=True)
    else:
        print("pixel-antigravity: izleniyor: " + ", ".join(r for r in roots if os.path.isdir(r)), flush=True)

    def _term(*_):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _term)
            except (ValueError, OSError):
                pass
    try:
        while True:
            try:
                watcher.poll()
            except KeyboardInterrupt:
                raise
            except Exception as e:                       # keep watching; one bad poll must not end the service
                _debug("antigravity poll: %s" % type(e).__name__)
            time.sleep(a.interval)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.shutdown(1.0)
    return 0


def main(argv: object = None, prog: str = AG_SCRIPT) -> int:
    a = build_parser(prog).parse_args(list(sys.argv[1:] if argv is None else argv))
    return cmd_antigravity(a)


if __name__ == "__main__":
    sys.exit(main())
