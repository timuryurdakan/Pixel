"""Request payload schema and string sanitising (CLOUD.md §5.3, §5.5, §15.3).

Shared by the cloud (`pixel_cloud.app`) and the local server (`pixel_office.httpd`), so both backends
accept exactly the same bodies and return the same 400 messages.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from . import settings as S
from .ids import OWNER_ID_RE, SESSION_RE

STATES = ("working", "thinking", "waiting_for_user", "idle", "sleeping", "offline")
ACTIVE_STATES = frozenset({"working", "thinking", "waiting_for_user"})

TOOL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}\Z")
SOURCE_RE = re.compile(r"^[a-z0-9-]{1,16}\Z")
CTL_RE = re.compile(r"^[a-z2-7]{16}\.[A-Za-z0-9_-]{1,24}\Z")   # v3: deviceId "." agentKey (V3.md §13.2)

# Bidi overrides/isolates and zero-width / invisible format characters.
_STRIP = {cp for cp in range(0x202A, 0x202F)} | {cp for cp in range(0x2066, 0x206A)} | {
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF, 0x180E, 0x061C}
_EXTRA_INVISIBLE = {0x115F, 0x1160, 0x3164, 0xFFA0}  # Hangul fillers (H22)
_WS_CONTROLS = "\t\n\r\x0b\x0c\x85"
_WS_RE = re.compile(r"\s+")


class ValidationError(ValueError):
    """→ HTTP 400 bad_request with str(self) as the message."""


def sanitize(value: str, limit: int) -> str:
    """NFC, strip control/bidi/zero-width chars, collapse whitespace, trim, truncate by code points."""
    s = unicodedata.normalize("NFC", value)
    out = []
    for ch in s:
        cp = ord(ch)
        if ch in _WS_CONTROLS:
            out.append(" ")
        elif cp < 0x20 or 0x7F <= cp <= 0x9F or cp in _STRIP or cp in _EXTRA_INVISIBLE:
            continue
        elif 0xD800 <= cp <= 0xDFFF:     # lone surrogates (only possible from surrogateescape'd input)
            continue
        elif 0xE0000 <= cp <= 0xE007F:   # tags block (H22)
            continue
        elif unicodedata.category(ch) == "Cf":  # format characters (H22)
            continue
        else:
            out.append(ch)
    s = _WS_RE.sub(" ", "".join(out)).strip()
    if len(s) > limit:
        s = s[:limit].rstrip()
    return s


def _opt_str(obj: dict, key: str) -> str | None:
    v = obj.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValidationError(f"'{key}' must be a string")
    return v


def _text(obj: dict, key: str, limit: int, *, required: bool = False) -> str | None:
    v = _opt_str(obj, key)
    if v is None:
        if required:
            raise ValidationError(f"'{key}' is required")
        return None
    s = sanitize(v, limit)
    if not s:
        if required:
            raise ValidationError(f"'{key}' must not be empty")
        return None
    return s


def _pattern(obj: dict, key: str, rx: re.Pattern, *, required: bool = False) -> str | None:
    v = _opt_str(obj, key)
    if v is None or v == "":
        if required:
            raise ValidationError(f"'{key}' is required")
        return None
    if not rx.match(v):
        raise ValidationError(f"'{key}' has an invalid format")
    return v


def _int(obj: dict, key: str, lo: int, hi: int) -> int | None:
    v = obj.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:  # bool is an int subclass; NaN
        raise ValidationError(f"'{key}' must be a number")
    if isinstance(v, float):
        if v in (float("inf"), float("-inf")):
            raise ValidationError(f"'{key}' must be a finite number")
        v = int(v)
    return max(lo, min(hi, v))


def clean_name(value: object) -> str | None:
    """Rename / reported name: None or "" → None (reset); otherwise 1–24 code points after sanitising.
    A non-empty input that sanitises to nothing is a 400 (§15.3)."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValidationError("'name' must be a string or null")
    s = sanitize(value, S.NAME_MAX)
    if not s:
        raise ValidationError("'name' is empty after removing invisible characters")
    return s


def clean_room_name(value: object) -> str:
    if value is None:
        return S.DEFAULT_ROOM_NAME
    if not isinstance(value, str):
        raise ValidationError("'name' must be a string")
    return sanitize(value, S.ROOM_NAME_MAX) or S.DEFAULT_ROOM_NAME


@dataclass(frozen=True, slots=True)
class Update:
    session: str
    sub: str | None
    owner: str
    owner_id: str | None          # None → caller derives the default from `owner`
    project: str | None
    state: str
    tool: str | None
    label: str | None
    agent_type: str | None
    source: str
    color: int | None
    state_age_ms: int | None
    started_age_ms: int | None
    tool_count: int | None
    name: str | None
    final: bool
    ctl: str | None = None        # v3 connector agents only; v1/v2 clients never send it


