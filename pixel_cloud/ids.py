"""Room ids, write keys and agent ids (CLOUD.md §3.1)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

ROOM_RE = re.compile(r"^[a-z2-7]{26}$")
WRITE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
AGENT_ID_RE = re.compile(r"^a[0-9a-f]{16}$")
OWNER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_KEY_PREFIX = b"pixel-write:v1:"


def new_room_id() -> str:
    """26 chars [a-z2-7] = base32 of 16 random bytes (128 bit), lowercase, no padding."""
    return base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=").lower()


def valid_room_id(room: object) -> bool:
    return isinstance(room, str) and bool(ROOM_RE.match(room))


def write_key(secret: bytes, room: str) -> str:
    mac = hmac.new(secret, _KEY_PREFIX + room.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii")[:32]


def check_write_key(secret: bytes | None, room: str, presented: str | None) -> bool:
    """Constant-time check of a presented write key. Never raises."""
    if not secret or not presented or not valid_room_id(room):
        return False
    expected = write_key(secret, room).encode("ascii")
    try:
        got = presented.encode("utf-8")
    except UnicodeError:
        return False
    return hmac.compare_digest(expected, got)


def bearer(headers) -> str | None:
    """Token from 'Authorization: Bearer <token>' (case-insensitive scheme), else None."""
    v = headers.get("Authorization") or headers.get("authorization")
    if not v:
        return None
    scheme, _, token = v.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def agent_id(room: str, owner_id: str, session: str, sub: str | None) -> str:
    return "a" + _h(f"{room}|{owner_id}|{session}|{sub or ''}")[:16]


def local_agent_id(owner_id: str, session: str, sub: str | None) -> str:
    return "r" + _h(f"local|{owner_id}|{session}|{sub or ''}")[:16]


def default_owner_id(owner: str) -> str:
    return "n_" + _h(owner.lower())[:10]


def constant_time_eq(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
