"""Token/cost ledger and budgets (V3.md §10).

`usage.sqlite` (stdlib sqlite3, WAL) holds one row per task. Periods use the device's local time zone (day = local
midnight, week = ISO week from Monday, month = calendar month). Budget token metric:
`in + out + cacheWrite + 0.1 × cacheRead`. Gauge states: ok < 80 % ≤ warn < 100 % ≤ over.
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Callable, List, Optional, Tuple

from .config import BUDGETS_FILE, read_json, validate_budget, write_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  task TEXT PRIMARY KEY, agent TEXT NOT NULL, engine TEXT NOT NULL, model TEXT,
  started_ms INTEGER NOT NULL, ended_ms INTEGER, status TEXT,
  in_tok INTEGER, out_tok INTEGER, cache_read INTEGER, cache_write INTEGER,
  cost_usd REAL, cost_basis TEXT, estimated INTEGER DEFAULT 0,
  prompt_sha TEXT, prompt TEXT, result TEXT, bid TEXT);
CREATE INDEX IF NOT EXISTS tasks_agent_time ON tasks(agent, started_ms);
CREATE INDEX IF NOT EXISTS tasks_time ON tasks(started_ms);
"""
CACHE_READ_WEIGHT = 0.1


def local_tz() -> tzinfo:
    return datetime.now().astimezone().tzinfo


def tz_name() -> str:
    tzn = os.environ.get("TZ")
    if tzn and "/" in tzn:
        return tzn
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    off = datetime.now().astimezone().strftime("%z")
    return off[:3] + ":" + off[3:] if off else "UTC"


def period_start_ms(period: str, now_ms: int, tz: Optional[tzinfo] = None) -> int:
    tz = tz or local_tz()
    now = datetime.fromtimestamp(now_ms / 1000.0, tz)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        start = day
    elif period == "week":
        start = day - timedelta(days=now.weekday())
    elif period == "month":
        start = day.replace(day=1)
    else:
        raise ValueError("period")
    # Re-anchor through the zone so DST changes inside the period are honoured.
    start = start.replace(tzinfo=None)
    if hasattr(tz, "localize"):
        start = tz.localize(start)          # pragma: no cover (pytz-like)
    else:
        start = start.replace(tzinfo=tz)
    return int(start.timestamp() * 1000)


def weighted_tokens(in_tok, out_tok, cache_read, cache_write) -> float:
    return (in_tok or 0) + (out_tok or 0) + (cache_write or 0) + CACHE_READ_WEIGHT * (cache_read or 0)


def gauge_state(pct: float) -> str:
    return "ok" if pct < 80 else ("warn" if pct < 100 else "over")


class Ledger:
    def __init__(self, path: str, clock: Callable[[], float] = time.time, tz: Optional[tzinfo] = None) -> None:
        self.path = path
        self.clock = clock
        self.tz = tz
        new = not os.path.exists(path)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if new:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        self._lock = threading.Lock()
        with self._lock:
            try:
                self._db.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def start_task(self, task: str, agent: str, engine: str, model: Optional[str], prompt_sha: str, prompt: str,
                   bid: Optional[str], started_ms: Optional[int] = None) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO tasks(task, agent, engine, model, started_ms, status, prompt_sha, "
                             "prompt, bid) VALUES (?,?,?,?,?,?,?,?,?)",
                             (task, agent, engine, model, started_ms or self.now_ms(), "running", prompt_sha, prompt,
                              bid))

    def end_task(self, task: str, status: str, usage: dict, cost_usd: Optional[float], cost_basis: str,
                 result: Optional[str], ended_ms: Optional[int] = None) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET ended_ms=?, status=?, in_tok=?, out_tok=?, cache_read=?, cache_write=?, "
                             "cost_usd=?, cost_basis=?, estimated=?, result=? WHERE task=?",
                             (ended_ms or self.now_ms(), status, usage.get("inTok"), usage.get("outTok"),
                              usage.get("cacheRead"), usage.get("cacheWrite"), cost_usd, cost_basis,
                              1 if usage.get("estimated") else 0, result, task))

    def get_task(self, task: str) -> Optional[dict]:
        with self._lock:
            cur = self._db.execute("SELECT * FROM tasks WHERE task=?", (task,))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
        return dict(zip(cols, row)) if row else None

    def list_tasks(self, agent: str, limit: int = 10) -> List[dict]:
        with self._lock:
            rows = self._db.execute("SELECT task, status, prompt, started_ms, ended_ms, in_tok, out_tok, cache_read, "
                                    "cache_write, cost_usd FROM tasks WHERE agent=? ORDER BY started_ms DESC LIMIT ?",
                                    (agent, int(limit))).fetchall()
        out = []
        for task, status, prompt, s, e, i, o, cr, cw, cost in rows:
            out.append({"task": task, "status": status, "preview": (prompt or "")[:120], "startedAt": s, "endedAt": e,
                        "tokens": int(weighted_tokens(i, o, cr, cw)), "costUsd": cost})
        return out

    def totals(self, period: str, agent: Optional[str] = None, engine: Optional[str] = None,
               now_ms: Optional[int] = None) -> dict:
        start = period_start_ms(period, now_ms or self.now_ms(), self.tz)
        q = ("SELECT COALESCE(SUM(in_tok),0), COALESCE(SUM(out_tok),0), COALESCE(SUM(cache_read),0), "
             "COALESCE(SUM(cache_write),0), COALESCE(SUM(cost_usd),0), COUNT(*) FROM tasks WHERE started_ms >= ?")
        args: list = [start]
        if agent is not None:
            q += " AND agent=?"
            args.append(agent)
        if engine is not None:
            q += " AND engine=?"
            args.append(engine)
        with self._lock:
            i, o, cr, cw, cost, n = self._db.execute(q, args).fetchone()
        return {"inTok": i, "outTok": o, "cacheRead": cr, "cacheWrite": cw, "costUsd": round(cost, 6), "tasks": n,
                "tokens": int(weighted_tokens(i, o, cr, cw))}

    def by_agent(self, period: str, now_ms: Optional[int] = None) -> List[dict]:
        start = period_start_ms(period, now_ms or self.now_ms(), self.tz)
        with self._lock:
            rows = self._db.execute("SELECT agent, COALESCE(SUM(in_tok),0), COALESCE(SUM(out_tok),0), "
                                    "COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(cost_usd),0)"
                                    " FROM tasks WHERE started_ms >= ? GROUP BY agent ORDER BY agent", (start,)).fetchall()
        return [{"agent": a, "tokens": int(weighted_tokens(i, o, cr, cw)), "costUsd": round(c, 6)}
                for a, i, o, cr, cw, c in rows]

    def purge(self, retain_days: int) -> int:
        cutoff = self.now_ms() - retain_days * 86400 * 1000
        with self._lock:
            cur = self._db.execute("UPDATE tasks SET prompt=NULL, result=NULL WHERE started_ms < ? AND "
                                   "(prompt IS NOT NULL OR result IS NOT NULL)", (cutoff,))
            return cur.rowcount


