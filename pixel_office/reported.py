"""Agents reported through the local `POST /api/status` (CLOUD.md §16).

Records use the cloud's stored-record format (`pixel_cloud.derive.make_record`), and a 1 s ticker applies
the same §6.2 read-time rules (`pixel_cloud.derive.derive_record`), then upserts/removes in the AgentStore.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from pixel_cloud import derive, ids
from pixel_cloud import settings as CS
from pixel_cloud.validate import Update

from .models import AgentInfo, State, color_seed_for, hash_name
from .store import AgentStore

log = logging.getLogger("pixel_office")


class RoomFull(Exception):
    pass


class ReportedAgents(threading.Thread):
    def __init__(self, store: AgentStore, clock: Callable[[], float] = time.time, tick: float = 1.0,
                 max_agents: int = CS.MAX_AGENTS_PER_ROOM) -> None:
        super().__init__(name="pixel-reported", daemon=True)
        self.store = store
        self.clock = clock
        self.tick_s = tick
        self.max_agents = max_agents
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._stop = threading.Event()

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    # -- writes -------------------------------------------------------------------------------------
    def apply(self, updates: list[Update]) -> list[str]:
        """Store a validated batch (same semantics as the cloud). Raises RoomFull."""
        now = self.now_ms()
        out: list[str] = []
        touched: list[str] = []
        removed: list[str] = []
        with self._lock:
            new_ids = set()
            for u in updates:
                oid = u.owner_id or ids.default_owner_id(u.owner)
                aid = ids.local_agent_id(oid, u.session, u.sub)
                if u.state != "offline" and aid not in self._records:
                    new_ids.add(aid)
            if new_ids and len(self._records) + len(new_ids) > self.max_agents:
                raise RoomFull
            for u in updates:
                oid = u.owner_id or ids.default_owner_id(u.owner)
                aid = ids.local_agent_id(oid, u.session, u.sub)
                out.append(aid)
                if u.state == "offline":
                    self._records.pop(aid, None)
                    removed.append(aid)
                    continue
                parent = ids.local_agent_id(oid, u.session, None) if u.sub else None
                prev = self._records.get(aid)
                p = (prev["st"], prev["ss"], prev["sa"]) if prev else None
                self._records[aid] = derive.make_record(u, owner_id=oid, parent_id=parent, now_ms=now, prev=p)
                touched.append(aid)
        for aid in removed:
            self.store.remove(aid)
        self._publish(now, only=set(touched))
        return out

    def ids(self) -> set[str]:
        with self._lock:
            return set(self._records)

    # -- time-based transitions ---------------------------------------------------------------------
    def _publish(self, now: int, only: set[str] | None = None) -> None:
        with self._lock:
            recs = {k: dict(v) for k, v in self._records.items()}
        gone: list[str] = []
        agents: list[AgentInfo] = []
        for aid, rec in recs.items():
            if only is not None and aid not in only:
                continue
            d = derive.derive_record(rec, now)
            if not d.visible:
                gone.append(aid)
                continue
            agents.append(self._agent(aid, rec, d, recs))
        if gone:
            with self._lock:
                for aid in gone:
                    if self._records.get(aid) == recs[aid]:
                        del self._records[aid]
            for aid in gone:
                self.store.remove(aid)
        for a in agents:
            try:
                self.store.upsert(a)
            except Exception:
                log.exception("store upsert failed for %s", a.id)

    @staticmethod
    def _agent(aid: str, rec: dict, d: derive.Derived, recs: dict[str, dict]) -> AgentInfo:
        is_sub = bool(rec.get("sub"))
        parent = rec.get("par") if rec.get("par") in recs else None
        nm = rec.get("nm")
        ts = int(rec.get("ts") or 0)
        return AgentInfo(
            id=aid,
            name=nm if nm else ((rec.get("atype") or "Alt-ajan") if is_sub else hash_name(aid)),
            name_source="reported" if nm else "auto",
            project=rec.get("proj") or derive.UNKNOWN_PROJECT,
            state=State(d.state),
            tool=d.tool,
            detail=derive.detail_for(d.tool, d.label),
            is_subagent=is_sub,
            parent_id=parent,
            agent_type=rec.get("atype"),
            color_seed=color_seed_for(aid),
            tool_count=int(rec.get("tc") or 0),
            started_at=min(int(rec.get("sa") or ts), d.since),
            state_since=d.since,
            updated_at=ts,
            source=rec.get("src") or "api",
        )

    def tick(self) -> None:
        self._publish(self.now_ms())

    def run(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self.tick()
            except Exception:
                log.exception("reported-agents tick failed")

    def stop(self) -> None:
        self._stop.set()
