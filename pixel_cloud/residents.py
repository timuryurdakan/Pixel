"""Resident agents: always-present characters whose state is computed from the clock at read time.

A room may hold up to MAX_RESIDENTS residents in one hash field (`_res`). They cost one write when set
and nothing afterwards: every /api/state read derives a deterministic day for each of them (work blocks
with changing tools, short thinking pauses, the odd permission prompt, coffee breaks, naps, and sleep
at night, Istanbul time). Their ids match AGENT_ID_RE, so the normal rename flow works on them.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass

from .validate import ValidationError, clean_name, parse_json, sanitize

FIELD = "_res"
MAX_RESIDENTS = 6
PROJECT_MAX = 32
CYCLE = 1500                 # s, one work/coffee/nap rhythm
NIGHT = (2, 8)               # local hours [start, end) spent asleep
TZ_OFFSET = 3 * 3600         # Europe/Istanbul, no DST

# role → [(tool, label)] they pick from while working
ROLES = {
    "backend": [
        ("Bash", "API testlerini çalıştırıyor"), ("Edit", "Endpoint yazıyor"), ("Read", "Servis kodunu okuyor"),
        ("Grep", "Logları tarıyor"), ("Bash", "Migration çalıştırıyor"), ("Edit", "Veritabanı şemasını düzenliyor"),
        ("Bash", "Docker imajı derliyor"), ("TodoWrite", "Sprint planını güncelliyor"),
    ],
    "frontend": [
        ("Edit", "Bileşen yazıyor"), ("Bash", "npm run dev"), ("Read", "Tasarımı inceliyor"),
        ("Edit", "CSS düzenliyor"), ("WebFetch", "Dokümantasyon okuyor"), ("Grep", "Bileşen arıyor"),
        ("Bash", "Testleri çalıştırıyor"), ("Edit", "Animasyon ekliyor"),
    ],
    "fullstack": [
        ("Edit", "Kod yazıyor"), ("Bash", "Komut çalıştırıyor"), ("Read", "Kod okuyor"),
        ("Grep", "Kodda arıyor"), ("WebFetch", "Web'de arıyor"), ("Agent", "Alt-ajan yönetiyor"),
    ],
}
DEFAULT_ROLE = "fullstack"


@dataclass(frozen=True, slots=True)
class Resident:
    name: str
    role: str
    project: str


def resident_id(room: str, index: int, name: str) -> str:
    return "a" + hashlib.sha256(f"{room}:resident:{index}:{name}".encode()).hexdigest()[:16]


def parse_residents(body: bytes) -> list[Resident]:
    """Body: {"residents": [{"name": "Mete", "role": "backend", "project": "api"}, ...]}; [] clears."""
    obj = parse_json(body)
    if not isinstance(obj, dict) or not isinstance(obj.get("residents"), list):
        raise ValidationError("body must be {\"residents\": [...]}")
    items = obj["residents"]
    if len(items) > MAX_RESIDENTS:
        raise ValidationError(f"at most {MAX_RESIDENTS} residents")
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise ValidationError("resident must be an object")
        name = clean_name(it.get("name"))
        if not name:
            raise ValidationError("resident name is required")
        role = it.get("role") if it.get("role") in ROLES else DEFAULT_ROLE
        project = sanitize(it["project"], PROJECT_MAX) if isinstance(it.get("project"), str) else ""
        out.append(Resident(name, role, project or role))
    return out


def dump_residents(items: list[Resident]) -> str:
    return json.dumps([{"name": r.name, "role": r.role, "project": r.project} for r in items],
                      ensure_ascii=False, separators=(",", ":"))


def load_residents(raw: str | None) -> list[Resident]:
    try:
        data = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    out = []
    for it in data if isinstance(data, list) else []:
        if isinstance(it, dict) and isinstance(it.get("name"), str):
            role = it.get("role") if it.get("role") in ROLES else DEFAULT_ROLE
            out.append(Resident(it["name"], role, str(it.get("project") or role)))
    return out[:MAX_RESIDENTS]


# -- schedule ------------------------------------------------------------------------------------------

def _cycle_plan(seed: str, cycle: int, role: str) -> list[tuple[int, str, str | None, str | None]]:
    """[(length_s, state, tool, label)] summing to exactly CYCLE seconds."""
    rng = random.Random(f"{seed}:{cycle}")
    tools = ROLES[role]
    segs: list[tuple[int, str, str | None, str | None]] = []

    def work_block():
        for _ in range(rng.randint(3, 5)):
            tool, label = rng.choice(tools)
            segs.append((rng.randint(45, 130), "working", tool, label))
            if rng.random() < 0.6:
                segs.append((rng.randint(10, 30), "thinking", None, None))

    work_block()
    if rng.random() < 0.35:
        segs.append((rng.randint(20, 45), "waiting_for_user", None, "Onay bekliyor"))
    segs.append((rng.randint(120, 240), "idle", None, None))          # coffee
    work_block()
    segs.append((rng.randint(150, 300), "sleeping", None, None))      # nap
    out, total = [], 0
    for length, *rest in segs:
        if total >= CYCLE:
            break
        length = min(length, CYCLE - total)
        out.append((length, *rest))
        total += length
    if total < CYCLE:                                                  # fill with work
        tool, label = rng.choice(tools)
        out.append((CYCLE - total, "working", tool, label))
    return out


def state_at(seed: str, role: str, now_ms: int) -> tuple[str, int, str | None, str | None, int]:
    """→ (state, since_ms, tool, label, tool_count) for this resident at `now_ms`."""
    t = now_ms // 1000
    local = t + TZ_OFFSET
    hour = (local // 3600) % 24
    if NIGHT[0] <= hour < NIGHT[1]:
        night_start = (local // 86400) * 86400 + NIGHT[0] * 3600 - TZ_OFFSET
        return "sleeping", night_start * 1000, None, None, 0
    offset = int(hashlib.sha256(seed.encode()).hexdigest()[:6], 16) % CYCLE
    shifted = t + offset
    cycle, pos = divmod(shifted, CYCLE)
    start = t - pos
    count = 0
    for length, state, tool, label in _cycle_plan(seed, cycle, role):
        if pos < length:
            return state, start * 1000, tool, label, (cycle % 50) * 17 + count
        pos -= length
        start += length
        count += 1 if state == "working" else 0
    return "working", start * 1000, None, None, count   # unreachable: the plan covers the whole cycle


def resident_records(room: str, raw: str | None, now_ms: int) -> list[tuple[str, Resident, tuple]]:
    """→ [(agent_id, resident, state_at(...))] for every resident of the room."""
    out = []
    for i, r in enumerate(load_residents(raw)):
        aid = resident_id(room, i, r.name)
        out.append((aid, r, state_at(aid, r.role, now_ms)))
    return out
