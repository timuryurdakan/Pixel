"""Claude Code headless adapter (V3.md §8.1).

    <claude> -p --output-format stream-json --verbose --include-partial-messages
             --permission-mode <dontAsk|auto> [--permission-prompts none]
             [--allowedTools …] --disallowedTools …  (--session-id <uuid> | --resume <uuid>)
             [--model <m>] --max-turns <n> [--max-budget-usd <x>] [--bare]

The prompt goes to stdin (never argv). No shell. `bypassPermissions` / `--dangerously-skip-permissions` have no code
path here: `build_argv` asserts their absence and tests grep this file. cwd = the resolved workspace; the child env is
built from an allow-list (`child_env`, Phase 20) plus PIXEL_OFFICE_DISABLE=1 (no duplicate office character from a v2
hook) and pinned git settings (GIT_CONFIG_COUNT/KEY/VALUE).
Cancel: SIGINT to the process group → SIGTERM after 5 s → SIGKILL after 10 s more.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .base import Cached, Running, TaskContext, empty_usage, short

MIN_PERMISSION_PROMPTS = (2, 1, 259)
MIN_CUMULATIVE_RESUME = (2, 1, 277)
BUILTIN_DENY = ["Bash(sudo *)", "Read(~/.ssh/**)", "Read(~/.gnupg/**)", "Read(~/.aws/**)", "Read(~/.pixel-office/**)",
                "Edit(~/.pixel-office/**)", "Read(~/.claude/.credentials.json)"]
# Phase 16 (docs/SECURITY_AUDIT.md S16-01): files where an *edit* turns into code execution later, outside the
# profile's promise ("edit" = no shell) and mostly invisible in `git diff`: git hooks/config (core.fsmonitor,
# core.hooksPath run on the next `git status`, which the dev profile allows), Claude Code settings with hooks and
# MCP servers (run by the next `claude -p` without a trust dialog), editor tasks/settings (.vscode) and direnv
# (.envrc) files that run when the folder is opened or entered (often gitignored), shell start-up files, user
# services/autostart, PATH shims, the Claude Code binary itself (~/.local/share/claude, VS Code extension builds). `Edit(...)` rules
# cover every built-in file-editing tool (Edit, Write, NotebookEdit). Both the absolute (`//`) and the cwd-relative
# form are listed, so a parent repository's .git is covered as well.
# Phase 20 (SECURITY_AUDIT.md S20-01, v2.md H1) closes what S16-01 left open. The rule for adding a path here: an edit
# to it runs code *without a further deliberate step by the user* (next git command, next `claude -p`, opening or
# entering the folder in an editor/shell) or it lives outside version control. Tracked build files (Makefile,
# package.json scripts, .husky/*) stay editable: that is the feature, and they show up in `git diff`.
#  - a `.git` *file* (`gitdir: <dir>`) redirects git to a directory whose config the agent wrote
#    (core.fsmonitor runs on the next `git status`, which the dev profile allows) → `**/.git` as well as `**/.git/**`;
#  - the whole `.claude/` tree: hook scripts, commands, agents, skills and output styles that settings*.json point at;
#  - JetBrains `.idea/` (run configurations, startup tasks), VS Code `*.code-workspace` (tasks/settings like
#    `.vscode/`), `.devcontainer/` (postCreate/postStart commands when the folder is reopened in a container);
#  - `~/.npmrc` (`script-shell`, `node-options` apply to every npm run outside the workspace).
EXEC_SENSITIVE_DENY = [
    "Edit(//**/.git/**)", "Edit(**/.git/**)", "Edit(//**/.git)", "Edit(**/.git)",
    "Edit(//**/.claude/settings.json)", "Edit(//**/.claude/settings.local.json)", "Edit(//**/.mcp.json)",
    "Edit(**/.claude/settings.json)", "Edit(**/.claude/settings.local.json)", "Edit(**/.mcp.json)",
    "Edit(//**/.claude/**)", "Edit(**/.claude/**)",
    "Edit(//**/.vscode/**)", "Edit(**/.vscode/**)", "Edit(//**/*.code-workspace)", "Edit(**/*.code-workspace)",
    "Edit(//**/.idea/**)", "Edit(**/.idea/**)", "Edit(//**/.devcontainer/**)", "Edit(**/.devcontainer/**)",
    "Edit(//**/.envrc)", "Edit(**/.envrc)",
    "Edit(~/.claude/**)", "Edit(~/.claude.json)", "Edit(~/.ssh/**)", "Edit(~/.gitconfig)", "Edit(~/.config/**)",
    "Edit(~/.local/bin/**)", "Edit(~/.local/share/**)", "Edit(~/.vscode/**)", "Edit(~/AppData/**)",
    "Edit(~/Library/LaunchAgents/**)", "Edit(~/.npmrc)",
    "Edit(~/.bashrc)", "Edit(~/.bash_profile)", "Edit(~/.bash_login)", "Edit(~/.profile)",
    "Edit(~/.zshrc)", "Edit(~/.zprofile)", "Edit(~/.zshenv)",
]
# Credentials that a prompt-injected run should not even read (it could copy them into the workspace). Phase 20 (H30):
# other agents' logins (Gemini CLI, Codex), package-registry tokens, desktop keyrings and browser profiles (cookies,
# saved passwords), cloud CLIs. Every directory here is also a denied workspace root (workspaces.HOME_DENY_DIRS; a
# test keeps the two lists consistent). macOS browser profiles live under "Application Support" (a space, which these
# rules can't express); ~/Library/Keychains is covered.
SECRET_READ_DENY = ["Read(~/.claude.json)", "Read(~/.config/gh/**)", "Read(~/.netrc)", "Read(~/.git-credentials)",
                    "Read(~/.docker/config.json)", "Read(~/.kube/**)",
                    "Read(~/.gemini/**)", "Read(~/.codex/**)", "Read(~/.npmrc)", "Read(~/.pypirc)",
                    "Read(~/.cargo/credentials)", "Read(~/.cargo/credentials.toml)", "Read(~/.config/gcloud/**)",
                    "Read(~/.azure/**)", "Read(~/.local/share/keyrings/**)", "Read(~/Library/Keychains/**)",
                    "Read(~/.mozilla/**)", "Read(~/.config/google-chrome/**)", "Read(~/.config/chromium/**)"]
BUILTIN_DENY = BUILTIN_DENY + EXEC_SENSITIVE_DENY + SECRET_READ_DENY
EDIT_TOOLS = ["Edit", "Write", "NotebookEdit"]
FORBIDDEN_ARGS = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "bypassPermissions")
RESUME_MISSING_RE = re.compile(r"no conversation found|session[^\n]{0,40}not found|not found[^\n]{0,40}session", re.I)
AUTH_MAP = {"claude.ai": "subscription", "oauth_token": "subscription", "api_key": "api_key",
            "api_key_helper": "api_key", "none": "unknown"}


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


# Phase 20 (v2.md H7): the child env is an allow-list, so GH_TOKEN, AWS_*, OpenAI keys and whatever else the user
# exported never reach a run that may be prompt-injected and has a shell. What stays is what Claude Code (Node) needs:
# locating itself and its config (PATH, HOME, ~/.claude via HOME/USERPROFILE), locale, terminal, temp dirs, time zone,
# the Windows system variables a process needs to start, its own configuration and API auth (ANTHROPIC_*, CLAUDE_*),
# corporate proxies and CA bundles. Names are compared upper-cased (Windows env names are case-insensitive).
# Not passed (documented limit): cloud-provider credentials in the env (AWS_* for Bedrock, GOOGLE_APPLICATION_CREDENTIALS
# for Vertex); use the provider's credential files instead.
CHILD_ENV_NAMES = frozenset((
    "PATH", "HOME", "USER", "LOGNAME", "USERNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "COLORTERM",
    "TMPDIR", "TEMP", "TMP", "TZ",
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "WINDIR", "HOMEDRIVE", "HOMEPATH",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
))
CHILD_ENV_PREFIXES = ("LC_", "XDG_", "ANTHROPIC_", "CLAUDE_")
# Never handed over even if an allowed prefix matched (Phase 15: the connector's own and server-side secrets).
SECRET_ENV_PREFIXES = ("PIXEL_", "UPSTASH_", "KV_REST_")
SECRET_ENV_NAMES = ("RELAY_SECRET",)
# Command-scope git config beats repository config, so a config the agent managed to plant (S20-01) can't run code via
# git commands the dev profile allows. Inherited GIT_* variables (GIT_CONFIG_*, GIT_DIR, GIT_SSH_COMMAND …) are
# dropped by the allow-list on purpose, so these pins replace rather than append to any user-set GIT_CONFIG_COUNT.
# `diff.external` is not pinned: git has no "unset" value for it and an empty one makes every `git diff` fail
# ("cannot run"); it can only come from repository config, which the Edit rules above cover. hooksPath = the null
# device (/dev/null, or NUL on Windows) is a path with no hook files under it, so no hook runs.
GIT_CONFIG_PINS = (("core.fsmonitor", "false"), ("core.hooksPath", os.devnull), ("core.pager", "cat"))


def _child_env_allowed(name: str) -> bool:
    n = name.upper()
    if n.startswith(SECRET_ENV_PREFIXES) or n in SECRET_ENV_NAMES:
        return False
    return n in CHILD_ENV_NAMES or n.startswith(CHILD_ENV_PREFIXES)


def child_env(base: Optional[Dict[str, str]] = None, drop: Iterable[str] = ()) -> Dict[str, str]:
    """Environment for every Claude Code process. `drop`: further names to remove (configured `apiKeyEnv`s)."""
    dropped = {d.upper() for d in drop}
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if _child_env_allowed(k) and k.upper() not in dropped}
    env["PIXEL_OFFICE_DISABLE"] = "1"
    env["GIT_CONFIG_COUNT"] = str(len(GIT_CONFIG_PINS))
    for i, (key, value) in enumerate(GIT_CONFIG_PINS):
        env["GIT_CONFIG_KEY_%d" % i] = key
        env["GIT_CONFIG_VALUE_%d" % i] = value
    return env


def build_argv(path: str, profile: str, profiles: dict, session_id: str, resume: bool, model: Optional[str],
               max_turns: int, max_budget_usd: Optional[float], version: Optional[Tuple[int, int, int]],
               bare: bool = False) -> List[str]:
    """argv for one run. Raises ValueError for an unknown/disabled profile."""
    if profile not in ("readonly", "edit", "dev", "auto"):
        raise ValueError("unknown profile")
    argv = [path, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    if profile == "auto":
        if not profiles.get("auto", {}).get("enabled"):
            raise ValueError("auto profile is disabled on this device")
        argv += ["--permission-mode", "auto"]
    else:
        argv += ["--permission-mode", "dontAsk"]
    if version is None or version >= MIN_PERMISSION_PROMPTS:
        argv += ["--permission-prompts", "none"]
    allow: List[str] = []
    if profile in ("edit", "dev"):
        allow += EDIT_TOOLS
    if profile == "dev":
        allow += list(profiles.get("dev", {}).get("allow") or [])
    if allow:
        argv += ["--allowedTools"] + allow
    deny = BUILTIN_DENY + list(profiles.get(profile, {}).get("deny") or [])
    argv += ["--disallowedTools"] + deny
    argv += ["--resume" if resume else "--session-id", session_id]
    if model:
        argv += ["--model", model]
    argv += ["--max-turns", str(int(max_turns))]
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", "%.2f" % max_budget_usd]
    if bare:
        argv.append("--bare")
    for a in argv:
        if any(f in a for f in FORBIDDEN_ARGS):        # defence in depth; config validation already rejects it
            raise ValueError("forbidden permission flag")
    return argv


def tool_summary(name: str, inp, cwd: Optional[str]) -> str:
    """Short, device-computed description of a tool call (≤ 200 chars, E2E only)."""
    if not isinstance(inp, dict):
        return ""
    v = ""
    if name in ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit"):
        p = inp.get("file_path") or inp.get("notebook_path") or ""
        if isinstance(p, str) and cwd and os.path.isabs(p):
            try:
                rel = os.path.relpath(p, cwd)
                p = rel if not rel.startswith("..") else p
            except ValueError:
                pass
        v = p
    elif name in ("Grep", "Glob"):
        v = inp.get("pattern") or ""
    elif name in ("Bash", "PowerShell"):
        v = short(inp.get("command") or "", 120)
    elif name == "WebFetch":
        try:
            from urllib.parse import urlsplit
            v = urlsplit(str(inp.get("url") or "")).hostname or ""
        except ValueError:
            v = ""
    elif name == "WebSearch":
        v = short(inp.get("query") or "", 80)
    elif name in ("Agent", "Task"):
        v = inp.get("description") or ""
    return short(v, 200)


class Canceller:
    """SIGINT → (grace) → SIGTERM → (term) → SIGKILL on the child's process group. `wait(timeout)` → True when the
    process has exited. Injectable for tests (fake clock / fake signals)."""

    def __init__(self, proc, grace: float = 5.0, term: float = 10.0,
                 signal_fn: Optional[Callable[[int], None]] = None,
                 wait_fn: Optional[Callable[[float], bool]] = None) -> None:
        self.proc = proc
        self.grace, self.term = grace, term
        self.signal_fn = signal_fn or self._signal
        self.wait_fn = wait_fn or self._wait
        self.sent: List[str] = []
        self._started = False
        self._lock = threading.Lock()

    def _signal(self, sig: int) -> None:
        try:
            if sys.platform == "win32":
                if sig == getattr(signal, "CTRL_BREAK_EVENT", None):
                    self.proc.send_signal(sig)
                else:
                    self.proc.terminate()
            else:
                os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _wait(self, timeout: float) -> bool:
        try:
            self.proc.wait(timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def run(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        if sys.platform == "win32":
            steps = [("CTRL_BREAK", getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM), self.grace),
                     ("TERM", signal.SIGTERM, self.term)]
        else:
            steps = [("INT", signal.SIGINT, self.grace), ("TERM", signal.SIGTERM, self.term),
                     ("KILL", signal.SIGKILL, None)]
        for name, sig, wait in steps:
            self.sent.append(name)
            self.signal_fn(sig)
            if wait is None or self.wait_fn(wait):
                return

    def start(self) -> None:
        threading.Thread(target=self.run, daemon=True, name="claude-cancel").start()


class ClaudeEngine:
    name = "claude"

    def __init__(self, cfg: dict, profiles: dict, popen=subprocess.Popen, run=subprocess.run,
                 env: Optional[Dict[str, str]] = None, ledger_note: Optional[Callable[[dict], None]] = None,
                 drop_env: Iterable[str] = ()) -> None:
        self.cfg = cfg
        self.drop_env = tuple(drop_env)
        self.profiles = profiles
        self.popen = popen
        self.run_cmd = run
        self.env = env
        self.on_rate_limit = ledger_note or (lambda info: None)
        self._version = Cached(self._probe_version, 3600)
        self._auth = Cached(self._probe_auth, 3600)
        self.last_init: Optional[dict] = None

    def child_env(self) -> Dict[str, str]:
        return child_env(self.env, self.drop_env)

    # -- probes ----------------------------------------------------------------------------------
    def _probe_version(self):
        path = self.cfg.get("path")
        if not path or not self.cfg.get("enabled", True):
            return None
        try:
            r = self.run_cmd([path, "--version"], capture_output=True, text=True, timeout=20,
                             stdin=subprocess.DEVNULL, env=self.child_env())
            return parse_version(r.stdout)
        except (OSError, subprocess.SubprocessError):
            return None

    def _probe_auth(self) -> str:
        path = self.cfg.get("path")
        if not path:
            return "unknown"
        try:
            r = self.run_cmd([path, "auth", "status", "--json"], capture_output=True, text=True, timeout=20,
                             stdin=subprocess.DEVNULL, env=self.child_env())
            d = json.loads(r.stdout or "{}")
            return AUTH_MAP.get(d.get("authMethod"), "other") if isinstance(d, dict) else "unknown"
        except (OSError, subprocess.SubprocessError, ValueError):
            return "unknown"

    def version(self) -> Optional[Tuple[int, int, int]]:
        return self._version.get()

    def available(self) -> dict:
        v = self.version()
        return {"available": v is not None, "version": "%d.%d.%d" % v if v else None,
                "auth": self._auth.get() if v else "unknown"}

    def refresh_auth(self) -> None:
        self._auth.get(force=True)

    # -- run -------------------------------------------------------------------------------------
    def start(self, ctx: TaskContext, events) -> "ClaudeRun":
        r = ClaudeRun(self, ctx, events)
        r.start()
        return r


class ClaudeRun(Running):
    def __init__(self, engine: ClaudeEngine, ctx: TaskContext, events) -> None:
        self.engine = engine
        self.ctx = ctx
        self.events = events
        self.cancel_requested = False
        self.proc = None
        self.canceller: Optional[Canceller] = None
        self._lock = threading.Lock()
        self._ended = False
        self.argv_used: List[List[str]] = []

    def start(self) -> None:
        threading.Thread(target=self._main, daemon=True, name="claude-run").start()

    def cancel(self) -> None:
        with self._lock:
            self.cancel_requested = True
            proc = self.proc
            if proc is None or self.canceller is not None:
                return
            cfg = self.engine.cfg
            self.canceller = Canceller(proc, cfg.get("cancelGraceS", 5.0), cfg.get("cancelTermS", 10.0))
        self.canceller.start()

    def kill(self) -> None:
        """Last resort when the connector shuts down and a cancelled run hasn't ended in time (app.stop, v2.md H11):
        SIGKILL to the process group, so no Claude process outlives the connector."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _end(self, *a, **kw) -> None:
        if self._ended:
            return
        self._ended = True
        self.events.end(*a, **kw)

    def _main(self) -> None:
        try:
            agent = self.ctx.agent
            if self.ctx.fresh or not agent.get("claudeSession"):
                agent["claudeSession"] = str(uuid.uuid4())
                agent["claudeSessionStarted"] = False
                agent["claudeTotals"] = None
                self.ctx.save_agent(agent)
            res = self._attempt(resume=bool(agent.get("claudeSessionStarted")))
            if res == "retry_new_session":
                agent["claudeSession"] = str(uuid.uuid4())
                agent["claudeSessionStarted"] = False
                agent["claudeTotals"] = None
                self.ctx.save_agent(agent)
                self.events.part({"k": "note", "v": "Yeni oturum başlatıldı"})
                self._attempt(resume=False)
        except Exception as e:      # never leave a task hanging
            self._end("error", None, empty_usage(True), None, "claude-estimate",
                      error={"code": "internal", "message": short(type(e).__name__ + ": " + str(e), 300)})

    def _attempt(self, resume: bool) -> Optional[str]:
        eng = self.engine
        cfg = eng.cfg
        agent = self.ctx.agent
        version = eng.version()
        if version is None:
            self._end("error", None, empty_usage(), None, "unknown",
                      error={"code": "engine_unavailable", "message": "claude not found"})
            return None
        model = self.ctx.model or cfg.get("defaultModel")
        argv = build_argv(cfg["path"], self.ctx.profile, eng.profiles, agent["claudeSession"], resume, model,
                          cfg.get("maxTurns", 50), self.ctx.max_budget_usd, version, bool(cfg.get("bare")))
        self.argv_used.append(argv)
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        with self._lock:
            if self.cancel_requested:
                self._end("cancelled", None, empty_usage(), None, "claude-estimate")
                return None
            try:
                proc = eng.popen(argv, cwd=self.ctx.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=eng.child_env(), **kwargs)
            except OSError as e:
                self._end("error", None, empty_usage(), None, "unknown",
                          error={"code": "engine_unavailable", "message": short(str(e), 300)})
                return None
            self.proc = proc
        stderr_tail = bytearray()

        def feed_stdin():
            try:
                proc.stdin.write(self.ctx.prompt.encode("utf-8"))
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        def read_stderr():
            try:
                for chunk in iter(lambda: proc.stderr.read(4096), b""):
                    stderr_tail.extend(chunk)
                    del stderr_tail[:-4096]
            except (OSError, ValueError):
                pass
        threading.Thread(target=feed_stdin, daemon=True).start()
        th_err = threading.Thread(target=read_stderr, daemon=True)
        th_err.start()
        parser = StreamParser(self.ctx.cwd, bool(cfg.get("showThinking")), self.events, eng.on_rate_limit)
        for raw in proc.stdout:
            parser.feed(raw)
        proc.wait()
        th_err.join(2)
        for pipe in (proc.stdout, proc.stderr, proc.stdin):
            try:
                pipe.close()
            except (OSError, ValueError, AttributeError):
                pass
        err_text = bytes(stderr_tail).decode("utf-8", "replace")
        if parser.init is not None:
            eng.last_init = parser.init
            agent["claudeSessionStarted"] = True
            if parser.session_id and parser.session_id != agent.get("claudeSession"):
                agent["claudeSession"] = parser.session_id
        if resume and parser.init is None and parser.result is None and not self.cancel_requested \
                and RESUME_MISSING_RE.search(err_text):
            return "retry_new_session"
        if resume and parser.result is not None and parser.result.get("is_error") and parser.init is None \
                and RESUME_MISSING_RE.search(" ".join(str(x) for x in parser.result.get("errors") or [])):
            return "retry_new_session"
        self._finish(parser, resume, version, err_text, proc.returncode)
        return None

    def _finish(self, parser: "StreamParser", resume: bool, version, err_text: str, rc) -> None:
        agent = self.ctx.agent
        res = parser.result
        if res is None:
            status = "cancelled" if self.cancel_requested else "error"
            last = [l for l in err_text.strip().splitlines() if l.strip()]
            usage = parser.live_usage(estimated=True)
            usage["outTok"] = None
            self.ctx.save_agent(agent)
            self._end(status, parser.text_result() or None, usage, None, "claude-estimate", turns=None,
                      error=None if status == "cancelled" else
                      {"code": "engine_exit", "message": short(last[-1] if last else "exit code %s" % rc, 300)},
                      denials=parser.denials)
            return
        totals = sum_model_usage(res)
        zeroed = res.get("subtype") == "error_during_execution" and not any(totals[k] for k in totals)
        if zeroed:
            usage = parser.live_usage(estimated=True)
            usage["outTok"] = None
            cost = None
        else:
            prev = agent.get("claudeTotals") if resume else None
            task = totals
            if prev and version and version >= MIN_CUMULATIVE_RESUME \
                    and all(totals[k] >= prev.get(k, 0) for k in totals):
                task = {k: totals[k] - prev.get(k, 0) for k in totals}
            agent["claudeTotals"] = totals
            usage = {"inTok": task["inTok"], "outTok": task["outTok"], "cacheRead": task["cacheRead"],
                     "cacheWrite": task["cacheWrite"], "thinkTok": task.get("thinkTok"), "estimated": False}
            cost = round(task["costUsd"], 6)
        self.ctx.save_agent(agent)
        sub = res.get("subtype")
        error = None
        if self.cancel_requested:
            status = "cancelled"
        elif sub == "success" and not res.get("is_error"):
            status = "success"
        elif sub == "error_max_budget_usd":
            status = "budget"
            error = {"code": "budget_exceeded", "message": "max budget reached"}
        elif sub == "error_max_turns":
            status = "error"
            error = {"code": "max_turns", "message": "max turns reached"}
        else:
            status = "error"
            errs = res.get("errors") or []
            msg = res.get("result") if isinstance(res.get("result"), str) else (errs[0] if errs else sub or "error")
            error = {"code": "engine_error", "message": short(msg, 300)}
            if parser.init is None:
                self.engine.refresh_auth()
        result = res.get("result") if isinstance(res.get("result"), str) else (parser.text_result() or None)
        denials = max(parser.denials, len(res.get("permission_denials") or []))
        self._end(status, result, usage, cost, "claude-estimate", turns=res.get("num_turns"), error=error,
                  denials=denials)


