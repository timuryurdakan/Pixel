"""Device identity (`keys.json`) and paired browsers (`peers.json`), V3.md §5.2, §6.8, §7.2."""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable, List, Optional

from . import crypto as C
from .config import KEYS_FILE, PEERS_FILE, read_json, write_json

BID_RE = re.compile(r"^[a-z2-7]{16}\Z")


class DeviceKey:
    def __init__(self, priv: int, pub: bytes, created: int) -> None:
        self.priv = priv
        self.pub = pub
        self.device_id = C.peer_id(pub)
        self.created = created

    @classmethod
    def load_or_create(cls, home: str) -> "DeviceKey":
        path = os.path.join(home, KEYS_FILE)
        d = read_json(path)
        if isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("priv"), str):
            priv = int(d["priv"], 16)
            pub = C.public_key(priv)
            if C.b64u(pub) != d.get("pub"):
                raise ValueError("keys.json is inconsistent (public key does not match)")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return cls(priv, pub, int(d.get("created") or 0))
        return cls.create(home)

    @classmethod
    def create(cls, home: str) -> "DeviceKey":
        priv, pub = C.generate_keypair()
        k = cls(priv, pub, int(time.time() * 1000))
        write_json(os.path.join(home, KEYS_FILE), {"v": 1, "priv": "%064x" % priv, "pub": C.b64u(pub),
                                                   "deviceId": k.device_id, "created": k.created})
        return k


class PeerStore:
    """peers.json: {"v":1,"peers":[{bid, pub, label, pairedAt, lastSeen, revoked}]}."""

    def __init__(self, home: str, clock: Callable[[], float] = time.time) -> None:
        self.path = os.path.join(home, PEERS_FILE)
        self.clock = clock
        self._lock = threading.RLock()
        d = read_json(self.path, {"v": 1, "peers": []})
        self.peers: List[dict] = [p for p in (d.get("peers") if isinstance(d, dict) else None) or []
                                  if isinstance(p, dict) and isinstance(p.get("bid"), str)]
        self._last_save = 0.0

    def save(self) -> None:
        with self._lock:
            write_json(self.path, {"v": 1, "peers": self.peers})
            self._last_save = self.clock()

    def reload(self) -> None:
        with self._lock:
            d = read_json(self.path, {"v": 1, "peers": []})
            self.peers = [p for p in (d.get("peers") or []) if isinstance(p, dict) and isinstance(p.get("bid"), str)]

    def get(self, bid: str) -> Optional[dict]:
        with self._lock:
            for p in self.peers:
                if p["bid"] == bid:
                    return p
        return None

    def active(self, bid: str) -> Optional[dict]:
        p = self.get(bid)
        return p if p is not None and not p.get("revoked") else None

    def add(self, bid: str, pub: bytes, label: str) -> dict:
        if not BID_RE.match(bid) or C.peer_id(pub) != bid:
            raise ValueError("bid does not match the public key")
        now = int(self.clock() * 1000)
        with self._lock:
            self.peers = [p for p in self.peers if p["bid"] != bid]
            rec = {"bid": bid, "pub": C.b64u(pub), "label": (label or "Tarayıcı")[:40], "pairedAt": now,
                   "lastSeen": now, "revoked": False}
            self.peers.append(rec)
            self.save()
            return rec

    def touch(self, bid: str) -> None:
        with self._lock:
            p = self.get(bid)
            if p is None:
                return
            p["lastSeen"] = int(self.clock() * 1000)
            if self.clock() - self._last_save > 300:
                self.save()

    def revoke(self, bid: str) -> bool:
        with self._lock:
            p = self.get(bid)
            if p is None or p.get("revoked"):
                return False
            p["revoked"] = True
            p["revokedAt"] = int(self.clock() * 1000)
            self.save()
            return True

    def revoke_all(self) -> List[str]:
        with self._lock:
            out = [p["bid"] for p in self.peers if not p.get("revoked")]
            for p in self.peers:
                p["revoked"] = True
            self.save()
            return out

    def public_list(self, self_bid: Optional[str] = None) -> List[dict]:
        with self._lock:
            return [{"bid": p["bid"], "label": p.get("label"), "pairedAt": p.get("pairedAt"),
                     "lastSeen": p.get("lastSeen"), "self": p["bid"] == self_bid}
                    for p in self.peers if not p.get("revoked")]
