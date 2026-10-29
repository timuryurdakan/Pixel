"""v4-C workflow engine (docs/V4_ANTIGRAVITY.md §3 A10, V3.md §20 "v4-C / A10").

A workflow is an ordered list of steps run on this device: agent steps (one task each, through the normal
`Scheduler.submit` path: the step agent's own profile, folder, budget, confirmation rule and rate limit) and human
approval steps (a local `Approvals` item, answerable on the computer or from a paired browser).

State machine, for every step and for the run as a whole (invalid transitions raise WorkflowError("bad_transition")):

    waiting (BEKLİYOR) → running (ÇALIŞIYOR) → done (BİTTİ) | error (HATA) | cancelled (İPTAL)
    waiting → approval (ONAY BEKLİYOR) → done | cancelled | error

Guardrails per run: total cost cap (USD), total turns, total wall time, and at most MAX_STEPS steps per template. A
step that ends over a limit stops the run with `error` and the next step never starts; a Claude step gets the remaining
cost as `--max-budget-usd`, so it is also stopped mid-step. A denied approval cancels the run (code `denied`), an
expired one counts as denied (code `approval_timeout`).

Templates live only on this computer: the built-in one below plus an optional `workflows.json` in the connector
directory, both checked against an allow-list at load time. Nothing here is ever downloaded. The wire format of a run
(`workflow.state`) carries ids, states, numbers and template labels, never prompts, the user's brief or model output.
"""

from __future__ import annotations

import math
import os
import re
import secrets
import threading
import time
import unicodedata
from typing import Callable, Dict, List, Optional, Tuple

from .agents import AgentError
from .config import MODEL_RE, PROFILES, read_json
from .tasks import TaskError, handoff_prompt
from .workspaces import WorkspaceDenied, validate_sub

TEMPLATES_FILE = "workflows.json"
RUN_ID_RE = re.compile(r"^w_[A-Za-z0-9_-]{10}\Z")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}\Z")

MAX_STEPS = 8
MAX_TEMPLATES = 20
NAME_MAX = 60
DESCRIPTION_MAX = 300
ROLE_MAX = 24
TITLE_MAX = 80
PROMPT_MAX = 4096           # UTF-8 bytes of a step instruction
BRIEF_MAX = 4096            # UTF-8 bytes of the user's request at start
COST_BOUNDS = (0.01, 20.0)
TURN_BOUNDS = (1, 500)
TIMEOUT_BOUNDS = (60, 14400)
APPROVAL_TIMEOUT_BOUNDS = (30, 3600)
DEFAULT_GUARDRAILS = {"maxCostUsd": 1.0, "maxTurns": 60, "timeoutS": 1800}
DEFAULT_APPROVAL_TIMEOUT_S = 900
MIN_CLAUDE_BUDGET = 0.01    # Claude's --max-budget-usd has cent precision (usage.Budgets.check_task)
MAX_ACTIVE_RUNS = 1         # one run at a time: its step agents are reserved for it
KEEP_FINISHED = 5           # finished runs kept in memory for workflow.list
STEP_ENGINES = ("claude", "ollama")
BRIEF_HEAD = "Kullanıcının isteği:"

WAITING, RUNNING, APPROVAL, DONE, ERROR, CANCELLED = "waiting", "running", "approval", "done", "error", "cancelled"
STATE_LABELS = {WAITING: "Bekliyor", RUNNING: "Çalışıyor", APPROVAL: "Onay bekliyor", DONE: "Bitti", ERROR: "Hata",
                CANCELLED: "İptal"}
TERMINAL = frozenset({DONE, ERROR, CANCELLED})
STEP_TRANSITIONS = {WAITING: {RUNNING, APPROVAL, CANCELLED}, RUNNING: {DONE, ERROR, CANCELLED},
                    APPROVAL: {DONE, ERROR, CANCELLED}}
RUN_TRANSITIONS = {WAITING: {RUNNING, APPROVAL, ERROR, CANCELLED}, RUNNING: {APPROVAL, DONE, ERROR, CANCELLED},
                   APPROVAL: {RUNNING, DONE, ERROR, CANCELLED}}
# task.end status → (step state, code when the task carries no error code)
TASK_OUTCOME = {"success": (DONE, None), "cancelled": (CANCELLED, "cancelled"), "denied": (CANCELLED, "denied"),
                "budget": (ERROR, "budget"), "timeout": (ERROR, "timeout"), "error": (ERROR, "error")}

# Plan runs (Patron Odası P3, `launch_plan`): steps come from the Boss's approved plan, not from a template. An order
# step may wait for the user's approval before it starts (the Test role), so it goes approval → running.
PLAN_TEMPLATE_ID = "patron"
PLAN_STEP_TRANSITIONS = {WAITING: {RUNNING, APPROVAL, CANCELLED}, APPROVAL: {RUNNING, CANCELLED, ERROR},
                         RUNNING: {DONE, ERROR, CANCELLED}}
PLAN_STEP_FIELDS = frozenset({"id", "type", "role", "roleId", "engine", "profile", "model", "prompt", "agent",
                              "agentName", "inputs", "gate", "gateTitle", "gateTimeoutS", "gatePreview", "fresh",
                              "policy", "maxTurns", "final", "prior"})
# G4 (boss.retry_step): an order step may come with the outcome of the run it is resumed from; it starts in that
# terminal state and is not run again (its output still feeds later steps).
PLAN_PRIOR_FIELDS = frozenset({"state", "code", "result", "costUsd", "task"})
PLAN_MAX_PARALLEL = 2
SKIPPED = "skipped"         # step code: an input step did not finish, or no order was done before the final step
INPUT_NOTE = "Girdi %d/%d: %s iş emrinin (%s) çıktısı. Durum: %s."
INPUT_NO_OUTPUT = "Bu iş emri bitmedi; çıktısı yok."

TEMPLATE_FIELDS = frozenset({"id", "name", "description", "folder", "guardrails", "steps"})
GUARD_FIELDS = frozenset({"maxCostUsd", "maxTurns", "timeoutS"})
AGENT_STEP_FIELDS = frozenset({"id", "type", "role", "engine", "engines", "profile", "model", "prompt", "usePrevious",
                               "useBrief"})
APPROVAL_STEP_FIELDS = frozenset({"id", "type", "title", "timeoutS"})
START_FIELDS = frozenset({"type", "id", "template", "workspace", "brief", "maxCostUsd", "steps"})
STEP_OVERRIDE_FIELDS = frozenset({"engine", "model"})

# The first template (A10, user decision 2026-10-26): the architect's engine is selectable (Ollama supported), Claude
# readonly is the default because Ollama does not run on every computer.
BUILTIN_TEMPLATES = [{
    "id": "statik-site",
    "name": "Statik site taslağı",
    "description": "Mimar planlar, Geliştirici küçük bir statik site yazar (HTML/CSS), İnceleme raporlar, son kararı "
                   "siz verirsiniz.",
    "folder": "statik-site",
    "guardrails": {"maxCostUsd": 1.0, "maxTurns": 60, "timeoutS": 1800},
    "steps": [
        {"id": "mimar", "role": "Mimar", "engine": "claude", "engines": ["claude", "ollama"], "profile": "readonly",
         "useBrief": True,
         "prompt": "Sen bir web mimarısın. Aşağıdaki istek için küçük bir statik web sitesinin planını yaz: sayfa "
                   "bölümleri, içerik başlıkları, renkler ve dosya listesi (index.html, style.css, gerekirse "
                   "script.js). Dosya oluşturma; yalnızca planı kısa bir Markdown metni olarak yanıtla. Dış kütüphane, "
                   "CDN ya da sunucu kodu önerme."},
        {"id": "gelistirici", "role": "Geliştirici", "engine": "claude", "profile": "edit", "usePrevious": True,
         "prompt": "Aşağıdaki plana göre bu klasörde küçük bir statik site oluştur: index.html, style.css ve gerekirse "
                   "script.js. Planı plan.md dosyasına da kaydet. Yalnızca bu klasörde çalış; dış kütüphane, CDN, "
                   "satır içi betik ya da sunucu kodu kullanma. Bitince oluşturduğun dosyaları kısaca listele."},
        {"id": "inceleme", "role": "İnceleme", "engine": "claude", "profile": "readonly", "usePrevious": True,
         "prompt": "Bu klasördeki statik site dosyalarını (index.html, style.css, script.js, plan.md) oku ve incele: "
                   "HTML yapısı, erişilebilirlik, mobil uyum, güvenlik (dış kaynak, satır içi betik). Kısa bir "
                   "inceleme raporu yaz: güçlü yanlar, sorunlar, önerilen düzeltmeler. Dosyaları değiştirme."},
        {"id": "onay", "type": "approval", "title": "Taslak kabul edilsin mi?", "timeoutS": DEFAULT_APPROVAL_TIMEOUT_S},
    ],
}]


