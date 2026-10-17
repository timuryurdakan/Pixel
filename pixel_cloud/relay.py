"""Relay tickets (V3.md §6.2): minted by Vercel (`POST /api/relay_ticket`), verified by the Cloudflare Worker
(`relay/src/lib.js`) and by `tools/dev_relay.py` (this module's `verify_ticket`).

    ticket  = "v1." + payload + "." + mac
    payload = base64url(JSON {"r","ro","id","iat","exp","n"})          (no padding, compact JSON, this key order)
    mac     = base64url(HMAC-SHA256(RELAY_SECRET, "pixel-relay-ticket:v1:" + payload))   (all 32 bytes)

The byte-level format is pinned by `relay/test/ticket_vectors.json`, which both the Python tests and the
Worker's node:test suite check. Stateless: 0 storage commands. Nothing here logs a ticket or a secret.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets

TICKET_VERSION = "v1"
MAC_PREFIX = b"pixel-relay-ticket:v1:"
TICKET_TTL = 120            # s, exp - iat
MAX_FUTURE_SKEW = 30        # s, iat may be at most this far ahead of the verifier's clock
MAX_TICKET_LEN = 512        # chars; a real ticket is ~200
SUBPROTOCOL = "pixel-relay.v1"
TICKET_PROTOCOL_PREFIX = "tk."

ROLES = ("browser", "device")
ROOM_RE = re.compile(r"^[a-z2-7]{26}\Z")
PEER_ID_RE = re.compile(r"^[a-z2-7]{16}\Z")          # deviceId / bid (V3.md §3)
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}\Z")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]*\Z")


class TicketError(ValueError):
    """Ticket rejected. `reason` ∈ format | mac | claims | expired | not_yet_valid | lifetime | room."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(s: str) -> bytes:
    """Strict RFC 4648 §5 without padding: alphabet check, no length ≡ 1 (mod 4), canonical trailing bits."""
    if not isinstance(s, str) or not _B64URL_RE.match(s) or len(s) % 4 == 1:
        raise ValueError("bad base64url")
    try:
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError):
        raise ValueError("bad base64url") from None
    if b64url_encode(raw) != s:
        raise ValueError("non-canonical base64url")
    return raw


def _mac(secret: bytes, payload: str) -> bytes:
    return hmac.new(secret, MAC_PREFIX + payload.encode("ascii"), hashlib.sha256).digest()


def mint_ticket(secret: bytes, room: str, role: str, peer_id: str, now: int, nonce: bytes | None = None,
                ttl: int = TICKET_TTL) -> tuple[str, int]:
    """→ (ticket, exp). `now` in unix seconds. `nonce` (8 bytes) and `ttl` are for tests/vectors only."""
    if not ROOM_RE.match(room) or role not in ROLES or not PEER_ID_RE.match(peer_id):
        raise ValueError("invalid ticket claims")
    iat = int(now)
    exp = iat + int(ttl)
    claims = {"r": room, "ro": role, "id": peer_id, "iat": iat, "exp": exp,
              "n": b64url_encode(nonce if nonce is not None else secrets.token_bytes(8))}
    payload = b64url_encode(json.dumps(claims, separators=(",", ":")).encode("ascii"))
    return f"{TICKET_VERSION}.{payload}.{b64url_encode(_mac(secret, payload))}", exp


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and -(2 ** 53) < v < 2 ** 53


def verify_ticket(secret: bytes, ticket: object, now: int, room: str | None = None) -> dict:
    """Claims dict, or TicketError. Same checks and order as `verifyTicket` in relay/src/lib.js:
    format → MAC (constant time, before any JSON parsing) → claim formats → time → room."""
    if not secret or not isinstance(ticket, str) or len(ticket) > MAX_TICKET_LEN:
        raise TicketError("format")
    parts = ticket.split(".")
    if len(parts) != 3 or parts[0] != TICKET_VERSION or not parts[1]:
        raise TicketError("format")
    _, payload, mac_s = parts
    try:
        b64url_decode(payload)
        mac = b64url_decode(mac_s)
    except ValueError:
        raise TicketError("format") from None
    if len(mac) != 32 or not hmac.compare_digest(mac, _mac(secret, payload)):
        raise TicketError("mac")
    try:
        claims = json.loads(b64url_decode(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise TicketError("claims") from None
    if not isinstance(claims, dict):
        raise TicketError("claims")
    r, ro, pid, iat, exp, n = (claims.get(k) for k in ("r", "ro", "id", "iat", "exp", "n"))
    if (not isinstance(r, str) or not ROOM_RE.match(r) or ro not in ROLES or not isinstance(pid, str)
            or not PEER_ID_RE.match(pid) or not _is_int(iat) or not _is_int(exp)
            or not isinstance(n, str) or not NONCE_RE.match(n)):
        raise TicketError("claims")
    if exp < iat or exp - iat > TICKET_TTL:
        raise TicketError("lifetime")
    if iat > now + MAX_FUTURE_SKEW:
        raise TicketError("not_yet_valid")
    if exp < now:
        raise TicketError("expired")
    if room is not None and r != room:
        raise TicketError("room")
    return {"r": r, "ro": ro, "id": pid, "iat": iat, "exp": exp}


def ticket_from_protocols(header: str | None) -> str | None:
    """`Sec-WebSocket-Protocol: pixel-relay.v1, tk.<ticket>` → ticket, else None (needs both, exactly one tk.)."""
    if not header or len(header) > 1024:
        return None
    items = [p.strip() for p in header.split(",")]
    tickets = [p[len(TICKET_PROTOCOL_PREFIX):] for p in items if p.startswith(TICKET_PROTOCOL_PREFIX)]
    if SUBPROTOCOL not in items or len(tickets) != 1 or not tickets[0]:
        return None
    return tickets[0]


def parse_origins(value: str | None) -> list[str]:
    return [o.strip().rstrip("/") for o in (value or "").split(",") if o.strip()]


def origin_allowed(role: str, origin: str | None, allowed: list[str]) -> bool:
    """Browsers must send an allow-listed Origin; devices (the connector) must send none (V3.md §3.1)."""
    if role == "device":
        return origin is None
    return origin is not None and origin in allowed
