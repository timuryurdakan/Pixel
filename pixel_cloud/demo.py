"""`dev_server.py --demo`: creates room "Demo Oda" and lets 3 fake friends post through `dispatch`."""

from __future__ import annotations

import json
import random
import threading
import time

from .app import App, Request

OWNERS = [("Timur", "o_demoTimur1", 0, ["Pixel", "api"]),
          ("Ayşe", "o_demoAyse01", 6, ["bot", "notlar"]),
          ("Mert", "o_demoMert01", 3, ["web"])]
TOOLS = ["Bash", "Read", "Edit", "Grep", "WebSearch", "TodoWrite", "Write", "Glob"]


class _Session:
    def __init__(self, owner: tuple, idx: int, rng: random.Random, now: float) -> None:
        self.owner, self.oid, self.color, projects = owner
        self.session = f"demo-{self.oid[-4:]}-{idx}"
        self.project = projects[idx % len(projects)]
        self.started = now
        self.state = "idle"
        self.tool: str | None = None
        self.label: str | None = None
        self.since = now
        self.next_at = now + rng.uniform(1, 4)
        self.tc = 0
        self.sub_until = 0.0


class DemoWriter(threading.Thread):
    """Posts realistic-looking updates every few seconds (well inside the room rate limit)."""

    def __init__(self, app: App, base_url: str = "", seed: int | None = None, tick: float = 1.0,
                 room_name: str = "Demo Oda") -> None:
        super().__init__(name="pixel-cloud-demo", daemon=True)
        self.app = app
        self.rng = random.Random(seed)
        self.tick = tick
        self._stop = threading.Event()
        self.room_id: str | None = None
        self.write_key: str | None = None
        self.join_url: str | None = None
        self.room_name = room_name
        self.base_url = base_url
        self.sessions: list[_Session] = []

    def create_room(self) -> str:
        host = self.base_url.split("://", 1)[-1] if self.base_url else "127.0.0.1"
        scheme = self.base_url.split("://", 1)[0] if "://" in self.base_url else "http"
        resp = self.app.dispatch(Request("POST", "/api/rooms", {"Host": host, "Content-Type": "application/json"},
                                         body=json.dumps({"name": self.room_name,
                                                          "createToken": self.app.settings.create_token or ""}).encode(),
                                         client_ip="demo", scheme=scheme))
        if resp.status != 201:
            raise RuntimeError(f"demo room creation failed: HTTP {resp.status}")
        d = resp.json()
        self.room_id, self.write_key, self.join_url = d["roomId"], d["writeKey"], d["joinUrl"]
        now = time.time()
        for o in OWNERS:
            for i in range(2 if o[0] != "Mert" else 1):
                self.sessions.append(_Session(o, i, self.rng, now))
        return self.room_id

    def post(self, updates: list[dict]) -> int:
        resp = self.app.dispatch(Request(
            "POST", f"/api/status?room={self.room_id}",
            {"Authorization": f"Bearer {self.write_key}", "Content-Type": "application/json"},
            body=json.dumps({"agents": updates}, ensure_ascii=False).encode(), client_ip="demo"))
        return resp.status

    def _payload(self, s: _Session, now: float, **extra) -> dict:
        d = {"session": s.session, "owner": s.owner, "ownerId": s.oid, "project": s.project, "state": s.state,
             "tool": s.tool, "label": s.label, "source": "claude-code", "color": s.color,
             "stateAgeMs": int((now - s.since) * 1000), "startedAgeMs": int((now - s.started) * 1000),
             "toolCount": s.tc}
        d.update(extra)
        return d

    def step(self, now: float) -> None:
        batch: list[dict] = []
        for s in self.sessions:
            if now < s.next_at:
                continue
            r = self.rng.random()
            old = s.state
            if s.state in ("idle", "sleeping"):
                s.state, s.tool, s.label = ("thinking", None, None) if r < 0.7 else ("sleeping", None, None)
            elif s.state == "thinking":
                if r < 0.75:
                    s.state, s.tool, s.label = "working", self.rng.choice(TOOLS), None
                    s.tc += 1
                elif r < 0.85:
                    s.state, s.tool, s.label = "waiting_for_user", "Bash", "İzin bekliyor"
                else:
                    s.state, s.tool, s.label = "idle", None, None
            elif s.state == "working":
                if r < 0.5:
                    s.tool = self.rng.choice(TOOLS)
                    s.tc += 1
                elif r < 0.7:
                    s.state, s.tool, s.label = "thinking", None, None
                elif r < 0.8 and not s.sub_until:
                    s.tool = "Agent"
                    s.sub_until = now + self.rng.uniform(8, 20)
                    batch.append({"session": s.session, "sub": "sub1", "owner": s.owner, "ownerId": s.oid,
                                  "project": s.project, "state": "working", "tool": "Grep", "agentType": "Explore",
                                  "label": "Alt-ajan", "source": "claude-code", "color": s.color})
                else:
                    s.state, s.tool, s.label = "idle", None, None
            elif s.state == "waiting_for_user":
                s.state, s.tool, s.label = "thinking", None, None
            if s.state != old:
                s.since = now
            if s.sub_until and now >= s.sub_until:
                s.sub_until = 0.0
                batch.append({"session": s.session, "sub": "sub1", "owner": s.owner, "ownerId": s.oid,
                              "state": "sleeping", "agentType": "Explore", "source": "claude-code"})
            batch.append(self._payload(s, now))
            s.next_at = now + self.rng.uniform(3, 9)
        for i in range(0, len(batch), 10):
            self.post(batch[i:i + 10])

    def run(self) -> None:
        if self.room_id is None:
            self.create_room()
        while not self._stop.is_set():
            try:
                self.step(time.time())
            except Exception:   # demo must never crash the dev server
                pass
            self._stop.wait(self.tick)

    def stop(self) -> None:
        self._stop.set()
