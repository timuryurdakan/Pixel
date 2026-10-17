"""Constants (CLOUD.md §6.3, §8, §12) and environment reading."""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Mapping

# -- read-time state machine (§6.2) -------------------------------------------------------------
ROOM_TTL = 30 * 86400          # s, refreshed on writes (EXPIRE at most once per hour per warm instance)
TTL_REFRESH_EVERY = 3600       # s
IDLE_TO_SLEEP = 180            # s
ACTIVE_STALE = 1800            # s
REMOVE_AFTER = 2700            # s
SUB_REMOVE_AFTER = 120         # s
SUB_STALE_REMOVE = 1800        # s
PURGE_AFTER = 6 * 3600         # s
FINISHED_REMOVE_AFTER = 300    # s (§16)

# -- limits (§8) ----------------------------------------------------------------------------------
MAX_AGENTS_PER_ROOM = 48
MAX_NAME_FIELDS = 96           # n:/no: rename fields per room (§15.3)
NAME_MAX = 24                  # code points
OWNER_MAX = 24
PROJECT_MAX = 32
LABEL_MAX = 40
TOOL_MAX = 32
AGENT_TYPE_MAX = 24
ROOM_NAME_MAX = 32
DEFAULT_ROOM_NAME = "Pixel Ofis"
MAX_BATCH = 10
MAX_STATUS_BODY = 4096         # bytes
MAX_CREATE_BODY = 1024
MAX_RENAME_BODY = 1024
MAX_CC_HOOK_BODY = 262_144

# -- rate limits (§8, §15.3): (tokens per second, burst) ------------------------------------------
RATE_ROOM_WRITE = (2.0, 30)
RATE_IP_WRITE = (4.0, 60)
RATE_IP_CREATE = (5 / 3600, 5)
RATE_ROOM_RENAME = (10 / 60, 10)
RATE_RELAY_TICKET = (0.5, 30)  # per room and per IP (V3.md §13.1): 30/min

# -- v3 relay (V3.md §6.2, §13.1) -----------------------------------------------------------------
MAX_TICKET_BODY = 256          # bytes
_RELAY_URL_RE = re.compile(r"^(?:wss://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?"
                           r"|ws://127\.0\.0\.1(?::\d{1,5})?)\Z")

# -- polling / caching (§5.4, §12) ----------------------------------------------------------------
POLL_ACTIVE_MS, POLL_CALM_MS, POLL_EMPTY_MS = 10_000, 30_000, 60_000
CDN_TTL_ACTIVE, CDN_TTL_CALM, CDN_TTL_EMPTY = 5, 15, 5   # empty = 5 s so the first agent shows up fast (§17.10)
MICRO_CACHE_S = 2
CONFIG_CACHE_CONTROL = "public, max-age=60, s-maxage=300"

UPSTASH_TIMEOUT = 3.0          # s
MIN_SECRET_LEN = 32


def _int_env(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(env.get(name, "") or default)
    except ValueError:
        return default
    return max(lo, min(hi, v))


@dataclass
class Settings:
    """Everything that comes from the environment. Built once per App (tests pass their own env)."""
    secret: bytes | None
    secret_generated: bool
    create_token: str | None
    public_url: str | None
    on_vercel: bool
    poll_active_ms: int = POLL_ACTIVE_MS
    poll_calm_ms: int = POLL_CALM_MS
    poll_empty_ms: int = POLL_EMPTY_MS
    relay_secret: bytes | None = None     # RELAY_SECRET (≥ 32 chars); v3 is on only if both relay fields are set
    relay_url: str | None = None          # PIXEL_RELAY_URL, e.g. wss://pixel-relay.<sub>.workers.dev
    extra: dict = field(default_factory=dict)

    @property
    def relay_enabled(self) -> bool:
        return bool(self.relay_secret and self.relay_url)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        on_vercel = bool(env.get("VERCEL"))
        raw = env.get("PIXEL_SECRET") or ""
        generated = False
        if raw and (len(raw) >= MIN_SECRET_LEN or not on_vercel):
            secret: bytes | None = raw.encode("utf-8")
        elif on_vercel:
            secret = None              # → 503 on every write; never fall back to a random secret in production
        else:
            secret = secrets.token_urlsafe(48).encode()   # dev: random per process
            generated = True
        public = (env.get("PIXEL_PUBLIC_URL") or "").strip().rstrip("/") or None
        return cls(
            secret=secret, secret_generated=generated,
            create_token=env.get("PIXEL_CREATE_TOKEN") or None,
            public_url=public, on_vercel=on_vercel,
            poll_active_ms=_int_env(env, "PIXEL_POLL_ACTIVE_MS", POLL_ACTIVE_MS, 3_000, 120_000),
            poll_calm_ms=_int_env(env, "PIXEL_POLL_CALM_MS", POLL_CALM_MS, 3_000, 120_000),
            poll_empty_ms=_int_env(env, "PIXEL_POLL_EMPTY_MS", POLL_EMPTY_MS, 3_000, 120_000),
            **relay_from_env(env, on_vercel),
        )


def relay_from_env(env: Mapping[str, str], on_vercel: bool = False) -> dict:
    """{relay_secret, relay_url}: both valid or both None (a half-configured relay is off, V3.md §13.1).
    `ws://127.0.0.1[:port]` is accepted for local development only, never on Vercel."""
    raw = (env.get("RELAY_SECRET") or "").strip()
    url = (env.get("PIXEL_RELAY_URL") or "").strip().rstrip("/")
    if len(raw) < MIN_SECRET_LEN or not _RELAY_URL_RE.match(url) or (on_vercel and not url.startswith("wss://")):
        return {"relay_secret": None, "relay_url": None}
    return {"relay_secret": raw.encode("utf-8"), "relay_url": url}
