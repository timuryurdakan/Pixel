"""Engine protocol (V3.md §8) and helpers shared by the adapters.

    engine.available() -> {"available": bool, "version": str|None, ...}         (cached 60 s)
    engine.start(ctx: TaskContext, events: TaskEvents) -> Running                 (never blocks; runs on a thread)
    running.cancel()                                                              (idempotent, non-blocking)

`TaskEvents` is implemented by tasks.py: part(dict), live(**usage), office(state, tool, label), end(...). An engine
calls `end` exactly once.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, List, Optional


class TaskContext:
    """Everything an engine needs for one task. `agent` is the agent record (engines may update session fields
    through `save_agent`)."""

    def __init__(self, task: str, agent: dict, prompt: str, cwd: Optional[str], model: Optional[str],
                 profile: str = "edit", fresh: bool = False, max_budget_usd: Optional[float] = None,
                 save_agent: Optional[Callable[[dict], None]] = None, history_path: Optional[str] = None) -> None:
        self.task = task
        self.agent = agent
        self.prompt = prompt
        self.cwd = cwd
        self.model = model
        self.profile = profile
        self.fresh = fresh
        self.max_budget_usd = max_budget_usd
        self.save_agent = save_agent or (lambda a: None)
        self.history_path = history_path


class Running:
    def cancel(self) -> None:          # pragma: no cover - interface
        raise NotImplementedError


def empty_usage(estimated: bool = False) -> dict:
    return {"inTok": 0, "outTok": 0, "cacheRead": 0, "cacheWrite": 0, "thinkTok": None, "estimated": estimated}


def estimate_tokens(text: str) -> int:
    return (len(text or "") + 3) // 4


def short(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


class Cached:
    """Value cached for `ttl` seconds (engine availability probes)."""

    def __init__(self, fn: Callable[[], dict], ttl: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.fn, self.ttl, self.clock = fn, ttl, clock
        self._v = None
        self._t = -1e18
        self._lock = threading.Lock()

    def get(self, force: bool = False):
        with self._lock:
            if force or self.clock() - self._t > self.ttl:
                self._v = self.fn()
                self._t = self.clock()
            return self._v

    def invalidate(self) -> None:
        with self._lock:
            self._t = -1e18


# -- chat history for local engines (§8.2) --------------------------------------------------------

def read_history(path: Optional[str], limit: int, num_ctx: Optional[int] = None) -> List[dict]:
    if not path or limit <= 0:
        return []
    out: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                    out.append({"role": m["role"], "content": m["content"]})
    except OSError:
        return []
    out = out[-limit:]
    if num_ctx:
        budget = int(num_ctx * 0.75)
        while out and sum(estimate_tokens(m["content"]) for m in out) > budget:
            out.pop(0)
    return out


def append_history(path: Optional[str], messages: List[dict]) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        for m in messages:
            os.write(fd, (json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def clear_history(path: Optional[str]) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
