"""Transcript line parsing and per-file state tracking (ARCHITECTURE.md §2.3, §7.3)."""

import json
import logging
import os
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import (
    DETAIL_LEN,
    IDLE_TO_SLEEP,
    INTERACTIVE_TOOLS,
    LONG_RUNNING_TOOLS,
    PERMISSION_WAIT,
    SILENCE_TO_SLEEP,
    SNIPPET_LEN,
)
from .models import AgentInfo, State, color_seed_for, name_for

log = logging.getLogger("pixel_office")

INTERRUPT_PREFIX = "[Request interrupted"
END_STOP_REASONS = frozenset({"end_turn", "stop_sequence", "max_tokens"})
_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------

def parse_line(raw: str) -> dict | None:
    """json.loads one transcript line; None on error, blank line or non-dict."""
    if not raw or not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


def parse_ts(value: str | None) -> float | None:
    """ISO-8601 timestamp → epoch seconds (naive timestamps are treated as UTC)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def snippet(text: str, n: int = SNIPPET_LEN) -> str:
    """Collapse whitespace and truncate to n chars (with a trailing ellipsis)."""
    if not isinstance(text, str):
        text = str(text)
    s = _WS.sub(" ", text).strip()
    if len(s) > n:
        s = s[: max(0, n - 1)].rstrip() + "…"
    return s


def _first_line(s: str) -> str:
    for line in s.splitlines():
        if line.strip():
            return line.strip()
    return ""


def summarize_tool_input(name: str, inp: dict) -> str:
    """Short human-readable detail for a tool call (≤ DETAIL_LEN chars)."""
    if not isinstance(inp, dict):
        return ""

    def s(key: str) -> str:
        v = inp.get(key)
        return v if isinstance(v, str) else ""

    out = ""
    try:
        if name == "Bash":
            out = s("description") or _first_line(s("command"))
        elif name in ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit"):
            p = s("file_path") or s("notebook_path")
            out = os.path.basename(p.rstrip("/")) if p else ""
        elif name in ("Grep", "Glob"):
            out = s("pattern")
        elif name in ("Agent", "Task"):
            out = s("description")
        elif name == "WebFetch":
            url = s("url")
            try:
                out = urlsplit(url).hostname or url
            except ValueError:
                out = url
        elif name == "WebSearch":
            out = s("query")
        elif name == "TodoWrite":
            out = "todos"
        else:
            for v in inp.values():
                if isinstance(v, str) and v.strip():
                    out = _first_line(v)
                    break
    except Exception:  # defensive: weird input must never break tracking
        out = ""
    return snippet(out, DETAIL_LEN)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


# ---------------------------------------------------------------------------------------
# SessionTracker
# ---------------------------------------------------------------------------------------

class SessionTracker:
    """Accumulates facts from one transcript file and derives the agent state."""

    def __init__(self, agent_id: str, path: Path, *, is_subagent: bool, parent_id: str | None,
                 project_dir_name: str, meta: dict | None = None) -> None:
        self.agent_id = agent_id
        self.path = path
        self.is_subagent = is_subagent
        self.parent_id = parent_id
        self.project_dir_name = project_dir_name
        self.meta: dict | None = meta if isinstance(meta, dict) else None
        self.last_write: float = 0.0

        # facts
        self.pending: OrderedDict[str, tuple[str, str, float]] = OrderedDict()
        self.last_event: str | None = None
        self.last_event_ts: float | None = None
        self.ended: bool = False
        self.ended_ts: float | None = None
        self.seen_turn: bool = False          # any user/assistant line seen
        self.tool: str | None = None
        self.detail: str | None = None
        self.tool_count: int = 0
        self.last_message: str | None = None
        self.last_prompt: str | None = None
        self.title: str | None = None
        self.model: str | None = None
        self.cwd: str | None = None
        self.first_ts: float | None = None
        self.first_ts_hint: float | None = None   # from the file head when the start was tailed away
        self.tool_results: dict[str, float] = {}   # tool_use_id → ts of its tool_result (subagent completion)
        self.finished_ts: float | None = None      # subagent: parent received the Agent/Task tool_result

        self._state: State | None = None
        self._state_since: float = 0.0

    # -- feeding ----------------------------------------------------------------------------
    def feed(self, obj: dict, ingest_ts: float) -> None:
        if not isinstance(obj, dict):
            return
        typ = obj.get("type")
        ts = parse_ts(obj.get("timestamp"))
        if ts is not None and self.first_ts is None:
            self.first_ts = ts
        if ts is None:
            ts = ingest_ts

        if typ == "ai-title":
            t = obj.get("aiTitle")
            if isinstance(t, str) and t.strip():
                self.title = snippet(t)
            return
        if typ == "last-prompt":
            p = obj.get("lastPrompt")
            if isinstance(p, str) and p.strip():
                self.last_prompt = snippet(p)
            return
        if typ == "cost-state":
            self.ended = True
            self.ended_ts = ts
            return
        if typ not in ("user", "assistant"):
            return  # attachment, queue-operation, file-history-*, mode, … → ignored

        cwd = obj.get("cwd")
        if self.cwd is None and isinstance(cwd, str) and cwd:
            self.cwd = cwd
        if typ == "user" and obj.get("isMeta"):
            return

        msg = obj.get("message")
        if not isinstance(msg, dict):
            msg = {}
        self.seen_turn = True
        self.ended = False
        self.ended_ts = None

        if typ == "user":
            self._feed_user(msg.get("content"), ts)
        else:
            self._feed_assistant(obj, msg, ts)

    def _set_event(self, kind: str, ts: float) -> None:
        self.last_event = kind
        self.last_event_ts = ts

    def _feed_user(self, content: Any, ts: float) -> None:
        if isinstance(content, str):
            self._user_text(content, ts)
            return
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                tid = block.get("tool_use_id")
                if isinstance(tid, str):
                    self.pending.pop(tid, None)
                    self.tool_results[tid] = ts
                self._set_event("tool_result", ts)
            elif btype == "text":
                text = block.get("text")
                if isinstance(text, str):
                    self._user_text(text, ts)

    def _user_text(self, text: str, ts: float) -> None:
        self.pending.clear()
        if text.lstrip().startswith(INTERRUPT_PREFIX):
            self._set_event("interrupt", ts)
        else:
            if text.strip():
                self.last_prompt = snippet(text)
            self._set_event("prompt", ts)

    def _feed_assistant(self, obj: dict, msg: dict, ts: float) -> None:
        model = msg.get("model")
        if isinstance(model, str) and model and not model.startswith("<"):
            self.model = model
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            self.last_message = snippet(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    name = block.get("name")
                    name = name if isinstance(name, str) and name else "tool"
                    inp = block.get("input")
                    detail = summarize_tool_input(name, inp if isinstance(inp, dict) else {})
                    tid = block.get("id")
                    if not isinstance(tid, str) or not tid:
                        tid = f"_anon{self.tool_count}"
                    self.pending.pop(tid, None)
                    self.pending[tid] = (name, detail, ts)
                    self.tool, self.detail = name, detail
                    self.tool_count += 1
                elif btype == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        self.last_message = snippet(text)
        stop = msg.get("stop_reason")
        if obj.get("isApiErrorMessage") or stop in END_STOP_REASONS:
            self._set_event("assistant_end", ts)
        else:
            self._set_event("assistant_stream", ts)

    # -- derivation -------------------------------------------------------------------------
    def _derive(self, now: float, effective_last_write: float) -> tuple[State, float, tuple[str, str] | None]:
        """Returns (state, natural start time of that state, (tool, detail) or None)."""
        silence = now - effective_last_write
        if self.ended:
            return State.SLEEPING, self.ended_ts or effective_last_write, None
        if self.finished_ts is not None and (self.last_event_ts or 0.0) <= self.finished_ts:
            # subagent whose result the parent already received (and not resumed since)
            return State.SLEEPING, self.finished_ts, None
        if silence >= SILENCE_TO_SLEEP:
            return State.SLEEPING, effective_last_write + SILENCE_TO_SLEEP, None
        if self.pending:
            name, detail, ts = next(reversed(self.pending.values()))
            td = (name, detail)
            if name in INTERACTIVE_TOOLS:
                return State.WAITING, ts, td
            if name in LONG_RUNNING_TOOLS:
                return State.WORKING, ts, td
            if now - ts >= PERMISSION_WAIT and silence >= PERMISSION_WAIT:
                return State.WAITING, max(ts, effective_last_write) + PERMISSION_WAIT, td
            return State.WORKING, ts, td
        ev_ts = self.last_event_ts if self.last_event_ts is not None else effective_last_write
        if self.last_event in ("assistant_end", "interrupt"):
            if self.is_subagent:
                return State.SLEEPING, ev_ts, None
            if now - ev_ts < IDLE_TO_SLEEP:
                return State.IDLE, ev_ts, None
            return State.SLEEPING, ev_ts + IDLE_TO_SLEEP, None
        if self.last_event in ("prompt", "tool_result", "assistant_stream"):
            return State.THINKING, ev_ts, None
        return State.IDLE, effective_last_write, None

    @property
    def tool_use_id(self) -> str | None:
        """Subagent: id of the parent's Agent/Task tool_use that spawned it (from .meta.json)."""
        tid = (self.meta or {}).get("toolUseId")
        return tid if isinstance(tid, str) and tid else None

    def derive_state(self, now: float, effective_last_write: float) -> State:
        return self._derive(now, effective_last_write)[0]

    def _project(self) -> tuple[str, str | None]:
        if self.cwd:
            base = os.path.basename(self.cwd.rstrip("/\\")) or self.cwd
            return base, self.cwd
        name = self.project_dir_name.rsplit("-", 1)[-1] or self.project_dir_name or "?"
        return name, None

    def to_agent(self, now: float, effective_last_write: float) -> AgentInfo:
        state, natural_since, td = self._derive(now, effective_last_write)
        if state != self._state:
            self._state = state
            self._state_since = min(natural_since, now)
        project, project_path = self._project()
        meta = self.meta or {}
        if self.is_subagent:
            d = meta.get("description")
            title = snippet(d) if isinstance(d, str) and d.strip() else None
            at = meta.get("agentType")
            agent_type = at if isinstance(at, str) and at else None
        else:
            title, agent_type = self.title, None
        started = self.first_ts_hint or self.first_ts or effective_last_write or now
        return AgentInfo(
            id=self.agent_id,
            name=name_for(self.agent_id),
            project=project,
            state=state,
            project_path=project_path,
            title=title,
            tool=td[0] if td else None,
            detail=(td[1] or None) if td else None,
            last_message=self.last_message,
            last_prompt=self.last_prompt,
            is_subagent=self.is_subagent,
            parent_id=self.parent_id,
            agent_type=agent_type,
            model=self.model,
            color_seed=color_seed_for(self.agent_id),
            tool_count=self.tool_count,
            started_at=int(started * 1000),
            state_since=int(self._state_since * 1000),
            updated_at=int(effective_last_write * 1000),
        )
