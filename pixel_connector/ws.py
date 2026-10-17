"""Minimal RFC 6455 WebSocket client and server framing on stdlib `socket`/`ssl` (V3.md §2, Phase 12).

Used by the connector (client, Phase 13), `tools/dev_relay.py` (server) and the relay conformance suite (client).
Python ≥ 3.8 syntax, stdlib only. No extensions (no permessage-deflate), text and binary messages, fragmentation,
ping/pong (pings are answered automatically), close handshake with codes, a message size cap (→ close 1009).

    ws = connect("wss://relay.example/v1/rooms/<rid>/ws", protocols=["pixel-relay.v1", "tk." + ticket])
    ws.send_text('{"t":"hello","v":1}')
    msg = ws.recv(timeout=5)          # str (text) | bytes (binary); raises ConnectionClosed / socket.timeout

Server side: `read_http_request(sock)` → `HttpRequest`, `accept_response(key, protocol)` → bytes to send,
then `WebSocket(sock, client=False)`.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
import time
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL = 1002
CLOSE_UNSUPPORTED = 1003
CLOSE_NO_STATUS = 1005
CLOSE_ABNORMAL = 1006
CLOSE_INVALID_DATA = 1007
CLOSE_POLICY = 1008
CLOSE_TOO_BIG = 1009
CLOSE_INTERNAL = 1011

DEFAULT_MAX_MESSAGE = 1 << 20          # 1 MiB
MAX_HEADER_BYTES = 16 * 1024


class WebSocketError(Exception):
    pass


class HandshakeError(WebSocketError):
    """The server did not answer 101. `status`, `headers` and `body` (≤ 64 KiB) describe its answer."""

    def __init__(self, status: int, reason: str = "", headers: Optional[Dict[str, str]] = None, body: bytes = b"") -> None:
        super().__init__(f"handshake failed: HTTP {status} {reason}".strip())
        self.status = status
        self.reason = reason
        self.headers = headers or {}
        self.body = body


class ConnectionClosed(WebSocketError):
    def __init__(self, code: int = CLOSE_ABNORMAL, reason: str = "") -> None:
        super().__init__(f"connection closed: {code} {reason}".strip())
        self.code = code
        self.reason = reason


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


# -- HTTP helpers -------------------------------------------------------------------------------------------

class HttpRequest:
    def __init__(self, method: str, target: str, version: str, headers: Dict[str, str]) -> None:
        self.method = method
        self.target = target
        self.version = version
        self.headers = headers        # lower-case names; repeated headers joined with ", "

    def header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self.headers.get(name.lower(), default)


def _read_head(sock: socket.socket, buf: bytearray) -> bytes:
    while b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEADER_BYTES:
            raise WebSocketError("header too large")
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketError("connection closed during handshake")
        buf += chunk
    end = buf.index(b"\r\n\r\n") + 4
    head = bytes(buf[:end])
    del buf[:end]
    return head


def _parse_headers(lines: List[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for line in lines:
        if not line:
            continue
        if ":" not in line:
            raise WebSocketError("malformed header line")
        k, v = line.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        headers[k] = headers[k] + ", " + v if k in headers else v
    return headers


def read_http_request(sock: socket.socket, buf: Optional[bytearray] = None) -> HttpRequest:
    """Read one request head from a server socket. Leftover bytes stay in `buf` (pass it to WebSocket)."""
    buf = buf if buf is not None else bytearray()
    head = _read_head(sock, buf).decode("latin-1")
    lines = head.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        raise WebSocketError("malformed request line")
    return HttpRequest(parts[0], parts[1], parts[2], _parse_headers(lines[1:]))


def is_upgrade_request(req: HttpRequest) -> bool:
    conn = [p.strip().lower() for p in (req.header("connection") or "").split(",")]
    return (req.method == "GET" and (req.header("upgrade") or "").lower() == "websocket" and "upgrade" in conn
            and req.header("sec-websocket-version") == "13" and bool(req.header("sec-websocket-key")))


def accept_response(key: str, protocol: Optional[str] = None, extra: Optional[List[Tuple[str, str]]] = None) -> bytes:
    lines = ["HTTP/1.1 101 Switching Protocols", "Upgrade: websocket", "Connection: Upgrade",
             "Sec-WebSocket-Accept: " + accept_key(key)]
    if protocol:
        lines.append("Sec-WebSocket-Protocol: " + protocol)
    for k, v in extra or []:
        lines.append(f"{k}: {v}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def http_response(status: int, reason: str, body: bytes = b"", content_type: str = "application/json; charset=utf-8",
                  extra: Optional[List[Tuple[str, str]]] = None) -> bytes:
    lines = [f"HTTP/1.1 {status} {reason}", "Content-Type: " + content_type, f"Content-Length: {len(body)}",
             "Cache-Control: no-store", "Connection: close"]
    for k, v in extra or []:
        lines.append(f"{k}: {v}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


# -- the socket ---------------------------------------------------------------------------------------------

class WebSocket:
    def __init__(self, sock: socket.socket, client: bool = True, max_message: int = DEFAULT_MAX_MESSAGE,
                 subprotocol: Optional[str] = None, buffered: bytes = b"") -> None:
        self.sock = sock
        self.client = client
        self.max_message = max_message
        self.subprotocol = subprotocol
        self._buf = bytearray(buffered)
        self._parts: List[bytes] = []       # fragments of the message being received (survive timeouts)
        self._msg_op: Optional[int] = None
        self._size = 0
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()
        self.closed = False                 # our close frame was sent or the socket is gone
        self.close_code: Optional[int] = None
        self.close_reason = ""
        self.last_rx = time.monotonic()     # any bytes received (pongs included); the connector's dead-link check

    # -- low level --
    def _send_frame(self, opcode: int, payload: bytes, fin: bool = True) -> None:
        head = bytearray([(0x80 if fin else 0) | opcode])
        mask_bit = 0x80 if self.client else 0
        n = len(payload)
        if n < 126:
            head.append(mask_bit | n)
        elif n < 65536:
            head.append(mask_bit | 126)
            head += struct.pack("!H", n)
        else:
            head.append(mask_bit | 127)
            head += struct.pack("!Q", n)
        if self.client:
            mask = os.urandom(4)
            head += mask
            payload = _mask(payload, mask)
        with self._send_lock:
            self.sock.sendall(bytes(head) + payload)

    def _fill(self) -> None:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionClosed(CLOSE_ABNORMAL, "eof")
        self.last_rx = time.monotonic()
        self._buf += chunk

    def _read_frame(self) -> Tuple[bool, int, bytes]:
        """Parse one frame from the buffer; bytes are consumed only once the whole frame is there, so a
        socket.timeout in the middle of a frame leaves the stream intact."""
        while True:
            buf = self._buf
            if len(buf) >= 2:
                b0, b1 = buf[0], buf[1]
                fin, rsv, opcode = bool(b0 & 0x80), b0 & 0x70, b0 & 0x0F
                masked, n = bool(b1 & 0x80), b1 & 0x7F
                if rsv:
                    raise _Fail(CLOSE_PROTOCOL, "reserved bits set")
                if opcode >= 0x8 and (n > 125 or not fin):
                    raise _Fail(CLOSE_PROTOCOL, "bad control frame")
                if masked == self.client:           # servers must not mask, clients must
                    raise _Fail(CLOSE_PROTOCOL, "bad masking")
                pos = 2
                if n == 126:
                    if len(buf) < 4:
                        self._fill()
                        continue
                    n = struct.unpack("!H", bytes(buf[2:4]))[0]
                    pos = 4
                elif n == 127:
                    if len(buf) < 10:
                        self._fill()
                        continue
                    n = struct.unpack("!Q", bytes(buf[2:10]))[0]
                    pos = 10
                if n > self.max_message:
                    raise _Fail(CLOSE_TOO_BIG, "message too big")
                total = pos + (4 if masked else 0) + n
                if len(buf) >= total:
                    mask = bytes(buf[pos:pos + 4]) if masked else b""
                    start = pos + (4 if masked else 0)
                    payload = bytes(buf[start:total])
                    del buf[:total]
                    if masked:
                        payload = _mask(payload, mask)
                    return fin, opcode, payload
            self._fill()

    # -- public API --
    def send_text(self, text: str) -> None:
        if self.closed:
            raise ConnectionClosed(self.close_code or CLOSE_ABNORMAL, "already closed")
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionClosed(self.close_code or CLOSE_ABNORMAL, "already closed")
        self._send_frame(OP_BINARY, bytes(data))

    def send_raw_text_frames(self, parts: List[bytes]) -> None:
        """Send one text message split into several frames (tests)."""
        for i, p in enumerate(parts):
            self._send_frame(OP_TEXT if i == 0 else OP_CONT, p, fin=i == len(parts) - 1)

    def ping(self, data: bytes = b"") -> None:
        self._send_frame(OP_PING, data[:125])

    def recv(self, timeout: Optional[float] = None) -> Union[str, bytes]:
        """Next data message. Answers pings, skips pongs. Raises ConnectionClosed (with the peer's code) at the end,
        socket.timeout when `timeout` passes (the connection stays usable)."""
        with self._recv_lock:
            self.sock.settimeout(timeout)
            try:
                while True:
                    try:
                        fin, op, payload = self._read_frame()
                    except _Fail as f:
                        self._fail(f.code, f.reason)
                        raise ConnectionClosed(f.code, f.reason) from None
                    except (ConnectionResetError, BrokenPipeError, ssl.SSLError, OSError) as e:
                        if isinstance(e, socket.timeout):
                            raise
                        self._gone()
                        raise ConnectionClosed(CLOSE_ABNORMAL, type(e).__name__) from None
                    if op == OP_PING:
                        try:
                            self._send_frame(OP_PONG, payload)
                        except OSError:
                            pass
                        continue
                    if op == OP_PONG:
                        continue
                    if op == OP_CLOSE:
                        code, reason = _parse_close(payload)
                        self.close_code, self.close_reason = code, reason
                        if not self.closed:
                            try:
                                self._send_frame(OP_CLOSE, payload[:2] if code != CLOSE_NO_STATUS else b"")
                            except OSError:
                                pass
                        self._gone()
                        raise ConnectionClosed(code, reason)
                    if op in (OP_TEXT, OP_BINARY):
                        if self._msg_op is not None:
                            self._fail(CLOSE_PROTOCOL, "expected continuation")
                            raise ConnectionClosed(CLOSE_PROTOCOL, "expected continuation")
                        self._msg_op = op
                    elif op == OP_CONT:
                        if self._msg_op is None:
                            self._fail(CLOSE_PROTOCOL, "unexpected continuation")
                            raise ConnectionClosed(CLOSE_PROTOCOL, "unexpected continuation")
                    else:
                        self._fail(CLOSE_PROTOCOL, "unknown opcode")
                        raise ConnectionClosed(CLOSE_PROTOCOL, "unknown opcode")
                    self._size += len(payload)
                    if self._size > self.max_message:
                        self._fail(CLOSE_TOO_BIG, "message too big")
                        raise ConnectionClosed(CLOSE_TOO_BIG, "message too big")
                    self._parts.append(payload)
                    if fin:
                        data, op = b"".join(self._parts), self._msg_op
                        self._parts, self._msg_op, self._size = [], None, 0
                        if op == OP_BINARY:
                            return data
                        try:
                            return data.decode("utf-8")
                        except UnicodeDecodeError:
                            self._fail(CLOSE_INVALID_DATA, "invalid utf-8")
                            raise ConnectionClosed(CLOSE_INVALID_DATA, "invalid utf-8") from None
            finally:
                try:
                    self.sock.settimeout(None)
                except OSError:
                    pass

    def close(self, code: int = CLOSE_NORMAL, reason: str = "", wait: float = 0.0) -> None:
        """Send a close frame (once). Thread-safe while another thread blocks in recv(): that thread then receives
        the peer's close reply. With `wait` > 0, read (and discard) until the peer's close frame or `wait` seconds,
        then drop the socket."""
        if not self.closed:
            self.closed = True
            if self.close_code is None:
                self.close_code, self.close_reason = code, reason
            payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
            try:
                self._send_frame(OP_CLOSE, payload)
            except OSError:
                pass
        if wait > 0:
            deadline = time.monotonic() + wait
            try:
                while time.monotonic() < deadline:
                    self.recv(timeout=max(0.01, deadline - time.monotonic()))
            except (ConnectionClosed, OSError):
                pass
            self._gone()

    def _fail(self, code: int, reason: str) -> None:
        """Protocol failure on our side: close frame, then drain what the peer still sends (≤ 1 s) so that it reads
        our close frame instead of a TCP reset, then drop the socket."""
        self.close_code, self.close_reason = code, reason
        self.close(code, reason)
        self._gone(drain=True)

    def _gone(self, drain: bool = False) -> None:
        self.closed = True
        if drain:
            try:
                self.sock.shutdown(socket.SHUT_WR)
                self.sock.settimeout(0.2)
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline and self.sock.recv(65536):
                    pass
            except OSError:
                pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class _Fail(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _mask(data: bytes, mask: bytes) -> bytes:
    if not data:
        return data
    n = len(data)
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


def _parse_close(payload: bytes) -> Tuple[int, str]:
    if len(payload) < 2:
        return CLOSE_NO_STATUS, ""
    code = struct.unpack("!H", payload[:2])[0]
    return code, payload[2:].decode("utf-8", "replace")


# -- client -------------------------------------------------------------------------------------------------

def connect(url: str, protocols: Optional[List[str]] = None, origin: Optional[str] = None,
            headers: Optional[List[Tuple[str, str]]] = None, timeout: float = 10.0,
            ssl_context: Optional[ssl.SSLContext] = None, max_message: int = DEFAULT_MAX_MESSAGE) -> WebSocket:
    """Open a client WebSocket. Raises HandshakeError on a non-101 answer, OSError on network errors."""
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise ValueError("url must be ws:// or wss://")
    secure = parts.scheme == "wss"
    host = parts.hostname
    port = parts.port or (443 if secure else 80)
    target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        if secure:
            ctx = ssl_context or ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host_hdr = host if ":" not in host else f"[{host}]"
        if parts.port:
            host_hdr += f":{parts.port}"
        lines = [f"GET {target} HTTP/1.1", f"Host: {host_hdr}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13", "User-Agent: pixel-connector-ws/1"]
        if protocols:
            lines.append("Sec-WebSocket-Protocol: " + ", ".join(protocols))
        if origin is not None:
            lines.append("Origin: " + origin)
        for k, v in headers or []:
            lines.append(f"{k}: {v}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        buf = bytearray()
        head = _read_head(sock, buf).decode("latin-1")
        hl = head.split("\r\n")
        status_parts = hl[0].split(" ", 2)
        if len(status_parts) < 2 or not status_parts[1].isdigit():
            raise WebSocketError("malformed status line")
        status = int(status_parts[1])
        resp_headers = _parse_headers(hl[1:])
        if status != 101:
            body = bytes(buf)
            try:
                n = int(resp_headers.get("content-length", "0"))
                sock.settimeout(min(timeout, 5.0))
                while len(body) < min(n, 65536):
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    body += chunk
            except (OSError, ValueError):
                pass
            raise HandshakeError(status, status_parts[2] if len(status_parts) > 2 else "", resp_headers, body[:65536])
        if resp_headers.get("sec-websocket-accept") != accept_key(key) \
                or (resp_headers.get("upgrade") or "").lower() != "websocket":
            raise WebSocketError("bad handshake answer")
        proto = resp_headers.get("sec-websocket-protocol")
        if proto is not None and (not protocols or proto not in protocols):
            raise WebSocketError("server chose an unoffered subprotocol")
        sock.settimeout(None)
        return WebSocket(sock, client=True, max_message=max_message, subprotocol=proto, buffered=bytes(buf))
    except BaseException:
        try:
            sock.close()
        except OSError:
            pass
        raise
