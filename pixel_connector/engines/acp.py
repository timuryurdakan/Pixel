"""F4-6a (docs/F46A_TASARIM.md): an Agent Client Protocol client engine.

An ACP agent (Codex CLI, Gemini CLI, OpenCode, …) runs as a child process speaking JSON-RPC 2.0, one message per line
on stdin/stdout. One process per task (A5): initialize → session/new → session/prompt → exit. The command line comes
from connector.json only (A1); the child env is Claude's allow-list without our API key variables (A6).

ACP lets an agent act with its own tools without asking (permission requests are a MAY), so the profile is enforced
only where the agent asks us: `session/request_permission` (A2) and the `fs/*` methods, which go through E3's
WorkspaceFiles (same folder, deny list, size caps and profile). No terminal capability, no MCP servers (K3), never
`allow_always`. Everything the agent sends is untrusted: lines are size-capped, methods allow-listed, texts shortened.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from typing import Callable, Dict, List, Optional

from .base import Running, TaskContext, empty_usage, estimate_tokens, short
from .claude import child_env
from .tools import WorkspaceFiles, ToolError

PROTOCOL_VERSION = 1
LINE_MAX = 1024 * 1024              # bytes per JSON-RPC line from the agent (A7)
RESULT_MAX = 256 * 1024             # characters of reply text kept for the task result
SETUP_TIMEOUT_S = 30.0              # initialize and session/new each
PERMISSION_TIMEOUT_S = 120.0        # A2: unanswered → rejected
CANCEL_GRACE_S = 5.0                # A7: after session/cancel, then the process group is killed
MAX_BAD_MESSAGES = 20
CLIENT_INFO = {"name": "pixel-office", "title": "Pixel Office", "version": "1"}

READ_KINDS = ("read",)
EDIT_KINDS = ("read", "edit", "delete", "move")
OFFICE_TOOL = {"read": "Read", "edit": "Edit", "delete": "Edit", "move": "Edit", "search": "Grep",
               "execute": "Bash", "fetch": "WebFetch"}
ERR_METHOD = -32601
ERR_PARAMS = -32602
ERR_DENIED = -32000

AUTH_TEXT = "Ajan giriş istiyor: önce terminalde ajanın kendi girişini yapın."
TEXT = {"acp_start": "Ajan başlatılamadı", "acp_version": "Ajan ACP sürüm 1'i desteklemiyor",
        "acp_timeout": "Ajan zamanında cevap vermedi", "acp_protocol": "Ajan geçersiz ACP mesajları gönderdi",
        "acp_exit": "Ajan beklenmedik şekilde kapandı", "auth_required": AUTH_TEXT,
        "max_tokens": "Ajanın jeton sınırı doldu", "max_turns": "Ajanın tur sınırı doldu",
        "refusal": "Ajan isteği reddetti", "workspace_required": "ACP ajanı bir çalışma klasörü ister"}


class AcpEngine:
    """One configured ACP agent (`engines.acp[i]`: id, name, command, enabled)."""

    name = "acp"

    def __init__(self, cfg: dict, approvals=None, drop_env=(), popen=subprocess.Popen) -> None:
        self.cfg = cfg
        self.approvals = approvals
        self.drop_env = tuple(drop_env)
        self.popen = popen

    def available(self, force: bool = False) -> dict:
        cmd = self.cfg.get("command") or []
        ok = bool(self.cfg.get("enabled")) and bool(cmd) and os.path.isfile(cmd[0]) and os.access(cmd[0], os.X_OK)
        return {"available": ok}

    def start(self, ctx: TaskContext, events) -> "AcpRun":
        r = AcpRun(self, ctx, events)
        r.start()
        return r


def decide_permission(tool_call: dict, profile: str, inside: Callable[[str], bool]) -> str:
    """A2 → "allow" | "reject" | "ask". Only reads (readonly) or reads and file changes (edit) whose every location is
    in the task's folder are allowed without the user; edit asks for everything else, readonly refuses it."""
    kind = tool_call.get("kind") if isinstance(tool_call.get("kind"), str) else "other"
    locs = tool_call.get("locations")
    paths = [x.get("path") for x in locs] if isinstance(locs, list) and all(isinstance(x, dict) for x in locs) else []
    all_inside = bool(paths) and all(isinstance(p, str) and inside(p) for p in paths)
    allowed = READ_KINDS if profile == "readonly" else EDIT_KINDS
    if kind in allowed and all_inside:
        return "allow"
    return "reject" if profile == "readonly" else "ask"


