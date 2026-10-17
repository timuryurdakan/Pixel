"""Read-time state machine, stored record ↔ Agent JSON, owner colours, name resolution (CLOUD.md §6, §15.2).

Pure functions on plain dicts and integer millisecond timestamps, so the cloud (read-time derive) and
the local server's ticker (`pixel_office.reported`) apply exactly the same rules.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass

from . import settings as S
from . import residents as R
from .ids import AGENT_ID_RE
from .validate import ACTIVE_STATES, CTL_RE, Update

OWNER_COLORS = ["#e8564a", "#f29b38", "#f2d03b", "#8bc34a", "#3fb67d", "#2bb3b1", "#3a9be0", "#5b6ee1",
                "#9b5de5", "#d65db1", "#ff8fa3", "#a1887f", "#78909c", "#ffffff", "#c0ca33", "#26a69a"]

SUBAGENT_DEFAULT_NAME = "Alt-ajan"
UNKNOWN_PROJECT = "?"

_TOOL_DETAILS = {
    "Komut çalıştırıyor": ("Bash", "PowerShell", "BashOutput", "Monitor"),
    "Dosya okuyor": ("Read",),
    "Dosya düzenliyor": ("Edit", "Write", "MultiEdit", "NotebookEdit"),
    "Kodda arıyor": ("Grep", "Glob"),
    "Web'de arıyor": ("WebFetch", "WebSearch"),
    "Alt-ajan yönetiyor": ("Agent", "Task"),
    "Plan güncelliyor": ("TodoWrite",),
    "Araç kullanıyor (MCP)": ("MCP",),
}
TOOL_DETAIL = {tool: phrase for phrase, tools in _TOOL_DETAILS.items() for tool in tools}
DEFAULT_DETAIL = "Çalışıyor"


def detail_for(tool: str | None, label: str | None) -> str | None:
    if label:
        return label
    if not tool:
        return None
    return TOOL_DETAIL.get(tool, DEFAULT_DETAIL)


def crc32(s: str) -> int:
    return zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF


# -- records ----------------------------------------------------------------------------------------

def make_record(u: Update, *, owner_id: str, parent_id: str | None, now_ms: int,
                prev: tuple[str, int, int] | None = None) -> dict:
    """Compact stored record (§4.2). `prev` = (state, ss, sa) of the last write this process saw for the
    agent; used only when the client omits stateAgeMs/startedAgeMs (best effort, never trusted clocks)."""
    ts = now_ms
    if u.state_age_ms is not None:
        ss = ts - u.state_age_ms
    elif prev is not None and prev[0] == u.state:
        ss = prev[1]
    else:
        ss = ts
    if u.started_age_ms is not None:
        sa = ts - u.started_age_ms
    elif prev is not None:
        sa = prev[2]
    else:
        sa = ts
    sa = min(sa, ss)
    rec = {
        "v": 1, "owner": u.owner, "oid": owner_id, "proj": u.project, "st": u.state, "tool": u.tool,
        "lbl": u.label, "sub": u.sub is not None, "par": parent_id, "atype": u.agent_type, "src": u.source,
        "col": u.color if u.color is not None else crc32(owner_id) % 16,
        "ts": ts, "ss": ss, "sa": sa, "tc": u.tool_count or 0,
    }
    if u.name:
        rec["nm"] = u.name
    if u.final:
        rec["fin"] = True
    if u.ctl:
        rec["ctl"] = u.ctl
    return rec


def control_of(rec: dict) -> dict | None:
    """v3 Agent JSON `control` (V3.md §13.2): {"device", "agent"} for connector agents, else None."""
    ctl = rec.get("ctl")
    if not isinstance(ctl, str) or not CTL_RE.match(ctl):
        return None
    device, _, agent = ctl.partition(".")
    return {"device": device, "agent": agent}


def dump_record(rec: dict) -> str:
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


def load_json_obj(raw: str | bytes | None) -> dict | None:
    if raw is None:
        return None
    try:
        v = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return None
    return v if isinstance(v, dict) else None


def _num(v: object, default: int = 0) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _str(v: object) -> str | None:
    return v if isinstance(v, str) and v else None


# -- §6.2 read-time transitions ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Derived:
    visible: bool
    purge: bool = False
    state: str = "idle"
    since: int = 0
    tool: str | None = None
    label: str | None = None


OMIT = Derived(visible=False)
PURGE = Derived(visible=False, purge=True)


def derive_record(rec: dict, now_ms: int) -> Derived:
    """Apply the §6.2 table to one stored record. First matching rule wins."""
    ts = _num(rec.get("ts"))
    age = (now_ms - ts) / 1000.0
    sub = bool(rec.get("sub"))
    st = rec.get("st")
    if st not in ("working", "thinking", "waiting_for_user", "idle", "sleeping"):
        st = "idle"
    if age > S.PURGE_AFTER:                                                   # 1
        return PURGE
    if sub and st == "sleeping" and age > S.SUB_REMOVE_AFTER:                 # 2
        return OMIT
    if rec.get("fin") and age > S.FINISHED_REMOVE_AFTER:                      # 2b
        return OMIT
    if sub and age > S.SUB_STALE_REMOVE:                                      # 3
        return OMIT
    if not sub and age > S.REMOVE_AFTER:                                      # 4
        return OMIT
    if st == "idle" and age > S.IDLE_TO_SLEEP:                                # 5
        return Derived(True, False, "sleeping", ts + S.IDLE_TO_SLEEP * 1000, None, None)
    if st in ACTIVE_STATES and age > S.ACTIVE_STALE:                          # 6
        return Derived(True, False, "sleeping", ts + S.ACTIVE_STALE * 1000, None, None)
    return Derived(True, False, st, _num(rec.get("ss"), ts), _str(rec.get("tool")), _str(rec.get("lbl")))  # 7


# -- activity → pollMs / CDN TTL --------------------------------------------------------------------

def activity(states: list[str]) -> str:
    if any(s in ACTIVE_STATES for s in states):
        return "active"
    return "calm" if states else "empty"


def poll_ms(act: str, st: S.Settings | None = None) -> int:
    if st is None:
        return {"active": S.POLL_ACTIVE_MS, "calm": S.POLL_CALM_MS}.get(act, S.POLL_EMPTY_MS)
    return {"active": st.poll_active_ms, "calm": st.poll_calm_ms}.get(act, st.poll_empty_ms)


def cache_control(act: str) -> str:
    ttl = {"active": S.CDN_TTL_ACTIVE, "calm": S.CDN_TTL_CALM}.get(act, S.CDN_TTL_EMPTY)
    return f"public, max-age=0, s-maxage={ttl}"


# -- owner colours (§6.4) ---------------------------------------------------------------------------

def assign_owner_colors(prefs: dict[str, int]) -> dict[str, str]:
    """{ownerId: preferred index} → {ownerId: hex}. Owners sorted by id; preferred index, else the next free."""
    taken: set[int] = set()
    out: dict[str, str] = {}
    for oid in sorted(prefs):
        pref = prefs[oid] % 16
        idx = pref
        for k in range(16):
            cand = (pref + k) % 16
            if cand not in taken:
                idx = cand
                break
        taken.add(idx)
        out[oid] = OWNER_COLORS[idx]
    return out


# -- name resolution (§15.2, cloud column) ------------------------------------------------------------

def resolve_name(*, custom: str | None, owner_default: str | None, reported: str | None,
                 auto: str, is_sub: bool) -> tuple[str, str, str]:
    """→ (name, nameSource, defaultName). defaultName = what resetting the winning rename would show."""
    chain: list[tuple[str, str]] = []
    if custom:
        chain.append((custom, "custom"))
    if owner_default and not is_sub:
        chain.append((owner_default, "owner"))
    if reported:
        chain.append((reported, "reported"))
    chain.append((auto, "auto"))
    name, source = chain[0]
    default = chain[1][0] if source in ("custom", "owner") and len(chain) > 1 else name
    return name, source, default


def _rename_value(raw: str | None) -> tuple[str | None, int]:
    obj = load_json_obj(raw)
    if not obj:
        return None, 0
    return _str(obj.get("name")), _num(obj.get("ts"))


@dataclass
class Snapshot:
    payload: dict
    activity: str
    purge: list[str]


def snapshot(room_id: str, fields: dict[str, str], now_ms: int, st: S.Settings | None = None) -> Snapshot:
    """HGETALL result → §5.4 response body, activity class and fields to HDEL (lazy purge)."""
    meta = load_json_obj(fields.get("_meta")) or {}
    room_name = _str(meta.get("name")) or S.DEFAULT_ROOM_NAME
    custom: dict[str, tuple[str | None, int]] = {}
    owner_names: dict[str, str] = {}
    records: dict[str, dict] = {}
    purge: list[str] = []
    for f, raw in fields.items():
        if f.startswith("n:"):
            custom[f[2:]] = _rename_value(raw)
        elif f.startswith("no:"):
            n, _ = _rename_value(raw)
            if n:
                owner_names[f[3:]] = n
        elif AGENT_ID_RE.match(f):
            rec = load_json_obj(raw)
            if rec is None:
                purge.append(f)        # corrupt record: nothing can ever read it
            else:
                records[f] = rec

    visible: dict[str, tuple[dict, Derived]] = {}
    for aid, rec in records.items():
        d = derive_record(rec, now_ms)
        if d.purge:
            purge.append(aid)
        elif d.visible:
            visible[aid] = (rec, d)

    resident = R.resident_records(room_id, fields.get(R.FIELD), now_ms)
    resident_ids = {aid for aid, _, _ in resident}

    # rename fields of agents that are gone (no record at all) for longer than PURGE_AFTER
    for aid, (_, ts) in custom.items():
        if aid not in records and aid not in resident_ids and now_ms - ts > S.PURGE_AFTER * 1000:
            purge.append("n:" + aid)

    prefs: dict[str, int] = {}
    for rec, _ in visible.values():
        oid = _str(rec.get("oid")) or "?"
        prefs.setdefault(oid, _num(rec.get("col")))
    for aid, _, _ in resident:
        prefs.setdefault("r" + aid[1:], crc32(aid) % 16)
    colors = assign_owner_colors(prefs)

    agents = []
    for aid, (rec, d) in visible.items():
        is_sub = bool(rec.get("sub"))
        oid = _str(rec.get("oid")) or "?"
        owner = _str(rec.get("owner")) or "?"
        atype = _str(rec.get("atype"))
        parent = _str(rec.get("par"))
        if parent is not None and parent not in visible:
            parent = None
        auto = (atype or SUBAGENT_DEFAULT_NAME) if is_sub else owner
        name, src, default = resolve_name(custom=custom.get(aid, (None, 0))[0], owner_default=owner_names.get(oid),
                                          reported=_str(rec.get("nm")), auto=auto, is_sub=is_sub)
        ts = _num(rec.get("ts"))
        agents.append({
            "id": aid,
            "name": name,
            "project": _str(rec.get("proj")) or UNKNOWN_PROJECT,
            "projectPath": None, "title": None, "lastMessage": None, "lastPrompt": None, "model": None,
            "state": d.state,
            "tool": d.tool,
            "detail": detail_for(d.tool, d.label),
            "isSubagent": is_sub, "parentId": parent, "agentType": atype,
            "colorSeed": crc32(aid),
            "toolCount": _num(rec.get("tc")),
            "startedAt": min(_num(rec.get("sa"), ts), d.since),
            "stateSince": d.since,
            "updatedAt": ts,
            "owner": owner, "ownerId": oid, "ownerColor": colors.get(oid, OWNER_COLORS[0]),
            "source": _str(rec.get("src")) or "api",
            "nameSource": src, "defaultName": default,
            "control": control_of(rec),
        })
    for aid, r, (state, since, tool, label, tc) in resident:
        oid = "r" + aid[1:]
        name, src, default = resolve_name(custom=custom.get(aid, (None, 0))[0], owner_default=owner_names.get(oid),
                                          reported=None, auto=r.name, is_sub=False)
        agents.append({
            "id": aid, "name": name, "project": r.project,
            "projectPath": None, "title": None, "lastMessage": None, "lastPrompt": None, "model": None,
            "state": state, "tool": tool, "detail": detail_for(tool, label),
            "isSubagent": False, "parentId": None, "agentType": None,
            "colorSeed": crc32(aid), "toolCount": tc,
            "startedAt": since, "stateSince": since, "updatedAt": now_ms,
            "owner": r.name, "ownerId": oid, "ownerColor": colors.get(oid, OWNER_COLORS[0]),
            "source": "claude-code", "nameSource": src, "defaultName": default,
            "control": None,
        })
    agents.sort(key=lambda a: (a["startedAt"], a["id"]))
    # Residents never drive the poll rate: a room with only residents polls like an empty one (§12 budget).
    act = activity([a["state"] for a in agents if a["id"] not in resident_ids])
    payload = {
        "mode": "cloud",
        "serverTime": now_ms,
        "room": {"id": room_id, "name": room_name},
        "pollMs": poll_ms(act, st),
        "agents": agents,
    }
    return Snapshot(payload, act, purge)
