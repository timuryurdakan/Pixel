"""L1 relay client for the connector (V3.md §3.1–§3.3, §5.1).

A fresh ticket (`POST /api/relay_ticket`, write key) for every attempt, `hello`, a protocol ping every 30 s, the
connection is considered dead after 75 s without any received byte, reconnect backoff 1 → 30 s with ±20 % jitter and
5 minutes after 10 consecutive failures. The connector sends no Origin header (the relay requires that for devices).
"""

from __future__ import annotations

import json
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from . import __version__
from . import ws as W

PING_S = 30.0
DEAD_S = 75.0
BACKOFF = (1, 2, 4, 8, 16, 30)
LONG_BACKOFF = 300.0


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
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read(65536))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(4096)).get("error", "")
        except Exception:
            msg = ""
        raise TicketError(e.code, msg) from None
    if not isinstance(d, dict) or not isinstance(d.get("url"), str) or not isinstance(d.get("ticket"), str):
        raise TicketError(0, "bad ticket answer")
    return d


class RelayClient:
    """Runs on its own thread. `on_frame(dict)` gets every parsed L1 frame; `on_status(str)` gets
    connecting | live | reconnecting | stopped | error:<reason>; `on_connect()` runs after `welcome`."""

    def __init__(self, server: str, room: str, key: str, device_id: str, on_frame: Callable[[dict], None],
                 on_status: Optional[Callable[[str], None]] = None, on_connect: Optional[Callable[[], None]] = None,
                 ticket_fn: Optional[Callable[[], dict]] = None, ping_s: float = PING_S, dead_s: float = DEAD_S,
                 backoff=BACKOFF) -> None:
        self.server, self.room, self.key, self.device_id = server, room, key, device_id
        self.on_frame = on_frame
        self.on_status = on_status or (lambda s: None)
        self.on_connect = on_connect or (lambda: None)
        self.ticket_fn = ticket_fn or (lambda: fetch_ticket(server, room, key, device_id))
        self.ping_s, self.dead_s, self.backoff = ping_s, dead_s, backoff
        self.ws: Optional[W.WebSocket] = None
        self.status = "idle"
        self.welcome: Optional[dict] = None
        self.last_error: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.connected = threading.Event()

    def _set(self, s: str) -> None:
        self.status = s
        try:
            self.on_status(s)
        except Exception:
            pass

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="relay", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        ws = self.ws
        if ws is not None:
            try:
                ws.close(1000, "bye")
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout)
        self._set("stopped")

    def send(self, frame: dict) -> bool:
        ws = self.ws
        if ws is None or ws.closed or not self.connected.is_set():
            return False
        try:
            ws.send_text(json.dumps(frame, separators=(",", ":")))
            return True
        except (OSError, W.WebSocketError):
            return False

    def _sleep(self, failures: int) -> None:
        if failures >= 10:
            delay = LONG_BACKOFF
        else:
            delay = self.backoff[min(failures, len(self.backoff)) - 1] if failures else self.backoff[0]
        delay *= 1 + random.uniform(-0.2, 0.2)
        self._stop.wait(delay)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            self._set("connecting" if failures == 0 else "reconnecting")
            ok = False
            try:
                t = self.ticket_fn()
                ws = W.connect(t["url"], protocols=["pixel-relay.v1", "tk." + t["ticket"]], timeout=15,
                               max_message=200 * 1024)
                self.ws = ws
                ok = self._session(ws)
            except TicketError as e:
                self.last_error = "ticket:%s" % e.status
                self._set("error:" + self.last_error)
                if e.status in (400, 401, 403, 404):
                    failures = max(failures, 9)          # misconfigured: back off hard (5 min)
            except W.HandshakeError as e:
                self.last_error = "handshake:%s" % e.status
            except (OSError, W.WebSocketError, ValueError, KeyError) as e:
                self.last_error = type(e).__name__
            finally:
                self.connected.clear()
                ws, self.ws = self.ws, None
                if ws is not None:
                    try:
                        ws.close(1000, "")
                        ws._gone()
                    except Exception:
                        pass
            if self._stop.is_set():
                break
            failures = 1 if ok else failures + 1
            self._sleep(failures)
        self._set("stopped")

    def _session(self, ws: W.WebSocket) -> bool:
        """Serve one connection. → True if it was live (welcome received)."""
        ws.send_text(json.dumps({"t": "hello", "v": 1, "app": "connector/" + __version__, "caps": ["e2e1"]}))
        live = False
        last_ping = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now - ws.last_rx > self.dead_s:
                self.last_error = "dead"
                return live
            if now - last_ping >= self.ping_s:
                try:
                    ws.ping(b"p")
                except OSError:
                    return live
                last_ping = now
            try:
                msg = ws.recv(timeout=min(1.0, self.ping_s))
            except socket.timeout:
                continue
            except OSError:
                return live
            except W.ConnectionClosed as e:
                self.last_error = "closed:%s" % e.code
                if e.code == 4009:
                    self._stop.wait(5)                  # replaced by another instance: don't fight over the slot
                return live
            if not isinstance(msg, str):
                continue
            try:
                f = json.loads(msg)
            except ValueError:
                continue
            if not isinstance(f, dict):
                continue
            if f.get("t") == "welcome" and not live:
                live = True
                self.welcome = f
                self.connected.set()
                self._set("live")
                try:
                    self.on_connect()
                except Exception:
                    pass
                continue
            try:
                self.on_frame(f)
            except Exception:
                pass
        return live
