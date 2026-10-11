"""Scripted fake agents for --demo mode (ARCHITECTURE.md §7.5).

All behaviour is driven by `step(now)` so it can be unit-tested with a fake clock; the
thread just calls it every `tick` seconds with `time.time()`.
"""

import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass

from .config import NAMES
from .models import AgentInfo, State, color_seed_for
from .store import AgentStore

log = logging.getLogger("pixel_office")

PROJECTS = ["pixel-office", "web-shop", "ml-pipeline", "docs-site", "api-gateway"]
SPAWN_STAGGER = 2.0         # s between the initial agents
RESPAWN_DELAY = 3.0         # s after an agent leaves until its replacement enters
CYCLES_PER_LIFE = 3
SUB_REMOVE_AFTER = 15.0     # s a finished demo subagent keeps sleeping before it leaves

TITLES = {
    "pixel-office": "Pixel ofis sprite'ları ve oyun döngüsü",
    "web-shop": "Checkout flow refactor",
    "ml-pipeline": "Eğitim pipeline'ında veri sızıntısı",
    "docs-site": "Docs site search + dark mode",
    "api-gateway": "Rate limiter for the API gateway",
}
PROMPTS = {
    "pixel-office": "Karakterler duvarların içinden geçiyor, A* yol bulmayı düzeltir misin?",
    "web-shop": "The checkout page double-charges on refresh. Please find and fix the bug.",
    "ml-pipeline": "Validation skoru şüpheli derecede yüksek, veri sızıntısı var mı kontrol et.",
    "docs-site": "Add full-text search to the docs site and make it work offline.",
    "api-gateway": "Implement a token-bucket rate limiter per API key with Redis fallback.",
}
FILES = {
    "pixel-office": ["office.js", "pathfinding.js", "agents.js", "renderer.js", "sprites.js", "main.js"],
    "web-shop": ["checkout.py", "cart.py", "payments.py", "test_checkout.py", "views.py"],
    "ml-pipeline": ["train.py", "dataset.py", "features.py", "config.yaml", "eval.ipynb"],
    "docs-site": ["search.ts", "index.md", "theme.css", "build.mjs", "sidebar.tsx"],
    "api-gateway": ["limiter.go", "middleware.go", "redis.go", "limiter_test.go", "main.go"],
}
BASH = {
    "pixel-office": ["Run unit tests", "python3 -m http.server 8000", "git status", "Validate sprite maps"],
    "web-shop": ["pytest -q", "Run checkout tests", "git diff --stat", "npm run build"],
    "ml-pipeline": ["python train.py --epochs 1", "pytest tests/ -q", "Check GPU memory", "dvc status"],
    "docs-site": ["npm run dev", "npm test", "Build the static site", "git log --oneline -5"],
    "api-gateway": ["go test ./...", "go vet ./...", "Benchmark limiter", "docker compose up -d redis"],
}
GREPS = ["TODO", "def test_", "rate_limit", "import .*", "FIXME", "class .*Error", "useEffect"]
URLS = ["docs.python.org", "developer.mozilla.org", "pkg.go.dev", "stackoverflow.com", "github.com"]
SEARCHES = ["token bucket algorithm", "A* tie breaking", "SSE reconnect best practice", "pandas leakage"]
TODOS = ["todos"]
MESSAGES = {
    State.THINKING: [
        "Önce mevcut kodu okuyup yapıyı anlamam gerekiyor.",
        "Let me look at how this module is wired together.",
        "Testler geçti, şimdi kenar durumlarına bakalım.",
        "I see the problem — the offset is reset too late.",
        "Bir sonraki adım: hatayı yeniden üretmek.",
    ],
    State.IDLE: [
        "Değişiklikler tamam, tüm testler geçiyor. ✅",
        "Done! The bug was an off-by-one in the loop bound.",
        "Özet: 3 dosya güncellendi, 12 test eklendi.",
        "Implemented the feature and updated the docs.",
        "Hazır — incelemek ister misin?",
    ],
    State.WAITING: [
        "Bu dosyayı düzenlemek için izin gerekiyor.",
        "I need your permission to edit this file.",
    ],
    State.SLEEPING: [
        "Oturum tamamlandı.",
        "All tasks complete. Goodbye!",
    ],
}
SUB_MESSAGES = [
    "Kod tabanını tarıyorum…",
    "Found 4 call sites of the function.",
    "Reporting findings back to the main agent.",
]


@dataclass
class _Phase:
    state: State
    duration: float
    spawn_sub: bool = False


@dataclass
class _Agent:
    info: AgentInfo
    phases: deque[_Phase]
    phase_end: float = 0.0
    next_tool_at: float = float("inf")
    is_sub: bool = False
    sub_id: str | None = None          # parent: id of its live subagent
    remove_at: float | None = None
    pinned_tool: bool = False          # tool fixed for the phase (Agent call / waiting)
    index: int = 0


