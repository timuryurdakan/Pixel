#!/usr/bin/env python3
"""Pixel Office — Claude Code hook client (CLOUD.md §7). Python >= 3.8, stdlib only, single file.

    python3 pixel_hook.py install --server https://<host> --room <roomId> --key <writeKey> --name "Timur"
    python3 pixel_hook.py install --server http://127.0.0.1:8765 --name "Timur"     # local server (no room/key)
    python3 pixel_hook.py uninstall | test | doctor
    python3 pixel_hook.py status --session build --state working --tool Build --label "Derleniyor"
    python3 pixel_hook.py hook          # what Claude Code runs: hook JSON on stdin, always exit 0, no output

Privacy (§7.4): the hook JSON is parsed, but only these keys are ever read: hook_event_name, session_id
(hashed), agent_id (hashed), agent_type (built-in names only), cwd (basename / alias), source, tool_name,
is_interrupt, notification_type, error (allow-listed enum). Prompts, tool input/output, messages,
paths and raw ids never leave the machine and are never written to disk.

Environment: PIXEL_OFFICE_HOME (default ~/.pixel-office), PIXEL_OFFICE_CLAUDE_SETTINGS (default
~/.claude/settings.json), PIXEL_OFFICE_DISABLE=1 (report nothing for this process).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

try:                        # POSIX only; Windows relies on atomic replace + the seq check (§7.3)
    import fcntl            # type: ignore
except Exception:           # pragma: no cover
    fcntl = None            # type: ignore

VERSION = "2.0.0"
SOURCE = "claude-code"
MARKER = "pixel_hook"

DEBOUNCE = 1.0              # s (§7.3 step 6)
KEEPALIVE = 300.0           # s (§7.3 step 3)
TOOL_MIN_INTERVAL = 30.0    # s, main records (§7.3 step 5)
TOOL_MIN_INTERVAL_SUB = 60.0
POST_TIMEOUT = 4.0          # s (§7.3 step 7)
END_TIMEOUT = 2.5           # s; SessionEnd runs synchronously with "timeout": 5
WATCHDOG = 30               # s; a hook process never lives longer than this
LOG_MAX = 64 * 1024
STDIN_MAX = 64 * 1024 * 1024
STATE_PRUNE_AGE = 7 * 86400

PROJECT_MAX = 32
LABEL_MAX = 40
TOOL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
ROOM_RE = re.compile(r"^[a-z2-7]{26}$")
OWNER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STATES = ("working", "thinking", "waiting_for_user", "idle", "sleeping", "offline")

# Fixed Turkish labels (§7.2). Nothing else is ever sent as a label by the hook path.
L_ASK = "Soru soruyor"
L_PLAN = "Plan onayı bekliyor"
L_PERMISSION = "İzin bekliyor"
L_ANSWER = "Yanıt bekliyor"
L_INTERRUPTED = "Durduruldu"
L_COMPACT = "Bağlam sıkıştırılıyor"
L_API_ERROR = "API hatası"
L_SUBAGENT = "Alt-ajan"
L_HIDDEN_PROJECT = "Gizli proje"
L_CUSTOM_AGENT = "Özel ajan"
L_SETUP_TEST = "Kurulum testi"

BUILTIN_AGENT_TYPES = frozenset({"general-purpose", "Explore", "Plan", "statusline-setup", "claude-code-guide",
                                 "claude"})
STOP_FAILURE_ERRORS = frozenset({"rate_limit", "authentication_failed", "billing_error", "invalid_request",
                                 "server_error", "max_output_tokens", "overloaded", "unknown"})
WAIT_NOTIFICATIONS = {"permission_prompt": L_PERMISSION, "elicitation_dialog": L_ANSWER,
                      "agent_needs_input": L_ANSWER}
KNOWN_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse",
                "PostToolUseFailure", "Notification", "PreCompact", "Stop", "StopFailure", "SubagentStart",
                "SubagentStop", "SessionEnd")
# Events registered by the installer, and which take a tool matcher (§7.5.1).
MATCHER_EVENTS = ("PreToolUse", "PermissionRequest", "PostToolUse", "PostToolUseFailure")


# --------------------------------------------------------------------------------------------------
# Pure mapping (§7.2, §7.4). Also imported by the server's /api/cc_hook (stateless: last_state=None).
# --------------------------------------------------------------------------------------------------

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hash_id(salt: str, raw: object) -> str:
    """Opaque id sent instead of a raw Claude Code session/agent id: sha256(salt + id)[:20]."""
    return _sha(str(salt or "") + (raw if isinstance(raw, str) else ""))[:20]


def _clean(s: str, limit: int) -> str:
    out = "".join(ch for ch in s if ord(ch) >= 0x20 and not 0x7F <= ord(ch) <= 0x9F)
    out = " ".join(out.split())
    return out[:limit].strip()


def split_path(path: str) -> list:
    """Path components, splitting on both '/' and '\\' (Windows paths arrive with backslashes)."""
    return [p for p in re.split(r"[\\/]+", path) if p]


def _norm_path(path: str) -> str:
    parts = split_path(path)
    lead = "/" if path[:1] in ("/", "\\") else ""
    return lead + "/".join(parts)


def project_for(cwd: object, cfg: dict) -> object:
    """Basename of cwd, or a configured alias, or "Gizli proje". Never the full path."""
    if cfg.get("hide_projects"):
        return L_HIDDEN_PROJECT
    if not isinstance(cwd, str) or not cwd:
        return None
    aliases = cfg.get("project_aliases") or {}
    if isinstance(aliases, dict) and aliases:
        here = _norm_path(cwd)
        here_cmp = here.lower() if os.name == "nt" or ":" in here[:3] else here
        best, best_len = None, -1
        for key, alias in aliases.items():
            if not isinstance(key, str) or not isinstance(alias, str) or not key:
                continue
            k = _norm_path(key)
            k_cmp = k.lower() if os.name == "nt" or ":" in k[:3] else k
            if here_cmp == k_cmp or here_cmp.startswith(k_cmp.rstrip("/") + "/"):
                if len(k_cmp) > best_len:
                    best, best_len = alias, len(k_cmp)
        if best is not None:
            a = _clean(best.replace("/", "-").replace("\\", "-"), PROJECT_MAX)
            return a or None
    parts = split_path(cwd)
    if not parts:
        return None
    base = _clean(parts[-1], PROJECT_MAX)
    if not base or base.endswith(":"):          # "C:" alone is not a project name
        return None
    return base


def tool_for(name: object, cfg: dict) -> object:
    if not isinstance(name, str) or not name:
        return None
    if name.startswith("mcp__"):
        if not cfg.get("send_mcp_names"):
            return "MCP"
        name = name[:32]
    return name if TOOL_RE.match(name) else None


def agent_type_for(value: object) -> object:
    if not isinstance(value, str) or not value:
        return None
    return value if value in BUILTIN_AGENT_TYPES else L_CUSTOM_AGENT


def record_ids(event: dict, cfg: dict) -> tuple:
    """(session, sub) hashed ids of the record an event applies to (§7.2: agent_id → subagent record)."""
    salt = str(cfg.get("salt") or "")
    session = hash_id(salt, event.get("session_id"))
    agent_raw = event.get("agent_id")
    sub = hash_id(salt, agent_raw) if isinstance(agent_raw, str) and agent_raw else None
    return session, sub


def map_event(event: dict, cfg: dict, last_state: object = None) -> list:
    """Hook event → list of §5.5 update dicts (empty = send nothing). Pure: no I/O, no clock.

    `last_state` is the last state of the record the event applies to (None when unknown, as on the
    server's /api/cc_hook). Only allow-listed keys of `event` are indexed. Keys whose value would be
    null are omitted (the server treats a missing tool/label as cleared)."""
    if not isinstance(event, dict) or not isinstance(cfg, dict):
        return []
    if cfg.get("enabled") is False:
        return []
    name = event.get("hook_event_name")
    if name not in KNOWN_EVENTS:
        return []
    session, sub = record_ids(event, cfg)
    if name == "SessionEnd":
        sub = None                                   # session-level event
    agent_type = agent_type_for(event.get("agent_type")) if sub else None

    def upd(state, tool=None, label=None, rec_sub=sub, atype=agent_type):
        d = {"session": session}
        if rec_sub:
            d["sub"] = rec_sub
        owner = cfg.get("name") or cfg.get("owner")
        if isinstance(owner, str) and owner:
            d["owner"] = owner[:24]
        oid = cfg.get("ownerId")
        if isinstance(oid, str) and OWNER_ID_RE.match(oid):
            d["ownerId"] = oid
        proj = project_for(event.get("cwd"), cfg)
        if proj:
            d["project"] = proj
        d["state"] = state
        if tool:
            d["tool"] = tool
        if label:
            d["label"] = label
        if atype:
            d["agentType"] = atype
        d["source"] = SOURCE
        color = cfg.get("color")
        if isinstance(color, int) and not isinstance(color, bool) and 0 <= color <= 15:
            d["color"] = color
        return [d]

    if name == "SessionStart":
        return [] if event.get("source") == "compact" else upd("idle")
    if name == "UserPromptSubmit":
        return upd("thinking")
    if name == "PreToolUse":
        raw_tool = event.get("tool_name")
        tool = tool_for(raw_tool, cfg)
        if raw_tool == "AskUserQuestion":
            return upd("waiting_for_user", tool, L_ASK)
        if raw_tool == "ExitPlanMode":
            return upd("waiting_for_user", tool, L_PLAN)
        return upd("working", tool)
    if name == "PermissionRequest":
        return upd("waiting_for_user", tool_for(event.get("tool_name"), cfg), L_PERMISSION)
    if name == "PostToolUseFailure" and event.get("is_interrupt") is True:
        return upd("idle", None, L_INTERRUPTED)
    if name in ("PostToolUse", "PostToolUseFailure"):
        return upd("thinking") if last_state == "waiting_for_user" else []
    if name == "Notification":
        ntype = event.get("notification_type")
        if ntype in WAIT_NOTIFICATIONS:
            if last_state == "waiting_for_user":
                return []                            # already waiting (PermissionRequest came first)
            return upd("waiting_for_user", None, WAIT_NOTIFICATIONS[ntype])
        if ntype == "idle_prompt":
            return upd("idle")
        return []
    if name == "PreCompact":
        return upd("thinking", None, L_COMPACT)
    if name == "Stop":
        return upd("idle")
    if name == "StopFailure":
        err = event.get("error")
        label = L_API_ERROR + ": " + err if isinstance(err, str) and err in STOP_FAILURE_ERRORS else L_API_ERROR
        return upd("idle", None, label)
    if name == "SubagentStart":
        if not sub:
            return []
        return upd("working", None, L_SUBAGENT)
    if name == "SubagentStop":
        if not sub:
            return []
        return upd("sleeping")
    if name == "SessionEnd":
        return upd("offline")
    return []


# --------------------------------------------------------------------------------------------------
# Paths, config, log
# --------------------------------------------------------------------------------------------------

def office_home() -> str:
    env = os.environ.get("PIXEL_OFFICE_HOME")
    return os.path.abspath(os.path.expanduser(env)) if env else os.path.join(os.path.expanduser("~"), ".pixel-office")


def claude_settings_path(override: object = None) -> str:
    if override:
        return os.path.abspath(os.path.expanduser(str(override)))
    env = os.environ.get("PIXEL_OFFICE_CLAUDE_SETTINGS")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def load_json_file(path: str) -> object:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json_atomic(path: str, obj: object, mode: object = None, indent: object = None) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".%s.%s.tmp" % (os.path.basename(path), secrets.token_hex(4)))
    text = json.dumps(obj, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))
    if indent:
        text += "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp, flags, 0o600 if mode is None else mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_config(home: object = None) -> dict:
    cfg = load_json_file(os.path.join(home or office_home(), "config.json"))
    return cfg if isinstance(cfg, dict) else {}


def log_line(home: str, text: str) -> None:
    """Event names and HTTP status codes only. Rotated at 64 KB."""
    try:
        path = os.path.join(home, "hook.log")
        try:
            if os.path.getsize(path) > LOG_MAX:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), text))
    except Exception:
        pass


# --------------------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------------------

def status_url(cfg: dict) -> str:
    base = str(cfg.get("server") or "http://127.0.0.1:8765").rstrip("/")
    room = cfg.get("room")
    url = base + "/api/status"
    if room:
        url += "?room=" + urllib.parse.quote(str(room), safe="")
    return url


def post_updates(cfg: dict, updates: list, timeout: float = POST_TIMEOUT, retries: int = 1,
                 sleep=time.sleep) -> int:
    """POST one update or a batch. Returns the HTTP status, 0 on network error. Never raises."""
    if not updates:
        return 0
    body = updates[0] if len(updates) == 1 else {"agents": updates[:10]}
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "pixel-hook/" + VERSION}
    if cfg.get("key"):
        headers["Authorization"] = "Bearer " + str(cfg["key"])
    status = 0
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(status_url(cfg), data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(65536)
                return resp.status
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                e.close()
            except Exception:
                pass
            if status >= 500 and attempt < retries:
                sleep(1.0)
                continue
            return status                             # 4xx incl. 429: dropped
        except Exception:
            status = 0
            if attempt < retries:
                sleep(1.0)
                continue
    return status


# --------------------------------------------------------------------------------------------------
# Stateful hook runner: debounce, coalescing, keep-alive (§7.3)
# --------------------------------------------------------------------------------------------------

def ignored_cwd(cwd: object) -> bool:
    """True if `.pixel-office-ignore` exists in cwd or any parent."""
    if not isinstance(cwd, str) or not cwd:
        return False
    try:
        p = os.path.abspath(cwd)
    except Exception:
        return False
    for _ in range(64):
        try:
            if os.path.exists(os.path.join(p, ".pixel-office-ignore")):
                return True
        except Exception:
            return False
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return False


def _key(st: object, tool: object, lbl: object, proj: object) -> list:
    return [st, tool, lbl, proj]


class HookRunner:
    """Applies §7.3 to one hook event. All time and network access is injectable for tests."""

    def __init__(self, cfg: dict, home: object = None, clock=time.time, sleep=time.sleep, post=None) -> None:
        self.cfg = cfg
        self.home = home or office_home()
        self.state_dir = os.path.join(self.home, "state")
        self.clock = clock
        self.sleep = sleep
        self.post = post or (lambda updates, timeout, retries: post_updates(self.cfg, updates, timeout, retries,
                                                                          sleep=self.sleep))

    # -- state files -------------------------------------------------------------------------------
    def rec_path(self, session: str, sub: object) -> str:
        rid = _sha(str(self.cfg.get("salt") or "") + session + (sub or ""))[:16]
        return os.path.join(self.state_dir, rid + ".json")

    @contextmanager
    def locked(self):
        if fcntl is None:
            yield
            return
        os.makedirs(self.state_dir, exist_ok=True)
        fd = os.open(os.path.join(self.state_dir, ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def load(self, session: str, sub: object) -> dict:
        rec = load_json_file(self.rec_path(session, sub))
        if not isinstance(rec, dict):
            rec = {}
        rec.setdefault("seq", 0)
        rec.setdefault("last", None)
        rec.setdefault("pending", None)
        rec.setdefault("since", None)
        rec.setdefault("started", None)
        rec.setdefault("tc", 0)
        rec["s"] = session
        rec["sub"] = sub
        return rec

    def save(self, rec: dict) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        write_json_atomic(self.rec_path(rec["s"], rec["sub"]), rec, mode=0o600)

    def delete(self, session: str, sub: object) -> None:
        try:
            os.unlink(self.rec_path(session, sub))
        except OSError:
            pass

    def session_files(self, session: str) -> list:
        out = []
        try:
            names = os.listdir(self.state_dir)
        except OSError:
            return out
        for n in names:
            if n.endswith(".json"):
                rec = load_json_file(os.path.join(self.state_dir, n))
                if isinstance(rec, dict) and rec.get("s") == session:
                    out.append(os.path.join(self.state_dir, n))
        return out

    def live_subs(self, session: str) -> list:
        out = []
        try:
            names = os.listdir(self.state_dir)
        except OSError:
            return out
        for n in names:
            if not n.endswith(".json"):
                continue
            rec = load_json_file(os.path.join(self.state_dir, n))
            if not isinstance(rec, dict) or rec.get("s") != session or not rec.get("sub"):
                continue
            last = rec.get("last") or {}
            if isinstance(last, dict) and last.get("st") not in (None, "offline"):   # sleeping ones too (§17.8)
                out.append(rec["sub"])
        return sorted(out)

    def prune(self) -> None:
        now = time.time()
        try:
            for n in os.listdir(self.state_dir):
                p = os.path.join(self.state_dir, n)
                if n.endswith(".json") and now - os.path.getmtime(p) > STATE_PRUNE_AGE:
                    os.unlink(p)
        except OSError:
            pass

    # -- the algorithm -----------------------------------------------------------------------------
    @staticmethod
    def effective_last(rec: dict) -> object:
        p = rec.get("pending")
        if isinstance(p, dict) and p.get("state"):
            return p["state"]
        last = rec.get("last")
        return last.get("st") if isinstance(last, dict) else None

    def _stamp(self, rec: dict, u: dict, now: float) -> dict:
        prev = self.effective_last(rec)
        if rec.get("since") is None or prev != u["state"]:
            rec["since"] = now
        if rec.get("started") is None:
            rec["started"] = now
        u = dict(u)
        u["stateAgeMs"] = max(0, int((now - rec["since"]) * 1000))
        u["startedAgeMs"] = max(0, int((now - rec["started"]) * 1000))
        u["toolCount"] = int(rec.get("tc") or 0)
        return u

    def handle(self, event: dict) -> str:
        """Returns a short outcome code (for tests/log): none|dedupe|throttled|superseded|sent:<status>."""
        if not isinstance(event, dict) or self.cfg.get("enabled") is False:
            return "none"
        name = event.get("hook_event_name")
        if name not in KNOWN_EVENTS:
            return "none"
        if ignored_cwd(event.get("cwd")):
            return "none"
        session, sub = record_ids(event, self.cfg)
        if name == "SessionEnd":
            sub = None
        is_end = name == "SessionEnd"
        now = self.clock()

        with self.locked():
            rec = self.load(session, sub)
            if name == "PreToolUse":
                rec["tc"] = int(rec.get("tc") or 0) + 1
            updates = map_event(event, self.cfg, self.effective_last(rec))
            if not updates:
                if name == "PreToolUse":
                    self.save(rec)
                return "none"
            if sub:                                     # remember the subagent type for later events
                if updates[0].get("agentType"):
                    rec["atype"] = updates[0]["agentType"]
                elif isinstance(rec.get("atype"), str):
                    updates[0]["agentType"] = rec["atype"]
            u = self._stamp(rec, updates[0], now)
            last = rec.get("last") if isinstance(rec.get("last"), dict) else None
            ukey = _key(u.get("state"), u.get("tool"), u.get("label"), u.get("project"))
            if last and _key(last.get("st"), last.get("tool"), last.get("lbl"), last.get("proj")) == ukey \
                    and now - float(last.get("t") or 0) < KEEPALIVE and not is_end:
                rec["pending"] = None
                self.save(rec)
                return "dedupe"
            rec["seq"] = int(rec.get("seq") or 0) + 1
            rec["pending"] = u
            my_seq = rec["seq"]
            state_change = (last is None or last.get("st") != u["state"]
                            or "waiting_for_user" in (last.get("st"), u["state"])
                            or "offline" in (last.get("st"), u["state"]))
            if not state_change:
                interval = TOOL_MIN_INTERVAL_SUB if sub else TOOL_MIN_INTERVAL
                if now - float(last.get("t") or 0) < interval:
                    self.save(rec)
                    return "throttled"
            self.save(rec)

        extra = []
        if is_end:
            for s in self.live_subs(session)[:9]:
                extra.append({k: v for k, v in (("session", session), ("sub", s), ("owner", u.get("owner")),
                                                ("ownerId", u.get("ownerId")), ("state", "offline"),
                                                ("source", SOURCE)) if v is not None})
        elif state_change:
            self.sleep(DEBOUNCE)
            with self.locked():
                cur = self.load(session, sub)
                if int(cur.get("seq") or 0) != my_seq:
                    return "superseded"
                if isinstance(cur.get("pending"), dict):
                    u = cur["pending"]

        status = self.post([u] + extra, END_TIMEOUT if is_end else POST_TIMEOUT, 0 if is_end else 1)
        log_line(self.home, "%s %s" % (name, status or "neterr"))
        if is_end:
            for path in self.session_files(session):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self.prune()
            return "sent:%s" % status
        if 200 <= status < 300:
            with self.locked():
                cur = self.load(session, sub)
                cur["last"] = {"st": u["state"], "tool": u.get("tool"), "lbl": u.get("label"),
                               "proj": u.get("project"), "t": self.clock()}
                if int(cur.get("seq") or 0) == my_seq:
                    cur["pending"] = None
                self.save(cur)
        return "sent:%s" % status


def run_hook(stdin=None, home: object = None) -> int:
    """`hook` subcommand. Always returns 0 and prints nothing."""
    try:
        if os.environ.get("PIXEL_OFFICE_DISABLE") == "1":
            _drain(stdin)
            return 0
        home = home or office_home()
        cfg = load_config(home)
        raw = _read_stdin(stdin)
        if not cfg or cfg.get("enabled") is False or raw is None:
            return 0
        try:
            event = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return 0
        del raw
        if not isinstance(event, dict):
            return 0
        _watchdog(4 if event.get("hook_event_name") == "SessionEnd" else WATCHDOG)
        HookRunner(cfg, home).handle(event)
    except BaseException:
        pass
    return 0


def _read_stdin(stdin=None) -> object:
    src = stdin if stdin is not None else getattr(sys.stdin, "buffer", sys.stdin)
    chunks, total = [], 0
    while True:
        b = src.read(1 << 20)
        if not b:
            break
        if isinstance(b, str):
            b = b.encode("utf-8", "replace")
        total += len(b)
        if total > STDIN_MAX:
            _drain(src)
            return None
        chunks.append(b)
    return b"".join(chunks)


def _drain(stdin=None) -> None:
    try:
        src = stdin if stdin is not None else getattr(sys.stdin, "buffer", sys.stdin)
        while src.read(1 << 20):
            pass
    except Exception:
        pass


def _watchdog(seconds: int) -> None:
    try:
        import signal
        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, lambda *_: os._exit(0))
            signal.alarm(seconds)
    except Exception:
        pass


# --------------------------------------------------------------------------------------------------
# settings.json merge (§7.5.1)
# --------------------------------------------------------------------------------------------------

class SettingsError(Exception):
    pass


def _is_ours(hook: object) -> bool:
    if not isinstance(hook, dict):
        return False
    parts = [hook.get("command")] + (hook.get("args") if isinstance(hook.get("args"), list) else [])
    return any(isinstance(p, str) and MARKER in p for p in parts)


def build_entries(command: str, args: object) -> dict:
    """Matcher groups for every event. `args` None → shell-string form (command only)."""
    def one(event: str) -> dict:
        h = {"type": "command", "command": command}
        if args is not None:
            h["args"] = list(args)
        if event == "SessionEnd":
            h["timeout"] = 5
        else:
            h["async"] = True
        g = {}
        if event in MATCHER_EVENTS:
            g["matcher"] = "*"
        g["hooks"] = [h]
        return g
    return {ev: [one(ev)] for ev in KNOWN_EVENTS}


def remove_ours(settings: dict) -> int:
    """Remove our hook entries in place. Foreign entries are kept untouched. Returns how many were removed."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for ev in list(hooks.keys()):
        groups = hooks[ev]
        if not isinstance(groups, list):
            continue
        new_groups = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(_is_ours(h) for h in g["hooks"]):
                kept = [h for h in g["hooks"] if not _is_ours(h)]
                removed += len(g["hooks"]) - len(kept)
                if kept:
                    g2 = dict(g)
                    g2["hooks"] = kept
                    new_groups.append(g2)
            else:
                new_groups.append(g)
        if new_groups:
            hooks[ev] = new_groups
        else:
            del hooks[ev]
    if not hooks:
        del settings["hooks"]
    return removed


def merge_settings(settings: dict, entries: dict) -> dict:
    if not isinstance(settings, dict):
        raise SettingsError("settings.json is not a JSON object")
    if "hooks" in settings and not isinstance(settings["hooks"], dict):
        raise SettingsError('"hooks" in settings.json is not an object')
    remove_ours(settings)
    hooks = settings.setdefault("hooks", {})
    for ev, groups in entries.items():
        cur = hooks.get(ev)
        if cur is None:
            hooks[ev] = list(groups)
        elif isinstance(cur, list):
            cur.extend(groups)
        else:
            raise SettingsError('"hooks.%s" in settings.json is not an array' % ev)
    return settings


def read_settings(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except OSError as e:
        raise SettingsError("cannot read %s: %s" % (path, e))
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as e:
        raise SettingsError("%s is not valid JSON (%s). Nothing was changed." % (path, e))
    if not isinstance(data, dict):
        raise SettingsError("%s is not a JSON object. Nothing was changed." % path)
    return data


def write_settings(path: str, data: dict) -> None:
    mode = None
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        pass
    write_json_atomic(path, data, mode=mode if mode is not None else 0o644, indent=2)


def backup_settings(path: str) -> object:
    """Back up once (settings.json.pixel-backup). Returns the backup path or None."""
    if not os.path.exists(path):
        return None
    bak = path + ".pixel-backup"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


def _real(path: str) -> str:
    """Follow symlinks: a dotfiles-managed ~/.claude/settings.json symlink must stay a symlink (the atomic
    replace would otherwise swap the link for a plain file and leave the real file unchanged)."""
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return path


def install_hooks(settings_path: str, command: str, args: object) -> dict:
    settings_path = _real(settings_path)
    data = read_settings(settings_path)           # raises on invalid JSON before anything is written
    backup = backup_settings(settings_path)
    merge_settings(data, build_entries(command, args))
    write_settings(settings_path, data)
    return {"settings": settings_path, "backup": backup}


def uninstall_hooks(settings_path: str) -> int:
    settings_path = _real(settings_path)
    if not os.path.exists(settings_path):
        return 0
    data = read_settings(settings_path)
    backup_settings(settings_path)
    n = remove_ours(data)
    if n:
        write_settings(settings_path, data)
    return n


def hook_command(script: str, style: str = "exec", powershell: bool = False) -> tuple:
    """(command, args) for settings.json. exec form = absolute interpreter + args (no shell)."""
    if powershell:
        cmd = "powershell.exe" if os.name == "nt" else (shutil.which("pwsh") or "pwsh")
        args = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, "hook"]
        if style == "shell":
            return (" ".join([_q(cmd)] + [_q(a) for a in args]), None)
        return (cmd, args)
    py = sys.executable or "python3"
    if style == "shell":
        return ("%s %s hook" % (_q(py), _q(script)), None)
    return (py, [script, "hook"])


def _q(s: str) -> str:
    if os.name == "nt":
        return '"%s"' % s if (" " in s or "\t" in s) else s
    import shlex
    return shlex.quote(s)


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------

def _mask(key: object) -> str:
    if not isinstance(key, str) or not key:
        return "(yok)"
    return key[:4] + "…" + key[-2:] if len(key) > 8 else "****"


def _validate_install_args(a) -> object:
    u = urllib.parse.urlsplit(a.server or "")
    if u.scheme not in ("http", "https") or not u.netloc:
        return "--server must be an http(s) URL"
    if a.room and not ROOM_RE.match(a.room):
        return "--room must be a 26-character room id"
    if a.key and (len(a.key) < 8 or any(c.isspace() for c in a.key)):
        return "--key looks invalid"
    if a.room and not a.key:
        return "--key is required with --room"
    if not a.name or not a.name.strip() or len(a.name.strip()) > 24:
        return "--name must be 1-24 characters"
    if a.color is not None and not 0 <= a.color <= 15:
        return "--color must be 0-15"
    return None


def cmd_install(a) -> int:
    err = _validate_install_args(a)
    if err:
        print("Hata: " + err, file=sys.stderr)
        return 2
    home = office_home()
    os.makedirs(home, exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    cfg = load_config(home)
    cfg.update({"server": a.server.rstrip("/"), "room": a.room or None, "key": a.key or None,
                "name": a.name.strip(), "version": VERSION})
    if a.color is not None:
        cfg["color"] = a.color
    if not (isinstance(cfg.get("ownerId"), str) and OWNER_ID_RE.match(cfg["ownerId"])):
        cfg["ownerId"] = "o_" + secrets.token_urlsafe(9)
    if not isinstance(cfg.get("salt"), str) or len(cfg["salt"]) < 16:
        cfg["salt"] = secrets.token_hex(16)
    cfg.setdefault("project_aliases", {})
    cfg.setdefault("hide_projects", False)
    cfg.setdefault("send_mcp_names", False)
    cfg.setdefault("enabled", True)

    here = os.path.abspath(__file__)
    if a.force_powershell:
        src = os.path.join(os.path.dirname(here), "pixel_hook.ps1")
        dst = os.path.join(home, "pixel_hook.ps1")
        if not os.path.exists(src) and not os.path.exists(dst):
            print("Hata: pixel_hook.ps1 bulunamadı (%s)" % src, file=sys.stderr)
            return 1
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
            shutil.copyfile(src, dst)
        cfg["client"] = "powershell"
        command, args = hook_command(dst, a.command_style, powershell=True)
    else:
        dst = os.path.join(home, "pixel_hook.py")
        if os.path.abspath(dst) != here:
            shutil.copyfile(here, dst)
        cfg["client"] = "python"
        command, args = hook_command(dst, a.command_style)
    write_json_atomic(os.path.join(home, "config.json"), cfg, mode=0o600, indent=2)
    settings_path = claude_settings_path(a.settings_path)
    try:
        res = install_hooks(settings_path, command, args)
    except (SettingsError, OSError) as e:
        print("Hata: %s" % e, file=sys.stderr)
        return 1
    print("Pixel Office kancası kuruldu.")
    print("  Yapılandırma: %s" % os.path.join(home, "config.json"))
    print("  Claude ayarları: %s%s" % (res["settings"], " (yedek: %s)" % res["backup"] if res["backup"] else ""))
    if not a.no_test:
        cmd_test(a)
    print("Açık Claude Code oturumlarını yeniden başlatın.")
    return 0


def cmd_uninstall(a) -> int:
    path = claude_settings_path(a.settings_path)
    try:
        n = uninstall_hooks(path)
    except (SettingsError, OSError) as e:
        print("Hata: %s" % e, file=sys.stderr)
        return 1
    print("%d Pixel Office kancası kaldırıldı (%s). Diğer kancalara dokunulmadı." % (n, path))
    print("Yapılandırma klasörü korunuyor: %s (isterseniz silebilirsiniz)" % office_home())
    return 0


def cmd_test(a) -> int:
    cfg = load_config()
    if not cfg:
        print("Hata: önce 'install' çalıştırın.", file=sys.stderr)
        return 1
    u = {"session": "setup-test", "owner": cfg.get("name") or "?", "state": "idle", "label": L_SETUP_TEST,
         "source": SOURCE}
    if cfg.get("ownerId"):
        u["ownerId"] = cfg["ownerId"]
    status = post_updates(cfg, [u])
    if 200 <= status < 300:
        print("Test başarılı: ofiste 'Kurulum testi' karakteri görünmeli (20 sn sonra çıkar).")
        _spawn_detached([sys.executable, os.path.abspath(__file__), "_offline-later", "20"])
        return 0
    print("Test başarısız (HTTP %s). 'doctor' komutuyla kontrol edin." % (status or "bağlantı hatası"),
          file=sys.stderr)
    return 1


def cmd_offline_later(a) -> int:
    try:
        time.sleep(float(a.seconds))
        cfg = load_config()
        u = {"session": "setup-test", "owner": cfg.get("name") or "?", "state": "offline", "source": SOURCE}
        if cfg.get("ownerId"):
            u["ownerId"] = cfg["ownerId"]
        post_updates(cfg, [u])
    except BaseException:
        pass
    return 0


def _spawn_detached(argv: list) -> None:
    try:
        kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "close_fds": True}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        subprocess.Popen(argv, **kw)
    except Exception:
        pass


def cmd_status(a) -> int:
    cfg = load_config()
    if a.server:
        cfg = dict(cfg, server=a.server, room=a.room, key=a.key)
    if not SESSION_RE.match(a.session or ""):
        print("Hata: --session yalnızca A-Z a-z 0-9 _ - içerebilir (1-64)", file=sys.stderr)
        return 2
    u = {"session": a.session, "owner": a.owner or cfg.get("name") or os.environ.get("USER")
         or os.environ.get("USERNAME") or "local", "state": a.state, "source": a.source}
    if cfg.get("ownerId") and not a.owner:
        u["ownerId"] = cfg["ownerId"]
    for k, v in (("tool", a.tool), ("label", a.label), ("project", a.project)):
        if v:
            u[k] = v
    if a.final:
        u["final"] = True
    status = post_updates(cfg, [u])
    if 200 <= status < 300:
        return 0
    print("Gönderilemedi (HTTP %s)" % (status or "bağlantı hatası"), file=sys.stderr)
    return 1


def cmd_doctor(a) -> int:
    home = office_home()
    cfg = load_config(home)
    print("Pixel Office hook istemcisi %s" % VERSION)
    print("Klasör: %s" % home)
    if not cfg:
        print("config.json yok: önce 'install' çalıştırın.")
    else:
        shown = dict(cfg)
        shown["key"] = _mask(cfg.get("key"))
        shown["salt"] = _mask(cfg.get("salt"))
        print("Yapılandırma: " + json.dumps(shown, ensure_ascii=False))
    path = claude_settings_path(a.settings_path)
    try:
        data = read_settings(path)
        hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
        found = [ev for ev, groups in hooks.items() if isinstance(groups, list) and any(
            isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(_is_ours(h) for h in g["hooks"])
            for g in groups)]
        missing = [ev for ev in KNOWN_EVENTS if ev not in found]
        print("settings.json: %s — kancalı olaylar: %d/%d%s" % (
            path, len(found), len(KNOWN_EVENTS), (" (eksik: %s)" % ", ".join(missing)) if missing else ""))
    except SettingsError as e:
        print("settings.json: %s" % e)
    try:
        with open(os.path.join(home, "hook.log"), "r", encoding="utf-8") as f:
            lines = f.readlines()[-20:]
        print("Son günlük satırları:")
        for ln in lines:
            print("  " + ln.rstrip())
    except OSError:
        print("Günlük yok.")
    if cfg.get("server"):
        try:
            with urllib.request.urlopen(str(cfg["server"]).rstrip("/") + "/api/health", timeout=5) as r:
                print("Sunucu: %s → HTTP %s" % (cfg["server"], r.status))
        except urllib.error.HTTPError as e:
            print("Sunucu: %s → HTTP %s" % (cfg["server"], e.code))
        except Exception as e:
            print("Sunucu: %s → ulaşılamıyor (%s)" % (cfg["server"], type(e).__name__))
    return 0


def cmd_map(a) -> int:
    """Debug/test helper: {"event":…, "cfg":…, "last":…} on stdin → mapped updates JSON on stdout."""
    data = json.loads(sys.stdin.read() or "{}")
    out = map_event(data.get("event") or {}, data.get("cfg") or {}, data.get("last"))
    sys.stdout.write(json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pixel_hook.py", description="Pixel Office Claude Code hook client")
    sp = p.add_subparsers(dest="cmd")
    sp.add_parser("hook", help="read a hook event on stdin (used by Claude Code)")
    i = sp.add_parser("install", help="write config and merge hooks into ~/.claude/settings.json")
    i.add_argument("--server", required=True)
    i.add_argument("--room")
    i.add_argument("--key")
    i.add_argument("--name", required=True)
    i.add_argument("--color", type=int)
    i.add_argument("--force-powershell", action="store_true")
    i.add_argument("--settings-path", help="settings.json to edit (default ~/.claude/settings.json)")
    i.add_argument("--command-style", choices=("exec", "shell"), default="exec",
                   help="exec = command + args (default), shell = one quoted command string")
    i.add_argument("--no-test", action="store_true")
    u = sp.add_parser("uninstall", help="remove our hooks from settings.json")
    u.add_argument("--settings-path")
    sp.add_parser("test", help="send a test character")
    o = sp.add_parser("_offline-later")
    o.add_argument("seconds")
    s = sp.add_parser("status", help="generic status CLI for scripts and bots")
    s.add_argument("--session", required=True)
    s.add_argument("--state", required=True, choices=STATES)
    s.add_argument("--tool")
    s.add_argument("--label")
    s.add_argument("--project")
    s.add_argument("--owner")
    s.add_argument("--source", default="cli")
    s.add_argument("--final", action="store_true")
    s.add_argument("--server")
    s.add_argument("--room")
    s.add_argument("--key")
    d = sp.add_parser("doctor", help="show config, hook presence, log and server reachability")
    d.add_argument("--settings-path")
    sp.add_parser("map", help=argparse.SUPPRESS)
    return p


def main(argv: object = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "hook":
        return run_hook()
    a = build_parser().parse_args(argv)
    handlers = {"install": cmd_install, "uninstall": cmd_uninstall, "test": cmd_test, "status": cmd_status,
                "doctor": cmd_doctor, "map": cmd_map, "_offline-later": cmd_offline_later}
    if a.cmd not in handlers:
        build_parser().print_help()
        return 2
    return handlers[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
