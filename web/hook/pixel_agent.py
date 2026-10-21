#!/usr/bin/env python3
"""Pixel Office — local AI agents (CLOUD.md §17). Python >= 3.8, stdlib only, single file.

Library:
    from pixel_agent import Agent
    bot = Agent("research-bot", project="notes")
    bot.report("thinking")                                   # never raises, never blocks
    with bot.working(tool="llm", label="Özetliyor"):
        summarize()
    bot.done()                                               # sleeping + final (bed, then leaves after 300 s)

CLI:
    python3 pixel_agent.py report --name aider --state working [--tool T] [--label L] [--project P]
    python3 pixel_agent.py run   [--name N] [--idle-after 15] [--on-exit sleeping|offline] -- aider …   (pixel-run)
    python3 pixel_agent.py proxy --listen 127.0.0.1:11435 --upstream http://127.0.0.1:11434 --name ollama   (pixel-proxy)
    python3 pixel_agent.py antigravity [--name Antigravity] …   (pixel-antigravity; needs pixel_antigravity.py next to
                                                             this file, see that file)
    python3 pixel_agent.py install-shims                     # ~/.pixel-office/bin/pixel-run, pixel-proxy[, pixel-antigravity]

Target (§17.1): explicit args → env PIXEL_OFFICE_URL / _ROOM / _KEY / _OWNER → ~/.pixel-office/config.json →
http://127.0.0.1:8765 (local server, no room/key).

Privacy: only state, tool name, label, project basename, name and owner are sent. Never prompts, model
output, command lines, arguments, stdout/stderr content, request/response bodies or API keys.
"""

from __future__ import annotations

import argparse
import atexit
import functools
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

VERSION = "2.0.0"
DEFAULT_URL = "http://127.0.0.1:8765"
STATES = ("working", "thinking", "waiting_for_user", "idle", "sleeping", "offline")
GROUPS = {"thinking": "active", "working": "active", "waiting_for_user": "wait",
          "idle": "rest", "sleeping": "rest", "offline": "rest"}
TOOL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
SOURCE_RE = re.compile(r"^[a-z0-9-]{1,16}$")
OWNER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")

LOCAL_DEBOUNCE = 0.3        # s (§17.2)
CLOUD_DEBOUNCE = 1.0        # s, group changes
CLOUD_INTRA_GROUP = 30.0    # s, thinking<->working, tool/label changes
KEEPALIVE = 300.0           # s
POST_TIMEOUT = 4.0
MAX_BATCH = 10

L_ERROR = "Hata"
L_DONE = "Bitti"
L_LLM_ERROR = "LLM hatası"
AG_MODULE = "pixel_antigravity"     # optional sibling file with the Antigravity watcher (Phase 18)


def _debug(msg: str) -> None:
    if os.environ.get("PIXEL_OFFICE_DEBUG") == "1":
        try:
            sys.stderr.write("[pixel] %s\n" % msg)
        except Exception:
            pass


# --------------------------------------------------------------------------------------------------
# Config (§17.1)
# --------------------------------------------------------------------------------------------------

def office_home() -> str:
    env = os.environ.get("PIXEL_OFFICE_HOME")
    return os.path.abspath(os.path.expanduser(env)) if env else os.path.join(os.path.expanduser("~"), ".pixel-office")


