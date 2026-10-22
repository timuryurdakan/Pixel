"""The connector process (V3.md §5.1): relay client → session.Router → dispatcher → tasks.Scheduler → engines,
plus the office reporter, the loopback control API and the periodic housekeeping.

Every inner message (§3.4) is handled here. Requests carry `id`; replies carry `re`. Errors are
`{"type":"error","re","code","message"}` with the codes of §11.7.
"""

from __future__ import annotations

import copy
import hashlib
import os
import platform
import queue
import re
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

from . import __build__, __version__
from . import crypto as C
from .agents import LABELS, AgentError, AgentStore, OfficeReporter
from .approvals import (TOOL_ID_RE, Approvals, ControlServer, ToolApproval, ToolLimit, summary_hash,
                        validate_tool_request)
from .audit import AuditLog
from .config import (AUDIT_FILE, LEDGER_FILE, Config, claude_path_label, claude_path_refreshed, ensure_dir, find_claude,
                     remote_approvals_state)
from .engines.base import TaskContext, clear_history
from .engines.claude import ClaudeEngine
from .engines.ollama import OllamaEngine
from .engines.openai_compat import OpenAIEngine
from .peers import DeviceKey, PeerStore
from .relay import RelayClient
from .runtimes import RuntimeError_, Runtimes
from .session import Peer, Router
from .tasks import Scheduler, TaskError, TaskRun, fit_result, prompt_sha
from .usage import Budgets, Ledger, tz_name
from .workspaces import WorkspaceDenied, Workspaces

REQ_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}\Z")
TASK_RE = re.compile(r"^t_[A-Za-z0-9_-]{10}\Z")
PROFILE_LABELS = {"readonly": "Sadece okuma", "edit": "Düzenleyebilir", "dev": "Geliştirici", "auto": "Otomatik"}
STOP_WAIT_S = 10.0          # stop(): how long all cancelled runs together may take to end (v2.md H11)
APPROVAL_RESULT = {"ok": "allow", "denied": "deny", "timeout": "expired", "withdrawn": "withdrawn"}
APPROVAL_AUDIT = {"ok": "approval.approve", "denied": "approval.deny", "timeout": "approval.expire",
                  "withdrawn": "approval.withdraw"}
APPROVAL_POLL_S = 0.5       # how often a waiting /ctl/approvals/tool call checks that its hook is still there
SLOW_TYPES = {"peer.hello", "runtime.list", "runtime.start", "runtime.stop", "model.load", "model.unload",
              "workspace.list", "agent.create", "peer.revoke"}