class DemoSimulator(threading.Thread):
    def __init__(self, store: AgentStore, n_agents: int = 5, seed: int | None = None, tick: float = 0.5) -> None:
        super().__init__(name="pixel-office-demo", daemon=True)
        self.store = store
        self.n_agents = n_agents
        self.tick = tick
        self.rng = random.Random(seed)
        self._stop_event = threading.Event()
        self._agents: dict[str, _Agent] = {}
        self._pending_spawns: list[tuple[float, int]] = []   # (when, index)
        self._next_index = 1
        self._sub_counter = 0
        self._t0: float | None = None

    # -- thread -----------------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.step(time.time())
            except Exception:
                log.exception("demo simulator step failed")
            self._stop_event.wait(self.tick)

    def stop(self) -> None:
        self._stop_event.set()

    # -- simulation -------------------------------------------------------------------------
    def step(self, now: float) -> None:
        if self._t0 is None:
            self._t0 = now
            for i in range(self.n_agents):
                self._pending_spawns.append((now + i * SPAWN_STAGGER, self._take_index()))

        due = [p for p in self._pending_spawns if p[0] <= now]
        self._pending_spawns = [p for p in self._pending_spawns if p[0] > now]
        for when, index in sorted(due):
            self._spawn(index, when)

        for aid in list(self._agents):
            agent = self._agents.get(aid)
            if agent is not None:
                self._advance(agent, now)

    def _take_index(self) -> int:
        i = self._next_index
        self._next_index += 1
        return i

    def _unique_name(self, aid: str) -> str:
        """Deterministic name for aid that no live demo agent is using (falls back to a suffix)."""
        used = {a.info.name for a in self._agents.values()}
        start = color_seed_for(aid) % len(NAMES)
        for k in range(len(NAMES)):
            name = NAMES[(start + k) % len(NAMES)]
            if name not in used:
                return name
        return f"{NAMES[start]} {aid.rsplit('-', 1)[-1]}"

    def _project(self, index: int) -> str:
        return PROJECTS[(index - 1) % len(PROJECTS)]

    def _build_life(self, index: int) -> deque[_Phase]:
        r = self.rng
        phases: deque[_Phase] = deque()
        # demo-1 sleeps after a single cycle so leaving/replacement is visible early on.
        cycles = 1 if index == 1 else CYCLES_PER_LIFE
        wants_sub = index % len(PROJECTS) == 2          # demo-2, demo-7, …
        sub_cycle = r.randrange(min(cycles, 2)) if wants_sub else -1
        for c in range(cycles):
            reps = r.randint(2, 5)
            sub_rep = r.randrange(reps) if c == sub_cycle else -1
            for k in range(reps):
                phases.append(_Phase(State.THINKING, r.uniform(2, 5)))
                if k == sub_rep:
                    phases.append(_Phase(State.WORKING, 0.0, spawn_sub=True))  # duration set at spawn
                else:
                    phases.append(_Phase(State.WORKING, r.uniform(4, 12)))
            if r.random() < 0.25:
                phases.append(_Phase(State.WAITING, r.uniform(5, 8)))
            phases.append(_Phase(State.IDLE, r.uniform(8, 15)))
        phases.append(_Phase(State.SLEEPING, r.uniform(20, 30)))
        return phases

    def _spawn(self, index: int, when: float) -> None:
        aid = f"demo-{index}"
        project = self._project(index)
        ms = int(when * 1000)
        info = AgentInfo(
            id=aid, name=self._unique_name(aid), project=project, state=State.THINKING,
            project_path=f"/home/demo/projects/{project}", title=TITLES[project],
            last_prompt=PROMPTS[project], model=self.rng.choice(["claude-opus-5-5", "claude-sonnet-5"]),
            color_seed=color_seed_for(aid), started_at=ms, state_since=ms, updated_at=ms,
        )
        agent = _Agent(info=info, phases=self._build_life(index), phase_end=when, index=index)
        self._agents[aid] = agent
        self._enter_next_phase(agent, when)
        self.store.upsert(agent.info)

    def _advance(self, agent: _Agent, now: float) -> None:
        changed = False
        # Catch up on all phase boundaries that passed (robust to large clock jumps).
        while True:
            if agent.remove_at is not None:
                if now >= agent.remove_at:
                    self._remove(agent, agent.remove_at)
                    return
                break
            if now < agent.phase_end:
                break
            # A parent waiting on its subagent keeps working until the subagent is done.
            if agent.sub_id and agent.sub_id in self._agents and \
                    self._agents[agent.sub_id].info.state != State.SLEEPING:
                agent.phase_end = now + 1.0
                break
            self._enter_next_phase(agent, agent.phase_end)
            changed = True
        if agent.info.state == State.WORKING and not agent.pinned_tool and now >= agent.next_tool_at:
            while agent.next_tool_at <= now:
                t = agent.next_tool_at
                self._pick_tool(agent, t)
                agent.next_tool_at = t + self.rng.uniform(2, 4)
            changed = True
        if changed:
            self.store.upsert(agent.info)

    def _enter_next_phase(self, agent: _Agent, at: float) -> None:
        info = agent.info
        if not agent.phases:
            # Lifecycle over: leave the office.
            agent.remove_at = at
            return
        ph = agent.phases.popleft()
        prev = info.state
        info.state = ph.state
        info.state_since = int(at * 1000)
        info.updated_at = int(at * 1000)
        agent.pinned_tool = False
        agent.next_tool_at = float("inf")
        info.tool = info.detail = None
        r = self.rng
        dur = ph.duration

        if ph.state == State.WORKING:
            if ph.spawn_sub and not agent.is_sub:
                sub_dur = r.uniform(10, 20)
                dur = sub_dur + 2.0
                agent.pinned_tool = True
                info.tool, info.detail = "Agent", "Explore the codebase"
                info.tool_count += 1
                self._spawn_sub(agent, at, sub_dur)
            else:
                self._pick_tool(agent, at)
                agent.next_tool_at = at + r.uniform(2, 4)
        elif ph.state == State.WAITING:
            agent.pinned_tool = True
            if r.random() < 0.3:
                info.tool, info.detail = "AskUserQuestion", "Hangi yaklaşımı tercih edersin?"
            else:
                info.tool, info.detail = "Edit", r.choice(FILES[info.project])
            info.last_message = r.choice(MESSAGES[State.WAITING])
        elif ph.state in (State.IDLE, State.SLEEPING):
            info.last_message = r.choice(MESSAGES[ph.state])
        elif ph.state == State.THINKING and prev != State.THINKING and r.random() < 0.6:
            info.last_message = r.choice(MESSAGES[State.THINKING])
        agent.phase_end = at + dur

    def _pick_tool(self, agent: _Agent, at: float) -> None:
        r = self.rng
        info = agent.info
        project = info.project
        if agent.is_sub:
            tool = r.choice(["Read", "Grep", "Glob", "Read", "Bash"])
        else:
            tool = r.choices(
                ["Bash", "Read", "Edit", "Write", "Grep", "Glob", "WebFetch", "WebSearch", "TodoWrite"],
                weights=[5, 5, 5, 2, 3, 2, 2, 1, 1],
            )[0]
        if tool == "Bash":
            detail = r.choice(BASH.get(project, ["ls -la"]))
        elif tool in ("Read", "Edit", "Write"):
            detail = r.choice(FILES.get(project, ["main.py"]))
        elif tool == "Grep":
            detail = r.choice(GREPS)
        elif tool == "Glob":
            detail = r.choice(["**/*.py", "src/**/*.ts", "**/*_test.go", "web/js/*.js"])
        elif tool == "WebFetch":
            detail = r.choice(URLS)
        elif tool == "WebSearch":
            detail = r.choice(SEARCHES)
        else:
            detail = "todos"
        info.tool, info.detail = tool, detail
        info.tool_count += 1
        info.updated_at = int(at * 1000)

    def _spawn_sub(self, parent: _Agent, at: float, duration: float) -> None:
        self._sub_counter += 1
        sid = f"{parent.info.id}:sub-{self._sub_counter}"
        ms = int(at * 1000)
        p = parent.info
        info = AgentInfo(
            id=sid, name=self._unique_name(sid), project=p.project, state=State.WORKING,
            project_path=p.project_path, title="Explore the codebase",
            last_prompt="Find every place where the state machine is updated and report back.",
            is_subagent=True, parent_id=p.id, agent_type="Explore", model=p.model,
            color_seed=color_seed_for(sid), started_at=ms, state_since=ms, updated_at=ms,
        )
        phases = deque([_Phase(State.WORKING, duration), _Phase(State.SLEEPING, SUB_REMOVE_AFTER)])
        sub = _Agent(info=info, phases=phases, phase_end=at, is_sub=True)
        parent.sub_id = sid
        self._agents[sid] = sub
        self._enter_next_phase(sub, at)
        sub.info.last_message = self.rng.choice(SUB_MESSAGES)
        self.store.upsert(sub.info)

    def _remove(self, agent: _Agent, at: float) -> None:
        aid = agent.info.id
        self._agents.pop(aid, None)
        self.store.remove(aid)
        if agent.sub_id:
            sub = self._agents.pop(agent.sub_id, None)
            if sub is not None:
                self.store.remove(sub.info.id)
        if not agent.is_sub:
            self._pending_spawns.append((at + RESPAWN_DELAY, self._take_index()))