def parse_update(obj: object, *, default_owner: str | None = None) -> Update:
    """One §5.5 update object. Unknown fields are ignored. `default_owner` (local server only) is used
    when `owner` is missing; the cloud passes None, which makes `owner` required."""
    if not isinstance(obj, dict):
        raise ValidationError("each update must be a JSON object")
    session = _pattern(obj, "session", SESSION_RE, required=True)
    sub = _pattern(obj, "sub", SESSION_RE)
    owner = _text(obj, "owner", S.OWNER_MAX, required=default_owner is None)
    if owner is None:
        owner = sanitize(default_owner or "", S.OWNER_MAX) or "local"
    owner_id = _pattern(obj, "ownerId", OWNER_ID_RE)
    state = obj.get("state")
    if not isinstance(state, str) or state not in STATES:
        raise ValidationError("'state' must be one of " + "|".join(STATES))
    project_raw = _opt_str(obj, "project")
    if project_raw is not None and ("/" in project_raw or "\\" in project_raw):
        raise ValidationError("'project' must be a folder name, not a path")
    project = _text(obj, "project", S.PROJECT_MAX)
    tool = _pattern(obj, "tool", TOOL_RE)
    label = _text(obj, "label", S.LABEL_MAX)
    agent_type = _text(obj, "agentType", S.AGENT_TYPE_MAX)
    source = _pattern(obj, "source", SOURCE_RE) or "api"
    color = obj.get("color")
    if color is not None:
        if isinstance(color, bool) or not isinstance(color, int) or not 0 <= color <= 15:
            raise ValidationError("'color' must be an integer 0–15")
    state_age = _int(obj, "stateAgeMs", 0, 86_400_000)
    started_age = _int(obj, "startedAgeMs", 0, 604_800_000)
    tool_count = _int(obj, "toolCount", 0, 1_000_000)
    name = clean_name(obj.get("name")) if "name" in obj else None
    final = obj.get("final", False)
    if final is None:
        final = False
    if not isinstance(final, bool):
        raise ValidationError("'final' must be a boolean")
    if final and state != "sleeping":
        raise ValidationError("'final' is only allowed with state 'sleeping'")
    ctl = _pattern(obj, "ctl", CTL_RE)
    return Update(session=session, sub=sub, owner=owner, owner_id=owner_id, project=project, state=state,
                  tool=tool, label=label, agent_type=agent_type, source=source, color=color,
                  state_age_ms=state_age, started_age_ms=started_age, tool_count=tool_count, name=name,
                  final=final, ctl=ctl)


def parse_json(body: bytes) -> object:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValidationError("body must be UTF-8 JSON") from None


def parse_status_body(body: bytes, *, default_owner: str | None = None) -> list[Update]:
    """A single update object or {"agents": [update, …]} with 1–MAX_BATCH updates."""
    data = parse_json(body)
    if not isinstance(data, dict):
        raise ValidationError("body must be a JSON object")
    if "agents" in data:
        items = data["agents"]
        if not isinstance(items, list) or not items:
            raise ValidationError("'agents' must be a non-empty array")
        if len(items) > S.MAX_BATCH:
            raise ValidationError(f"at most {S.MAX_BATCH} updates per request")
    else:
        items = [data]
    return [parse_update(o, default_owner=default_owner) for o in items]


@dataclass(frozen=True, slots=True)
class RenameRequest:
    scope: str             # "agent" | "owner" (cloud) | "project" (local)
    target: str            # agentId / ownerId (cloud) or local agent id
    name: str | None


def parse_cloud_rename(body: bytes) -> RenameRequest:
    from .ids import AGENT_ID_RE
    data = parse_json(body)
    if not isinstance(data, dict):
        raise ValidationError("body must be a JSON object")
    scope = data.get("scope", "agent")
    if scope == "agent":
        target = data.get("agentId")
        if not isinstance(target, str) or not AGENT_ID_RE.match(target):
            raise ValidationError("'agentId' has an invalid format")
    elif scope == "owner":
        target = data.get("ownerId")
        if not isinstance(target, str) or not OWNER_ID_RE.match(target):
            raise ValidationError("'ownerId' has an invalid format")
    else:
        raise ValidationError("'scope' must be 'agent' or 'owner'")
    return RenameRequest(scope=scope, target=target, name=clean_name(data.get("name")))


LOCAL_ID_MAX = 200


def parse_local_rename(body: bytes) -> RenameRequest:
    data = parse_json(body)
    if not isinstance(data, dict):
        raise ValidationError("body must be a JSON object")
    scope = data.get("scope", "agent")
    if scope not in ("agent", "project"):
        raise ValidationError("'scope' must be 'agent' or 'project'")
    target = data.get("id")
    if not isinstance(target, str) or not target or len(target) > LOCAL_ID_MAX:
        raise ValidationError("'id' is required")
    return RenameRequest(scope=scope, target=target, name=clean_name(data.get("name")))
