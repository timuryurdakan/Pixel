"""L1 relay client for the connector (V3.md §3.1–§3.3, §5.1).

A fresh ticket (`POST /api/relay_ticket`, write key) for every attempt, `hello`, a protocol ping every 30 s, the
connection is considered dead after 75 s without any received byte. The connector sends no Origin header (the relay
requires that for devices).

Reconnect (v2.md H6): network-type failures back off 1 → 2 → 4 … → 60 s (±20 % jitter) and never longer; only a ticket
refused with 401/403/404 (wrong key, unknown room: a configuration problem) waits 5 minutes. A connection that stayed
live for 30 s resets the backoff. Every status change is logged with its reason (`last_error`, never a secret).

One I/O thread (v2.md H5): reads and writes share the socket (and its TLS object, which must not be used by two threads
at once), so `send()` only queues the frame and wakes the relay thread, which writes queued frames between reads with a
write deadline of its own. Reads wait in select() rather than under a socket timeout that writes would inherit. Frames
still queued when a connection ends are counted and logged, never dropped silently.
"""

from __future__ import annotations

import json
import random
import re
import select
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Callable, Deque, Optional, Tuple

from . import __version__
from . import localhttp
from . import ws as W

PING_S = 30.0
DEAD_S = 75.0
BACKOFF = (1, 2, 4, 8, 16, 32, 60)          # seconds; the last value is the cap for network-type failures
LONG_BACKOFF = 300.0                        # ticket refused (401/403/404): misconfigured, don't hammer the server
LONG_BACKOFF_STATUSES = (401, 403, 404)
STABLE_S = 30.0                             # a connection live this long resets the backoff
JITTER = 0.2
READ_SLICE_S = 1.0                          # longest a read may hold the I/O thread before queued writes go out
WRITE_TIMEOUT_S = 30.0                      # deadline for one queued frame (a 56 KiB chunk on a slow uplink)
MAX_OUTBOX_BYTES = 8 * 1024 * 1024
# The relay URL from the ticket answer: wss:// anywhere, plain ws:// only to the local dev relay (same rule as the
# browser's relay.js), and always the room's own WebSocket path (Phase 15: the ticket never goes out in clear text).
RELAY_URL_RE = re.compile(r"^(?:wss://[A-Za-z0-9.-]+(?::\d{1,5})?|ws://127\.0\.0\.1(?::\d{1,5})?)"
                          r"/v1/rooms/([a-z2-7]{26})/ws\Z")


class TicketError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        super().__init__("ticket HTTP %s %s" % (status, message))
        self.status = status


def fetch_ticket(server: str, room: str, key: str, device_id: str, timeout: float = 10.0) -> dict:
    url = "%s/api/relay_ticket?room=%s" % (server.rstrip("/"), room)
    body = json.dumps({"role": "device", "id": device_id}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                          "User-Agent": "pixel-connector/" + __version__})
    try:
        with localhttp.urlopen(req, timeout=timeout) as r:      # a local dev server: no proxy (H8)
            d = json.loads(r.read(65536))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(4096)).get("error", "")
        except Exception:
            msg = ""
        raise TicketError(e.code, msg) from None
    if not isinstance(d, dict) or not isinstance(d.get("url"), str) or not isinstance(d.get("ticket"), str):
        raise TicketError(0, "bad ticket answer")
    m = RELAY_URL_RE.match(d["url"])
    if m is None or m.group(1) != room:
        raise TicketError(0, "bad relay url")
    return d


