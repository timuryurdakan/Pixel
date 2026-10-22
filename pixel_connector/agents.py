"""Agent registry (`agents.json`) and office presence through the bundled `pixel_agent.Sender` (V3.md §5.1, §5.2).

Each connector agent is one office character: session "c_<key>", source "connector", project = workspace name,
name = agent name, ctl = "<deviceId>.<key>". The v2 coalescing rules apply; the keep-alive is 20 minutes so v2's
45-minute removal never triggers while the connector runs. Only state, tool name, fixed labels, the workspace name,
the agent name and the owner reach Vercel, exactly as in v2 (no prompts, no output).
"""

from __future__ import annotations

import copy
import importlib.util
import os
import re
import secrets
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

from .config import AGENTS_FILE, MODEL_RE, PROFILES, read_json, validate_budget, write_json

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}\Z")
KEEPALIVE_S = 20 * 60
SESSION_FIELDS = ("claudeSession", "claudeSessionStarted", "claudeTotals")
LABELS = {"success": "Görev bitti", "error": "Hata", "cancelled": "Durduruldu", "denied": "Reddedildi",
          "budget": "Bütçe doldu", "timeout": "Süre aşımı", "confirm": "Onay bekliyor"}


def load_pixel_agent():
    """`pixel_agent` from the zipapp root, else from web/hook/ in a source checkout."""
    try:
        import pixel_agent       # type: ignore  # noqa: F401
        return sys.modules["pixel_agent"]
    except ImportError:
        pass
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "hook", "pixel_agent.py")
    spec = importlib.util.spec_from_file_location("pixel_agent", path)
    if spec is None or spec.loader is None:
        raise ImportError("pixel_agent.py not found")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pixel_agent"] = mod
    spec.loader.exec_module(mod)
    return mod


def clean_name(name) -> Optional[str]:
    if not isinstance(name, str):
        return None
    s = " ".join("".join(ch for ch in name if ch.isprintable()).split())[:24]
    return s or None


class AgentError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class AgentStore:
    def __init__(self, home: str, clock: Callable[[], float] = time.time) -> None:
        self.path = os.path.join(home, AGENTS_FILE)
        self.clock = clock
        self._lock = threading.RLock()
        self._epochs: Dict[str, int] = {}          # agent key → bumped by reset_session (in memory only)
        d = read_json(self.path, {"v": 1, "agents": []})
        self.agents: Dict[str, dict] = {}
        for a in (d or {}).get("agents") or []:
            if isinstance(a, dict) and isinstance(a.get("key"), str) and KEY_RE.match(a["key"]):
                self.agents[a["key"]] = a

    def save(self) -> None:
        with self._lock:
            write_json(self.path, {"v": 1, "agents": list(self.agents.values())})

    def get(self, key) -> dict:
        a = self.agents.get(key) if isinstance(key, str) else None
        if a is None:
            raise AgentError("unknown_agent", "unknown agent")
        return a

    def list(self) -> List[dict]:
        with self._lock:
            return list(self.agents.values())

    def _new_key(self) -> str:
        while True:
            k = "a" + secrets.token_hex(3)
            if k not in self.agents:
                return k

    def create(self, name, engine, model, workspace, profile, budget, engine_id: Optional[str] = None) -> dict:
        nm = clean_name(name)
        if not nm:
            raise AgentError("bad_request", "name")
        if engine not in ("claude", "ollama", "openai"):
            raise AgentError("bad_request", "engine")
        if model is not None and (not isinstance(model, str) or not MODEL_RE.match(model)):
            raise AgentError("bad_request", "model")
        if engine in ("ollama", "openai") and not model:
            raise AgentError("bad_request", "model required")
        if profile not in PROFILES:
            raise AgentError("bad_request", "profile")
        b = None
        if budget is not None:
            errs: List[str] = []
            b = validate_budget(budget, errs, "budget", need_scope=False)
            if b is None:
                raise AgentError("bad_request", "budget")
        with self._lock:
            key = self._new_key()
            a = {"key": key, "name": nm, "engine": engine, "engineId": engine_id, "model": model,
                 "workspace": {"id": workspace["id"], "sub": workspace.get("sub")}, "profile": profile, "budget": b,
                 "claudeSession": None, "claudeSessionStarted": False, "claudeTotals": None,
                 "createdAt": int(self.clock() * 1000)}
            self.agents[key] = a
            self.save()
            return a

    def update(self, key, fields: dict) -> dict:
        with self._lock:
            a = self.get(key)
            if "name" in fields:
                nm = clean_name(fields["name"])
                if not nm:
                    raise AgentError("bad_request", "name")
                a["name"] = nm
            if "model" in fields:
                m = fields["model"]
                if m is not None and (not isinstance(m, str) or not MODEL_RE.match(m)):
                    raise AgentError("bad_request", "model")
                if m is None and a["engine"] != "claude":
                    raise AgentError("bad_request", "model required")
                a["model"] = m
            if "profile" in fields:
                if fields["profile"] not in PROFILES:
                    raise AgentError("bad_request", "profile")
                a["profile"] = fields["profile"]
            if "budget" in fields:
                if fields["budget"] is None:
                    a["budget"] = None
                else:
                    errs: List[str] = []
                    b = validate_budget(fields["budget"], errs, "budget", need_scope=False)
                    if b is None:
                        raise AgentError("bad_request", "budget")
                    a["budget"] = b
            self.save()
            return a

    # -- Claude session fields, written by engine threads (v2.md H32) -------------------------------------------------
    def session_epoch(self, key: str) -> int:
        with self._lock:
            return self._epochs.get(key, 0)

    def save_session(self, key: str, record: dict, epoch: int) -> bool:
        """Copy-on-write merge of an engine's SESSION_FIELDS into the stored agent. Dropped (→ False) when the agent is
        gone or was reset since `epoch` was read, so a run that ends after an agent.reset can't bring the old session
        back. Other fields (a rename during the run) are kept as stored."""
        with self._lock:
            cur = self.agents.get(key)
            if cur is None or self._epochs.get(key, 0) != epoch:
                return False
            new = dict(cur)
            for f in SESSION_FIELDS:
                if f in record:
                    new[f] = copy.deepcopy(record[f])
            self.agents[key] = new
            self.save()
            return True

    def reset_session(self, key) -> dict:
        """agent.reset: forget the Claude session and invalidate the session writes of runs still in flight."""
        with self._lock:
            new = dict(self.get(key))
            new.update({"claudeSession": None, "claudeSessionStarted": False, "claudeTotals": None})
            self.agents[key] = new
            self._epochs[key] = self._epochs.get(key, 0) + 1
            self.save()
            return new

    def put(self, agent: dict) -> None:
        with self._lock:
            if agent.get("key") in self.agents:
                self.agents[agent["key"]] = agent
                self.save()

    def delete(self, key) -> dict:
        with self._lock:
            a = self.get(key)
            del self.agents[key]
            self.save()
            return a

    def budgets(self):
        return [(a["key"], a.get("budget")) for a in self.list()]