class Reply(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def os_name() -> str:
    return {"linux": "linux", "darwin": "darwin", "win32": "windows"}.get(sys.platform,
                                                                          "linux" if sys.platform.startswith("linux") else sys.platform)


def _ms(t: Optional[float]) -> Optional[int]:
    return int(t * 1000) if isinstance(t, (int, float)) else None


def running_pyz_sha256() -> Optional[str]:
    """SHA-256 of the zipapp this process runs from (None from a source checkout). Read once at start-up: the installer
    replaces the file before restarting the service, and the running code is what matters."""
    import pixel_connector
    archive = getattr(getattr(pixel_connector, "__loader__", None), "archive", None)
    if not archive or not os.path.isfile(archive):
        return None
    h = hashlib.sha256()
    try:
        with open(archive, "rb") as f:
            for block in iter(lambda: f.read(1 << 16), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


class Connector:
    def __init__(self, home: str, cfg: Config, clock: Callable[[], float] = time.time, relay_factory=None,
                 office_sender=None, engines: Optional[dict] = None, start_ctl: bool = True,
                 user_home: Optional[str] = None, log: Callable[[str], None] = lambda s: None,
                 stop_wait_s: float = STOP_WAIT_S) -> None:
        if not cfg.ok:
            raise ValueError("invalid configuration: " + "; ".join(cfg.errors))
        self.home = ensure_dir(home)
        self.cfgobj = cfg
        self.cfg = cfg.data
        self.clock = clock
        self.log = log
        self.stop_wait_s = stop_wait_s
        self.key = DeviceKey.load_or_create(home)
        self.peers = PeerStore(home, clock)
        self.audit = AuditLog(os.path.join(home, AUDIT_FILE), self.cfg["audit"]["storePrompts"], clock=clock)
        self.agents = AgentStore(home, clock)
        self.ledger = Ledger(os.path.join(home, LEDGER_FILE), clock)
        self._ledger_lock = threading.Lock()
        self._ledger_closed = False
        self.budgets = Budgets(home, self.cfg["budgets"], self.ledger, self.agents.budgets)
        self.workspaces = Workspaces(self.cfg["workspaces"], user_home)
        self.runtimes = Runtimes(self.cfg["runtimes"], home=user_home)
        self.claude_limit: Optional[dict] = None
        eng = engines or {}
        # The OpenAI-compatible servers' API keys are this process's business only, never the Claude child's (H7).
        api_key_envs = [e["apiKeyEnv"] for e in self.cfg["engines"]["openai"] if e.get("apiKeyEnv")]
        # H38: a stale engines.claude.path (VS Code extension updated) is replaced at run time with the setup discovery;
        # workspaces are excluded because an agent can write there.
        self.claude = eng.get("claude") or ClaudeEngine(
            self.cfg["engines"]["claude"], self.cfg["profiles"], ledger_note=self._rate_limit_event,
            drop_env=api_key_envs,
            discover=lambda: find_claude(user_home, exclude=[w["real"] for w in self.cfg["workspaces"]]),
            on_path_refresh=self._claude_path_refreshed)
        if getattr(self.claude, "on_rate_limit", None) is not None and engines:
            self.claude.on_rate_limit = self._rate_limit_event
        self.ollama = eng.get("ollama") or OllamaEngine(self.cfg["engines"]["ollama"])
        self.openai: Dict[str, OpenAIEngine] = eng.get("openai") or {
            e["id"]: OpenAIEngine(e) for e in self.cfg["engines"]["openai"]}
        self.approvals = Approvals(self.cfg["confirm"]["timeoutS"], clock=clock)
        self.router = Router(self.key, self.peers, self.audit, self.send_l1, self._on_inner,
                             self.cfg["deviceName"], clock, self.cfg["limits"]["handshakesPerHour"],
                             on_peer_closed=lambda p: None)
        self.scheduler = Scheduler(self, self.cfg["limits"], clock)
        self.reporter = OfficeReporter(self.cfg["server"], self.cfg["room"], self.cfg["key"], self.cfg["owner"],
                                       self.key.device_id, sender=office_sender, clock=clock)
        factory = relay_factory or (lambda **kw: RelayClient(**kw))
        self.relay = factory(server=self.cfg["server"], room=self.cfg["room"], key=self.cfg["key"],
                             device_id=self.key.device_id, on_frame=self._on_frame, on_status=self._on_status,
                             on_connect=self._on_connect, log=log)
        self._inbox: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self.ctl: Optional[ControlServer] = None
        self._start_ctl = start_ctl
        self._threads: List[threading.Thread] = []
        self._last_purge = 0.0
        self._agent_state: Dict[str, str] = {}
        self.pyz_sha256 = running_pyz_sha256()

    # -- lifecycle -------------------------------------------------------------------------------
    def start(self) -> None:
        self.audit.write("config.load", hash=self.cfgobj.hash[:16], deviceId=self.key.device_id)
        for target, name in ((self._worker, "inbox"), (self._ticker, "ticker")):
            th = threading.Thread(target=target, name=name, daemon=True)
            th.start()
            self._threads.append(th)
        if self._start_ctl:
            self.ctl = ControlServer(self.cfg["approvalPort"], self.ctl_routes(), self.approvals)   # raises if busy
            self.ctl.write_control_file(self.home)
            self.ctl.start()
        # Logged once the control port is bound (v2.md H27): a start that dies on a busy port is not a service start.
        self.audit.write("service.start", version=__version__, build=__build__)
        for a in self.agents.list():
            self.office(a, "idle", None, None, None)
        self.relay.start()

    def stop(self) -> None:
        self._stop.set()
        runs = list(self.scheduler.runs.values())
        for run in runs:
            self.scheduler.cancel(run.id)
        self._wait_runs(runs)
        for a in self.agents.list():
            self.reporter.offline(a, self._ws_name(a))
        self.reporter.flush(3.0)
        self.reporter.stop()
        try:
            self.relay.stop()
        except Exception:
            pass
        if self.ctl is not None:
            self.ctl.shutdown()
            self.ctl.server_close()
            try:
                os.unlink(os.path.join(self.home, "control.json"))
            except OSError:
                pass
        self.scheduler.stop()
        self.audit.write("service.stop")
        with self._ledger_lock:
            self._ledger_closed = True
            self.ledger.close()

    def _wait_runs(self, runs: List[TaskRun]) -> None:
        """Give cancelled runs up to `stop_wait_s` in total to end, so their engines stop writing (ledger, agents.json)
        before the files close and no Claude process outlives the connector on macOS/Windows (v2.md H11)."""
        deadline = time.monotonic() + self.stop_wait_s
        late = []
        for run in runs:
            if not run.ended.wait(max(0.0, deadline - time.monotonic())):
                late.append(run)
        for run in late:
            kill = getattr(run.running, "kill", None)
            if kill is not None:
                try:
                    kill()
                except Exception as e:
                    self.log("kapanış: %s durdurulamadı (%s)" % (run.id, type(e).__name__))
        if late:
            self.log("kapanış: %d görev %.0f sn içinde bitmedi, zorla sonlandırıldı" % (len(late), self.stop_wait_s))

    def _ticker(self) -> None:
        while not self._stop.wait(1.0):
            for fn in (self.router.tick, self.approvals.tick, self.scheduler.tick):
                try:
                    fn()
                except Exception:
                    pass
            if self.clock() - self._last_purge > 86400:
                self._last_purge = self.clock()
                try:
                    self.ledger.purge(self.cfg["limits"]["retainDays"])
                except Exception:
                    pass

    # -- relay plumbing --------------------------------------------------------------------------
    def send_l1(self, frame: dict) -> bool:
        return self.relay.send(frame)

    def _on_frame(self, f: dict) -> None:
        self._inbox.put(f)

    def _on_connect(self) -> None:
        self._inbox.put({"t": "__connected"})

    def _on_status(self, s: str) -> None:
        if s in ("reconnecting", "connecting", "stopped") or s.startswith("error"):
            self._inbox.put({"t": "__disconnected"})

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                f = self._inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                t = f.get("t")
                if t == "__connected":
                    self.router.open_slot()
                elif t == "__disconnected":
                    self.router.reset()
                else:
                    self.router.handle_frame(f)
            except Exception as e:
                self.log("inbox error: %s" % type(e).__name__)

    # -- inner messages --------------------------------------------------------------------------
    def _on_inner(self, peer: Peer, msg: dict) -> None:
        t = msg.get("type")
        if t in SLOW_TYPES:
            threading.Thread(target=self._handle, args=(peer, msg), daemon=True, name="h-" + t).start()
        else:
            self._handle(peer, msg)

    def reply(self, peer: Peer, req: dict, obj: dict) -> None:
        rid = req.get("id")
        if isinstance(rid, str) and REQ_ID_RE.match(rid):
            obj = dict(obj, re=rid)
        try:
            self.router.send(peer, obj)
        except ValueError:
            self.router.send(peer, {"type": "error", "re": obj.get("re"), "code": "internal", "message": "too large"})

    def _handle(self, peer: Peer, msg: dict) -> None:
        t = msg.get("type")
        fn = getattr(self, "h_" + t.replace(".", "_"), None) if isinstance(t, str) else None
        try:
            if fn is None:
                raise Reply("bad_request", "unknown type")
            out = fn(peer, msg)
            if out is not None:
                self.reply(peer, msg, out)
        except Reply as e:
            self.reply(peer, msg, {"type": "error", "code": e.code, "message": str(e)[:200]})
        except (AgentError, TaskError, RuntimeError_) as e:
            self.reply(peer, msg, {"type": "error", "code": e.code, "message": str(e)[:200]})
        except WorkspaceDenied as e:
            self.audit.write("denied", bid=peer.bid, reason="workspace: %s" % e)
            self.reply(peer, msg, {"type": "error", "code": "workspace_denied", "message": str(e)[:200]})
        except Exception as e:
            self.log("handler error %s: %s" % (t, type(e).__name__))
            self.reply(peer, msg, {"type": "error", "code": "internal", "message": "internal error"})

    # -- helpers ---------------------------------------------------------------------------------
    def _ws_name(self, agent: dict) -> Optional[str]:
        ws = agent.get("workspace") or {}
        e = self.workspaces.entries.get(ws.get("id"))
        if e is None:
            return None
        return e["name"] + ("/" + ws["sub"].split("/")[-1] if ws.get("sub") else "")

    def _rate_limit_event(self, info: dict) -> None:
        self.claude_limit = info

    def agent_public(self, a: dict) -> dict:
        ws = a.get("workspace") or {}
        e = self.workspaces.entries.get(ws.get("id"))
        run = self.scheduler.active_for(a["key"])
        today = self.ledger.totals("day", agent=a["key"])
        return {"key": a["key"], "name": a["name"], "engine": a["engine"], "engineId": a.get("engineId"),
                "model": a.get("model"), "workspace": {"id": ws.get("id"), "name": e["name"] if e else None,
                                                       "sub": ws.get("sub")} if ws.get("id") else None,
                "profile": a.get("profile"), "state": self._agent_state.get(a["key"], "idle"),
                "task": run.id if run else None, "createdAt": a.get("createdAt"), "budget": a.get("budget"),
                "today": {"tokens": today["tokens"], "costUsd": today["costUsd"]}}

    def office(self, agent: dict, state: str, tool: Optional[str], label: Optional[str], task: Optional[str]) -> None:
        self._agent_state[agent["key"]] = state
        self.reporter.report(agent, self._ws_name(agent), state, tool, label)
        for p in self.router.all_peers():
            if agent["key"] in p.watching:
                try:
                    self.router.send(p, {"type": "agent.state", "agent": agent["key"], "state": state, "task": task})
                except ValueError:
                    pass

    def engine_for(self, agent: dict):
        if agent["engine"] == "claude":
            return self.claude
        if agent["engine"] == "ollama":
            return self.ollama
        eid = agent.get("engineId") or (next(iter(self.openai)) if self.openai else None)
        e = self.openai.get(eid) if eid else None
        if e is None:
            raise TaskError("engine_unavailable", "no such OpenAI-compatible server")
        return e

    def resolve_workspace(self, agent: dict) -> Optional[str]:
        ws = agent.get("workspace") or {}
        if not ws.get("id"):
            if agent["engine"] == "claude":
                raise TaskError("workspace_denied", "Claude agents need a workspace")
            return None
        try:
            return self.workspaces.resolve(ws["id"], ws.get("sub"))
        except WorkspaceDenied as e:
            raise TaskError("workspace_denied", str(e)) from None

    def _check_profile(self, agent: dict) -> None:
        if agent["engine"] == "claude" and agent.get("profile") == "auto" and not self.cfg["profiles"]["auto"]["enabled"]:
            raise TaskError("not_allowed", "auto profile disabled on this device")

    # -- Scheduler callbacks ---------------------------------------------------------------------
    def precheck(self, run: TaskRun) -> None:
        a = run.agent
        try:
            self.resolve_workspace(a)
            self._check_profile(a)
            per_task = self.cfg["engines"]["claude"]["maxBudgetUsdPerTask"] if a["engine"] == "claude" else None
            ok, _ = self.budgets.check_task(a["key"], a["engine"], per_task)
            if not ok:
                raise TaskError("budget_exceeded", "budget exceeded")
        except TaskError as e:
            self.audit.write("denied", bid=run.bid, agent=a["key"], reason=e.code)
            raise

    def task_created(self, run: TaskRun) -> None:
        a = run.agent
        self.ledger.start_task(run.id, a["key"], a["engine"], a.get("model"), prompt_sha(run.prompt), run.prompt,
                               run.bid)

    def task_audit(self, run: TaskRun, ev: str, result: str) -> None:
        """`task.start` (result "ok": the task may run) or `task.denied` (result "denied" | "timeout": the local
        confirmation said no). A task waiting for confirmation is logged once it is decided (v2.md H27)."""
        a = run.agent
        ws = a.get("workspace") or {}
        self.audit.write(ev, bid=run.bid, peer=run.peer_label, agent=a["key"], engine=a["engine"],
                         ws=ws.get("id"), sub=ws.get("sub"), profile=a.get("profile"), prompt=run.prompt,
                         result=result, task=run.id)

    def start_engine(self, run: TaskRun):
        a = self.agents.get(run.agent_key)
        run.agent = a
        cwd = self.resolve_workspace(a)
        self._check_profile(a)
        eng = self.engine_for(a)
        max_budget = None
        if a["engine"] == "claude":
            ok, max_budget = self.budgets.check_task(a["key"], "claude", self.cfg["engines"]["claude"]["maxBudgetUsdPerTask"])
            if not ok:
                raise TaskError("budget_exceeded", "budget exceeded")
            self._ensure_claude_path()
            if not self.cfg["engines"]["claude"]["enabled"] or not self.cfg["engines"]["claude"]["path"]:
                raise TaskError("engine_unavailable", "Claude Code not configured")
        elif a["engine"] == "ollama" and not self.cfg["engines"]["ollama"]["enabled"]:
            raise TaskError("engine_unavailable", "Ollama disabled")
        hist = os.path.join(self.home, "history", a["key"] + ".jsonl")
        if run.fresh and a["engine"] != "claude":
            clear_history(hist)
        # H32: the engine thread gets its own copy and writes back only the Claude session fields through the store
        # (under its lock, copy-on-write). An agent.reset or rename during the run bumps/keeps the stored record and
        # the run's late write is dropped or merged instead of overwriting it.
        epoch = self.agents.session_epoch(a["key"])
        key = a["key"]
        ctx = TaskContext(run.id, copy.deepcopy(a), run.prompt, cwd, a.get("model"), a.get("profile") or "edit",
                          run.fresh, max_budget, history_path=hist,
                          save_agent=lambda rec: self.agents.save_session(key, rec, epoch))
        self.office(a, "thinking", None, None, run.id)
        return eng.start(ctx, run)

    def task_finished(self, run: TaskRun, msg: dict) -> None:
        u = msg["usage"] or {}
        with self._ledger_lock:
            if self._ledger_closed:
                self.log("kapanış sonrası görev sonu kaydedilmedi: %s" % run.id)
            else:
                self.ledger.end_task(run.id, msg["status"], u, msg["costUsd"], msg["costBasis"], msg["result"])
        tokens = sum(int(u.get(k) or 0) for k in ("inTok", "outTok", "cacheRead", "cacheWrite"))
        self.audit.write("task.end", task=run.id, agent=run.agent_key, status=msg["status"], tokens=tokens,
                         costUsd=msg["costUsd"], code=(msg.get("error") or {}).get("code"))
        try:
            a = self.agents.get(run.agent_key)
            nxt = self.scheduler.active_for(run.agent_key)
            if nxt is None:
                self.office(a, "idle", None, LABELS.get(msg["status"], "Görev bitti"), None)
        except AgentError:
            pass
        self.send_to_task(run, fit_result(msg))

    def send_to_task(self, run: TaskRun, msg: dict) -> None:
        targets = []
        if run.peer is not None and not run.peer.channel.closed:
            targets.append(run.peer)
        for p in self.router.all_peers():
            if run.agent_key in p.watching and p not in targets:
                targets.append(p)
        for p in targets:
            try:
                self.router.send(p, msg)
            except ValueError:
                pass

    # -- Claude Code path (H38) -------------------------------------------------------------------
    def _ensure_claude_path(self) -> Optional[str]:
        ensure = getattr(self.claude, "ensure_path", None)
        return ensure() if ensure else self.cfg["engines"]["claude"]["path"]

    def _claude_path_refreshed(self, old: Optional[str], new: str, version: Optional[str]) -> None:
        err = claude_path_refreshed(self.home, self.audit, old, new, version)
        self.log("Claude Code yolu yenilendi: %s → %s%s" % (claude_path_label(old) or "-", claude_path_label(new),
                                                            "" if err is None else " (connector.json yazılamadı: %s)" % err))

    # -- handlers: device / agents ---------------------------------------------------------------
    def device_info(self) -> dict:
        c = self.claude.available() if self.cfg["engines"]["claude"]["enabled"] else {"available": False}
        o = self.ollama.available() if self.cfg["engines"]["ollama"]["enabled"] else {"available": False}
        oa = []
        for eid, e in self.openai.items():
            av = e.available()
            oa.append({"id": eid, "name": e.cfg.get("name", eid), "available": av.get("available"),
                       "models": av.get("models", [])})
        return {"type": "device.info", "name": self.cfg["deviceName"], "deviceId": self.key.device_id, "os": os_name(),
                "connector": __version__, "build": __build__, "pyz": self.pyz_sha256,
                "engines": {"claude": {"available": bool(c.get("available")), "version": c.get("version"),
                                       "auth": c.get("auth", "unknown")},
                            "ollama": {"available": bool(o.get("available")), "version": o.get("version"),
                                       "models": o.get("models", [])},
                            "openai": oa},
                "limits": {"maxPrompt": self.cfg["limits"]["maxPromptBytes"],
                           "maxConcurrent": self.cfg["limits"]["maxConcurrent"]},
                "profiles": [{"id": p, "label": PROFILE_LABELS[p],
                              "enabled": p != "auto" or self.cfg["profiles"]["auto"]["enabled"]}
                             for p in ("readonly", "edit", "dev", "auto")],
                "remote": dict(self.cfg["remote"]), "approvals": {"remote": self.remote_approvals()["enabled"]},
                "time": int(self.clock() * 1000), "tz": tz_name()}

    def h_peer_hello(self, peer: Peer, msg: dict) -> None:
        lbl = msg.get("label")
        if isinstance(lbl, str) and lbl.strip():
            peer.label = "".join(ch for ch in lbl if ch.isprintable())[:40]
        self.reply(peer, msg, self.device_info())
        for p in self._tool_pending():                  # a phone that opens the office now still sees them
            self._send_peer(peer, {"type": "approval.request", "approval": p.wire()})
        return None

    # -- handlers: remote approvals (Phase 19) ---------------------------------------------------
    def remote_approvals(self) -> dict:
        return remote_approvals_state(self.home)

    def _tool_pending(self) -> List[ToolApproval]:
        with self.approvals._lock:
            return [p for p in self.approvals.pending.values() if isinstance(p, ToolApproval)]

    def _send_peer(self, peer: Peer, obj: dict) -> None:
        if self.peers.active(peer.bid) is None:         # revoked meanwhile: nothing more for this channel
            return
        try:
            self.router.send(peer, obj)
        except ValueError:
            pass

    def broadcast(self, obj: dict) -> None:
        """Every open E2E channel of an active paired browser. Every paired browser has full control (V3.md §6.8:
        there is no view-only pairing); unpaired browsers have no channel and the relay sees ciphertext only."""
        for p in self.router.all_peers():
            self._send_peer(p, obj)

    def h_approval_list(self, peer, msg) -> dict:
        return {"type": "approval.list.result", "approvals": [p.wire() for p in self._tool_pending()]}

    def h_approval_answer(self, peer, msg) -> dict:
        aid, decision, answers = msg.get("approval"), msg.get("decision"), msg.get("answers")
        if not isinstance(aid, str) or not TOOL_ID_RE.match(aid):
            raise Reply("bad_request", "approval")
        if decision not in ("allow", "deny"):
            raise Reply("bad_request", "decision")
        if answers is not None and (decision != "allow" or not isinstance(answers, list) or len(answers) > 4):
            raise Reply("bad_request", "answers")
        res = self.approvals.answer(aid, decision == "allow", answers, by=peer.bid, tool_only=True)
        if res == "unknown":
            raise Reply("unknown_approval", "no such pending approval (answered elsewhere or expired)")
        if res == "bad_answer":
            raise Reply("bad_request", "answers")
        return {"type": "approval.answered", "approval": aid, "decision": decision}

    def _tool_done(self, p: ToolApproval, result: str) -> None:
        self.audit.write(APPROVAL_AUDIT.get(result, "approval.withdraw"), id=p.id, tool=p.req["tool"],
                         ask=p.req["ask"], by=p.by)
        self.broadcast({"type": "approval.resolved", "approval": p.id, "result": APPROVAL_RESULT.get(result, "withdrawn")})

    def ctl_tool_approval(self, body: dict, query: dict, gone: Callable[[], bool]):
        """POST /ctl/approvals/tool (the PermissionRequest hook): long-poll until the first answer, expiry, withdrawal or
        the hook going away. → {"decision": "allow"|"deny"|"timeout"|"withdrawn"|"off", "answers"?}."""
        state = self.remote_approvals()
        if not state["enabled"]:
            return 200, {"decision": "off"}
        try:
            req = validate_tool_request(body)
        except ValueError as e:
            return 400, {"error": "bad_request", "field": str(e)}
        try:
            p = self.approvals.request_tool(req, float(state["timeoutS"]), self._tool_done)
        except ToolLimit:
            return 429, {"error": "too_many"}
        self.audit.write("approval.request", id=p.id, tool=req["tool"], ask=req["ask"],
                         summarySha=summary_hash(req["summary"]) if req["summary"] else None)
        self.broadcast({"type": "approval.request", "approval": p.wire()})
        while not p.done.wait(APPROVAL_POLL_S):
            if gone() or self._stop.is_set():
                self.approvals.withdraw(pid=p.id)
        return 200, p.outcome()

    def h_agent_list(self, peer, msg) -> dict:
        return {"type": "agent.list.result", "agents": [self.agent_public(a) for a in self.agents.list()]}

    def h_agent_create(self, peer, msg) -> Optional[dict]:
        if not self.cfg["remote"]["agentCreate"]:
            raise Reply("not_allowed", "remote agent creation disabled")
        if len(self.agents.list()) >= self.cfg["limits"]["maxAgents"]:
            raise Reply("not_allowed", "too many agents")
        if not self.scheduler.check_rate("agent:" + peer.bid, 10 ** 6, self.cfg["limits"]["agentCreatePerHour"]):
            raise Reply("rate_limited", "too many agents created")
        engine = msg.get("engine")
        ws = msg.get("workspace")
        if ws is not None and not isinstance(ws, dict):
            raise Reply("bad_request", "workspace")
        if engine == "claude" and not ws:
            raise Reply("workspace_denied", "workspace required")
        if ws:
            self.workspaces.resolve(ws.get("id"), ws.get("sub"))      # WorkspaceDenied → workspace_denied
        profile = msg.get("profile") or "edit"
        if engine == "claude" and profile == "auto" and not self.cfg["profiles"]["auto"]["enabled"]:
            raise Reply("not_allowed", "auto profile disabled on this device")
        engine_id = None
        if engine == "openai":
            engine_id = msg.get("engineId") or (next(iter(self.openai)) if self.openai else None)
            if engine_id not in self.openai:
                raise Reply("engine_unavailable", "no OpenAI-compatible server configured")
        elif engine == "claude" and not (self.cfg["engines"]["claude"]["enabled"] and self._ensure_claude_path()):
            raise Reply("engine_unavailable", "Claude Code not found")
        elif engine == "ollama" and not self.cfg["engines"]["ollama"]["enabled"]:
            raise Reply("engine_unavailable", "Ollama disabled")

        def create() -> dict:
            a = self.agents.create(msg.get("name"), engine, msg.get("model"),
                                   {"id": ws.get("id"), "sub": ws.get("sub")} if ws else {"id": None, "sub": None},
                                   profile, msg.get("budget"), engine_id)
            self.audit.write("agent.create", bid=peer.bid, agent=a["key"], engine=engine, profile=profile,
                             ws=(ws or {}).get("id"))
            self.office(a, "idle", None, "Hazır", None)
            return {"type": "agent.created", "agent": self.agent_public(a)}

        if self.cfg["confirm"]["agentCreate"]:
            def done(res: str) -> None:
                self.audit.write("confirm", action="agent.create", result=res, bid=peer.bid)
                if res == "ok":
                    try:
                        self.reply(peer, msg, create())
                    except AgentError as e:
                        self.reply(peer, msg, {"type": "error", "code": e.code, "message": str(e)})
                else:
                    self.reply(peer, msg, {"type": "error", "code": "confirm_denied" if res == "denied"
                                           else "confirm_timeout", "message": "not confirmed"})
            self.approvals.request("agentCreate", "Yeni ajan: %s" % str(msg.get("name"))[:24], None,
                                   str(msg.get("name"))[:24], done)
            return None
        return create()

    def h_agent_update(self, peer, msg) -> dict:
        fields = {k: msg[k] for k in ("name", "model", "profile", "budget") if k in msg}
        if fields.get("profile") == "auto" and not self.cfg["profiles"]["auto"]["enabled"]:
            raise Reply("not_allowed", "auto profile disabled on this device")
        if "budget" in fields and not self.cfg["remote"]["budgetEdit"]:
            raise Reply("not_allowed", "remote budget editing disabled")
        a = self.agents.update(msg.get("agent"), fields)
        self.audit.write("agent.update", bid=peer.bid, agent=a["key"], fields=sorted(fields))
        if "name" in fields:
            self.office(a, self._agent_state.get(a["key"], "idle"), None, None, None)
        return {"type": "agent.updated", "agent": self.agent_public(a)}

    def h_agent_delete(self, peer, msg) -> dict:
        a = self.agents.get(msg.get("agent"))
        for r in list(self.scheduler.runs.values()):
            if r.agent_key == a["key"]:
                self.scheduler.cancel(r.id)
        self.agents.delete(a["key"])
        clear_history(os.path.join(self.home, "history", a["key"] + ".jsonl"))
        self.reporter.offline(a, self._ws_name(a))
        self.audit.write("agent.delete", bid=peer.bid, agent=a["key"])
        return {"type": "agent.deleted", "agent": a["key"]}

    def h_agent_reset(self, peer, msg) -> dict:
        a = self.agents.reset_session(msg.get("agent"))
        clear_history(os.path.join(self.home, "history", a["key"] + ".jsonl"))
        self.audit.write("agent.reset", bid=peer.bid, agent=a["key"])
        return {"type": "agent.updated", "agent": self.agent_public(a)}

    def h_agent_watch(self, peer, msg) -> None:
        key = msg.get("agent")
        if not isinstance(key, str) or key not in self.agents.agents:
            raise Reply("unknown_agent", "unknown agent")
        if msg.get("on"):
            peer.watching.add(key)
        else:
            peer.watching.discard(key)
        return None

    def h_workspace_list(self, peer, msg) -> dict:
        return {"type": "workspace.list.result", "workspaces": self.workspaces.public_list()}

    # -- handlers: tasks -------------------------------------------------------------------------
    def h_task_start(self, peer, msg) -> dict:
        a = self.agents.get(msg.get("agent"))
        prompt = msg.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise Reply("bad_request", "prompt")
        if len(prompt.encode("utf-8")) > self.cfg["limits"]["maxPromptBytes"]:
            raise Reply("too_large", "prompt too large")
        fresh = msg.get("fresh") is True
        mode = self.cfg["confirm"]["task"]
        # "new_session" is about Claude Code sessions; other engines have none, so it never asks for them (H25).
        need = mode == "always" or (mode == "new_session" and a["engine"] == "claude"
                                    and (fresh or not a.get("claudeSessionStarted")))
        try:
            ack = self.scheduler.submit(a, prompt, fresh, peer, peer.label, need)
        except TaskError as e:
            if e.code in ("rate_limited", "busy"):
                self.audit.write("denied", bid=peer.bid, agent=a["key"], reason=e.code)
            raise
        return dict({"type": "task.ack"}, **ack)

    def h_task_cancel(self, peer, msg) -> Optional[dict]:
        tid = msg.get("task")
        if not isinstance(tid, str) or not self.scheduler.cancel(tid):
            raise Reply("unknown_task", "unknown or finished task")
        return None

    def h_task_list(self, peer, msg) -> dict:
        a = self.agents.get(msg.get("agent"))
        lim = msg.get("limit")
        lim = lim if isinstance(lim, int) and not isinstance(lim, bool) and 1 <= lim <= 20 else 10
        tasks = self.ledger.list_tasks(a["key"], lim)
        running = {r.id: r for r in self.scheduler.runs.values()}
        for t in tasks:
            if t["task"] in running:
                t["status"] = running[t["task"]].state
        return {"type": "task.list.result", "agent": a["key"], "tasks": tasks}

    def h_task_get(self, peer, msg) -> dict:
        tid = msg.get("task")
        row = self.ledger.get_task(tid) if isinstance(tid, str) and TASK_RE.match(tid) else None
        if row is None:
            raise Reply("unknown_task", "unknown task")
        out = {"type": "task.get.result", "task": row["task"], "agent": row["agent"], "prompt": row["prompt"],
               "result": row["result"], "status": row["status"],
               "usage": {"inTok": row["in_tok"], "outTok": row["out_tok"], "cacheRead": row["cache_read"],
                         "cacheWrite": row["cache_write"], "estimated": bool(row["estimated"])},
               "costUsd": row["cost_usd"], "costBasis": row["cost_basis"], "startedAt": row["started_ms"],
               "endedAt": row["ended_ms"]}
        out = fit_result(out, "result", 40 * 1024)
        return fit_result(out, "prompt")

    # -- handlers: runtimes ----------------------------------------------------------------------
    def h_runtime_list(self, peer, msg) -> dict:
        return {"type": "runtime.list.result", "runtimes": self.runtimes.list()}

    def _runtime_action(self, peer, msg, op: str) -> Optional[dict]:
        if not self.scheduler.check_rate("rt:" + peer.bid, self.cfg["limits"]["runtimePerMinute"], 10 ** 6):
            raise Reply("rate_limited", "too many runtime operations")
        rid = msg.get("runtime")
        self.runtimes.get(rid)                  # runtime_unknown before anything else

        def act() -> dict:
            if op in ("start", "stop"):
                res = self.runtimes.start_stop(rid, op)
                self.audit.write("runtime." + op, bid=peer.bid, runtime=rid, exitCode=res["exitCode"], ok=res["ok"])
            else:
                res = self.runtimes.model_op(rid, msg.get("model"), op, self.cfg["engines"]["ollama"]["keepAlive"])
                self.audit.write("model." + op, bid=peer.bid, runtime=rid, ok=res["ok"])
            try:
                self.ollama.available(force=True)
            except Exception:
                pass
            return dict({"type": "runtime.result"}, **res)

        if self.cfg["confirm"]["runtime"]:
            def done(res: str) -> None:
                self.audit.write("confirm", action="runtime." + op, runtime=rid, result=res)
                if res == "ok":
                    try:
                        self.reply(peer, msg, act())
                    except RuntimeError_ as e:
                        self.reply(peer, msg, {"type": "error", "code": e.code, "message": str(e)})
                else:
                    self.reply(peer, msg, {"type": "error", "code": "confirm_denied" if res == "denied"
                                           else "confirm_timeout", "message": "not confirmed"})
            self.approvals.request("runtime", "Çalışma ortamı: %s %s" % (rid, op), None, str(rid), done)
            return None
        return act()

    def h_runtime_start(self, peer, msg):
        return self._runtime_action(peer, msg, "start")

    def h_runtime_stop(self, peer, msg):
        return self._runtime_action(peer, msg, "stop")

    def h_model_load(self, peer, msg):
        return self._runtime_action(peer, msg, "load")

    def h_model_unload(self, peer, msg):
        return self._runtime_action(peer, msg, "unload")

    # -- handlers: usage / budgets ---------------------------------------------------------------
    def usage_msg(self, period: str, agent: Optional[str]) -> dict:
        if period not in ("day", "week", "month"):
            raise Reply("bad_request", "period")
        scope = "device"
        if agent is not None:
            self.agents.get(agent)
            scope = "agent:" + agent
        tot = self.ledger.totals(period, agent=agent)
        budgets = self.budgets.evaluate()
        if agent is not None:
            budgets = [b for b in budgets if b["scope"] in ("device", "agent:" + agent, "engine:" + self.agents.get(agent)["engine"])]
        return {"type": "usage", "period": period, "scope": scope,
                "totals": {"inTok": tot["inTok"], "outTok": tot["outTok"], "cacheRead": tot["cacheRead"],
                           "cacheWrite": tot["cacheWrite"], "tokens": tot["tokens"], "costUsd": tot["costUsd"],
                           "tasks": tot["tasks"]},
                "byAgent": self.ledger.by_agent(period) if agent is None else [],
                "budgets": budgets, "claudeLimit": self.claude_limit}

    def h_usage_get(self, peer, msg) -> dict:
        return self.usage_msg(msg.get("period") or "day", msg.get("agent"))

    def h_budget_set(self, peer, msg) -> dict:
        if not self.cfg["remote"]["budgetEdit"]:
            raise Reply("not_allowed", "remote budget editing disabled")
        b = {k: msg.get(k) for k in ("scope", "period", "unit", "limit", "action", "delete") if k in msg}
        ok, code = self.budgets.set(b)
        if not ok:
            raise Reply("not_allowed" if code in ("locked", "use_agent_update") else "bad_request", code)
        self.audit.write("budget.set", bid=peer.bid, scope=b.get("scope"), period=b.get("period"),
                         delete=bool(b.get("delete")) or None)
        return self.usage_msg(b.get("period") if b.get("period") in ("day", "week", "month") else "day", None)

    # -- handlers: peers -------------------------------------------------------------------------
    def h_peer_list(self, peer, msg) -> dict:
        return {"type": "peer.list.result", "peers": self.peers.public_list(peer.bid)}

    def h_peer_revoke(self, peer, msg) -> Optional[dict]:
        bid = msg.get("bid")
        if not isinstance(bid, str) or self.peers.active(bid) is None:
            raise Reply("bad_request", "unknown peer")

        def do() -> None:
            self.revoke(bid, by=peer.bid)
            self.reply(peer, msg, {"type": "peer.revoked", "bid": bid})
            if bid == peer.bid:
                self.router.close_bid(bid)

        if self.cfg["confirm"]["peerRevoke"] and bid != peer.bid:
            def done(res: str) -> None:
                if res == "ok":
                    do()
                else:
                    self.reply(peer, msg, {"type": "error", "code": "confirm_denied" if res == "denied"
                                           else "confirm_timeout", "message": "not confirmed"})
            self.approvals.request("peerRevoke", "Eşleşme kaldırma", None, None, done)
            return None
        do()
        return None

    def revoke(self, bid: str, by: Optional[str] = None) -> bool:
        ok = self.peers.revoke(bid)
        if ok:
            self.audit.write("peer.revoke", bid=bid, by=by or "local")
            if bid != by:
                self.router.close_bid(bid)
        return ok

    # -- loopback control API (CLI) --------------------------------------------------------------
    def ctl_routes(self) -> dict:
        def status(b, q):
            return 200, {"deviceId": self.key.device_id, "relay": self.relay.status, "version": __version__,
                         "build": __build__, "pyz": self.pyz_sha256,
                         "relayError": getattr(self.relay, "last_error", None),
                         "relayErrorAt": _ms(getattr(self.relay, "last_error_at", None)),
                         "channels": len(self.router.all_peers()), "agents": len(self.agents.list()),
                         "running": len(self.scheduler.runs)}

        def pair_start(b, q):
            if not getattr(self.relay, "connected", None) or not self.relay.connected.is_set():
                return 503, {"error": "relay_offline"}
            p = self.router.start_pairing()
            return 200, p.info()

        def pair_status(b, q):
            p = self.router.pairing
            if p is None:
                return 404, {"error": "no_pairing"}
            if q.get("wait"):
                try:
                    w = min(25.0, float(q["wait"]))
                except ValueError:
                    w = 0.0
                p.wait(set(q.get("until", "confirm,done,failed,expired").split(",")), w)
            return 200, p.info()

        def pair_confirm(b, q):
            p = self.router.pairing
            if p is None or not p.confirm(bool(b.get("ok"))):
                return 409, {"error": "not_waiting"}
            return 200, p.info()

        def peers(b, q):
            return 200, {"peers": self.peers.public_list()}

        def peers_revoke(b, q):
            if b.get("all"):
                bids = [p["bid"] for p in self.peers.public_list()]
                for bid in bids:
                    self.revoke(bid)
                return 200, {"revoked": bids}
            bid = b.get("bid")
            if not isinstance(bid, str) or not self.revoke(bid):
                return 404, {"error": "unknown_peer"}
            return 200, {"revoked": [bid]}

        def agents(b, q):
            return 200, {"agents": [self.agent_public(a) for a in self.agents.list()]}

        def agents_delete(b, q):
            try:
                a = self.agents.get(b.get("agent"))
            except AgentError:
                return 404, {"error": "unknown_agent"}
            for r in list(self.scheduler.runs.values()):
                if r.agent_key == a["key"]:
                    self.scheduler.cancel(r.id)
            self.agents.delete(a["key"])
            clear_history(os.path.join(self.home, "history", a["key"] + ".jsonl"))       # same as the remote handler
            self.reporter.offline(a, self._ws_name(a))
            self.audit.write("agent.delete", agent=a["key"], by="local")
            return 200, {"deleted": a["key"]}

        def runtimes(b, q):
            return 200, {"runtimes": self.runtimes.list()}

        def rt(op):
            def fn(b, q):
                try:
                    res = self.runtimes.start_stop(b.get("runtime"), op)
                except RuntimeError_ as e:
                    return 404, {"error": e.code}
                self.audit.write("runtime." + op, runtime=b.get("runtime"), exitCode=res["exitCode"], by="local")
                return 200, res
            return fn

        def approvals(b, q):
            return 200, {"pending": self.approvals.list(), "link": self.ctl.page_link() if self.ctl else None}

        def approvals_resolve(b, q):
            res = self.approvals.answer(str(b.get("id")), b.get("ok") is True, None, by="cli")
            if res == "bad_answer":
                return 409, {"error": "question"}         # a question can only be answered with its options
            if res != "ok":
                return 404, {"error": "unknown"}
            return 200, {"ok": True}

        def usage(b, q):
            try:
                return 200, self.usage_msg(q.get("period") or "day", None)
            except Reply as e:
                return 400, {"error": e.code}

        def tool_approval(b, q, gone):
            return self.ctl_tool_approval(b, q, gone)
        tool_approval.long_poll = True

        def approvals_withdraw(b, q):
            session, match = b.get("session"), b.get("match")
            if not isinstance(session, str) or not re.match(r"^[0-9a-f]{20}\Z", session):
                return 400, {"error": "bad_request"}
            if match is not None and (not isinstance(match, str) or not re.match(r"^[0-9a-f]{16,64}\Z", match)):
                return 400, {"error": "bad_request"}
            return 200, {"withdrawn": len(self.approvals.withdraw(session, match))}

        return {("GET", "/ctl/status"): status, ("POST", "/ctl/pair/start"): pair_start,
                ("GET", "/ctl/pair/status"): pair_status, ("POST", "/ctl/pair/confirm"): pair_confirm,
                ("GET", "/ctl/peers"): peers, ("POST", "/ctl/peers/revoke"): peers_revoke,
                ("GET", "/ctl/agents"): agents, ("POST", "/ctl/agents/delete"): agents_delete,
                ("GET", "/ctl/runtimes"): runtimes, ("POST", "/ctl/runtimes/start"): rt("start"),
                ("POST", "/ctl/runtimes/stop"): rt("stop"), ("GET", "/ctl/approvals"): approvals,
                ("POST", "/ctl/approvals/resolve"): approvals_resolve, ("GET", "/ctl/usage"): usage,
                ("POST", "/ctl/approvals/tool"): tool_approval, ("POST", "/ctl/approvals/withdraw"): approvals_withdraw}
