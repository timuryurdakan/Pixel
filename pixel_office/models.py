"""Agent data model and JSON serialisation (ARCHITECTURE.md §3.3, §7.1)."""

import enum
import json
import logging
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import config
from .config import NAMES

log = logging.getLogger("pixel_office")


class State(enum.StrEnum):
    WORKING = "working"
    THINKING = "thinking"
    WAITING = "waiting_for_user"
    IDLE = "idle"
    SLEEPING = "sleeping"


@dataclass(slots=True)
class AgentInfo:
    id: str
    name: str
    project: str
    state: State
    project_path: str | None = None
    title: str | None = None
    tool: str | None = None
    detail: str | None = None
    last_message: str | None = None
    last_prompt: str | None = None
    is_subagent: bool = False
    parent_id: str | None = None
    agent_type: str | None = None
    model: str | None = None
    color_seed: int = 0
    tool_count: int = 0
    started_at: int = 0      # ms
    state_since: int = 0     # ms
    updated_at: int = 0      # ms
    name_source: str = "auto"        # custom|project|file|reported|auto (CLOUD.md §15.2)
    default_name: str | None = None  # what a reset would show; None → same as name
    source: str | None = None        # status-API agents only (CLOUD.md §16)

    def to_dict(self, *, names: bool = False) -> dict:
        """v1 Agent JSON (ARCHITECTURE.md §3.3). names=True adds the v2 `nameSource`/`defaultName` fields
        (CLOUD.md §15.2); the AgentStore always serialises with names=True."""
        d = {
            "id": self.id,
            "name": self.name,
            "project": self.project,
            "projectPath": self.project_path,
            "title": self.title,
            "state": str(self.state),
            "tool": self.tool,
            "detail": self.detail,
            "lastMessage": self.last_message,
            "lastPrompt": self.last_prompt,
            "isSubagent": self.is_subagent,
            "parentId": self.parent_id,
            "agentType": self.agent_type,
            "model": self.model,
            "colorSeed": int(self.color_seed),
            "toolCount": int(self.tool_count),
            "startedAt": int(self.started_at),
            "stateSince": int(self.state_since),
            "updatedAt": int(self.updated_at),
        }
        if names:
            d["nameSource"] = self.name_source
            d["defaultName"] = self.default_name if self.default_name is not None else self.name
        if self.source is not None:
            d["source"] = self.source
        return d


def color_seed_for(agent_id: str) -> int:
    return zlib.crc32(agent_id.encode("utf-8")) & 0xFFFFFFFF


_overrides_lock = threading.Lock()
_overrides_key: tuple[Path, float] | None = None
_overrides: dict[str, str] = {}


def name_overrides(path: Path | None = None) -> dict[str, str]:
    """{agent_id: name} from NAMES_FILE; re-read only when its mtime changes. Missing/bad file → {}."""
    global _overrides_key, _overrides
    path = path or config.NAMES_FILE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _overrides_lock:
        if _overrides_key == (path, mtime):
            return _overrides
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("ignoring %s: %s", path, e)
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        _overrides = {k: v.strip() for k, v in raw.items() if isinstance(k, str) and isinstance(v, str) and v.strip()}
        _overrides_key = (path, mtime)
        return _overrides


def hash_name(agent_id: str) -> str:
    """The v1 automatic name (Ada, Linus, …), ignoring every override."""
    return NAMES[color_seed_for(agent_id) % len(NAMES)]


def name_for(agent_id: str) -> str:
    """Legacy names.json override (priority 3) or the v1 hash name (priority 5). UI renames (priorities
    1–2, CLOUD.md §15.2) are applied on top by `names_store.Namer`, which the AgentStore runs on every upsert."""
    return name_overrides().get(agent_id) or hash_name(agent_id)


def now_ms() -> int:
    return int(time.time() * 1000)
