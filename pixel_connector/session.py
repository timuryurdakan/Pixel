"""Device side of pairing (§7.3), the per-connection handshake (§7.4) and encrypted channels (§7.5).

`Router.handle_frame(frame)` takes every L1 frame the relay delivers to the device. Handshake bodies (`k: "hs"`) are
base64url(JSON); everything else (`k: "e"`) is a sealed inner message. Decrypted inner messages go to `on_message(ch,
obj)`. Failures never produce a reply (no error oracle); they are written to the audit log.

Pairing bodies (all `k: "hs"`, `ch: null`):
    browser → pair:<slot>   {"t":"pair1","v":1,"slot","qB","nB","label"}
    device  → c:<conn>      {"t":"pair2","v":1,"deviceId","qC","nC"}
    browser → <deviceId>    {"t":"pair3","v":1,"macB"}
    device  → c:<conn>      {"t":"pair4","ok":true,"macC","name"} | {"t":"pair4","ok":false,"reason":"code"|"refused"|"timeout"}
Handshake:
    browser → <deviceId>    {"t":"hs1","v":1,"bid","eB","nb"}
    device  → c:<conn>      {"t":"hs2","v":1,"eC","nc","ch","mac"}
    browser → <deviceId>    {"t":"hs3","ch","mac"}            (L1 ch = ch)
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from typing import Callable, Dict, List, Optional

from . import crypto as C

CONN_RE = re.compile(r"^c:[A-Za-z0-9_-]{12}\Z")
BID_RE = re.compile(r"^[a-z2-7]{16}\Z")
MAX_INNER = 56 * 1024
HS_PENDING_S = 30.0
PAIR3_WAIT_S = 120.0
SAS_TIMEOUT_S = 60.0
CHANNEL_MAX_AGE_S = 24 * 3600


def encode_body(obj) -> str:
    return C.b64u(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def decode_body(b) -> dict:
    obj = json.loads(C.unb64u(b).decode("utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("t"), str):
        raise ValueError("bad body")
    return obj


class Peer:
    """An open E2E channel to one browser connection."""

    def __init__(self, conn: str, bid: str, label: str, channel: C.Channel, created: float) -> None:
        self.conn = conn
        self.bid = bid
        self.label = label
        self.channel = channel
        self.created = created
        self.watching = set()       # agent keys (agent.watch)

    @property
    def ch(self) -> str:
        return self.channel.ch


class PairingSession:
    """One pairing code. States: opening → open → wait3 → confirm → done | failed | expired."""

    def __init__(self, router: "Router", code: str, iterations: int, ttl: float) -> None:
        self.router = router
        self.code = code
        self.slot, self.secret = code[:4], code[4:]
        self.display = C.format_code(code)
        self.created = router.clock()
        self.expires = self.created + ttl
        self.state = "opening"
        self.reason: Optional[str] = None
        self.n_c = secrets.token_bytes(16)
        self.conn: Optional[str] = None
        self.bid: Optional[str] = None
        self.q_b: Optional[bytes] = None
        self.n_b: Optional[bytes] = None
        self.label: Optional[str] = None
        self.sas: Optional[str] = None
        self.t: Optional[bytes] = None
        self.confirm_deadline = 0.0
        self.wait3_deadline = 0.0
        self.changed = threading.Condition()
        self._k: Optional[bytes] = None
        self._k_ready = threading.Event()
        threading.Thread(target=self._derive, args=(iterations,), daemon=True, name="pair-pbkdf2").start()

    def _derive(self, iterations: int) -> None:
        self._k = C.pairing_key(self.slot, self.secret, iterations)
        self._k_ready.set()

    def key(self) -> bytes:
        self._k_ready.wait(30)
        if self._k is None:
            raise RuntimeError("pairing key unavailable")
        return self._k

    def _set(self, state: str, reason: Optional[str] = None) -> None:
        with self.changed:
            self.state = state
            if reason:
                self.reason = reason
            self.changed.notify_all()

    def info(self) -> dict:
        return {"code": self.display, "state": self.state, "reason": self.reason, "expiresAt": int(self.expires * 1000),
                "label": self.label, "bid": self.bid, "sas": self.sas if self.state == "confirm" else None}

    def wait(self, states, timeout: float) -> str:
        end = time.monotonic() + timeout
        with self.changed:
            while self.state not in states:
                left = end - time.monotonic()
                if left <= 0:
                    break
                self.changed.wait(min(left, 0.5))
            return self.state

    def confirm(self, ok: bool) -> bool:
        """Answer the SAS question. → False if the session was not waiting for an answer."""
        return self.router._pair_confirm(self, bool(ok))


class Router:
    def __init__(self, key, peers, audit, send_l1: Callable[[dict], bool],
                 on_message: Callable[[Peer, dict], None], device_name: str = "Cihaz",
                 clock: Callable[[], float] = time.time, handshakes_per_hour: int = 20,
                 on_peer_closed: Optional[Callable[[Peer], None]] = None) -> None:
        self.key = key
        self.peers = peers
        self.audit = audit
        self.send_l1 = send_l1
        self.on_message = on_message
        self.on_peer_closed = on_peer_closed
        self.device_name = device_name
        self.clock = clock
        self.hs_limit = handshakes_per_hour
        self._lock = threading.RLock()
        self.channels: Dict[str, Peer] = {}          # conn → Peer (one channel per browser connection)
        self._pending: Dict[str, dict] = {}          # conn → handshake waiting for hs3
        self._hs_times: Dict[str, List[float]] = {}  # bid → handshake timestamps (rate limit)
        self._hs_seq: Dict[str, int] = {}            # conn → seq counter for k=hs sends
        self.pairing: Optional[PairingSession] = None
        self.pair_iterations = C.PAIR_ITERATIONS
        self.pair_ttl = float(C.PAIR_TTL_S)

    # -- outbound --------------------------------------------------------------------------------
    def _send_hs(self, conn: str, body: dict, ch: Optional[str] = None) -> bool:
        with self._lock:
            seq = self._hs_seq.get(conn, 0)
            self._hs_seq[conn] = seq + 1
        return self.send_l1({"t": "send", "to": conn, "ch": ch, "seq": seq, "k": "hs", "b": encode_body(body)})

    def send(self, peer: Peer, obj: dict) -> bool:
        """Seal and send one inner message; frames leave in seq order. → False when the channel/socket is gone."""
        if peer.channel.closed:
            return False
        size = len(C.encode_inner(obj))
        if size > MAX_INNER:
            raise ValueError("inner message too large (%d bytes)" % size)
        with peer.channel.send_lock:
            try:
                seq, b = peer.channel.seal(obj)
            except C.ChannelClosed:
                return False
            return self.send_l1({"t": "send", "to": peer.conn, "ch": peer.ch, "seq": seq, "k": "e", "b": b})

    # -- inbound ---------------------------------------------------------------------------------
    def handle_frame(self, f: dict) -> None:
        t = f.get("t")
        if t == "msg":
            self._on_msg(f)
        elif t == "left":
            conn = f.get("conn")
            if isinstance(conn, str):
                self.drop_conn(conn)
        elif t == "pair.opened":
            p = self.pairing
            if p is not None and f.get("slot") == p.slot and p.state == "opening":
                p._set("open")
        elif t == "err":
            p = self.pairing
            if p is not None and p.state == "opening" and f.get("code") in ("slot_taken", "slot_limit"):
                p._set("failed", f.get("code"))

    def _on_msg(self, f: dict) -> None:
        conn, bid, k, b, ch, seq = f.get("from"), f.get("bid"), f.get("k"), f.get("b"), f.get("ch"), f.get("seq")
        if not isinstance(conn, str) or not CONN_RE.match(conn) or not isinstance(bid, str) or not BID_RE.match(bid):
            return
        if k == "hs":
            try:
                body = decode_body(b)
            except (ValueError, UnicodeDecodeError, RecursionError):
                self.audit.write("hs.fail", bid=bid, reason="format")
                return
            t = body.get("t")
            if t == "pair1":
                self._pair1(conn, bid, body)
            elif t == "pair3":
                self._pair3(conn, bid, body)
            elif t == "hs1":
                self._hs1(conn, bid, body)
            elif t == "hs3":
                self._hs3(conn, bid, body)
            return
        if k != "e":
            return
        with self._lock:
            peer = self.channels.get(conn)
        if peer is None or peer.ch != ch or peer.bid != bid:
            return
        if self.peers.active(bid) is None:          # revoked meanwhile
            self.close_peer(peer)
            return
        try:
            obj = peer.channel.open(seq, b)
        except C.ChannelClosed:
            self.audit.write("tamper", bid=bid, peer=peer.label, reason="3 failures, channel closed")
            self.close_peer(peer)
            return
        except C.CryptoError:
            return
        if self.clock() - peer.created > CHANNEL_MAX_AGE_S:
            self.close_peer(peer)
            return
        self.on_message(peer, obj)

    # -- handshake -------------------------------------------------------------------------------
    def _hs_allowed(self, bid: str) -> bool:
        now = self.clock()
        with self._lock:
            ts = [x for x in self._hs_times.get(bid, []) if now - x < 3600]
            if len(ts) >= self.hs_limit:
                self._hs_times[bid] = ts
                return False
            ts.append(now)
            self._hs_times[bid] = ts
            return True

    def _hs1(self, conn: str, bid: str, body: dict) -> None:
        peer_rec = self.peers.active(bid)
        if peer_rec is None:
            self.audit.write("hs.fail", bid=bid, reason="not paired or revoked")
            return
        if body.get("v") != 1 or body.get("bid") != bid:
            self.audit.write("hs.fail", bid=bid, reason="version/bid")
            return
        if not self._hs_allowed(bid):
            self.audit.write("denied", bid=bid, reason="handshake rate limit")
            return
        try:
            e_b = C.unb64u(body.get("eB"), 65)
            n_b = C.unb64u(body.get("nb"), 16)
            q_b = C.unb64u(peer_rec["pub"], 65)
            e_c_priv, e_c = C.generate_keypair()
            n_c = secrets.token_bytes(16)
            t = C.handshake_transcript(self.key.device_id, bid, self.key.pub, q_b, e_b, e_c, n_b, n_c)
            ee = C.ecdh(e_c_priv, e_b)
            ss = C.ecdh(self.key.priv, q_b)
        except (ValueError, C.CryptoError):
            self.audit.write("hs.fail", bid=bid, reason="bad key material")
            return
        del e_c_priv
        k_b2c, k_c2b, k_conf, ch = C.handshake_keys(t, ee, ss)
        with self._lock:
            self._pending[conn] = {"bid": bid, "t": t, "k_conf": k_conf, "k_b2c": k_b2c, "k_c2b": k_c2b, "ch": ch,
                                   "at": self.clock(), "label": peer_rec.get("label") or "Tarayıcı"}
        self._send_hs(conn, {"t": "hs2", "v": 1, "eC": C.b64u(e_c), "nc": C.b64u(n_c), "ch": ch,
                             "mac": C.b64u(C.hmac_sha256(k_conf, b"C" + t))})

    def _hs3(self, conn: str, bid: str, body: dict) -> None:
        with self._lock:
            pend = self._pending.pop(conn, None)
        if pend is None or pend["bid"] != bid or self.clock() - pend["at"] > HS_PENDING_S:
            self.audit.write("hs.fail", bid=bid, reason="no pending handshake")
            return
        try:
            mac = C.unb64u(body.get("mac"), 32)
        except ValueError:
            mac = b""
        if body.get("ch") != pend["ch"] or not C.ct_equal(mac, C.hmac_sha256(pend["k_conf"], b"B" + pend["t"])):
            self.audit.write("hs.fail", bid=bid, reason="mac")
            return
        if self.peers.active(bid) is None:
            self.audit.write("hs.fail", bid=bid, reason="revoked")
            return
        chan = C.Channel(pend["ch"], pend["k_b2c"], pend["k_c2b"], "device")
        peer = Peer(conn, bid, pend["label"], chan, self.clock())
        with self._lock:
            old = self.channels.get(conn)
            self.channels[conn] = peer
        if old is not None:
            old.channel.closed = True
            if self.on_peer_closed:
                self.on_peer_closed(old)
        self.peers.touch(bid)
        self.audit.write("hs.ok", bid=bid, peer=peer.label)

    # -- channels --------------------------------------------------------------------------------
    def drop_conn(self, conn: str) -> None:
        with self._lock:
            peer = self.channels.pop(conn, None)
            self._pending.pop(conn, None)
            self._hs_seq.pop(conn, None)
        if peer is not None:
            peer.channel.closed = True
            if self.on_peer_closed:
                self.on_peer_closed(peer)

    def close_peer(self, peer: Peer) -> None:
        with self._lock:
            if self.channels.get(peer.conn) is peer:
                del self.channels[peer.conn]
        peer.channel.closed = True
        if self.on_peer_closed:
            self.on_peer_closed(peer)

    def close_bid(self, bid: str) -> int:
        with self._lock:
            victims = [p for p in self.channels.values() if p.bid == bid]
            for c in [c for c, p in self._pending.items() if p["bid"] == bid]:
                del self._pending[c]
        for p in victims:
            self.close_peer(p)
        return len(victims)

    def reset(self) -> None:
        """Relay connection lost: every channel and pending handshake is void."""
        with self._lock:
            peers = list(self.channels.values())
            self.channels.clear()
            self._pending.clear()
            self._hs_seq.clear()
        for p in peers:
            p.channel.closed = True
            if self.on_peer_closed:
                self.on_peer_closed(p)
        pr = self.pairing
        if pr is not None and pr.state in ("opening", "open"):
            pr._set("opening")

    def all_peers(self) -> List[Peer]:
        with self._lock:
            return list(self.channels.values())

    # -- pairing ---------------------------------------------------------------------------------
    def start_pairing(self, code: Optional[str] = None) -> PairingSession:
        with self._lock:
            old = self.pairing
            if old is not None and old.state in ("opening", "open", "wait3", "confirm"):
                self._pair_fail(old, "replaced", audit=False)
            p = PairingSession(self, C.normalize_code(code) if code else C.new_pairing_code(), self.pair_iterations,
                               self.pair_ttl)
            self.pairing = p
        self.open_slot()
        return p

    def open_slot(self) -> None:
        p = self.pairing
        if p is not None and p.state in ("opening", "open"):
            self.send_l1({"t": "pair.open", "slot": p.slot})

    def _close_slot(self, p: PairingSession) -> None:
        self.send_l1({"t": "pair.close", "slot": p.slot})

    def _pair_fail(self, p: PairingSession, reason: str, notify: bool = False, audit: bool = True) -> None:
        was = p.state
        if was in ("opening", "open"):
            self._close_slot(p)
        if notify and p.conn:
            self._send_hs(p.conn, {"t": "pair4", "ok": False, "reason": reason})
        p._set("expired" if reason == "expired" else "failed", reason)
        if audit:
            self.audit.write("pair.fail", bid=p.bid, peer=p.label, reason=reason)

    def _pair1(self, conn: str, bid: str, body: dict) -> None:
        p = self.pairing
        if p is None or p.state != "open" or body.get("slot") != p.slot or self.clock() > p.expires:
            return
        if body.get("v") != 1:
            return
        # One attempt: the slot is burnt as soon as the first pair1 arrives.
        self._close_slot(p)
        try:
            q_b = C.unb64u(body.get("qB"), 65)
            n_b = C.unb64u(body.get("nB"), 16)
            C.decode_point(q_b)
            if C.peer_id(q_b) != bid:
                raise ValueError("bid mismatch")
        except (ValueError, C.CryptoError) as e:
            p.conn = conn
            p.bid = bid
            self._pair_fail(p, "code", notify=True)
            self.audit.write("pair.fail", bid=bid, reason="pair1: %s" % e)
            return
        label = body.get("label")
        label = "".join(ch for ch in label if ch.isprintable())[:40] if isinstance(label, str) else ""
        p.conn, p.bid, p.q_b, p.n_b, p.label = conn, bid, q_b, n_b, label or "Tarayıcı"
        p.wait3_deadline = self.clock() + PAIR3_WAIT_S
        p._set("wait3")
        self._send_hs(conn, {"t": "pair2", "v": 1, "deviceId": self.key.device_id, "qC": C.b64u(self.key.pub),
                             "nC": C.b64u(p.n_c)})

    def _pair3(self, conn: str, bid: str, body: dict) -> None:
        p = self.pairing
        if p is None or p.state != "wait3" or conn != p.conn or bid != p.bid:
            return
        k = p.key()
        p.t = C.pairing_transcript(p.slot, self.key.device_id, bid, self.key.pub, p.q_b, p.n_c, p.n_b)
        try:
            mac = C.unb64u(body.get("macB"), 32)
        except ValueError:
            mac = b""
        if not C.ct_equal(mac, C.pairing_mac(k, "B", p.t)):
            self._pair_fail(p, "code", notify=True)
            return
        p.sas = C.sas(k, p.t)
        p.confirm_deadline = self.clock() + SAS_TIMEOUT_S
        p._set("confirm")

    def _pair_confirm(self, p: PairingSession, ok: bool) -> bool:
        with self._lock:
            if p is not self.pairing or p.state != "confirm":
                return False
            if not ok:
                self._pair_fail(p, "refused", notify=True)
                return True
            self.peers.add(p.bid, p.q_b, p.label)
            mac_c = C.pairing_mac(p.key(), "C", p.t)
            self._send_hs(p.conn, {"t": "pair4", "ok": True, "macC": C.b64u(mac_c), "name": self.device_name})
            p._set("done")
        self.audit.write("pair.ok", bid=p.bid, peer=p.label)
        return True

    def tick(self) -> None:
        """Expire pairing codes, SAS questions and stale handshakes (called about once a second)."""
        now = self.clock()
        p = self.pairing
        if p is not None:
            if p.state in ("opening", "open") and now > p.expires:
                self._pair_fail(p, "expired")
            elif p.state == "wait3" and now > min(p.wait3_deadline, p.expires + PAIR3_WAIT_S):
                self._pair_fail(p, "timeout", notify=True)
            elif p.state == "confirm" and now > p.confirm_deadline:
                self._pair_fail(p, "timeout", notify=True)
        with self._lock:
            for conn in [c for c, v in self._pending.items() if now - v["at"] > HS_PENDING_S]:
                del self._pending[conn]