def _load_config() -> dict:
    try:
        with open(os.path.join(office_home(), "config.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def os_user() -> str:
    for k in ("USER", "USERNAME", "LOGNAME"):
        v = os.environ.get(k)
        if v:
            return v
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return "local"


def resolve_target(url: object = None, room: object = None, key: object = None, owner: object = None) -> dict:
    """{url, room, key, owner, ownerId}. url/room/key come as one unit from the first source that has a URL."""
    env = os.environ
    cfg = _load_config()
    if url:
        t = {"url": url, "room": room, "key": key}
    elif env.get("PIXEL_OFFICE_URL"):
        t = {"url": env["PIXEL_OFFICE_URL"], "room": room or env.get("PIXEL_OFFICE_ROOM"),
             "key": key or env.get("PIXEL_OFFICE_KEY")}
    elif cfg.get("server"):
        t = {"url": cfg["server"], "room": room or cfg.get("room"), "key": key or cfg.get("key")}
    else:
        t = {"url": DEFAULT_URL, "room": room, "key": key}
    t["url"] = str(t["url"]).rstrip("/")
    t["room"] = t["room"] or None
    t["key"] = t["key"] or None
    oid = None
    if owner:
        own = owner
    elif env.get("PIXEL_OFFICE_OWNER"):
        own = env["PIXEL_OFFICE_OWNER"]
    elif cfg.get("name"):
        own = cfg["name"]
        oid = cfg.get("ownerId")
    else:
        own = os_user()
    if oid is None and cfg.get("name") and cfg.get("name") == own:
        oid = cfg.get("ownerId")
    t["owner"] = str(own)[:24] or "local"
    t["ownerId"] = oid if isinstance(oid, str) and OWNER_ID_RE.match(oid) else None
    return t


def _is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _urlopen(req, timeout: float):
    """urlopen that sends loopback requests directly: an `http_proxy` without `no_proxy` would otherwise get the
    local-mode traffic, write key included (v2.md H8)."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    if _is_loopback_url(url):
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def status_url(target: dict) -> str:
    url = target["url"] + "/api/status"
    if target.get("room"):
        url += "?room=" + urllib.parse.quote(str(target["room"]), safe="")
    return url


def post_updates(target: dict, updates: list, timeout: float = POST_TIMEOUT, retries: int = 1,
                 sleep=time.sleep) -> int:
    """POST one update or a batch; returns the HTTP status (0 = network error). Never raises."""
    if not updates:
        return 0
    body = updates[0] if len(updates) == 1 else {"agents": updates[:MAX_BATCH]}
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "pixel-agent/" + VERSION}
    if target.get("key"):
        headers["Authorization"] = "Bearer " + str(target["key"])
    status = 0
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(status_url(target), data=data, headers=headers, method="POST")
            with _urlopen(req, timeout=timeout) as resp:
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
            _debug("HTTP %s" % status)
            return status
        except Exception as e:
            status = 0
            _debug("network error: %s" % type(e).__name__)
            if attempt < retries:
                sleep(1.0)
                continue
    return status


# --------------------------------------------------------------------------------------------------
# Small sanitisers (the server validates again; these keep requests valid)
# --------------------------------------------------------------------------------------------------

def slug(name: object) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")
    return s[:64] or "agent"


def clean_tool(tool: object, fallback: object = None) -> object:
    if not tool:
        return fallback
    s = re.sub(r"[^A-Za-z0-9_.:-]", "", str(tool))[:32]
    return s if TOOL_RE.match(s) else fallback


def _text(v: object, limit: int) -> object:
    if v is None:
        return None
    s = "".join(ch for ch in str(v) if ord(ch) >= 0x20 and not 0x7F <= ord(ch) <= 0x9F)
    s = " ".join(s.split())[:limit].strip()
    return s or None


def clean_project(p: object) -> object:
    if not p:
        return None
    parts = [x for x in re.split(r"[\\/]+", str(p)) if x]
    return _text(parts[-1], 32) if parts else None


# --------------------------------------------------------------------------------------------------
# Sender (§17.2 policy): one daemon thread per target, batching, dedupe, debounce/throttle, keep-alive
# --------------------------------------------------------------------------------------------------

class _Rec:
    __slots__ = ("desired", "dkey", "skey", "sent_t", "sent_group", "first_pending", "final_or_gone")

    def __init__(self) -> None:
        self.desired = None
        self.dkey = None
        self.skey = None
        self.sent_t = None
        self.sent_group = None
        self.first_pending = None
        self.final_or_gone = False


def _ukey(u: dict) -> tuple:
    return (u.get("state"), u.get("tool"), u.get("label"), u.get("project"), u.get("name"), bool(u.get("final")))


class Sender:
    def __init__(self, target: dict, post=None, clock=time.time, start_thread: bool = True) -> None:
        self.target = target
        self.cloud = bool(target.get("room"))
        self.clock = clock
        self._post = post or (lambda updates, timeout: post_updates(self.target, updates, timeout=timeout))
        self._recs = {}
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._send_lock = threading.Lock()
        self._stop = False
        self._thread = None
        if start_thread:
            self._thread = threading.Thread(target=self._loop, name="pixel-agent-sender", daemon=True)
            self._thread.start()

    # -- policy ------------------------------------------------------------------------------------
    def submit(self, update: dict) -> None:
        now = self.clock()
        k = (update["session"], update.get("sub"))
        with self._cond:
            rec = self._recs.get(k)
            if rec is None:
                rec = self._recs[k] = _Rec()
            key = _ukey(update)
            rec.desired = update
            if key != rec.dkey:
                rec.dkey = key
                if key == rec.skey:
                    rec.first_pending = None           # back to what the server already shows
                elif rec.first_pending is None:
                    rec.first_pending = now
            self._cond.notify()

    def _due(self, rec: _Rec) -> object:
        if rec.desired is None:
            return None
        if rec.dkey == rec.skey:
            if rec.final_or_gone or rec.sent_t is None:
                return None
            return rec.sent_t + KEEPALIVE
        fp = rec.first_pending if rec.first_pending is not None else self.clock()
        if not self.cloud:
            return fp + LOCAL_DEBOUNCE
        st = rec.desired.get("state")
        if rec.sent_group is None or GROUPS.get(st) != rec.sent_group or st == "offline" or rec.desired.get("final"):
            return fp + CLOUD_DEBOUNCE
        return max(fp, rec.sent_t + CLOUD_INTRA_GROUP)

    def next_due(self) -> object:
        with self._lock:
            dues = [d for d in (self._due(r) for r in self._recs.values()) if d is not None]
        return min(dues) if dues else None

    def _materialize(self, u: dict, now: float) -> dict:
        out = {k: v for k, v in u.items() if not k.startswith("_") and v is not None and v is not False}
        if "_since" in u:
            out["stateAgeMs"] = max(0, int((now - u["_since"]) * 1000))
        if "_started" in u:
            out["startedAgeMs"] = max(0, int((now - u["_started"]) * 1000))
        return out

    def tick(self, force: bool = False, timeout: float = POST_TIMEOUT) -> list:
        """Send everything that is due (or everything pending when force). Returns the posted batches."""
        with self._send_lock:
            now = self.clock()
            picked = []
            with self._lock:
                for k, rec in self._recs.items():
                    d = self._due(rec)
                    if d is None:
                        continue
                    if force and rec.dkey == rec.skey:
                        continue                           # flush only pending changes
                    if force or d <= now:
                        picked.append((k, rec.dkey, self._materialize(rec.desired, now)))
            batches = []
            for i in range(0, len(picked), MAX_BATCH):
                chunk = picked[i:i + MAX_BATCH]
                updates = [p[2] for p in chunk]
                try:
                    status = self._post(updates, timeout)
                except Exception:
                    status = 0
                batches.append(updates)
                _debug("sent %d update(s) → %s" % (len(updates), status))
                # Success, 4xx (incl. 429) and repeated network errors are all "done": dropped, never retried hot.
                with self._lock:
                    t = self.clock()
                    for k, key, u in chunk:
                        rec = self._recs.get(k)
                        if rec is None:
                            continue
                        rec.skey = key
                        rec.sent_t = t
                        rec.sent_group = GROUPS.get(u.get("state"))
                        rec.final_or_gone = u.get("state") == "offline" or bool(u.get("final"))
                        rec.first_pending = None if rec.dkey == key else t
                        if u.get("state") == "offline" and rec.dkey == key:
                            del self._recs[k]
            return batches

    def flush(self, timeout: float = 2.0) -> None:
        """Send all pending changes now (bounded by `timeout`). Never raises."""
        try:
            if self._thread is None or threading.current_thread() is not self._thread:
                done = threading.Event()

                def run():
                    try:
                        self.tick(force=True, timeout=max(0.2, timeout - 0.1))
                    finally:
                        done.set()
                threading.Thread(target=run, daemon=True).start()
                done.wait(timeout)
        except Exception:
            pass

    def _loop(self) -> None:
        while True:
            with self._cond:
                if self._stop:
                    return
                nd = None
                dues = [d for d in (self._due(r) for r in self._recs.values()) if d is not None]
                if dues:
                    nd = min(dues)
                wait = 5.0 if nd is None else max(0.0, min(5.0, nd - self.clock()))
                if wait > 0:
                    self._cond.wait(wait)
            try:
                self.tick()
            except Exception:
                pass

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()


_senders = {}
_senders_lock = threading.Lock()


def get_sender(target: dict) -> Sender:
    k = (target["url"], target.get("room"), target.get("key"))
    with _senders_lock:
        s = _senders.get(k)
        if s is None:
            s = _senders[k] = Sender(target)
        return s


# --------------------------------------------------------------------------------------------------
# Library API (§17.2)
# --------------------------------------------------------------------------------------------------

class Agent:
    """One office character. Every method is safe: it never raises and never blocks (except offline()/flush(),
    bounded by a short timeout)."""

    def __init__(self, name: str, project: object = None, session: object = None, owner: object = None,
                 url: object = None, room: object = None, key: object = None, source: str = "custom",
                 sub: object = None, agent_type: object = None, color: object = None, sender: object = None,
                 clock=time.time, register_atexit: bool = True) -> None:
        self.clock = clock
        self.finished = False
        try:
            self.target = resolve_target(url, room, key, owner)
            self.name = _text(name, 24) or "agent"
            self.session = slug(session or name)
            self.sub = slug(sub) if sub else None
            self.project = clean_project(project)
            self.source = source if isinstance(source, str) and SOURCE_RE.match(source) else "custom"
            self.agent_type = _text(agent_type, 24)
            self.color = color if isinstance(color, int) and not isinstance(color, bool) and 0 <= color <= 15 else None
            self.sender = sender if sender is not None else get_sender(self.target)
        except Exception:
            self.sender = None
        self._state = None
        self._since = None
        self._started = clock()
        self._tool = None
        self._tool_count = 0
        self._lock = threading.Lock()
        if register_atexit:
            atexit.register(self._atexit)

    # -- core --------------------------------------------------------------------------------------
    def report(self, state: str, tool: object = None, label: object = None, final: bool = False) -> None:
        try:
            if state not in STATES or self.sender is None:
                return
            now = self.clock()
            tool = clean_tool(tool)
            with self._lock:
                if state != self._state:
                    self._since = now
                    self._state = state
                if tool and tool != self._tool and state == "working":
                    self._tool_count += 1
                self._tool = tool
                self.finished = state == "offline" or (state == "sleeping" and bool(final))
                u = {"session": self.session, "sub": self.sub, "owner": self.target["owner"],
                     "ownerId": self.target.get("ownerId"), "name": self.name, "project": self.project,
                     "state": state, "tool": tool, "label": _text(label, 40), "agentType": self.agent_type,
                     "source": self.source, "color": self.color, "toolCount": self._tool_count,
                     "final": bool(final) and state == "sleeping", "_since": self._since, "_started": self._started}
            self.sender.submit(u)
        except Exception:
            pass

    def thinking(self, label: object = None) -> None:
        self.report("thinking", label=label)

    def idle(self, label: object = None) -> None:
        self.report("idle", label=label)

    def waiting(self, label: object = None) -> None:
        self.report("waiting_for_user", label=label)

    def done(self, label: object = None) -> None:
        self.report("sleeping", label=label, final=True)

    def offline(self, timeout: float = 1.0) -> None:
        self.report("offline")
        self.flush(timeout)

    def flush(self, timeout: float = 2.0) -> None:
        try:
            if self.sender is not None:
                self.sender.flush(timeout)
        except Exception:
            pass

    def _atexit(self) -> None:
        if not self.finished:
            self.offline(1.0)

    # -- helpers -----------------------------------------------------------------------------------
    def working(self, tool: object = None, label: object = None) -> "_Working":
        """Context manager: working on enter → idle on normal exit, idle + "Hata" on exception (re-raised)."""
        return _Working(self, tool, label)

    def task(self, tool: object = None, label: object = None):
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                with self.working(tool=tool or getattr(fn, "__name__", None), label=label):
                    return fn(*a, **kw)
            return wrapper
        return deco


class _Working:
    def __init__(self, agent: Agent, tool: object, label: object) -> None:
        self.agent, self.tool, self.label = agent, tool, label

    def __enter__(self) -> Agent:
        self.agent.report("working", tool=self.tool, label=self.label)
        return self.agent

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.agent.report("idle")
        else:
            self.agent.report("idle", label=L_ERROR)        # the exception text is never sent
        return False


# --------------------------------------------------------------------------------------------------
# pixel-run (§17.3)
# --------------------------------------------------------------------------------------------------

class Activity:
    """working while output flows, quiet_state after idle_after seconds of silence. Byte counts only."""

    def __init__(self, agent, tool: object, idle_after: float, quiet_state: str, clock=time.monotonic) -> None:
        self.agent, self.tool, self.idle_after, self.quiet_state, self.clock = agent, tool, idle_after, quiet_state, clock
        self.lock = threading.Lock()
        self.last = clock()
        self.state = None
        self.bytes = 0

    def start(self) -> None:
        with self.lock:
            self.last = self.clock()
            self.state = "working"
        self.agent.report("working", tool=self.tool)

    def output(self, n: int) -> None:
        flip = False
        with self.lock:
            self.bytes += n
            self.last = self.clock()
            if self.state != "working":
                self.state = "working"
                flip = True
        if flip:
            self.agent.report("working", tool=self.tool)

    def check(self) -> None:
        flip = False
        with self.lock:
            if self.state == "working" and self.idle_after > 0 and self.clock() - self.last >= self.idle_after:
                self.state = self.quiet_state
                flip = True
        if flip:
            self.agent.report(self.quiet_state)


def _exit_code(rc: int) -> int:
    return 128 - rc if rc < 0 else rc


def _write_all(fd: int, data: bytes) -> None:
    while data:
        try:
            n = os.write(fd, data)
        except InterruptedError:
            continue
        data = data[n:]


def _forward_signals(get_proc) -> dict:
    """Install handlers forwarding SIGINT/SIGTERM/SIGHUP to the child. Returns the previous handlers."""
    old = {}
    if threading.current_thread() is not threading.main_thread():
        return old

    def fwd(signum, frame):
        p = get_proc()
        if p is not None and p.poll() is None:
            try:
                p.send_signal(signum)
            except Exception:
                pass
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            old[sig] = signal.signal(sig, fwd)
        except (ValueError, OSError):
            pass
    return old


def _restore_signals(old: dict) -> None:
    for sig, h in old.items():
        try:
            signal.signal(sig, h)
        except Exception:
            pass


def _run_pty(cmd: list, act: Activity, poll: float = 0.2) -> int:
    import fcntl
    import select
    import struct
    import termios
    import tty

    master, slave = os.openpty()
    stdin_fd = 0
    stdout_fd = 1
    in_tty = os.isatty(stdin_fd)

    def copy_winsize(*_):
        try:
            src = stdout_fd if os.isatty(stdout_fd) else (stdin_fd if in_tty else None)
            if src is not None:
                ws = fcntl.ioctl(src, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
                fcntl.ioctl(master, termios.TIOCSWINSZ, ws)
        except Exception:
            pass
    copy_winsize()

    def child_setup():
        try:
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)     # make the pty our controlling terminal (after setsid)
        except Exception:
            pass

    proc = None
    try:
        proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                preexec_fn=child_setup, close_fds=True)
    except OSError:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)
    old_sig = _forward_signals(lambda: proc)
    old_winch = None
    if hasattr(signal, "SIGWINCH") and threading.current_thread() is threading.main_thread():
        try:
            old_winch = signal.signal(signal.SIGWINCH, copy_winsize)
        except Exception:
            old_winch = None
    old_attrs = None
    if in_tty:
        try:
            old_attrs = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd, termios.TCSANOW)      # keep type-ahead input
        except Exception:
            old_attrs = None
    read_fds = [master] + ([stdin_fd] if _readable_stdin() else [])
    exited_at = None
    try:
        while True:
            try:
                r, _, _ = select.select(read_fds, [], [], poll)
            except InterruptedError:
                r = []
            if master in r:
                try:
                    data = os.read(master, 65536)
                except OSError:              # EIO: the child side is closed
                    data = b""
                if not data:
                    break
                _write_all(stdout_fd, data)
                act.output(len(data))
            if stdin_fd in r:
                try:
                    data = os.read(stdin_fd, 65536)
                except OSError:
                    data = b""
                if data:
                    _write_all(master, data)
                else:
                    read_fds = [master]
            act.check()
            if proc.poll() is not None:
                if exited_at is None:
                    exited_at = time.monotonic()
                elif not r and time.monotonic() - exited_at > 0.3:
                    break                    # a background grandchild may keep the pty open
    finally:
        if old_attrs is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, old_attrs)
            except Exception:
                pass
        if old_winch is not None:
            try:
                signal.signal(signal.SIGWINCH, old_winch)
            except Exception:
                pass
        _restore_signals(old_sig)
        try:
            os.close(master)
        except OSError:
            pass
    return _exit_code(proc.wait())


def _readable_stdin() -> bool:
    try:
        os.fstat(0)
        return True
    except OSError:
        return False


def _run_pipe(cmd: list, act: Activity, poll: float = 0.2) -> int:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    old_sig = _forward_signals(lambda: proc)

    def pump(src, dst_fd):
        fd = src.fileno()
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            try:
                _write_all(dst_fd, data)
            except OSError:
                pass
            act.output(len(data))
    threads = [threading.Thread(target=pump, args=(proc.stdout, 1), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, 2), daemon=True)]
    for t in threads:
        t.start()
    try:
        while proc.poll() is None:
            act.check()
            time.sleep(poll)
        for t in threads:
            t.join(2.0)
    finally:
        _restore_signals(old_sig)
        for f in (proc.stdout, proc.stderr):
            try:
                f.close()
            except Exception:
                pass
    return _exit_code(proc.wait())


def _run_inherit(cmd: list, act: Activity) -> int:
    proc = subprocess.Popen(cmd)
    old_sig = _forward_signals(lambda: proc)
    try:
        return _exit_code(proc.wait())
    finally:
        _restore_signals(old_sig)


def run_command(cmd: list, agent, tool: object = None, idle_after: float = 15.0, quiet_state: str = "idle",
                on_exit: str = "sleeping", mode: str = "auto", poll: float = 0.2) -> int:
    """Run `cmd`, mirror its activity to `agent`, return the child's exit code (128+N if killed by signal N)."""
    tool = clean_tool(tool or os.path.basename(cmd[0]), "cmd")
    if mode == "auto":
        if os.name == "nt":
            mode = "inherit"
        else:
            mode = "pty" if os.isatty(1) else "pipe"
    act = Activity(agent, tool, idle_after, quiet_state)
    act.start()
    try:
        if mode == "pty":
            rc = _run_pty(cmd, act, poll)
        elif mode == "pipe":
            rc = _run_pipe(cmd, act, poll)
        else:
            rc = _run_inherit(cmd, act)
    except FileNotFoundError:
        sys.stderr.write("pixel-run: komut bulunamadı: %s\n" % cmd[0])
        rc = 127
    except OSError as e:
        sys.stderr.write("pixel-run: başlatılamadı: %s\n" % e)
        rc = 126
    try:
        if on_exit == "offline":
            agent.report("offline")
        else:
            agent.report("sleeping", label=L_DONE if rc == 0 else "Hata: kod %d" % rc, final=True)
        agent.flush(2.0)
    except Exception:
        pass
    return rc


# --------------------------------------------------------------------------------------------------
# pixel-proxy (§17.4)
# --------------------------------------------------------------------------------------------------

GEN_PATHS = frozenset({"/v1/chat/completions", "/v1/completions", "/v1/responses", "/api/chat", "/api/generate",
                       "/completion", "/completions", "/chat/completions", "/api/v0/chat/completions",
                       "/api/v0/completions"})
HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
                        "te", "trailer", "trailers", "transfer-encoding", "upgrade"})
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,24}$")
SCAN_JSON_MAX = 8 * 1024 * 1024
SCAN_LINE_MAX = 1024 * 1024