class Budgets:
    """Budgets from three places: connector.json (locked), budgets.json (set remotely), agent records."""

    def __init__(self, home: str, config_budgets: List[dict], ledger: Ledger,
                 agent_budgets: Callable[[], List[Tuple[str, dict]]] = lambda: []) -> None:
        self.path = os.path.join(home, BUDGETS_FILE)
        self.locked = [dict(b, locked=True) for b in config_budgets]
        self.ledger = ledger
        self.agent_budgets = agent_budgets
        d = read_json(self.path, {"v": 1, "budgets": []})
        errs: List[str] = []
        self.remote = [b for b in (validate_budget(x, errs, "budgets.json") for x in (d or {}).get("budgets") or []) if b]
        self._lock = threading.Lock()

    def all(self) -> List[dict]:
        out = list(self.locked) + [dict(b, locked=False) for b in self.remote]
        for key, b in self.agent_budgets():
            if b:
                out.append(dict(b, scope="agent:" + key, locked=False))
        return out

    def set(self, budget: dict) -> Tuple[bool, str]:
        """Add/replace a remote budget, or delete with {scope, delete:true}. → (ok, error code)."""
        scope = budget.get("scope")
        if isinstance(scope, str) and scope.startswith("agent:"):
            return False, "use_agent_update"
        if any(b["scope"] == scope and b["period"] == budget.get("period") for b in self.locked):
            return False, "locked"
        with self._lock:
            if budget.get("delete"):
                self.remote = [b for b in self.remote if not (b["scope"] == scope and
                               (budget.get("period") is None or b["period"] == budget.get("period")))]
            else:
                errs: List[str] = []
                v = validate_budget(budget, errs, "budget")
                if not v:
                    return False, "bad_request"
                self.remote = [b for b in self.remote if not (b["scope"] == v["scope"] and b["period"] == v["period"])]
                self.remote.append(v)
            write_json(self.path, {"v": 1, "budgets": self.remote})
        return True, ""

    def _used(self, b: dict, now_ms: Optional[int]) -> float:
        scope = b["scope"]
        if scope == "device":
            t = self.ledger.totals(b["period"], now_ms=now_ms)
        elif scope.startswith("agent:"):
            t = self.ledger.totals(b["period"], agent=scope[6:], now_ms=now_ms)
        else:
            t = self.ledger.totals(b["period"], engine=scope[7:], now_ms=now_ms)
        return t["costUsd"] if b["unit"] == "usd" else t["tokens"]

    def evaluate(self, now_ms: Optional[int] = None, scopes: Optional[List[str]] = None) -> List[dict]:
        out = []
        for b in self.all():
            if scopes is not None and b["scope"] not in scopes:
                continue
            used = self._used(b, now_ms)
            pct = min(999, int(round(100.0 * used / b["limit"]))) if b["limit"] else 999
            out.append(dict(b, used=round(used, 6) if b["unit"] == "usd" else int(used), pct=pct,
                            state=gauge_state(pct)))
        return out

    def check_task(self, agent_key: str, engine: str, max_per_task_usd: Optional[float] = None,
                   now_ms: Optional[int] = None) -> Tuple[bool, Optional[float]]:
        """→ (allowed, max_budget_usd for Claude). A `block` budget that is over rejects the task; for Claude, USD
        block budgets cap --max-budget-usd at the remaining amount (rejected below 0.01)."""
        scopes = ["device", "agent:" + agent_key, "engine:" + engine]
        cap = max_per_task_usd
        for b in self.evaluate(now_ms, scopes):
            if b["action"] != "block":
                continue
            if b["state"] == "over":
                return False, None
            if engine == "claude" and b["unit"] == "usd":
                remaining = b["limit"] - b["used"]
                if remaining < 0.01:
                    return False, None
                cap = remaining if cap is None else min(cap, remaining)
        if cap is None:
            return True, None
        cap = math.floor(cap * 100 + 1e-9) / 100.0        # never round above the remaining budget
        if cap < 0.01:
            return False, None
        return True, cap
