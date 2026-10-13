"""Room storage: one Redis hash per room (CLOUD.md §4).

`UpstashStorage` talks to the Upstash REST API with stdlib `http.client`; `MemoryStorage` is the
process-local dev/test fallback. `get_storage()` picks one from the environment.
"""

from __future__ import annotations

import http.client
import json
import os
import ssl
import threading
import time
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit

from . import settings as S

KEY_PREFIX = "po:room:"


def room_key(room: str) -> str:
    return KEY_PREFIX + room


class StorageError(Exception):
    """Storage unreachable or returned an error → HTTP 503 storage_unavailable. Never carries the token."""


class StorageNotConfigured(StorageError):
    """Running on Vercel without Upstash env vars → HTTP 503 storage_not_configured."""


class Storage(Protocol):
    name: str

    def put_agents(self, room: str, items: dict[str, str], refresh_ttl: bool) -> dict[str, bool]: ...
    def count(self, room: str) -> int: ...
    def keys(self, room: str) -> list[str]: ...
    def delete(self, room: str, fields: list[str]) -> None: ...
    def read_room(self, room: str) -> dict[str, str]: ...
    def create_room(self, room: str, meta_json: str) -> None: ...
    def ping(self) -> bool: ...


# -- memory ----------------------------------------------------------------------------------------

class MemoryStorage:
    """Thread-safe, process-local. Honours key TTLs with an injectable clock (seconds)."""
    name = "memory"

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, str]] = {}
        self._expires: dict[str, float] = {}
        self.commands = 0          # Redis-equivalent command count (tests / budget checks)

    def _live(self, key: str) -> dict[str, str] | None:
        exp = self._expires.get(key)
        if exp is not None and self._clock() >= exp:
            self._data.pop(key, None)
            self._expires.pop(key, None)
        return self._data.get(key)

    def _expire(self, key: str, ttl: int) -> None:
        if key in self._data:
            self._expires[key] = self._clock() + ttl

    def put_agents(self, room: str, items: dict[str, str], refresh_ttl: bool) -> dict[str, bool]:
        key = room_key(room)
        with self._lock:
            h = self._live(key)
            if h is None:
                h = self._data[key] = {}
            created = {}
            for f, v in items.items():
                created[f] = f not in h
                h[f] = v
                self.commands += 1
            if refresh_ttl:
                self._expire(key, S.ROOM_TTL)
                self.commands += 1
            return created

    def count(self, room: str) -> int:
        with self._lock:
            self.commands += 1
            return len(self._live(room_key(room)) or {})

    def keys(self, room: str) -> list[str]:
        with self._lock:
            self.commands += 1
            return list(self._live(room_key(room)) or {})

    def delete(self, room: str, fields: list[str]) -> None:
        if not fields:
            return
        key = room_key(room)
        with self._lock:
            self.commands += 1
            h = self._live(key)
            if h is None:
                return
            for f in fields:
                h.pop(f, None)
            if not h:                       # Redis deletes empty hashes
                self._data.pop(key, None)
                self._expires.pop(key, None)

    def read_room(self, room: str) -> dict[str, str]:
        with self._lock:
            self.commands += 1
            return dict(self._live(room_key(room)) or {})

    def create_room(self, room: str, meta_json: str) -> None:
        key = room_key(room)
        with self._lock:
            self.commands += 2
            h = self._live(key)
            if h is None:
                h = self._data[key] = {}
            h["_meta"] = meta_json
            self._expire(key, S.ROOM_TTL)

    def ping(self) -> bool:
        with self._lock:
            self.commands += 1
        return True

    def ttl(self, room: str) -> float | None:   # test helper
        with self._lock:
            self._live(room_key(room))
            exp = self._expires.get(room_key(room))
            return None if exp is None else exp - self._clock()


# -- upstash ---------------------------------------------------------------------------------------