def sum_model_usage(res: dict) -> dict:
    """Σ over result.modelUsage (subagents included); falls back to result.usage + total_cost_usd."""
    t = {"inTok": 0, "outTok": 0, "cacheRead": 0, "cacheWrite": 0, "costUsd": 0.0}
    think = None
    mu = res.get("modelUsage")
    if isinstance(mu, dict) and mu:
        for m in mu.values():
            if not isinstance(m, dict):
                continue
            t["inTok"] += int(m.get("inputTokens") or 0)
            t["outTok"] += int(m.get("outputTokens") or 0)
            t["cacheRead"] += int(m.get("cacheReadInputTokens") or 0)
            t["cacheWrite"] += int(m.get("cacheCreationInputTokens") or 0)
            t["costUsd"] += float(m.get("costUSD") or 0)
            if isinstance(m.get("thinkingTokens"), int):
                think = (think or 0) + m["thinkingTokens"]
        if not t["costUsd"] and isinstance(res.get("total_cost_usd"), (int, float)):
            t["costUsd"] = float(res["total_cost_usd"])
    else:
        u = res.get("usage") if isinstance(res.get("usage"), dict) else {}
        t["inTok"] = int(u.get("input_tokens") or 0)
        t["outTok"] = int(u.get("output_tokens") or 0)
        t["cacheRead"] = int(u.get("cache_read_input_tokens") or 0)
        t["cacheWrite"] = int(u.get("cache_creation_input_tokens") or 0)
        t["costUsd"] = float(res.get("total_cost_usd") or 0)
    if think is not None:
        t["thinkTok"] = think
    return t