def _names_from(obj: object, out: list) -> None:
    """Collect function names of tool calls (OpenAI chat/stream, Responses API, Ollama). Never keeps arguments."""
    if not isinstance(obj, dict):
        return

    def calls(lst):
        if isinstance(lst, list):
            for c in lst:
                if isinstance(c, dict):
                    fn = c.get("function")
                    out.append(fn.get("name") if isinstance(fn, dict) else None)

    for ch in obj.get("choices") or []:
        if isinstance(ch, dict):
            for part in ("message", "delta"):
                m = ch.get(part)
                if isinstance(m, dict):
                    calls(m.get("tool_calls"))
                    fc = m.get("function_call")
                    if isinstance(fc, dict):
                        out.append(fc.get("name"))
    msg = obj.get("message")
    if isinstance(msg, dict):
        calls(msg.get("tool_calls"))                     # Ollama /api/chat
    items = []
    if isinstance(obj.get("output"), list):
        items.extend(obj["output"])                      # Responses API
    resp = obj.get("response")
    if isinstance(resp, dict) and isinstance(resp.get("output"), list):
        items.extend(resp["output"])                     # Responses API stream: response.completed
    if isinstance(obj.get("item"), dict):
        items.append(obj["item"])                        # Responses API stream: output_item.added/done
    for it in items:
        if isinstance(it, dict) and it.get("type") == "function_call":
            out.append(it.get("name"))