class RelayClient:
    """Runs on its own thread. `on_frame(dict)` gets every parsed L1 frame; `on_status(str)` gets
    connecting | live | reconnecting | stopped | error:<reason>; `on_connect()` runs after `welcome`; `log(str)` gets one
    Turkish line per status change and per lost batch of frames."""

    def __init__(self, server: str, room: str, key: str, device_id: str, on_frame: Callable[[dict], None],
                 on_status: Optional[Callable[[str], None]] = None, on_connect: Optional[Callable[[], None]] = None,
                 ticket_fn: Optional[Callable[[], dict]] = None, ping_s: float = PING_S, dead_s: float = DEAD_S,
                 backoff=BACKOFF, log: Optional[Callable[[str], None]] = None,
                 connect_fn: Optional[Callable[..., W.WebSocket]] = None,
                 rand: Callable[[float, float], float] = random.uniform) -> None:
        self.server, self.room, self.key, self.device_id = server, room, key, device_id
        self.on_frame = on_frame
        self.on_status = on_status or (lambda s: None)
        self.on_connect = on_connect or (lambda: None)
        self.log = log or (lambda s: None)
        self.ticket_fn = ticket_fn or (lambda: fetch_ticket(server, room, key, device_id))
        self.connect_fn = connect_fn or W.connect
        self.ping_s, self.dead_s, self.backoff = ping_s, dead_s, tuple(backoff)
        self.rand = rand
        self.ws: Optional[W.WebSocket] = None
        self.status = "idle"
        self.welcome: Optional[dict] = None
        self.last_error: Optional[str] = None
        self.last_error_at: Optional[float] = None          # time.time() of the last failure
        self.lost_frames = 0                                # frames queued but never written (whole run)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._outbox: Deque[str] = deque()
        self._outbox_bytes = 0
        self._wake_r, self._wake_w = socket.socketpair()
        for s in (self._wake_r, self._wake_w):
            s.setblocking(False)
        self.connected = threading.Event()
        self._logged_status: Optional[Tuple[str, Optional[str]]] = None

    # -- status ----------------------------------------------------------------------------------
    def _set(self, s: str) -> None:
        self.status = s
        try:
            self.on_status(s)
        except Exception:
            pass

    def _fail(self, reason: str) -> None:
        self.last_error = _clean_reason(reason, (self.key,))
        self.last_error_at = time.time()

    def _announce(self, status: str, reason: Optional[str] = None, delay: Optional[float] = None) -> None:
        """One log line per change (E5): "Röle: reconnecting (sebep: ticket:401; 300 sn sonra yeniden denenecek)"."""
        if self._logged_status == (status, reason):
            return
        self._logged_status = (status, reason)
        extra = []
        if reason:
            extra.append("sebep: " + reason)
        if delay is not None:
            extra.append("%.0f sn sonra yeniden denenecek" % delay)
        self._log("Röle: %s%s" % (status, " (%s)" % "; ".join(extra) if extra else ""))

    def _log(self, line: str) -> None:
        try:
            self.log(line)
        except Exception:
            pass

    # -- lifecycle -------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="relay", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """The relay thread itself sends the close frame (no second thread touches the socket)."""
        self._stop.set()
        self._wake()
        if self._thread is not None:
            self._thread.join(timeout)
        self._set("stopped")
        for s in (self._wake_r, self._wake_w):
            try:
                s.close()
            except OSError:
                pass

    # -- outbound --------------------------------------------------------------------------------
    def send(self, frame: dict) -> bool:
        """Queue one L1 frame for the relay thread. → False (and counted) when not connected or the queue is full."""
        if self.ws is None or self.ws.closed or not self.connected.is_set():
            return False
        text = json.dumps(frame, separators=(",", ":"))
        with self._lock:
            if self._outbox_bytes + len(text) > MAX_OUTBOX_BYTES:
                self.lost_frames += 1
                full = True
            else:
                self._outbox.append(text)
                self._outbox_bytes += len(text)
                full = False
        if full:
            self._log("Röle: gönderim kuyruğu dolu, 1 mesaj gönderilemedi")
            return False
        self._wake()
        return True

    def queued(self) -> int:
        with self._lock:
            return len(self._outbox)

    def _wake(self) -> None:
        try:
            self._wake_w.send(b"x")
        except (BlockingIOError, OSError):
            pass                                # already awake (buffer full) or closed during stop()

    def _drain_wake(self) -> None:
        try:
            while self._wake_r.recv(4096):
                pass
        except (BlockingIOError, OSError):
            pass

    def _flush(self, ws: W.WebSocket) -> None:
        """Write every queued frame (relay thread only). Raises OSError/WebSocketError when the link breaks; the frame
        being written then counts as lost together with whatever is still queued."""
        while True:
            with self._lock:
                if not self._outbox:
                    return
                text = self._outbox[0]
            ws.send_text(text, timeout=WRITE_TIMEOUT_S)
            with self._lock:
                if self._outbox and self._outbox[0] is text:
                    self._outbox.popleft()
                    self._outbox_bytes -= len(text)

    def _drop_outbox(self, reason: str) -> None:
        with self._lock:
            n = len(self._outbox)
            self._outbox.clear()
            self._outbox_bytes = 0
            self.lost_frames += n
        if n:
            self._log("Röle: %d mesaj gönderilemedi (%s)" % (n, reason))

    # -- reconnect loop --------------------------------------------------------------------------
    def delay_for(self, failures: int, long_wait: bool) -> float:
        if long_wait:
            base = LONG_BACKOFF
        else:
            base = self.backoff[max(0, min(failures, len(self.backoff)) - 1)]
        return base * (1 + self.rand(-JITTER, JITTER))

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            if failures == 0:
                self._set("connecting")
                self._announce("connecting")
            live_for = 0.0
            long_wait = False
            try:
                t = self.ticket_fn()
                ws = self.connect_fn(t["url"], protocols=["pixel-relay.v1", "tk." + t["ticket"]], timeout=15,
                                     max_message=200 * 1024)
                self.ws = ws
                live_for = self._session(ws)
            except TicketError as e:
                self._fail("ticket:%s" % e.status)
                self._set("error:" + self.last_error)
                long_wait = e.status in LONG_BACKOFF_STATUSES
            except W.HandshakeError as e:
                self._fail("handshake:%s" % e.status)
            except (OSError, W.WebSocketError, ValueError, KeyError) as e:
                self._fail(_exc_reason(e))
            finally:
                self.connected.clear()
                ws, self.ws = self.ws, None
                if ws is not None:
                    try:
                        ws.close(1000, "bye" if self._stop.is_set() else "")
                        ws._gone()
                    except Exception:
                        pass
                self._drop_outbox("bağlantı kapandı" if self._stop.is_set() else
                                  "bağlantı koptu: %s" % (self.last_error or "?"))
            if self._stop.is_set():
                break
            failures = 1 if live_for >= STABLE_S else failures + 1
            delay = self.delay_for(failures, long_wait)
            self._set("reconnecting")
            self._announce("reconnecting", self.last_error, delay)
            self._stop.wait(delay)
        self._set("stopped")
        self._announce("stopped")

    def _session(self, ws: W.WebSocket) -> float:
        """Serve one connection. → seconds it was live (0.0 when no welcome came)."""
        ws.send_text(json.dumps({"t": "hello", "v": 1, "app": "connector/" + __version__, "caps": ["e2e1"]}),
                     timeout=WRITE_TIMEOUT_S)
        live_since: Optional[float] = None
        last_ping = time.monotonic()

        def live_for() -> float:
            return time.monotonic() - live_since if live_since is not None else 0.0

        while not self._stop.is_set():
            now = time.monotonic()
            if now - ws.last_rx > self.dead_s:
                self._fail("dead")
                return live_for()
            try:
                if now - last_ping >= self.ping_s:
                    ws.ping(b"p", timeout=WRITE_TIMEOUT_S)
                    last_ping = now
                self._flush(ws)
            except (OSError, W.WebSocketError) as e:
                self._fail("write: " + _exc_reason(e))
                return live_for()
            if not ws.pending():
                try:
                    ready, _, _ = select.select([ws.sock, self._wake_r], [], [], min(READ_SLICE_S, self.ping_s))
                except (OSError, ValueError) as e:
                    self._fail(_exc_reason(e))
                    return live_for()
                if self._wake_r in ready:
                    self._drain_wake()
                if ws.sock not in ready:
                    continue
            try:
                msg = ws.recv(timeout=READ_SLICE_S)
            except socket.timeout:
                continue
            except W.ConnectionClosed as e:
                self._fail("closed:%s" % e.code)
                if e.code == 4009:
                    self._stop.wait(5)                  # replaced by another instance: don't fight over the slot
                return live_for()
            except OSError as e:
                self._fail(_exc_reason(e))
                return live_for()
            if not isinstance(msg, str):
                continue
            try:
                f = json.loads(msg)
            except ValueError:
                continue
            if not isinstance(f, dict):
                continue
            if f.get("t") == "welcome" and live_since is None:
                live_since = time.monotonic()
                self.welcome = f
                self.connected.set()
                self._set("live")
                self._announce("live")
                try:
                    self.on_connect()
                except Exception:
                    pass
                continue
            try:
                self.on_frame(f)
            except Exception:
                pass
        return live_for()


def _exc_reason(e: BaseException) -> str:
    """Short, secret-free reason: exception class plus its message (errno text, TLS reason), at most 120 chars."""
    text = " ".join(str(e).split())
    return (type(e).__name__ + (": " + text if text else ""))[:120]


def _clean_reason(reason: str, secrets_: tuple) -> str:
    out = reason
    for s in secrets_:
        if s and len(s) >= 8 and s in out:
            out = out.replace(s, "***")
    return "".join(ch if ch.isprintable() else " " for ch in out)[:160]