class StreamParser:
    """stream-json line parser (§8.1.4). Unknown types are ignored; bad lines are skipped."""

    def __init__(self, cwd: Optional[str], show_thinking: bool, events, on_rate_limit=None) -> None:
        self.cwd = cwd
        self.show_thinking = show_thinking
        self.events = events
        self.on_rate_limit = on_rate_limit or (lambda info: None)
        self.init: Optional[dict] = None
        self.session_id: Optional[str] = None
        self.model: Optional[str] = None
        self.result: Optional[dict] = None
        self.denials = 0
        self.bad_lines = 0
        self._msg_in: Dict[str, Tuple[int, int, int]] = {}    # message id → (input, cacheRead, cacheWrite)
        self._msg_out: Dict[str, int] = {}
        self._cur_msg: Optional[str] = None
        self._delta_msgs = set()
        self._tools: Dict[str, str] = {}
        self._fallback_text: List[str] = []
        self._text: List[str] = []

    def live_usage(self, estimated: bool = False) -> dict:
        u = empty_usage(estimated)
        for i, cr, cw in self._msg_in.values():
            u["inTok"] += i
            u["cacheRead"] += cr
            u["cacheWrite"] += cw
        u["outTok"] = sum(self._msg_out.values())
        return u

    def _live(self) -> None:
        u = self.live_usage()
        self.events.live(inTok=u["inTok"], outTok=u["outTok"], cacheRead=u["cacheRead"], cacheWrite=u["cacheWrite"])

    def text_result(self) -> str:
        return "".join(self._text) or "".join(self._fallback_text)

    def feed(self, raw) -> None:
        try:
            line = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            obj = json.loads(line)
        except (ValueError, UnicodeDecodeError, RecursionError):
            if (raw or b"").strip():
                self.bad_lines += 1
            return
        if not isinstance(obj, dict):
            return
        try:
            self._handle(obj)
        except (KeyError, TypeError, ValueError, AttributeError):
            self.bad_lines += 1

    def _handle(self, o: dict) -> None:
        t = o.get("type")
        main = o.get("parent_tool_use_id") is None
        if t == "system":
            st = o.get("subtype")
            if st == "init":
                self.init = o
                self.session_id = o.get("session_id")
                self.model = o.get("model")
                self.events.office("thinking")
                if self.model:
                    self.events.part({"k": "note", "v": "Model: %s" % short(self.model, 60)})
            elif st == "permission_denied":
                self.denials += 1
                self.events.part({"k": "denied", "name": short(o.get("tool_name") or "?", 40),
                                  "v": short(o.get("message") or "", 300)})
            elif st == "api_retry":
                self.events.part({"k": "retry", "attempt": int(o.get("attempt") or 0),
                                  "waitMs": int(o.get("retry_delay_ms") or 0), "v": short(o.get("error") or "", 40)})
        elif t == "stream_event":
            ev = o.get("event") or {}
            et = ev.get("type")
            if not main:
                return
            if et == "message_start":
                msg = ev.get("message") or {}
                mid = msg.get("id")
                self._cur_msg = mid
                u = msg.get("usage") or {}
                if mid and mid not in self._msg_in:
                    self._msg_in[mid] = (int(u.get("input_tokens") or 0), int(u.get("cache_read_input_tokens") or 0),
                                         int(u.get("cache_creation_input_tokens") or 0))
                    if u.get("output_tokens"):
                        self._msg_out[mid] = int(u["output_tokens"])
                    self._live()
            elif et == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    if self._cur_msg:
                        self._delta_msgs.add(self._cur_msg)
                    self._text.append(d["text"])
                    self.events.part({"k": "text", "v": d["text"]})
                elif d.get("type") == "thinking_delta" and d.get("thinking") and self.show_thinking:
                    self.events.part({"k": "thinking", "v": d["thinking"]})
            elif et == "message_delta":
                u = ev.get("usage") or {}
                if self._cur_msg and isinstance(u.get("output_tokens"), int):
                    self._msg_out[self._cur_msg] = u["output_tokens"]
                    self._live()
        elif t == "assistant":
            msg = o.get("message") or {}
            mid = msg.get("id")
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = str(block.get("name") or "?")[:40]
                    if block.get("id"):
                        self._tools[block["id"]] = name
                    if main:
                        self.events.part({"k": "tool", "name": name,
                                          "v": tool_summary(name, block.get("input"), self.cwd)})
                        self.events.office("working", tool=name)
                elif block.get("type") == "text" and main and mid not in self._delta_msgs and block.get("text"):
                    self._fallback_text.append(block["text"])
                    self.events.part({"k": "text", "v": block["text"]})
        elif t == "user":
            msg = o.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list) and main:
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        name = self._tools.get(block.get("tool_use_id"), "?")
                        self.events.part({"k": "tool_done", "name": name, "ok": not block.get("is_error")})
                        self.events.office("thinking")
        elif t == "rate_limit_event":
            info = o.get("rate_limit_info") or {}
            if isinstance(info, dict):
                rec = {"status": info.get("status"), "resetsAt": info.get("resetsAt"),
                       "utilization": info.get("utilization"), "errorCode": info.get("errorCode"),
                       "at": int(time.time() * 1000)}
                self.on_rate_limit(rec)
                if info.get("status") == "rejected":
                    note = "Claude kullanım sınırına ulaşıldı"
                    r = info.get("resetsAt")
                    if isinstance(r, (int, float)) and not isinstance(r, bool) and r > 0:
                        secs = r / 1000.0 if r > 1e12 else float(r)       # unit not documented: accept s or ms
                        note += " (sıfırlanma: %s)" % time.strftime("%H:%M", time.localtime(secs))
                    self.events.part({"k": "note", "v": note})
        elif t == "result":
            self.result = o