class ToolScanner:
    """Fed a *copy* of the response bytes. Keeps only booleans and function names (§17.4)."""

    def __init__(self, content_type: str = "", content_encoding: str = "") -> None:
        self.ctype = (content_type or "").lower()
        self.has_tool = False
        self.name = None
        self._line = bytearray()
        self._skip_line = False
        self._whole = bytearray()
        self._whole_ok = "json" in self.ctype and "ndjson" not in self.ctype
        enc = (content_encoding or "").lower().strip()
        self._dec = None
        if enc in ("gzip", "x-gzip", "deflate"):
            import zlib
            self._dec = zlib.decompressobj(16 + zlib.MAX_WBITS if "gzip" in enc else zlib.MAX_WBITS)
        elif enc not in ("", "identity"):
            self._whole_ok = False
            self._disabled = True
        self._disabled = enc not in ("", "identity", "gzip", "x-gzip", "deflate")

    def _note(self, names: list) -> None:
        if names:
            self.has_tool = True
            if self.name is None:
                for n in names:
                    if isinstance(n, str) and n:
                        self.name = n
                        break

    def _parse(self, raw: bytes) -> None:
        s = raw.strip()
        if s.startswith(b"data:"):
            s = s[5:].strip()
        if not s or s == b"[DONE]" or s[:1] not in (b"{", b"["):
            return
        try:
            obj = json.loads(s.decode("utf-8", "replace"))
        except ValueError:
            return
        names = []
        _names_from(obj, names)
        self._note(names)

    def feed(self, chunk: bytes) -> None:
        if self._disabled or not chunk:
            return
        try:
            if self._dec is not None:
                chunk = self._dec.decompress(chunk)
            if self._whole_ok:
                if len(self._whole) + len(chunk) <= SCAN_JSON_MAX:
                    self._whole.extend(chunk)
                else:
                    self._whole_ok = False
                    self._whole = bytearray()
            start = 0
            while True:
                i = chunk.find(b"\n", start)
                if i < 0:
                    rest = chunk[start:]
                    if not self._skip_line:
                        if len(self._line) + len(rest) > SCAN_LINE_MAX:
                            self._line = bytearray()
                            self._skip_line = True
                        else:
                            self._line.extend(rest)
                    break
                if not self._skip_line:
                    self._line.extend(chunk[start:i])
                    if len(self._line) <= SCAN_LINE_MAX:
                        self._parse(bytes(self._line))
                self._line = bytearray()
                self._skip_line = False
                start = i + 1
        except Exception:
            self._disabled = True

    def close(self) -> tuple:
        try:
            if not self._disabled and self._line and not self._skip_line:
                self._parse(bytes(self._line))
            if not self.has_tool and self._whole_ok and self._whole:
                try:
                    obj = json.loads(bytes(self._whole).decode("utf-8", "replace"))
                    names = []
                    _names_from(obj, names)
                    self._note(names)
                except ValueError:
                    pass
        except Exception:
            pass
        self._line = bytearray()
        self._whole = bytearray()
        return self.has_tool, self.name