def pick_option(options, allow: bool) -> Optional[str]:
    """The optionId to answer with: allow_once only (never allow_always); reject_once, else reject_always."""
    if not isinstance(options, list):
        return None
    wanted = ("allow_once",) if allow else ("reject_once", "reject_always")
    for kind in wanted:
        for o in options:
            if isinstance(o, dict) and o.get("kind") == kind and isinstance(o.get("optionId"), str):
                return o["optionId"]
    return None


class AcpRun(Running):
    cost_basis = "unknown"

    def __init__(self, engine: AcpEngine, ctx: TaskContext, events) -> None:
        self.engine = engine
        self.ctx = ctx
        self.events = events
        self.proc = None
        self.cancelled = False
        self._ended = False
        self._next_id = 0
        self._wlock = threading.Lock()
        self._pending: Optional[int] = None         # id of our request being waited for
        self._session: Optional[str] = None
        self._deadline_kind: Optional[str] = None
        self._timer: Optional[threading.Timer] = None
        self._bad = 0
        self._text: List[str] = []
        self._text_len = 0
        self._started_content = False
        self._tools: Dict[str, str] = {}
        self._approval = None
        self.root = os.path.realpath(ctx.cwd) if ctx.cwd else None
        self.files = WorkspaceFiles(ctx.cwd, ctx.profile) if ctx.cwd else None

    # -- lifecycle --------------------------------------------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._main, daemon=True, name="acp-run").start()

    def cancel(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        if self._approval is not None and self.engine.approvals is not None:
            self.engine.approvals.cancel(self._approval.id)
        if self._session is not None:
            self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self._session}})
        t = threading.Timer(CANCEL_GRACE_S, self._kill)
        t.daemon = True
        t.start()

    def _kill(self) -> None:
        p = self.proc
        if p is None or p.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                p.kill()
            else:
                os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _end(self, status: str, error_code: Optional[str] = None) -> None:
        if self._ended:
            return
        self._ended = True
        if self._timer is not None:
            self._timer.cancel()
        result = "".join(self._text) or None
        usage = empty_usage(True)
        usage["inTok"] = estimate_tokens(self.ctx.prompt)
        usage["outTok"] = estimate_tokens(result or "")
        error = {"code": error_code, "message": TEXT.get(error_code, error_code)} if error_code else None
        self.events.end(status, result, usage, None, self.cost_basis, turns=1, error=error)

    def _main(self) -> None:
        if self.files is None:
            self._end("error", "workspace_required")
            return
        self.events.office("thinking")
        try:
            self.proc = self._spawn()
        except OSError:
            self._end("error", "acp_start")
            return
        try:
            self._run()
        except _Stop as s:
            self._kill()
            if self.cancelled:
                self._end("cancelled")
            else:
                self._end(s.status, s.code)
        except Exception:
            self._kill()
            self._end("cancelled" if self.cancelled else "error", None if self.cancelled else "acp_protocol")
        finally:
            self._close()

    def _spawn(self):
        kw = {"cwd": self.ctx.cwd, "env": child_env(drop=self.engine.drop_env), "stdin": subprocess.PIPE,
              "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "bufsize": 0}
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kw["start_new_session"] = True              # its own process group: the kill reaches its children
        return self.engine.popen(list(self.engine.cfg["command"]), **kw)

    def _close(self) -> None:
        p = self.proc
        if p is None:
            return
        for f in (p.stdin, p.stdout):
            try:
                if f is not None:
                    f.close()
            except OSError:
                pass
        try:
            p.wait(timeout=CANCEL_GRACE_S)
        except subprocess.TimeoutExpired:
            self._kill()

    # -- the conversation -------------------------------------------------------------------------------------------
    def _run(self) -> None:
        init = self._call("initialize", {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {
            "fs": {"readTextFile": True, "writeTextFile": True}, "terminal": False}, "clientInfo": CLIENT_INFO},
            SETUP_TIMEOUT_S)
        if not isinstance(init, dict) or init.get("protocolVersion") != PROTOCOL_VERSION:
            raise _Stop("error", "acp_version")
        auth = init.get("authMethods")
        try:
            new = self._call("session/new", {"cwd": self.ctx.cwd, "mcpServers": []}, SETUP_TIMEOUT_S)
        except _RpcError:
            raise _Stop("error", "auth_required" if auth else "acp_protocol") from None
        sid = new.get("sessionId") if isinstance(new, dict) else None
        if not isinstance(sid, str) or not 0 < len(sid) <= 256:
            raise _Stop("error", "acp_protocol")
        self._session = sid
        if self.cancelled:
            raise _Stop("cancelled")
        try:
            res = self._call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": self.ctx.prompt}]},
                             None)
        except _RpcError:
            raise _Stop("error", "acp_protocol") from None
        reason = res.get("stopReason") if isinstance(res, dict) else None
        if reason == "end_turn":
            self._end("success")
        elif reason == "cancelled" or self.cancelled:
            self._end("cancelled")
        elif reason in ("max_tokens", "refusal"):
            self._end("error", reason)
        elif reason == "max_turn_requests":
            self._end("error", "max_turns")
        else:
            self._end("error", "acp_protocol")

    def _call(self, method: str, params: dict, timeout: Optional[float]):
        """Send a request and handle the agent's messages until its response arrives. → result; _RpcError."""
        self._next_id += 1
        rid = self._next_id
        self._pending = rid
        self._arm(timeout)
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = self._read()
            if not msg:
                continue                                # unreadable line: already counted
            if "method" in msg:
                self._on_agent_message(msg)
                continue
            if msg.get("id") != rid:
                self._strike()
                continue
            self._arm(None)
            self._pending = None
            if "error" in msg:
                raise _RpcError(msg.get("error"))
            return msg.get("result")

    def _arm(self, timeout: Optional[float]) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if timeout:
            self._timer = threading.Timer(timeout, self._timed_out)
            self._timer.daemon = True
            self._timer.start()

    def _timed_out(self) -> None:
        self._deadline_kind = "acp_timeout"
        self._kill()

    def _read(self) -> dict:
        line = self.proc.stdout.readline(LINE_MAX + 1)
        if not line:
            if self.cancelled:
                raise _Stop("cancelled")
            raise _Stop("error", self._deadline_kind or "acp_exit")
        if len(line) > LINE_MAX:
            raise _Stop("error", "acp_protocol")
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._strike()
            return {}
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self._strike()
            return {}
        return msg

    def _strike(self) -> None:
        self._bad += 1
        if self._bad > MAX_BAD_MESSAGES:
            raise _Stop("error", "acp_protocol")

    def _send(self, msg: dict) -> None:
        data = (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._wlock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError, AttributeError):
                pass                                    # the agent closed its input: _read sees the exit next

    def _reply(self, rid, result=None, error: Optional[tuple] = None) -> None:
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = {"code": error[0], "message": error[1]}
        else:
            msg["result"] = result
        self._send(msg)

    # -- messages from the agent ------------------------------------------------------------------------------------
    def _on_agent_message(self, msg: dict) -> None:
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params")
        if not isinstance(params, dict):
            params = {}
        if rid is None:                                 # a notification
            if method == "session/update":
                self._on_update(params.get("update"))
            return
        if not isinstance(rid, (int, str)) or isinstance(rid, bool):
            self._strike()
            return
        if method == "session/request_permission":
            self._reply(rid, {"outcome": self._permission(params)})
        elif method == "fs/read_text_file":
            self._fs_read(rid, params)
        elif method == "fs/write_text_file":
            self._fs_write(rid, params)
        else:                                           # terminal/*, elicitation/*, anything else
            self._reply(rid, error=(ERR_METHOD, "method not supported"))

    def _on_update(self, upd) -> None:
        if not isinstance(upd, dict):
            return
        kind = upd.get("sessionUpdate")
        if kind == "agent_message_chunk":
            c = upd.get("content")
            if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
                self._content(c["text"])
        elif kind == "tool_call":
            tid = upd.get("toolCallId")
            office = OFFICE_TOOL.get(upd.get("kind"), "tool")
            if isinstance(tid, str) and len(self._tools) < 1000:
                self._tools[tid[:128]] = office
            self.events.part({"k": "tool", "name": office, "v": short(self._label(upd), 120)})
            self.events.office("working", tool=office)
        elif kind == "tool_call_update":
            tid = upd.get("toolCallId")
            status = upd.get("status")
            if isinstance(tid, str) and status in ("completed", "failed") and tid[:128] in self._tools:
                office = self._tools.pop(tid[:128])
                self.events.part({"k": "tool_done", "name": office, "ok": status == "completed"})
                self.events.office("thinking")

    def _label(self, upd: dict) -> str:
        locs = upd.get("locations")
        if isinstance(locs, list) and locs and isinstance(locs[0], dict) and isinstance(locs[0].get("path"), str):
            rel = self._relative(locs[0]["path"])
            if rel is not None:
                return rel
        return upd.get("title") if isinstance(upd.get("title"), str) else ""

    def _content(self, text: str) -> None:
        if not text:
            return
        if not self._started_content:
            self._started_content = True
            self.events.office("working", tool="llm")
        if self._text_len < RESULT_MAX:
            keep = text[:RESULT_MAX - self._text_len]
            self._text.append(keep)
            self._text_len += len(keep)
        self.events.part({"k": "text", "v": text[:RESULT_MAX]})

    # -- A2: permission requests -----------------------------------------------------------------------------------
    def _permission(self, params: dict) -> dict:
        if self.cancelled:
            return {"outcome": "cancelled"}
        tc = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        options = params.get("options")
        verdict = decide_permission(tc, self.ctx.profile, lambda p: self._relative(p) is not None)
        if verdict == "ask":
            verdict = "allow" if self._ask_user(tc) else "reject"
        if self.cancelled:
            return {"outcome": "cancelled"}
        oid = pick_option(options, verdict == "allow")
        return {"outcome": "selected", "optionId": oid} if oid is not None else {"outcome": "cancelled"}

    def _ask_user(self, tc: dict) -> bool:
        approvals = self.engine.approvals
        if approvals is None:
            return False
        done = threading.Event()
        box: List[str] = []

        def on_done(result: str) -> None:
            box.append(result)
            done.set()

        kind = tc.get("kind") if isinstance(tc.get("kind"), str) else "other"
        locs = tc.get("locations") if isinstance(tc.get("locations"), list) else []
        paths = [short(x.get("path"), 200) for x in locs[:5] if isinstance(x, dict)]
        raw = tc.get("rawInput")
        preview = "\n".join(filter(None, [
            "Tür: %s" % short(kind, 24),
            ("Konum: " + ", ".join(paths)) if paths else "",
            ("Girdi: " + short(json.dumps(raw, ensure_ascii=False), 400)) if raw is not None else ""]))
        title = "%s: %s" % (short(self.ctx.agent.get("name"), 24), short(tc.get("title") or kind, 60))
        self._arm(None)
        self._approval = approvals.request("acpTool", title[:80], preview, short(self.ctx.agent.get("name"), 24),
                                           on_done, timeout_s=PERMISSION_TIMEOUT_S)
        while not done.wait(0.2):
            if self.cancelled:
                break
        self._approval = None
        return bool(box) and box[0] == "ok" and not self.cancelled

    # -- fs/* through E3's WorkspaceFiles ---------------------------------------------------------------------------
    def _relative(self, path) -> Optional[str]:
        """An absolute path the agent sent → the same path relative to the task's folder, or None outside it."""
        if not isinstance(path, str) or not os.path.isabs(path) or "\0" in path or len(path) > 4096:
            return None
        p = os.path.normpath(path)
        for base in {os.path.normpath(self.ctx.cwd), self.root}:
            if p.startswith(base + os.sep):
                return p[len(base) + 1:].replace(os.sep, "/")
        return None

    def _fs_read(self, rid, params: dict) -> None:
        rel = self._relative(params.get("path"))
        line, limit = params.get("line"), params.get("limit")
        if rel is None or any(v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1)
                              for v in (line, limit)):
            self._reply(rid, error=(ERR_PARAMS, "path outside the folder or bad range"))
            return
        try:
            text = self.files.read_file(rel)
        except ToolError as e:
            self._reply(rid, error=(ERR_DENIED, e.code))
            return
        except OSError:
            self._reply(rid, error=(ERR_DENIED, "io_error"))
            return
        if line is not None or limit is not None:
            lines = text.splitlines(keepends=True)
            start = (line or 1) - 1
            text = "".join(lines[start:start + limit] if limit is not None else lines[start:])
        self.events.part({"k": "tool", "name": "Read", "v": short(rel, 120)})
        self.events.part({"k": "tool_done", "name": "Read", "ok": True})
        self._reply(rid, {"content": text})

    def _fs_write(self, rid, params: dict) -> None:
        rel = self._relative(params.get("path"))
        if rel is None or not isinstance(params.get("content"), str):
            self._reply(rid, error=(ERR_PARAMS, "path outside the folder or bad content"))
            return
        try:
            self.files.write_file(rel, params["content"])
        except ToolError as e:
            self.events.part({"k": "tool_done", "name": "Write", "ok": False})
            self._reply(rid, error=(ERR_DENIED, e.code))
            return
        except OSError:
            self._reply(rid, error=(ERR_DENIED, "io_error"))
            return
        self.events.part({"k": "tool", "name": "Write", "v": short(rel, 120)})
        self.events.part({"k": "tool_done", "name": "Write", "ok": True})
        self._reply(rid, None)


class _Stop(Exception):
    def __init__(self, status: str, code: Optional[str] = None) -> None:
        super().__init__(code or status)
        self.status = status
        self.code = code


class _RpcError(Exception):
    pass


__all__ = ["AcpEngine", "AcpRun", "decide_permission", "pick_option", "PROTOCOL_VERSION"]