class WorkflowError(Exception):
    """Error code + short detail. Codes: bad_template, bad_request, too_large, unknown_template, unknown_workflow,
    unknown_approval, not_allowed, busy, engine_unavailable, bad_transition."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def check_transition(table: Dict[str, set], cur: str, new: str) -> None:
    if new not in table.get(cur, ()):
        raise WorkflowError("bad_transition", "%s → %s" % (cur, new))


def new_run_id() -> str:
    return "w_" + secrets.token_urlsafe(8)[:10]


# -- template validation (allow-list) -------------------------------------------------------------------------------

def _hidden_char(ch: str, allow_newlines: bool) -> bool:
    if allow_newlines and ch in "\n\t":
        return False
    return unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp")      # control, bidi/zero-width, line separators


def _text(raw, where: str, max_len: int, allow_newlines: bool = False, max_bytes: Optional[int] = None) -> str:
    if not isinstance(raw, str):
        raise WorkflowError("bad_template", where)
    s = raw.strip()
    if not s or len(s) > max_len or any(_hidden_char(ch, allow_newlines) for ch in s):
        raise WorkflowError("bad_template", where)
    if max_bytes is not None and len(s.encode("utf-8")) > max_bytes:
        raise WorkflowError("bad_template", where)
    return s


def _number(raw, where: str, bounds, integer: bool, code: str = "bad_template"):
    lo, hi = bounds
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise WorkflowError(code, where)
    if integer and not float(raw).is_integer():
        raise WorkflowError(code, where)
    if not lo <= raw <= hi:
        raise WorkflowError(code, where)
    return int(raw) if integer else float(raw)


def _bool(raw, where: str, default: bool) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise WorkflowError("bad_template", where)
    return raw


def _allowed(raw, fields, where: str) -> None:
    if not isinstance(raw, dict) or not set(raw) <= fields:
        raise WorkflowError("bad_template", where)


def _guardrails(raw) -> dict:
    out = dict(DEFAULT_GUARDRAILS)
    if raw is None:
        return out
    _allowed(raw, GUARD_FIELDS, "guardrails")
    if "maxCostUsd" in raw:
        out["maxCostUsd"] = _number(raw["maxCostUsd"], "guardrails.maxCostUsd", COST_BOUNDS, False)
    if "maxTurns" in raw:
        out["maxTurns"] = _number(raw["maxTurns"], "guardrails.maxTurns", TURN_BOUNDS, True)
    if "timeoutS" in raw:
        out["timeoutS"] = _number(raw["timeoutS"], "guardrails.timeoutS", TIMEOUT_BOUNDS, True)
    return out


def _agent_step(raw, where: str, auto_enabled: bool, has_previous: bool) -> dict:
    _allowed(raw, AGENT_STEP_FIELDS, where)
    engine = raw.get("engine")
    if engine not in STEP_ENGINES:
        raise WorkflowError("bad_template", where + ".engine")
    engines = raw.get("engines", [engine])
    if (not isinstance(engines, list) or not engines or len(set(map(repr, engines))) != len(engines)
            or any(e not in STEP_ENGINES for e in engines) or engine not in engines):
        raise WorkflowError("bad_template", where + ".engines")
    profile = raw.get("profile", "readonly")
    if profile not in PROFILES:
        raise WorkflowError("bad_template", where + ".profile")
    if profile == "auto" and not auto_enabled:
        raise WorkflowError("bad_template", where + ".profile: auto is disabled on this device")
    model = raw.get("model")
    if model is not None and (not isinstance(model, str) or not MODEL_RE.match(model)):
        raise WorkflowError("bad_template", where + ".model")
    use_previous = _bool(raw.get("usePrevious"), where + ".usePrevious", False)
    if use_previous and not has_previous:
        raise WorkflowError("bad_template", where + ".usePrevious: no earlier agent step")
    return {"id": raw["id"], "type": "agent", "role": _text(raw.get("role"), where + ".role", ROLE_MAX),
            "engine": engine, "engines": [e for e in STEP_ENGINES if e in engines], "profile": profile,
            "model": model, "prompt": _text(raw.get("prompt"), where + ".prompt", PROMPT_MAX, True, PROMPT_MAX),
            "usePrevious": use_previous, "useBrief": _bool(raw.get("useBrief"), where + ".useBrief", False)}


def _approval_step(raw, where: str) -> dict:
    _allowed(raw, APPROVAL_STEP_FIELDS, where)
    timeout = DEFAULT_APPROVAL_TIMEOUT_S
    if "timeoutS" in raw:
        timeout = _number(raw["timeoutS"], where + ".timeoutS", APPROVAL_TIMEOUT_BOUNDS, True)
    return {"id": raw["id"], "type": "approval", "title": _text(raw.get("title"), where + ".title", TITLE_MAX),
            "timeoutS": timeout}


def validate_template(raw, auto_enabled: bool) -> dict:
    """One template object → normalized copy. Unknown fields, wrong types, oversized texts, more than MAX_STEPS steps,
    unknown profiles and the `auto` profile on a device where it is off are refused (WorkflowError bad_template)."""
    _allowed(raw, TEMPLATE_FIELDS, "template")
    tid = raw.get("id")
    if not isinstance(tid, str) or not SLUG_RE.match(tid):
        raise WorkflowError("bad_template", "id")
    desc = raw.get("description")
    folder = raw.get("folder")
    try:
        folder = validate_sub(folder)
    except WorkspaceDenied:
        raise WorkflowError("bad_template", "folder") from None
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not 1 <= len(steps_raw) <= MAX_STEPS:
        raise WorkflowError("bad_template", "steps")
    steps, seen, agents = [], set(), 0
    for i, s in enumerate(steps_raw):
        where = "steps[%d]" % i
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not SLUG_RE.match(s["id"]) or s["id"] in seen:
            raise WorkflowError("bad_template", where + ".id")
        seen.add(s["id"])
        kind = s.get("type", "agent")
        if kind == "agent":
            steps.append(_agent_step(s, where, auto_enabled, agents > 0))
            agents += 1
        elif kind == "approval":
            steps.append(_approval_step(s, where))
        else:
            raise WorkflowError("bad_template", where + ".type")
    if not agents:
        raise WorkflowError("bad_template", "steps: no agent step")
    return {"id": tid, "name": _text(raw.get("name"), "name", NAME_MAX),
            "description": _text(desc, "description", DESCRIPTION_MAX) if desc is not None else None,
            "folder": folder, "guardrails": _guardrails(raw.get("guardrails")), "steps": steps}


def load_templates(home: str, auto_enabled: bool, log: Callable[[str], None] = lambda s: None) -> Tuple[List[dict], int]:
    """Built-in templates + the user's `<connector dir>/workflows.json` ({"v": 1, "templates": [...]}), read on every
    call so edits need no restart. Invalid user templates are left out (fail closed) and counted. → (templates, invalid)"""
    out = [validate_template(t, auto_enabled) for t in BUILTIN_TEMPLATES]
    ids = {t["id"] for t in out}
    try:
        raw = read_json(os.path.join(home, TEMPLATES_FILE))
    except (OSError, ValueError, UnicodeDecodeError):
        log("workflows.json okunamadı")
        return out, 1
    if raw is None:
        return out, 0
    if not isinstance(raw, dict) or not isinstance(raw.get("templates"), list):
        log("workflows.json geçersiz: {\"v\": 1, \"templates\": [...]} bekleniyor")
        return out, 1
    invalid = 0
    for i, t in enumerate(raw["templates"]):
        try:
            if i >= MAX_TEMPLATES:
                raise WorkflowError("bad_template", "too many templates")
            v = validate_template(t, auto_enabled)
            if v["id"] in ids:
                raise WorkflowError("bad_template", "duplicate id")
        except WorkflowError as e:
            invalid += 1
            log("workflows.json şablon %d geçersiz: %s" % (i, str(e)[:120]))
            continue
        ids.add(v["id"])
        out.append(v)
    return out, invalid


def template_public(t: dict, builtin: bool) -> dict:
    """What a paired browser needs to offer a template (no step instructions)."""
    steps = []
    for s in t["steps"]:
        if s["type"] == "agent":
            steps.append({"id": s["id"], "type": "agent", "role": s["role"], "engine": s["engine"],
                          "engines": list(s["engines"]), "profile": s["profile"], "model": s["model"]})
        else:
            steps.append({"id": s["id"], "type": "approval", "role": s["title"], "timeoutS": s["timeoutS"]})
    return {"id": t["id"], "name": t["name"], "description": t["description"], "folder": t["folder"],
            "builtin": builtin, "limits": dict(t["guardrails"]),
            "needsBrief": any(s["type"] == "agent" and s["useBrief"] for s in t["steps"]), "steps": steps}


# -- workflow.start at the boundary ------------------------------------------------------------------------------

def parse_start(msg: dict, template: dict) -> dict:
    """workflow.start fields (allow-list) for `template` → {"workspace", "brief", "maxCostUsd", "steps"}.
    Raises WorkflowError(bad_request | too_large)."""
    if not set(msg) <= START_FIELDS:
        raise WorkflowError("bad_request", "unknown field")
    ws = msg.get("workspace")
    if not isinstance(ws, dict) or not set(ws) <= {"id", "sub"} or not isinstance(ws.get("id"), str):
        raise WorkflowError("bad_request", "workspace")
    sub = ws.get("sub")
    if sub is not None and not isinstance(sub, str):
        raise WorkflowError("bad_request", "workspace.sub")
    brief = msg.get("brief")
    if brief is None:
        brief = ""
    if not isinstance(brief, str):
        raise WorkflowError("bad_request", "brief")
    if len(brief.encode("utf-8")) > BRIEF_MAX:
        raise WorkflowError("too_large", "brief too large")
    brief = brief.strip()
    if not brief and any(s["type"] == "agent" and s["useBrief"] for s in template["steps"]):
        raise WorkflowError("bad_request", "brief required")
    cap = template["guardrails"]["maxCostUsd"]
    if msg.get("maxCostUsd") is not None:
        # S26-04: the template's value is a ceiling the browser may only lower. Refused, not clamped, so the user never
        # believes a higher cap is in force.
        cap = _number(msg["maxCostUsd"], "maxCostUsd", (COST_BOUNDS[0], cap), False, code="bad_request")
    agent_steps = {s["id"]: s for s in template["steps"] if s["type"] == "agent"}
    raw_steps = msg.get("steps")
    if raw_steps is None:
        raw_steps = {}
    if not isinstance(raw_steps, dict) or not set(raw_steps) <= set(agent_steps):
        raise WorkflowError("bad_request", "steps")
    chosen = {}
    for sid, spec in agent_steps.items():
        o = raw_steps.get(sid) or {}
        if not isinstance(o, dict) or not set(o) <= STEP_OVERRIDE_FIELDS:
            raise WorkflowError("bad_request", "steps." + sid)
        engine = o.get("engine") or spec["engine"]
        if engine not in spec["engines"]:
            raise WorkflowError("bad_request", "steps.%s.engine" % sid)
        model = o.get("model") if "model" in o else None
        if model is None or model == "":
            model = spec["model"] if engine == spec["engine"] else None
        elif not isinstance(model, str) or not MODEL_RE.match(model):
            raise WorkflowError("bad_request", "steps.%s.model" % sid)
        if engine == "ollama" and not model:
            raise WorkflowError("bad_request", "steps.%s.model required" % sid)
        chosen[sid] = {"engine": engine, "model": model}
    return {"workspace": {"id": ws["id"], "sub": sub or None}, "brief": brief, "maxCostUsd": cap, "steps": chosen}


# -- runs ---------------------------------------------------------------------------------------------------------

def _ms(t: Optional[float]) -> Optional[int]:
    return int(t * 1000) if t is not None else None


class StepRun:
    TRANSITIONS = STEP_TRANSITIONS

    def __init__(self, spec: dict, index: int, engine: Optional[str] = None, model: Optional[str] = None) -> None:
        self.spec = spec
        self.index = index
        self.id = spec["id"]
        self.kind = spec["type"]
        self.engine = engine
        self.model = model
        self.agent: Optional[str] = None          # agent key (agent steps)
        self.agent_name: Optional[str] = None
        self.task: Optional[str] = None
        self.state = WAITING
        self.code: Optional[str] = None
        self.cost = 0.0
        self.turns = 0
        self.started: Optional[float] = None
        self.ended: Optional[float] = None
        self.result: Optional[str] = None         # the task's last reply: input of a later step, never sent anywhere
        self.truncated = False
        self.pending = None                       # approvals.Pending of an approval step
        self.cost_cap: Optional[float] = None     # remaining run cost given to a Claude step
        self.expires: Optional[float] = None

    def set_state(self, new: str, code: Optional[str] = None) -> None:
        check_transition(self.TRANSITIONS, self.state, new)
        self.state = new
        if code is not None:
            self.code = code

    def wire(self) -> dict:
        return {"id": self.id, "type": self.kind, "role": self.spec["role"] if self.kind == "agent" else self.spec["title"],
                "engine": self.engine, "model": self.model,
                "profile": self.spec["profile"] if self.kind == "agent" else None, "agent": self.agent,
                "task": self.task, "state": self.state, "code": self.code, "costUsd": round(self.cost, 6),
                "turns": self.turns, "startedAt": _ms(self.started), "endedAt": _ms(self.ended),
                "expiresAt": _ms(self.expires), "truncated": self.truncated}


class WorkflowRun:
    def __init__(self, template: dict, opts: dict, peer, label: str, ws_name: Optional[str], now: float) -> None:
        self.id = new_run_id()
        self.template = template
        self.peer = peer
        self.bid = getattr(peer, "bid", None)
        self.label = label
        self.workspace = {"id": opts["workspace"]["id"], "name": ws_name, "sub": opts["workspace"]["sub"]}
        self.brief = opts["brief"]
        self.limits = {"maxCostUsd": opts["maxCostUsd"], "maxTurns": template["guardrails"]["maxTurns"],
                       "timeoutS": template["guardrails"]["timeoutS"]}
        self.steps = [StepRun(s, i, **(opts["steps"].get(s["id"]) or {})) for i, s in enumerate(template["steps"])]
        self.state = WAITING
        self.code: Optional[str] = None
        self.cost = 0.0
        self.turns = 0
        self.started = now
        self.ended: Optional[float] = None
        self.stop_code: Optional[str] = None      # cancel or timeout requested while a task runs: (state, code)
        self.stop_state: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.state not in TERMINAL

    def set_state(self, new: str, code: Optional[str] = None) -> None:
        check_transition(RUN_TRANSITIONS, self.state, new)
        self.state = new
        if code is not None:
            self.code = code

    def current(self) -> Optional[StepRun]:
        for s in self.steps:
            if s.state in (RUNNING, APPROVAL):
                return s
        return None

    def previous_agent(self, index: int) -> Optional[StepRun]:
        for s in reversed(self.steps[:index]):
            if s.kind == "agent" and s.state == DONE:
                return s
        return None

    def wire(self) -> dict:
        cur = self.current()
        return {"id": self.id, "template": self.template["id"], "name": self.template["name"], "state": self.state,
                "code": self.code, "workspace": dict(self.workspace), "startedAt": _ms(self.started),
                "endedAt": _ms(self.ended), "costUsd": round(self.cost, 6), "turns": self.turns,
                "limits": dict(self.limits), "current": cur.id if cur else None, "steps": [s.wire() for s in self.steps]}


def inputs_prompt(note: str, inputs: List[Tuple[str, Optional[str], str]], limit: int) -> dict:
    """A step instruction followed by several earlier outputs, each in its own random fence and labelled as data
    (`tasks.handoff_prompt`, one call per output). `inputs` = [(label line, output or None when the step did not
    finish, author name)]. The prompt budget is shared equally; each output is cut to its share and to
    HANDOFF_OUTPUT_MAX (the cut is stated after its fence). → {"prompt", "truncated"}; TaskError("too_large")."""
    if not inputs:
        if len(note.encode("utf-8")) > limit:
            raise TaskError("too_large", "step prompt too large")
        return {"prompt": note, "truncated": False}
    sep = "\n\n"
    notes = [label if out is not None else "%s %s" % (label, INPUT_NO_OUTPUT) for label, out, _ in inputs]
    notes[0] = note + sep + notes[0]
    fixed = sum(len(n.encode("utf-8")) for n in notes) + len(sep.encode("utf-8")) * (len(inputs) - 1)
    share = (limit - fixed) // len(inputs)
    if share < 0:
        raise TaskError("too_large", "step prompt too large")
    pieces, truncated = [], False
    for n, (label, out, name) in zip(notes, inputs):
        if out is None:
            pieces.append(n)
            continue
        info = handoff_prompt(n, out, name, len(n.encode("utf-8")) + share)       # TaskError too_large if no room
        truncated = truncated or info["truncated"]
        pieces.append(info["prompt"])
    prompt = sep.join(pieces)
    if len(prompt.encode("utf-8")) > limit:
        raise TaskError("too_large", "step prompt too large")
    return {"prompt": prompt, "truncated": truncated}


class PlanStep(StepRun):
    """A step of a plan run. Its spec (built by connector code, `launch_plan`) carries the agent, the earlier steps
    whose output it gets (`inputs`), whether the user approves it first (`gate`), the session rule (`fresh`), the task
    policy factory (`policy(turns)`) and whether it is the final review step."""

    TRANSITIONS = PLAN_STEP_TRANSITIONS

    def __init__(self, spec: dict, index: int) -> None:
        super().__init__(spec, index, engine=spec["engine"], model=spec["model"])
        self.agent = spec["agent"]
        self.agent_name = spec["agentName"]
        self.turn_cap: Optional[int] = None       # this step's share of the run's remaining turns
        prior = spec.get("prior")
        if prior is not None:
            self.state, self.code, self.result = prior["state"], prior["code"], prior["result"]
            self.cost, self.task = prior["costUsd"], prior["task"]

    def wire(self) -> dict:
        out = super().wire()
        out.update({"roleId": self.spec["roleId"], "after": list(self.spec["inputs"]), "gate": self.spec["gate"],
                    "final": self.spec["final"]})
        return out


class PlanRun(WorkflowRun):
    """A run built from a validated plan (`WorkflowEngine.launch_plan`): up to PLAN_MAX_PARALLEL order steps at a time
    as `ready(finished ids, active ids)` allows, then the optional final step. `on_step(run, step)` is called under the
    engine lock (it must not take other locks); `on_end(run)` is called once, on its own thread, after the run ended."""

    def __init__(self, spec: dict, ws_name: Optional[str], now: float) -> None:
        template = {"id": PLAN_TEMPLATE_ID, "name": spec["name"], "guardrails": dict(spec["limits"]), "steps": []}
        opts = {"workspace": dict(spec["workspace"]), "brief": "", "maxCostUsd": spec["limits"]["maxCostUsd"],
                "steps": {}}
        super().__init__(template, opts, spec["peer"], spec["label"], ws_name, now)
        self.steps = [PlanStep(s, i) for i, s in enumerate(spec["steps"])]
        self.ready: Callable[[List[str], List[str]], List[str]] = spec["ready"]
        self.on_step: Optional[Callable] = spec.get("on_step")
        self.on_end: Optional[Callable] = spec.get("on_end")
        self.to_cancel: List[str] = []            # task ids to cancel once the engine lock is released
        self.cancel_sent: set = set()

    def step(self, sid: str) -> PlanStep:
        return next(s for s in self.steps if s.id == sid)

    def orders(self) -> List[PlanStep]:
        return [s for s in self.steps if not s.spec["final"]]

    def final(self) -> Optional[PlanStep]:
        return next((s for s in self.steps if s.spec["final"]), None)

    def active_steps(self) -> List[PlanStep]:
        return [s for s in self.steps if s.state in (RUNNING, APPROVAL)]


def _check_plan_steps(steps) -> None:
    """launch_plan's input comes from connector code; still refuse anything malformed (fail closed)."""
    if not isinstance(steps, list) or not steps:
        raise WorkflowError("internal", "plan steps")
    seen: List[str] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not set(s) <= PLAN_STEP_FIELDS or not callable(s.get("policy")):
            raise WorkflowError("internal", "plan step %d" % i)
        if s["id"] in seen or any(x not in seen for x in s["inputs"]) or (s["final"] and i != len(steps) - 1):
            raise WorkflowError("internal", "plan step %d" % i)
        prior = s.get("prior")
        if prior is not None and (s["final"] or not isinstance(prior, dict) or set(prior) != PLAN_PRIOR_FIELDS
                                  or prior["state"] not in TERMINAL
                                  or (prior["state"] == DONE) != isinstance(prior["result"], str)):
            raise WorkflowError("internal", "plan step %d prior" % i)
        seen.append(s["id"])
    orders = [s for s in steps if not s["final"]]
    if orders and all(s.get("prior") is not None for s in orders):
        raise WorkflowError("internal", "nothing to run")