class StateTracker:
    """Per agent name: in-flight count → thinking; finished → working(tool) / idle / idle "LLM hatası"."""

    def __init__(self, agent_factory) -> None:
        self.agent_factory = agent_factory
        self.agents = {}
        self.inflight = {}
        self.lock = threading.Lock()

    def agent(self, name: str):
        with self.lock:
            a = self.agents.get(name)
            if a is None:
                a = self.agents[name] = self.agent_factory(name)
            return a

    def start(self, name: str, label: object = None) -> None:
        a = self.agent(name)
        with self.lock:
            self.inflight[name] = self.inflight.get(name, 0) + 1
        a.report("thinking", tool="LLM", label=label)

    def finish(self, name: str, error: bool = False, tool: object = None, has_tool: bool = False,
               label: object = None) -> None:
        a = self.agent(name)
        with self.lock:                                  # report under the lock: ordering per agent
            n = max(0, self.inflight.get(name, 0) - 1)
            if n == 0:
                if has_tool:
                    a.report("working", tool=clean_tool(tool, "tool"), label=label)
                elif error:
                    a.report("idle", label=L_LLM_ERROR)
                else:
                    a.report("idle")
            self.inflight[name] = n

    def shutdown(self, timeout: float = 1.0) -> None:
        with self.lock:
            agents = list(self.agents.values())
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


