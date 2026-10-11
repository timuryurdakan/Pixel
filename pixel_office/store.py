"""Thread-safe agent registry that broadcasts change events (ARCHITECTURE.md §7.2)."""

import copy
import logging
import queue
import threading
from typing import Callable, NamedTuple

from .models import AgentInfo, now_ms

log = logging.getLogger("pixel_office")


class Event(NamedTuple):
    seq: int
    name: str
    data: dict


OVERFLOW = Event(-1, "overflow", {})


class AgentStore:
    def __init__(self, mode: str, namer: Callable[[AgentInfo], AgentInfo] | None = None) -> None:
        self.mode = mode
        self.namer = namer      # applies UI renames (names_store.Namer); None → names as given (v1)
        self._lock = threading.Lock()
        self._agents: dict[str, AgentInfo] = {}
        self._raw: dict[str, AgentInfo] = {}     # as produced, before the namer (for refresh_names)
        self._dicts: dict[str, dict] = {}
        self._subs: list[queue.Queue[Event]] = []
        self._seq = 0

    # -- internal (call with lock held) -------------------------------------------------
    def _emit(self, name: str, data: dict) -> None:
        self._seq += 1
        ev = Event(self._seq, name, data)
        dropped: list[queue.Queue[Event]] = []
        for q in self._subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                dropped.append(q)
        for q in dropped:
            self._subs.remove(q)
            log.warning("SSE subscriber too slow (queue full); dropping it")
            # Make room for the sentinel so the handler notices and closes.
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(OVERFLOW)
            except queue.Full:  # pragma: no cover - queue was just drained
                pass

    def _snapshot_locked(self) -> dict:
        agents = sorted(self._dicts.values(), key=lambda d: (d["startedAt"], d["id"]))
        return {"mode": self.mode, "serverTime": now_ms(), "agents": [dict(a) for a in agents]}

    # -- public API ----------------------------------------------------------------------
    def _named(self, raw: AgentInfo) -> AgentInfo:
        if self.namer is None:
            return copy.copy(raw)
        try:
            return self.namer(raw)
        except Exception:
            log.exception("name resolution failed for %s", raw.id)
            return copy.copy(raw)

    def upsert(self, agent: AgentInfo) -> bool:
        raw = copy.copy(agent)
        named = self._named(raw)
        d = named.to_dict(names=True)
        with self._lock:
            self._raw[agent.id] = raw
            old = self._dicts.get(agent.id)
            if old == d:
                return False
            self._agents[agent.id] = named
            self._dicts[agent.id] = d
            self._emit("agent_added" if old is None else "agent_updated", dict(d))
            return True

    def refresh_names(self, ids: set[str] | None = None) -> int:
        """Re-run the namer (after a rename) and emit `agent_updated` for every agent whose JSON changed."""
        with self._lock:
            raws = {k: r for k, r in self._raw.items() if ids is None or k in ids}
        named = {k: self._named(r) for k, r in raws.items()}
        changed = 0
        with self._lock:
            for k, n in named.items():
                if self._raw.get(k) is not raws[k]:
                    continue          # replaced meanwhile; that upsert already used the new names
                d = n.to_dict(names=True)
                if self._dicts.get(k) == d:
                    continue
                self._agents[k] = n
                self._dicts[k] = d
                self._emit("agent_updated", dict(d))
                changed += 1
        return changed

    def remove(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id not in self._agents:
                return False
            del self._agents[agent_id]
            del self._dicts[agent_id]
            self._raw.pop(agent_id, None)
            self._emit("agent_removed", {"id": agent_id})
            return True

    def get(self, agent_id: str) -> AgentInfo | None:
        with self._lock:
            a = self._agents.get(agent_id)
            return copy.copy(a) if a is not None else None

    def ids(self) -> set[str]:
        with self._lock:
            return set(self._agents)

    def count(self) -> int:
        with self._lock:
            return len(self._agents)

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked()

    def subscribe(self, maxsize: int = 1000) -> queue.Queue[Event]:
        q: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def subscribe_with_snapshot(self, maxsize: int = 1000) -> tuple[queue.Queue[Event], dict, int]:
        """Atomically subscribe and take a snapshot; returns (queue, snapshot, seq)."""
        q: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.append(q)
            return q, self._snapshot_locked(), self._seq

    def unsubscribe(self, q: queue.Queue[Event]) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)