class WorkflowEngine:
    """Runs workflows for the Connector (`app`): scheduler, agents, workspaces, approvals, audit, office, broadcast."""

    def __init__(self, app, clock: Callable[[], float] = time.time) -> None:
        self.app = app
        self.clock = clock
        self._lock = threading.RLock()
        self.runs: Dict[str, WorkflowRun] = {}

    # -- templates --------------------------------------------------------------------------------------------------
    def templates(self) -> Tuple[List[dict], int]:
        return load_templates(self.app.home, self.app.cfg["profiles"]["auto"]["enabled"], self.app.log)

    def template(self, tid) -> dict:
        if not isinstance(tid, str) or not SLUG_RE.match(tid):
            raise WorkflowError("bad_request", "template")
        for t in self.templates()[0]:
            if t["id"] == tid:
                return t
        raise WorkflowError("unknown_template", "unknown template")

    def list_msg(self) -> dict:
        tpls, invalid = self.templates()
        builtin = {t["id"] for t in BUILTIN_TEMPLATES}
        with self._lock:
            runs = [r.wire() for r in self.runs.values()]
        return {"type": "workflow.list.result", "templates": [template_public(t, t["id"] in builtin) for t in tpls],
                "invalid": invalid, "runs": runs}

    def get(self, run_id) -> dict:
        with self._lock:
            return self._run(run_id).wire()

    def _run(self, run_id) -> WorkflowRun:
        run = self.runs.get(run_id) if isinstance(run_id, str) and RUN_ID_RE.match(run_id) else None
        if run is None:
            raise WorkflowError("unknown_workflow", "unknown workflow")
        return run

    # -- start --------------------------------------------------------------------------------------------------------
    def prepare(self, peer, msg: dict) -> dict:
        """Validate a workflow.start request without side effects. → plan for launch(). Raises WorkflowError,
        WorkspaceDenied."""
        if not self.app.cfg["remote"]["agentCreate"]:
            raise WorkflowError("not_allowed", "remote agent creation disabled")      # workflows create step agents
        self._check_boss_free()
        tpl = self.template(msg.get("template"))
        opts = parse_start(msg, tpl)
        ws = self.app.workspaces.get(opts["workspace"]["id"])          # WorkspaceDenied → workspace_denied
        if opts["workspace"]["sub"] is not None:
            validate_sub(opts["workspace"]["sub"])
            if not ws.get("children"):
                raise WorkspaceDenied("sub folders not allowed here")
        self.app.workspaces.resolve(ws["id"])
        for sid, o in opts["steps"].items():
            self._check_engine(o["engine"])
        return {"template": tpl, "opts": opts, "peer": peer, "label": getattr(peer, "label", None) or "yerel"}

    def _check_engine(self, engine: str) -> None:
        cfg = self.app.cfg["engines"]
        if engine == "claude" and not (cfg["claude"]["enabled"] and self.app._ensure_claude_path()):
            raise WorkflowError("engine_unavailable", "Claude Code not found")
        if engine == "ollama" and not cfg["ollama"]["enabled"]:
            raise WorkflowError("engine_unavailable", "Ollama disabled")

    def start(self, peer, msg: dict) -> dict:
        return self.launch(self.prepare(peer, msg))

    def launch(self, plan: dict) -> dict:
        """Create the sub-folder, reserve/create the step agents and start the first step. → workflow.started."""
        tpl, opts = plan["template"], plan["opts"]
        with self._lock:
            if sum(1 for r in self.runs.values() if r.active) >= MAX_ACTIVE_RUNS:
                raise WorkflowError("busy", "a workflow is already running")
            self._check_boss_free()
            wid, sub = opts["workspace"]["id"], opts["workspace"]["sub"]
            try:
                self.app.workspaces.ensure_sub(wid, sub)                # escape, symlink, deny list, …
            except WorkspaceDenied as e:
                self.app.audit.write("denied", bid=getattr(plan["peer"], "bid", None), reason="workspace: %s" % e)
                raise WorkflowError("workspace_denied", str(e)) from None
            ws_name = self.app.workspaces.get(wid)["name"]
            run = WorkflowRun(tpl, opts, plan["peer"], plan["label"], ws_name, self.clock())
            self._reserve_agents(run)
            self.runs[run.id] = run
            self._trim()
            self.app.audit.write("workflow.start", bid=run.bid, run=run.id, template=tpl["id"], ws=wid, sub=sub,
                                 steps=len(run.steps), maxCostUsd=run.limits["maxCostUsd"],
                                 maxTurns=run.limits["maxTurns"], timeoutS=run.limits["timeoutS"],
                                 briefLen=len(run.brief.encode("utf-8")))
            self._advance(run)
            self._emit(run)
            return {"type": "workflow.started", "run": run.wire()}

    def _check_boss_free(self) -> None:
        """Patron Odası: no template run while a Boss job is active (planning, waiting for approval, running). Read
        without the desk's lock (`BossDesk.busy`), so the two locks are never taken in the opposite order."""
        boss = getattr(self.app, "boss", None)
        if boss is not None and boss.busy():
            raise WorkflowError("busy", "a Boss job is active")

    def launch_plan(self, spec: dict) -> PlanRun:
        """Patron Odası P3: start a run from steps built by connector code (`boss.BossDesk`), never from a message.
        `spec`: name, peer, label, workspace {id, sub} (an existing folder), limits {maxCostUsd, maxTurns, timeoutS},
        steps (PLAN_STEP_FIELDS; every step has a task policy factory, the final review step is last), ready(finished,
        active) → step ids that may start now, on_step, on_end. The step agents are the caller's. → the run."""
        _check_plan_steps(spec.get("steps"))
        with self._lock:
            if sum(1 for r in self.runs.values() if r.active) >= MAX_ACTIVE_RUNS:
                raise WorkflowError("busy", "a workflow is already running")
            ws = spec["workspace"]
            try:
                self.app.workspaces.resolve(ws["id"], ws["sub"])
                ws_name = self.app.workspaces.get(ws["id"])["name"]
            except WorkspaceDenied as e:
                raise WorkflowError("workspace_denied", str(e)) from None
            run = PlanRun(spec, ws_name, self.clock())
            self.runs[run.id] = run
            self._trim()
            self.app.audit.write("workflow.start", bid=run.bid, run=run.id, template=PLAN_TEMPLATE_ID, ws=ws["id"],
                                 sub=ws["sub"], steps=len(run.steps), maxCostUsd=run.limits["maxCostUsd"],
                                 maxTurns=run.limits["maxTurns"], timeoutS=run.limits["timeoutS"], briefLen=0)
            self._advance(run)
            self._emit(run)
        self._flush_cancels(run)
        return run

    def _reserve_agents(self, run: WorkflowRun) -> None:
        """One agent per (template, step, engine), reused across runs (tag in agents.json) and set to this run's folder,
        model and profile. All checks first (busy, agent limit), then the writes."""
        agents = self.app.agents
        plan = []
        for st in run.steps:
            if st.kind != "agent":
                continue
            tag = "%s/%s/%s" % (run.template["id"], st.id, st.engine)
            cur = next((a for a in agents.list() if a.get("workflow") == tag), None)
            if cur is not None and self.app.scheduler.active_for(cur["key"]) is not None:
                raise WorkflowError("busy", "step agent %s is busy" % cur["key"])
            plan.append((st, tag, cur))
        new = sum(1 for _, _, cur in plan if cur is None)
        if new and len(agents.list()) + new > self.app.cfg["limits"]["maxAgents"]:
            raise WorkflowError("not_allowed", "too many agents")
        ws = {"id": run.workspace["id"], "sub": run.workspace["sub"]}
        for st, tag, cur in plan:
            profile = st.spec["profile"] if st.engine == "claude" else "edit"
            if cur is None:
                a = agents.create(st.spec["role"], st.engine, st.model, ws, profile, None)
                a = dict(a, workflow=tag)
                agents.put(a)
                self.app.audit.write("agent.create", bid=run.bid, agent=a["key"], engine=st.engine, profile=profile,
                                     ws=ws["id"], workflow=run.id)
            else:
                a = dict(cur, model=st.model, profile=profile, workspace=dict(ws))
                agents.put(a)
            st.agent, st.agent_name = a["key"], a["name"]
            self.app.office(a, "idle", None, "Hazır", None)

    def _trim(self) -> None:
        done = [r for r in self.runs.values() if not r.active]
        for r in done[:max(0, len(done) - KEEP_FINISHED)]:
            del self.runs[r.id]

    # -- the state machine ------------------------------------------------------------------------------------------
    def _advance(self, run: WorkflowRun) -> None:
        """Start the next waiting step, or end the run. Caller holds the lock."""
        if isinstance(run, PlanRun):
            self._advance_plan(run)
            return
        if not run.active:
            return
        if run.stop_state is not None:
            self._finish(run, run.stop_state, run.stop_code)
            return
        if run.bid is not None and self.app.peers.active(run.bid) is None:
            self._finish(run, CANCELLED, "revoked")         # S26-02: a revoked browser starts nothing new
            return
        nxt = next((s for s in run.steps if s.state == WAITING), None)
        if nxt is None:
            self._finish(run, DONE, None)
            return
        if self.clock() - run.started >= run.limits["timeoutS"]:
            self._finish(run, ERROR, "timeout")
            return
        if nxt.kind == "approval":
            self._start_approval(run, nxt)
            return
        if run.turns >= run.limits["maxTurns"]:
            self._finish(run, ERROR, "max_turns")
            return
        remaining = run.limits["maxCostUsd"] - run.cost
        if remaining <= 0 or (nxt.engine == "claude" and remaining < MIN_CLAUDE_BUDGET):
            self._finish(run, ERROR, "cost_cap")
            return
        self._start_agent(run, nxt, remaining if nxt.engine == "claude" else None)

    def _step_prompt(self, run: WorkflowRun, st: StepRun) -> str:
        spec = st.spec
        note = spec["prompt"]
        if spec["useBrief"]:
            note = "%s\n\n%s\n%s" % (note, BRIEF_HEAD, run.brief)
        limit = self.app.cfg["limits"]["maxPromptBytes"]
        prev = run.previous_agent(st.index) if spec["usePrevious"] else None
        if prev is not None:
            info = handoff_prompt(note, prev.result, prev.agent_name or prev.spec["role"], limit)   # fenced as data
            st.truncated = info["truncated"]
            return info["prompt"]
        if len(note.encode("utf-8")) > limit:
            raise TaskError("too_large", "step prompt too large")
        return note

    def _start_agent(self, run: WorkflowRun, st: StepRun, cost_cap: Optional[float]) -> None:
        st.set_state(RUNNING)
        st.started = self.clock()
        st.cost_cap = cost_cap
        if run.state != RUNNING:
            run.set_state(RUNNING)
        try:
            agent = self.app.agents.get(st.agent)                      # deleted meanwhile → unknown_agent
            prompt = self._step_prompt(run, st)
            title = "İş akışı: %s · %s" % (run.template["name"][:30], st.spec["role"])
            ack = self.app.scheduler.submit(
                agent, prompt, True, run.peer, run.label, self.app._need_confirm(agent, True), confirm_title=title,
                on_end=lambda msg, r=run, s=st: self._task_ended(r, s, msg), cost_cap=cost_cap)
        except (TaskError, AgentError) as e:
            self._step_failed(run, st, e.code)
            return
        except Exception as e:
            self.app.log("workflow step error %s: %s" % (run.id, type(e).__name__))
            self._step_failed(run, st, "internal")
            return
        st.task = ack["task"]
        self.app.audit.write("workflow.step", run=run.id, step=st.id, index=st.index, state=RUNNING, agent=st.agent,
                             task=st.task, engine=st.engine, promptLen=len(prompt.encode("utf-8")),
                             truncated=st.truncated, costCap=cost_cap)

    def _step_failed(self, run: WorkflowRun, st: StepRun, code: str) -> None:
        st.set_state(ERROR, code)
        st.ended = self.clock()
        self.app.audit.write("workflow.step", run=run.id, step=st.id, index=st.index, state=ERROR, agent=st.agent,
                             code=code)
        self._finish(run, ERROR, code)

    def _task_ended(self, run: WorkflowRun, st: StepRun, msg: dict) -> None:
        """on_end of a step's task (engine thread, or synchronously inside submit when the start fails)."""
        with self._lock:
            if st.state != RUNNING:
                return
            st.task = msg.get("task") or st.task
            cost = msg.get("costUsd")
            st.cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0 else 0.0
            turns = msg.get("turns")
            st.turns = int(turns) if isinstance(turns, int) and not isinstance(turns, bool) and turns > 0 else 0
            run.cost += st.cost
            run.turns += st.turns
            st.result = msg.get("result") if isinstance(msg.get("result"), str) else None
            state, code = TASK_OUTCOME.get(msg.get("status"), (ERROR, "error"))
            err = (msg.get("error") or {}).get("code") if isinstance(msg.get("error"), dict) else None
            # A user's cancel carries no error; a connector cancel does (S26-08 `revoked`) and the run keeps that code.
            if state != DONE and err and msg.get("status") in ("error", "denied", "cancelled"):
                code = err
            if msg.get("status") == "budget" and st.cost_cap is not None and st.cost >= st.cost_cap - 1e-9:
                code = "cost_cap"                     # Claude stopped at the run's remaining cost (--max-budget-usd)
            st.set_state(state, code)
            st.ended = self.clock()
            self.app.audit.write("workflow.step", run=run.id, step=st.id, index=st.index, state=state, agent=st.agent,
                                 task=st.task, code=code, costUsd=round(st.cost, 6), turns=st.turns,
                                 outLen=len(st.result.encode("utf-8")) if st.result else 0)
            if isinstance(run, PlanRun):
                self._plan_task_ended(run, st, state, code)
            elif run.stop_state is not None:
                self._finish(run, run.stop_state, run.stop_code)
            elif state != DONE:
                self._finish(run, state, code)
            elif run.cost > run.limits["maxCostUsd"] + 1e-9:
                self._finish(run, ERROR, "cost_cap")
            elif run.turns > run.limits["maxTurns"]:
                self._finish(run, ERROR, "max_turns")
            else:
                self._advance(run)
            self._emit(run)
        if isinstance(run, PlanRun):
            self._flush_cancels(run)

    def _start_approval(self, run: WorkflowRun, st: StepRun) -> None:
        st.set_state(APPROVAL)
        st.started = self.clock()
        run.set_state(APPROVAL)
        left = run.limits["timeoutS"] - (st.started - run.started)
        timeout = max(1.0, min(float(st.spec["timeoutS"]), left))
        prev = run.previous_agent(st.index)
        preview = prev.result[:300] if prev is not None and prev.result else None
        title = "%s: %s" % (run.template["name"][:30], st.spec["title"])
        st.pending = self.app.approvals.request("workflow", title[:80], preview, prev.agent_name if prev else None,
                                                lambda res, r=run, s=st: self._approval_done(r, s, res),
                                                timeout_s=timeout)
        st.expires = st.pending.expires
        self.app.audit.write("workflow.step", run=run.id, step=st.id, index=st.index, state=APPROVAL,
                             approval=st.pending.id, timeoutS=int(timeout))
        if prev is not None:
            self._office_if_free(prev.agent, "waiting_for_user", "Onay bekliyor")

    def _approval_done(self, run: WorkflowRun, st: StepRun, result: str) -> None:
        with self._lock:
            if st.state != APPROVAL:
                return
            by = st.pending.by if st.pending is not None else None
            st.pending = None
            st.ended = self.clock()
            self.app.audit.write("workflow.approval", run=run.id, step=st.id, result=result, by=by)
            prev = run.previous_agent(st.index)
            if result == "ok":
                st.set_state(DONE)
                if prev is not None:
                    self._office_if_free(prev.agent, "idle", "Onaylandı")
                self._advance(run)
            else:
                code = "denied" if result == "denied" else "approval_timeout"
                st.set_state(CANCELLED, code)
                if prev is not None:
                    self._office_if_free(prev.agent, "idle", "Reddedildi")
                self._finish(run, CANCELLED, code)
            self._emit(run)

    # -- plan runs (Patron Odası P3) ----------------------------------------------------------------------------------
    def _advance_plan(self, run: PlanRun) -> None:
        """Start what may start now: orders as `run.ready` allows (at most PLAN_MAX_PARALLEL running or waiting for
        their approval), then the final step once every order ended and at least one is done. Caller holds the lock."""
        if not run.active:
            return
        if run.stop_state is not None:
            if not any(s.state == RUNNING for s in run.steps):
                self._finish(run, run.stop_state, run.stop_code)
            return
        if not self._plan_guard(run):
            return
        self._skip_blocked(run)
        active = run.active_steps()
        final = run.final()
        if all(s.state in TERMINAL for s in run.orders()):
            batch = []
            if final is not None and final.state == WAITING:
                if any(s.state == DONE for s in run.orders()):
                    batch = [final]
                else:
                    self._skip(run, final)
            if not batch:
                if not active:
                    self._finish(run, DONE, None)
                return
        else:
            finished = [s.id for s in run.orders() if s.state in TERMINAL]
            ids = run.ready(finished, [s.id for s in active])[:max(0, PLAN_MAX_PARALLEL - len(active))]
            batch = [run.step(i) for i in ids if run.step(i).state == WAITING]
            if not batch:
                if not active:                    # nothing can start and nothing runs: never wait forever
                    self._halt(run, ERROR, "internal")
                return
        if run.turns >= run.limits["maxTurns"]:
            self._halt(run, ERROR, "max_turns")
            return
        # Steps that may run at the same time share what is left: reserved caps of running (or approval-waiting) steps
        # are kept, the rest is split evenly, so all of them together stay under the run's cap.
        free_cost = run.limits["maxCostUsd"] - run.cost - sum(s.cost_cap or 0.0 for s in active)
        free_turns = run.limits["maxTurns"] - run.turns - sum(s.turn_cap or 0 for s in active)
        cost_each = free_cost / len(batch)
        turns_each = free_turns // len(batch)
        if cost_each < MIN_CLAUDE_BUDGET:
            self._halt(run, ERROR, "cost_cap")
            return
        if turns_each < 1:
            self._halt(run, ERROR, "max_turns")
            return
        for st in batch:
            st.cost_cap = cost_each
            st.turn_cap = min(int(st.spec["maxTurns"]), int(turns_each))
            if st.spec["gate"]:
                self._open_gate(run, st)
            else:
                self._start_plan_agent(run, st)
            if run.stop_state is not None:
                return

    def _plan_guard(self, run: PlanRun) -> bool:
        """False (and the run is stopping) when the browser was unpaired or the run's time is up."""
        if run.bid is not None and self.app.peers.active(run.bid) is None:
            self._halt(run, CANCELLED, "revoked")             # S26-02: a revoked browser starts nothing new
            return False
        if self.clock() - run.started >= run.limits["timeoutS"]:
            self._halt(run, ERROR, "timeout")
            return False
        return True

    def _skip(self, run: PlanRun, st: PlanStep) -> None:
        st.set_state(CANCELLED, SKIPPED)
        st.ended = self.clock()
        self.app.audit.write("workflow.step", run=run.id, step=st.id, index=st.index, state=CANCELLED, code=SKIPPED)
        self._notify_step(run, st)

    def _skip_blocked(self, run: PlanRun) -> None:
        """An order whose input order ended without being done is skipped (written order, so chains follow)."""
        for st in run.orders():
            if st.state == WAITING and any(run.step(i).state in (CANCELLED, ERROR) for i in st.spec["inputs"]):
                self._skip(run, st)

    def _open_gate(self, run: PlanRun, st: PlanStep) -> None:
        """The user approves this order before it starts (Test role): a local approval item, answerable on the computer
        or with `workflow.answer` from a paired browser. Unanswered within the time = denied (fail closed)."""
        st.set_state(APPROVAL)
        st.started = self.clock()
        self._sync_run_state(run)
        left = run.limits["timeoutS"] - (st.started - run.started)
        timeout = max(1.0, min(float(st.spec["gateTimeoutS"]), left))
        st.pending = self.app.approvals.request("workflow", st.spec["gateTitle"][:80], st.spec.get("gatePreview"),
                                                st.agent_name,
                                                lambda res, r=run, s=st: self._gate_done(r, s, res), timeout_s=timeout)
        st.expires = st.pending.expires
        self.app.audit.write("workflow.step", run=run.id, step=st.id, index=st.index, state=APPROVAL,
                             approval=st.pending.id, agent=st.agent, timeoutS=int(timeout))
        self._office_if_free(st.agent, "waiting_for_user", "Onay bekliyor")
        self._notify_step(run, st)

    def _gate_done(self, run: PlanRun, st: PlanStep, result: str) -> None:
        with self._lock:
            if st.state != APPROVAL:
                return
            by = st.pending.by if st.pending is not None else None
            st.pending = None
            self.app.audit.write("workflow.approval", run=run.id, step=st.id, result=result, by=by)
            if result == "ok":
                if self._plan_guard(run):
                    self._start_plan_agent(run, st)
            else:
                st.set_state(CANCELLED, "denied" if result == "denied" else "approval_timeout")
                st.ended = self.clock()
                self._office_if_free(st.agent, "idle", "Reddedildi")
                self._notify_step(run, st)
                self._advance_plan(run)
            self._sync_run_state(run)
            self._emit(run)
        self._flush_cancels(run)

    def _start_plan_agent(self, run: PlanRun, st: PlanStep) -> None:
        st.set_state(RUNNING)
        st.started = self.clock()
        if run.state != RUNNING:
            run.set_state(RUNNING)
        try:
            agent = self.app.agents.get(st.agent)                      # deleted meanwhile → unknown_agent
            limit = self.app.cfg["limits"]["maxPromptBytes"]
            inputs = []
            for i, sid in enumerate(st.spec["inputs"]):
                src = run.step(sid)
                label = INPUT_NOTE % (i + 1, len(st.spec["inputs"]), src.id, src.spec["role"],
                                      STATE_LABELS[src.state] + (" (%s)" % src.code if src.code else ""))
                inputs.append((label, (src.result or "") if src.state == DONE else None,
                               src.agent_name or src.spec["role"]))
            info = inputs_prompt(st.spec["prompt"], inputs, limit)
            st.truncated = info["truncated"]
            policy = st.spec["policy"](st.turn_cap)                    # connector code only (boss.task_policy)
            ack = self.app.scheduler.submit(
                agent, info["prompt"], st.spec["fresh"], run.peer, run.label,
                self.app._need_confirm(agent, st.spec["fresh"]), confirm_title="Patron: %s" % st.spec["role"],
                on_end=lambda msg, r=run, s=st: self._task_ended(r, s, msg), cost_cap=st.cost_cap, policy=policy)
        except (TaskError, AgentError, WorkflowError) as e:
            self._plan_step_failed(run, st, e.code)
            return
        except Exception as e:
            self.app.log("plan step error %s: %s" % (run.id, type(e).__name__))
            self._plan_step_failed(run, st, "internal")
            return
        if st.state != RUNNING:                   # the task already ended inside submit (engine failed to start)
            return
        st.task = ack["task"]
        self.app.audit.write("workflow.step", run=run.id, step=st.id, index=st.index, state=RUNNING, agent=st.agent,
                             task=st.task, engine=st.engine, promptLen=len(info["prompt"].encode("utf-8")),
                             truncated=st.truncated, costCap=round(st.cost_cap, 6), turnCap=st.turn_cap)
        self._notify_step(run, st)

    def _plan_step_failed(self, run: PlanRun, st: PlanStep, code: str) -> None:
        st.set_state(ERROR, code)
        st.ended = self.clock()
        self.app.audit.write("workflow.step", run=run.id, step=st.id, index=st.index, state=ERROR, agent=st.agent,
                             code=code)
        self._notify_step(run, st)
        self._halt(run, ERROR, code)

    def _plan_task_ended(self, run: PlanRun, st: PlanStep, state: str, code: Optional[str]) -> None:
        """A step's task ended (caller holds the lock). A failed step stops the whole run: the other running order is
        cancelled and nothing new starts (§2/9: the next order never starts, the running work is stopped)."""
        self._notify_step(run, st)
        if run.stop_state is not None:
            self._advance_plan(run)               # ends the run once nothing runs any more
        elif state != DONE:
            self._halt(run, state, code)
        elif run.cost > run.limits["maxCostUsd"] + 1e-9:
            self._halt(run, ERROR, "cost_cap")
        elif run.turns > run.limits["maxTurns"]:
            self._halt(run, ERROR, "max_turns")
        else:
            self._advance_plan(run)
        self._sync_run_state(run)

    def _halt(self, run: PlanRun, state: str, code: Optional[str]) -> None:
        """Stop a plan run (caller holds the lock): approvals still waiting are withdrawn, running tasks are queued for
        cancelling (`_flush_cancels`, after the lock), the run ends when the last running task ended. The first stop's
        state and code are kept."""
        if not run.active:
            return
        if run.stop_state is None:
            run.stop_state, run.stop_code = state, code
        for s in run.steps:
            if s.state == APPROVAL:
                if s.pending is not None:
                    self.app.approvals.cancel(s.pending.id)
                    s.pending = None
                s.set_state(CANCELLED if run.stop_state == CANCELLED else ERROR, run.stop_code)
                s.ended = self.clock()
                self._office_if_free(s.agent, "idle", STATE_LABELS[run.stop_state])
                self._notify_step(run, s)
        running = [s for s in run.steps if s.state == RUNNING]
        if not running:
            self._finish(run, run.stop_state, run.stop_code)
            return
        for s in running:
            if s.task is not None and s.task not in run.cancel_sent:
                run.cancel_sent.add(s.task)
                run.to_cancel.append(s.task)

    def _sync_run_state(self, run: PlanRun) -> None:
        """The run shows `approval` while its only active steps wait for the user, else `running`."""
        if not run.active:
            return
        if any(s.state == RUNNING for s in run.steps):
            want = RUNNING
        elif any(s.state == APPROVAL for s in run.steps):
            want = APPROVAL
        else:
            return
        if run.state != want:
            run.set_state(want)

    def _flush_cancels(self, run: PlanRun) -> None:
        with self._lock:
            tasks, run.to_cancel = run.to_cancel, []
        for t in tasks:
            self.app.scheduler.cancel(t)

    def _notify_step(self, run: PlanRun, st: PlanStep) -> None:
        if run.on_step is None:
            return
        try:
            run.on_step(run, st)
        except Exception as e:
            self.app.log("plan step callback error %s: %s" % (run.id, type(e).__name__))

    def _office_if_free(self, key: Optional[str], state: str, label: str) -> None:
        if key is None or self.app.scheduler.active_for(key) is not None:
            return
        try:
            self.app.office(self.app.agents.get(key), state, None, label, None)
        except AgentError:
            pass

    def _finish(self, run: WorkflowRun, state: str, code: Optional[str]) -> None:
        """End the run. Waiting steps become cancelled; a pending approval is withdrawn. Caller holds the lock."""
        if not run.active:
            return
        for s in run.steps:
            if s.state == WAITING:
                s.set_state(CANCELLED)
            elif s.state == APPROVAL:
                if s.pending is not None:
                    self.app.approvals.cancel(s.pending.id)
                    s.pending = None
                s.set_state(CANCELLED if state == CANCELLED else ERROR, code)
                s.ended = self.clock()
                who = s if isinstance(run, PlanRun) else (run.previous_agent(s.index) or s)
                self._office_if_free(who.agent, "idle", STATE_LABELS[state])
        run.set_state(state, code)
        run.ended = self.clock()
        self.app.audit.write("workflow.end", bid=run.bid, run=run.id, state=state, code=code,
                             costUsd=round(run.cost, 6), turns=run.turns,
                             durationMs=int((run.ended - run.started) * 1000))
        if isinstance(run, PlanRun) and run.on_end is not None:
            # Its own thread: the caller holds this engine's lock and on_end takes the Boss desk's.
            threading.Thread(target=self._call_end, args=(run,), daemon=True, name="plan-end").start()

    def _call_end(self, run: PlanRun) -> None:
        try:
            run.on_end(run)
        except Exception as e:
            self.app.log("plan run end error %s: %s" % (run.id, type(e).__name__))

    def _emit(self, run: WorkflowRun) -> None:
        self.app.broadcast({"type": "workflow.state", "run": run.wire()})

    # -- requests from outside ------------------------------------------------------------------------------------
    def cancel(self, run_id, by: Optional[str] = None) -> dict:
        return self._stop_run(run_id, CANCELLED, "cancelled", by)

    def _stop_run(self, run_id, state: str, code: str, by: Optional[str]) -> dict:
        """Stop a run: a pending approval or a waiting run ends at once; a running task is cancelled and the run ends
        when its task.end arrives (on_end)."""
        task = None
        with self._lock:
            run = self._run(run_id)
            if not run.active:
                raise WorkflowError("unknown_workflow", "workflow already finished")
            if run.stop_state is None:
                run.stop_state, run.stop_code = state, code
                self.app.audit.write("workflow.cancel" if state == CANCELLED else "workflow.timeout", run=run.id,
                                     by=by)
            if isinstance(run, PlanRun):
                self._halt(run, state, code)        # every running task is cancelled; ends when the last one ended
                self._emit(run)
            else:
                cur = run.current()
                if cur is not None and cur.kind == "agent" and cur.task is not None:
                    task = cur.task
                else:
                    if cur is not None and cur.kind == "agent":            # never submitted: nothing to wait for
                        cur.set_state(state, code)
                        cur.ended = self.clock()
                    self._finish(run, state, code)
                    self._emit(run)
        if isinstance(run, PlanRun):
            self._flush_cancels(run)
        if task is not None:
            self.app.scheduler.cancel(task)
        return {"type": "workflow.ok", "run": run_id}

    def stop_run(self, run_id, code: str, by: Optional[str]) -> None:
        """Cancel a run with the connector's own code (Patron Odası: the Boss's job was cancelled, revoked or stopped).
        A run that already ended is left alone."""
        try:
            self._stop_run(run_id, CANCELLED, code, by)
        except WorkflowError:
            pass

    def answer(self, run_id, approve: bool, by: Optional[str], step: Optional[str] = None) -> dict:
        """Answer the run's pending approval: a template's checkpoint, or the approval a plan step waits for before it
        starts (`step` picks one when two wait; without it the first in written order)."""
        with self._lock:
            run = self._run(run_id)
            cur = next((s for s in run.steps if s.state == APPROVAL and s.pending is not None
                        and (step is None or s.id == step)), None)
            if cur is None:
                raise WorkflowError("unknown_approval", "no approval pending")
            pid = cur.pending.id
        if self.app.approvals.answer(pid, bool(approve), None, by=by or "local") != "ok":
            raise WorkflowError("unknown_approval", "already answered or expired")
        return {"type": "workflow.ok", "run": run_id}

    def peer_revoked(self, bid: str) -> None:
        """S26-02: `peer.revoke` ends the active runs that browser started (a running step's task is cancelled)."""
        with self._lock:
            ids = [r.id for r in self.runs.values() if r.active and r.bid == bid]
        for rid in ids:
            try:
                self._stop_run(rid, CANCELLED, "revoked", "revoke")
            except WorkflowError:
                pass

    def tick(self) -> None:
        """Total wall time: an overdue run is stopped with error `timeout` (its running task is cancelled)."""
        now = self.clock()
        with self._lock:
            overdue = [r.id for r in self.runs.values()
                       if r.active and r.stop_state is None and now - r.started > r.limits["timeoutS"]]
        for rid in overdue:
            try:
                self._stop_run(rid, ERROR, "timeout", "timer")
            except WorkflowError:
                pass

    def stop(self) -> None:
        """Connector shutdown: every active run ends (tasks are cancelled by the scheduler)."""
        with self._lock:
            ids = [r.id for r in self.runs.values() if r.active]
        for rid in ids:
            try:
                self._stop_run(rid, CANCELLED, "stopped", "service")
            except WorkflowError:
                pass


__all__ = ["WorkflowEngine", "WorkflowError", "validate_template", "load_templates", "parse_start", "template_public",
           "check_transition", "BUILTIN_TEMPLATES", "STEP_TRANSITIONS", "RUN_TRANSITIONS", "MAX_STEPS", "TEMPLATES_FILE"]