def _is_loopback(host: str) -> bool:
    return host in ("localhost", "::1", "[::1]") or host.startswith("127.")


def _make_proxy_handler():
    from http.server import BaseHTTPRequestHandler

    class ProxyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "pixel-proxy/" + VERSION

        def log_message(self, format, *args):      # noqa: A002 — never log (paths may contain data)
            pass

        def _identity(self, path: str) -> tuple:
            srv = self.server
            name = None
            h = self.headers.get("X-Pixel-Agent")
            if h:
                cand = urllib.parse.unquote(h).strip()
                if AGENT_NAME_RE.match(cand):
                    name = cand
            if path.startswith("/agent/"):
                rest = path[len("/agent/"):]
                seg, sep, tail = rest.partition("/")
                cand = urllib.parse.unquote(seg).strip()
                path = "/" + tail if sep else "/"
                if name is None and AGENT_NAME_RE.match(cand):
                    name = cand
            return (name or srv.default_name), path

        def _read_body(self) -> object:
            te = (self.headers.get("Transfer-Encoding") or "").lower()
            if "chunked" in te:
                out = bytearray()
                while True:
                    line = self.rfile.readline(65537)
                    size = int(line.split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        while True:                  # trailers
                            t = self.rfile.readline(65537)
                            if t in (b"\r\n", b"\n", b""):
                                break
                        break
                    out.extend(self.rfile.read(size))
                    self.rfile.readline(3)
                return bytes(out)
            cl = self.headers.get("Content-Length")
            if cl is not None:
                n = int(cl)
                return self.rfile.read(n) if n > 0 else b""
            return None

        def _error(self, code: int, err: str) -> None:
            body = json.dumps({"error": err}).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                self.close_connection = True

        def handle_any(self) -> None:
            srv = self.server
            raw_path, _, query = self.path.partition("?")
            name, path = self._identity(raw_path)
            try:
                body = self._read_body()
            except (ValueError, OSError):
                self.close_connection = True
                return self._error(400, "bad_request")
            gen = self.command == "POST" and path in GEN_PATHS
            label = None
            if gen and srv.send_model and body:
                try:
                    m = json.loads(body.decode("utf-8", "replace")).get("model")
                    label = _text(m, 40) if isinstance(m, str) else None
                except Exception:
                    label = None
            if gen:
                srv.tracker.start(name, label)
            finished = [False]

            def finish(error=False, has_tool=False, tool=None):
                if gen and not finished[0]:
                    finished[0] = True
                    srv.tracker.finish(name, error=error, tool=tool, has_tool=has_tool, label=label)

            import http.client
            u = srv.upstream
            conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
            conn = conn_cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=srv.connect_timeout)
            try:
                try:
                    conn.connect()
                    conn.sock.settimeout(None)        # no timeouts on streams
                except OSError:
                    finish(error=True)
                    return self._error(502, "upstream_unreachable")
                target = (srv.upstream_base + path) or "/"
                if query:
                    target += "?" + query
                conn.putrequest(self.command, target, skip_host=True, skip_accept_encoding=True)
                drop = set(HOP_BY_HOP) | {"host", "content-length", "x-pixel-agent", "expect"}
                for tok in (self.headers.get("Connection") or "").split(","):
                    if tok.strip():
                        drop.add(tok.strip().lower())
                for k, v in self.headers.items():
                    if k.lower() not in drop:
                        conn.putheader(k, v)
                conn.putheader("Host", u.netloc)
                if body is not None:
                    conn.putheader("Content-Length", str(len(body)))
                conn.endheaders(body if body else None)
                resp = conn.getresponse()
            except OSError:
                finish(error=True)
                return self._error(502, "upstream_unreachable")

            status = resp.status
            no_body = self.command == "HEAD" or status in (204, 304) or 100 <= status < 200
            up_te = (resp.getheader("Transfer-Encoding") or "").lower()
            up_len = resp.getheader("Content-Length")
            chunked_out = not no_body and ("chunked" in up_te or up_len is None)
            resp_drop = set(HOP_BY_HOP)
            for tok in (resp.getheader("Connection") or "").split(","):
                if tok.strip():
                    resp_drop.add(tok.strip().lower())
            if chunked_out:
                resp_drop.add("content-length")
            scanner = ToolScanner(resp.getheader("Content-Type") or "", resp.getheader("Content-Encoding") or "") \
                if gen else None
            try:
                self.send_response_only(status, resp.reason)
                for k, v in resp.getheaders():
                    if k.lower() not in resp_drop:
                        self.send_header(k, v)
                if chunked_out:
                    self.send_header("Transfer-Encoding", "chunked")
                if (resp.getheader("Connection") or "").lower() == "close" and not chunked_out and up_len is None:
                    self.close_connection = True
                self.end_headers()
                if not no_body:
                    while True:
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        if chunked_out:
                            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                        else:
                            self.wfile.write(chunk)
                        self.wfile.flush()
                        if scanner is not None:
                            scanner.feed(chunk)
                    if chunked_out:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
            except (OSError, http.client.HTTPException):
                self.close_connection = True          # client (or upstream) went away
                try:
                    conn.close()
                except Exception:
                    pass
                finish(error=False)
                return
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            has_tool, tool = scanner.close() if scanner is not None else (False, None)
            finish(error=status >= 400, has_tool=has_tool and status < 400, tool=tool)
            try:
                conn.close()
            except Exception:
                pass

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = handle_any

    return ProxyHandler