class OfficeReporter:
    """Office presence for connector agents (status → Vercel `/api/status`, v2 rules)."""

    def __init__(self, server: str, room: str, key: str, owner: str, device_id: str, sender=None,
                 clock: Callable[[], float] = time.time) -> None:
        pa = load_pixel_agent()
        self.pa = pa
        self.device_id = device_id
        self.owner = owner
        self.clock = clock
        target = {"url": server.rstrip("/"), "room": room, "key": key, "owner": owner[:24], "ownerId": None}
        if sender is None:
            sender = _make_sender(pa, target, clock)
        self.sender = sender
        self._state: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def report(self, agent: dict, workspace_name: Optional[str], state: str, tool: Optional[str] = None,
               label: Optional[str] = None) -> None:
        key = agent["key"]
        now = self.clock()
        with self._lock:
            st = self._state.setdefault(key, {"since": now, "started": now, "state": None, "tools": 0, "tool": None})
            if st["state"] != state:
                st["since"] = now
                st["state"] = state
            tool = self.pa.clean_tool(tool)
            if tool and state == "working" and tool != st["tool"]:
                st["tools"] += 1
            st["tool"] = tool
            u = {"session": "c_" + key, "sub": None, "owner": self.owner[:24], "ownerId": None,
                 "name": agent.get("name"), "project": self.pa.clean_project(workspace_name), "state": state,
                 "tool": tool, "label": self.pa._text(label, 40), "agentType": None, "source": "connector",
                 "color": None, "toolCount": st["tools"], "final": False, "_since": st["since"],
                 "_started": st["started"], "ctl": "%s.%s" % (self.device_id, key)}
        try:
            self.sender.submit(u)
        except Exception:
            pass

    def offline(self, agent: dict, workspace_name: Optional[str] = None) -> None:
        self.report(agent, workspace_name, "offline")
        with self._lock:
            self._state.pop(agent["key"], None)

    def flush(self, timeout: float = 2.0) -> None:
        try:
            self.sender.flush(timeout)
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self.sender.stop()
        except Exception:
            pass


def _make_sender(pa, target: dict, clock, post=None, start_thread: bool = True):
    class ConnectorSender(pa.Sender):
        """v2 Sender with the connector keep-alive (20 min) and ages clamped to the server's limits."""

        def _materialize(self, u, now):
            out = super()._materialize(u, now)
            if "stateAgeMs" in out:
                out["stateAgeMs"] = min(out["stateAgeMs"], 86_400_000)
            if "startedAgeMs" in out:
                out["startedAgeMs"] = min(out["startedAgeMs"], 604_800_000)
            return out
    return ConnectorSender(target, post=post, clock=clock, start_thread=start_thread, keepalive=KEEPALIVE_S)
