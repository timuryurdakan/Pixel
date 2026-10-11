"""UI renames for the local server (CLOUD.md §15.2, §15.4).

File: `{"version":1,"agents":{id:{"name","ts"}},"projects":{path:{"name","ts"}}}` in the user data dir,
written atomically with mode 0600, created only on the first rename. Never inside the repo.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from pixel_cloud.validate import sanitize
from pixel_cloud import settings as CS

from .models import AgentInfo, hash_name, name_overrides

log = logging.getLogger("pixel_office")

MAX_AGENT_ENTRIES = 1000
MAX_PROJECT_ENTRIES = 1000


def default_names_path() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg and os.path.isabs(xdg) else home / ".local" / "share"
    return base / "pixel-office" / "names.json"


def _clean_section(raw: object, limit: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(k, str) or not k or not isinstance(v, dict):
            continue
        name = v.get("name")
        if not isinstance(name, str):
            continue
        name = sanitize(name, CS.NAME_MAX)
        if not name:
            continue
        ts = v.get("ts")
        out[k] = {"name": name, "ts": int(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else 0}
    if len(out) > limit:
        keep = sorted(out.items(), key=lambda kv: kv[1]["ts"], reverse=True)[:limit]
        out = dict(keep)
    return out


class NamesStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_names_path()
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._agents: dict[str, dict] = {}
        self._projects: dict[str, dict] = {}

    # -- reading ------------------------------------------------------------------------------------
    def _reload_locked(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime, self._agents, self._projects = None, {}, {}
            return
        if mtime == self._mtime:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable names file %s: %s", self.path, type(e).__name__)
            data = {}
        if not isinstance(data, dict):
            data = {}
        self._agents = _clean_section(data.get("agents"), MAX_AGENT_ENTRIES)
        self._projects = _clean_section(data.get("projects"), MAX_PROJECT_ENTRIES)
        self._mtime = mtime

    def agent(self, agent_id: str) -> str | None:
        with self._lock:
            self._reload_locked()
            e = self._agents.get(agent_id)
            return e["name"] if e else None

    def project(self, project_path: str | None) -> str | None:
        if not project_path:
            return None
        with self._lock:
            self._reload_locked()
            e = self._projects.get(project_path)
            return e["name"] if e else None

    # -- writing ------------------------------------------------------------------------------------
    def set(self, scope: str, key: str, name: str | None) -> None:
        """scope 'agent' (key = agent id) or 'project' (key = projectPath). name None → reset."""
        if scope not in ("agent", "project"):
            raise ValueError("scope")
        with self._lock:
            self._reload_locked()
            section = self._agents if scope == "agent" else self._projects
            if name is None:
                if key not in section:
                    return                      # nothing to reset; don't create the file
                del section[key]
            else:
                section[key] = {"name": name, "ts": int(time.time() * 1000)}
                limit = MAX_AGENT_ENTRIES if scope == "agent" else MAX_PROJECT_ENTRIES
                if len(section) > limit:
                    for k, _ in sorted(section.items(), key=lambda kv: kv[1]["ts"])[: len(section) - limit]:
                        del section[k]
            self._write_locked()

    def _write_locked(self) -> None:
        data = {"version": 1, "agents": self._agents, "projects": self._projects}
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix=".names-", suffix=".tmp", dir=str(self.path.parent))
        try:
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):   # Windows
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = None


class Namer:
    """Applies the §15.2 local priority list to an AgentInfo (returns a new object)."""

    def __init__(self, names: NamesStore | None) -> None:
        self.names = names

    def __call__(self, a: AgentInfo) -> AgentInfo:
        n = copy.copy(a)
        if a.name_source == "reported":
            reported = a.name
            auto = (a.agent_type or "Alt-ajan") if a.is_subagent else hash_name(a.id)
        else:
            reported, auto = None, a.name
        chain: list[tuple[str, str]] = []
        if self.names is not None:
            c = self.names.agent(a.id)
            if c:
                chain.append((c, "custom"))
            if not a.is_subagent and a.project_path:
                p = self.names.project(a.project_path)
                if p:
                    chain.append((p, "project"))
        legacy = name_overrides().get(a.id)
        if legacy:
            chain.append((legacy, "file"))
        if reported:
            chain.append((reported, "reported"))
        chain.append((auto, "auto"))
        n.name, n.name_source = chain[0]
        n.default_name = chain[1][0] if n.name_source in ("custom", "project") else n.name
        return n