def make_proxy(listen_host: str, listen_port: int, upstream: str, agent_factory, default_name: str = "local-llm",
               send_model: bool = False, connect_timeout: float = 10.0):
    from http.server import ThreadingHTTPServer

    class ProxyServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    u = urllib.parse.urlsplit(upstream)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("--upstream must be an http(s) URL")
    srv = ProxyServer((listen_host, listen_port), _make_proxy_handler())
    srv.upstream = u
    srv.upstream_base = u.path.rstrip("/")
    srv.default_name = default_name if AGENT_NAME_RE.match(default_name or "") else "local-llm"
    srv.send_model = send_model
    srv.connect_timeout = connect_timeout
    srv.tracker = StateTracker(agent_factory)
    return srv


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------

def _add_target_args(p) -> None:
    p.add_argument("--url", help="server base URL (default: env / config.json / http://127.0.0.1:8765)")
    p.add_argument("--room")
    p.add_argument("--key")
    p.add_argument("--owner")


def cmd_report(a) -> int:
    t = resolve_target(a.url, a.room, a.key, a.owner)
    u = {"session": slug(a.session or a.name), "owner": t["owner"], "name": _text(a.name, 24), "state": a.state,
         "source": a.source}
    if t.get("ownerId"):
        u["ownerId"] = t["ownerId"]
    for k, v in (("tool", clean_tool(a.tool)), ("label", _text(a.label, 40)), ("project", clean_project(a.project))):
        if v:
            u[k] = v
    if a.final and a.state == "sleeping":
        u["final"] = True
    status = post_updates(t, [u])
    ok = 200 <= status < 300
    if not ok and (a.strict or os.environ.get("PIXEL_OFFICE_DEBUG") == "1"):
        sys.stderr.write("pixel report: HTTP %s\n" % (status or "bağlantı hatası"))
    return 0 if ok or not a.strict else 1


def cmd_run(a, cmd: list) -> int:
    if not cmd:
        sys.stderr.write("pixel-run: komut eksik. Örnek: pixel-run --name aider -- aider\n")
        return 2
    name = a.name or os.path.basename(cmd[0]) or "cmd"
    agent = Agent(name, project=a.project or os.path.basename(os.getcwd()), url=a.url, room=a.room, key=a.key,
                  owner=a.owner, source="pixel-run", register_atexit=False)
    mode = "pty" if a.pty else ("pipe" if a.pipe else "auto")
    return run_command(cmd, agent, tool=a.tool, idle_after=a.idle_after, quiet_state=a.quiet_state,
                       on_exit=a.on_exit, mode=mode)


def cmd_proxy(a) -> int:
    host, _, port = a.listen.rpartition(":")
    host = host.strip("[]") or "127.0.0.1"
    try:
        port_i = int(port)
    except ValueError:
        sys.stderr.write("pixel-proxy: --listen host:port\n")
        return 2
    if not _is_loopback(host) and not a.allow_remote:
        sys.stderr.write("pixel-proxy: loopback dışı adres için --allow-remote gerekli\n")
        return 2

    def factory(name):
        return Agent(name, project=a.project, url=a.url, room=a.room, key=a.key, owner=a.owner, source="pixel-proxy",
                     register_atexit=False)
    try:
        srv = make_proxy(host, port_i, a.upstream, factory, a.name, a.send_model)
    except (OSError, ValueError) as e:
        sys.stderr.write("pixel-proxy: %s\n" % e)
        return 1
    print("pixel-proxy http://%s:%d → %s (ajan: %s)" % (host, srv.server_address[1], a.upstream, srv.default_name),
          flush=True)

    def _term(*_):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _term)
    except (ValueError, OSError):
        pass
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        srv.tracker.shutdown(1.0)
    return 0