class UpstashStorage:
    """Upstash Redis REST: POST / with a JSON command array, POST /pipeline with a 2-D array."""
    name = "upstash"

    def __init__(self, url: str, token: str, timeout: float = S.UPSTASH_TIMEOUT) -> None:
        parts = urlsplit(url.strip())
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise StorageError("invalid Upstash REST URL")
        self._https = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port
        self._base = parts.path.rstrip("/")
        self._token = token.strip()
        self._timeout = timeout
        self._conn: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()   # one keep-alive connection per warm instance; serialise use

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __repr__(self) -> str:          # never show the token
        return f"UpstashStorage(host={self._host!r})"

    def _connect(self) -> http.client.HTTPConnection:
        if self._https:
            return http.client.HTTPSConnection(self._host, self._port, timeout=self._timeout,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)

    def _post(self, path: str, payload: list) -> object:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        with self._lock:
            for attempt in (1, 2):           # reconnect once on a dead keep-alive connection
                if self._conn is None:
                    self._conn = self._connect()
                try:
                    self._conn.request("POST", self._base + path, body=body, headers=headers)
                    resp = self._conn.getresponse()
                    raw = resp.read()
                    status = resp.status
                    if resp.will_close:
                        self._conn.close()
                        self._conn = None
                    break
                except (OSError, http.client.HTTPException) as e:
                    try:
                        self._conn.close()
                    except Exception:  # pragma: no cover
                        pass
                    self._conn = None
                    if attempt == 2:
                        raise StorageError(f"storage request failed: {type(e).__name__}") from None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise StorageError(f"storage returned a non-JSON response (HTTP {status})") from None
        if status != 200:
            msg = data.get("error") if isinstance(data, dict) else None
            raise StorageError(f"storage HTTP {status}: {str(msg)[:80] if msg else 'error'}")
        return data

    @staticmethod
    def _result(item: object) -> object:
        if not isinstance(item, dict):
            raise StorageError("malformed storage response")
        if "error" in item:
            raise StorageError(f"storage error: {str(item['error'])[:80]}")
        return item.get("result")

    def command(self, *args: object) -> object:
        return self._result(self._post("/", list(args)))

    def pipeline(self, cmds: list[list[object]]) -> list[object]:
        if not cmds:
            return []
        data = self._post("/pipeline", cmds)
        if not isinstance(data, list) or len(data) != len(cmds):
            raise StorageError("malformed pipeline response")
        return [self._result(x) for x in data]

    def put_agents(self, room: str, items: dict[str, str], refresh_ttl: bool) -> dict[str, bool]:
        key = room_key(room)
        fields = list(items)
        cmds: list[list[object]] = [["HSET", key, f, items[f]] for f in fields]
        if refresh_ttl:
            cmds.append(["EXPIRE", key, S.ROOM_TTL])
        res = self.pipeline(cmds) if len(cmds) > 1 else [self.command(*cmds[0])]
        return {f: res[i] == 1 for i, f in enumerate(fields)}

    def count(self, room: str) -> int:
        r = self.command("HLEN", room_key(room))
        return int(r) if isinstance(r, int) else 0

    def keys(self, room: str) -> list[str]:
        r = self.command("HKEYS", room_key(room))
        return [x for x in r if isinstance(x, str)] if isinstance(r, list) else []

    def delete(self, room: str, fields: list[str]) -> None:
        if fields:
            self.command("HDEL", room_key(room), *fields)

    def read_room(self, room: str) -> dict[str, str]:
        r = self.command("HGETALL", room_key(room))
        if isinstance(r, dict):          # some REST variants return an object
            return {str(k): str(v) for k, v in r.items()}
        if not isinstance(r, list):
            return {}
        return {str(r[i]): str(r[i + 1]) for i in range(0, len(r) - 1, 2)}

    def create_room(self, room: str, meta_json: str) -> None:
        key = room_key(room)
        self.pipeline([["HSET", key, "_meta", meta_json], ["EXPIRE", key, S.ROOM_TTL]])

    def ping(self) -> bool:
        try:
            return self.command("PING") == "PONG"
        except StorageError:
            return False


class UnconfiguredStorage:
    """On Vercel without Upstash env vars: every data call fails with 503 storage_not_configured."""
    name = "unconfigured"

    def _fail(self, *a, **k):
        raise StorageNotConfigured("storage is not configured")

    put_agents = count = keys = delete = read_room = create_room = _fail

    def ping(self) -> bool:
        return False


# -- selection -------------------------------------------------------------------------------------

_URL_SUFFIXES = ("KV_REST_API_URL", "REDIS_REST_URL", "REST_API_URL")


def _upstash_env(env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Upstash REST credentials. The Vercel Marketplace lets the user pick any prefix (KV_, STORAGE_, …),
    so after the well-known names, accept any `<prefix>_…REST_API_URL` with its matching `…_TOKEN`."""
    for u, t in (("KV_REST_API_URL", "KV_REST_API_TOKEN"), ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN")):
        if env.get(u) and env.get(t):
            return env[u], env[t]
    for name in sorted(env):
        if name.endswith(_URL_SUFFIXES) and env.get(name):
            tok = env.get(name[:-len("URL")] + "TOKEN")
            if tok:
                return env[name], tok
    return None, None


def storage_from_env(env: Mapping[str, str] | None = None, clock: Callable[[], float] = time.time):
    env = os.environ if env is None else env
    url, token = _upstash_env(env)
    if url and token:
        try:
            return UpstashStorage(url, token)
        except StorageError:
            return UnconfiguredStorage()
    if env.get("VERCEL"):
        return UnconfiguredStorage()
    return MemoryStorage(clock)


_cached = None
_cached_lock = threading.Lock()


def get_storage():
    """Cached per process (one keep-alive connection per warm instance)."""
    global _cached
    with _cached_lock:
        if _cached is None:
            _cached = storage_from_env()
        return _cached


def reset_storage() -> None:
    global _cached
    with _cached_lock:
        _cached = None