def cmd_install_shims(a) -> int:
    home = office_home()
    bindir = os.path.join(home, "bin")
    os.makedirs(bindir, exist_ok=True)
    import shutil
    here = os.path.abspath(__file__)
    dst = os.path.join(home, "pixel_agent.py")
    if here != os.path.abspath(dst):
        shutil.copyfile(here, dst)
    shims = [("pixel-run", "run"), ("pixel-proxy", "proxy")]
    ag_src = os.path.join(os.path.dirname(here), AG_MODULE + ".py")
    if os.path.isfile(ag_src):                       # the Antigravity watcher is an optional sibling file
        ag_dst = os.path.join(home, AG_MODULE + ".py")
        if os.path.abspath(ag_src) != os.path.abspath(ag_dst):
            shutil.copyfile(ag_src, ag_dst)
        shims.append(("pixel-antigravity", "antigravity"))
    py = sys.executable or "python3"
    made = []
    for shim, sub in shims:
        if os.name == "nt":
            p = os.path.join(bindir, shim + ".cmd")
            with open(p, "w", encoding="utf-8", newline="\r\n") as f:
                f.write('@echo off\r\n"%s" "%s" %s %%*\r\n' % (py, dst, sub))
        else:
            import shlex
            p = os.path.join(bindir, shim)
            with open(p, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\nexec %s %s %s \"$@\"\n" % (shlex.quote(py), shlex.quote(dst), sub))
            os.chmod(p, 0o755)
        made.append(p)
    print("Oluşturuldu: " + ", ".join(made))
    if os.name == "nt":
        print('PATH\'e ekleyin (PowerShell): [Environment]::SetEnvironmentVariable("Path", $env:Path + ";%s", "User")'
              % bindir)
    else:
        print('PATH\'e ekleyin: echo \'export PATH="%s:$PATH"\' >> ~/.profile' % bindir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pixel_agent.py", description="Pixel Office local agent tools")
    sp = p.add_subparsers(dest="cmd")
    r = sp.add_parser("report", help="one-shot status report")
    r.add_argument("--name", required=True)
    r.add_argument("--state", required=True, choices=STATES)
    r.add_argument("--session")
    r.add_argument("--tool")
    r.add_argument("--label")
    r.add_argument("--project")
    r.add_argument("--source", default="custom")
    r.add_argument("--final", action="store_true")
    r.add_argument("--strict", action="store_true", help="exit 1 when the report could not be delivered")
    _add_target_args(r)
    rn = sp.add_parser("run", help="pixel-run: wrap a command")
    rn.add_argument("--name")
    rn.add_argument("--project")
    rn.add_argument("--tool")
    rn.add_argument("--idle-after", type=float, default=15.0)
    rn.add_argument("--quiet-state", choices=("idle", "waiting_for_user"), default="idle")
    rn.add_argument("--on-exit", choices=("sleeping", "offline"), default="sleeping")
    rn.add_argument("--pipe", action="store_true", help="pipe mode (idle detection, no TUI)")
    rn.add_argument("--pty", action="store_true", help="force pty mode (POSIX)")
    _add_target_args(rn)
    rn.add_argument("command", nargs=argparse.REMAINDER)
    px = sp.add_parser("proxy", help="pixel-proxy: OpenAI/Ollama passthrough")
    px.add_argument("--listen", default="127.0.0.1:11435")
    px.add_argument("--upstream", default="http://127.0.0.1:11434")
    px.add_argument("--name", default="local-llm")
    px.add_argument("--project")
    px.add_argument("--send-model", action="store_true", help="send the model name as the label")
    px.add_argument("--allow-remote", action="store_true")
    _add_target_args(px)
    sp.add_parser("antigravity", add_help=False,
                  help="pixel-antigravity: Antigravity IDE watcher (pixel_antigravity.py; --help for its flags)")
    sp.add_parser("install-shims", help="write ~/.pixel-office/bin/pixel-run, pixel-proxy (and pixel-antigravity)")
    return p


def cmd_antigravity(argv: list) -> int:
    """`antigravity …` → pixel_antigravity.py next to this file (kept separate so this library stays small and the
    connector, which bundles this file, does not carry the watcher; v2.md D6)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), AG_MODULE + ".py")
    if not os.path.isfile(path):
        sys.stderr.write("pixel-antigravity: %s.py bulunamadı. Bu dosyanın yanına indirin:\n"
                         "  curl -fsSL <sunucu>/hook/%s.py -o %s\n" % (AG_MODULE, AG_MODULE, path))
        return 2
    sys.modules.setdefault("pixel_agent", sys.modules[__name__])    # share this module (one Sender per target)
    import importlib.util
    spec = importlib.util.spec_from_file_location(AG_MODULE, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(argv, prog="pixel_agent.py antigravity")


def main(argv: object = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["antigravity"]:
        return cmd_antigravity(argv[1:])
    cmd = []
    if argv[:1] == ["run"] and "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    a = build_parser().parse_args(argv)
    if a.cmd == "report":
        return cmd_report(a)
    if a.cmd == "run":
        return cmd_run(a, cmd or [c for c in (a.command or []) if c != "--"])
    if a.cmd == "proxy":
        return cmd_proxy(a)
    if a.cmd == "install-shims":
        return cmd_install_shims(a)
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
