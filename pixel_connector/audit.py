"""Local audit log (V3.md §6.7): JSON Lines, rotated at 5 MB, 3 files kept (audit.log, .1, .2). Never sent anywhere.

Prompts are stored only as SHA-256 + length unless `audit.storePrompts` is true. Model output is never stored here.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

MAX_BYTES = 5 * 1024 * 1024
KEEP = 3

EVENTS = {"pair.ok", "pair.fail", "peer.revoke", "hs.ok", "hs.fail", "tamper", "task.start", "task.end", "task.cancel",
          "confirm", "agent.create", "agent.update", "agent.delete", "agent.reset", "runtime.start", "runtime.stop",
          "model.load", "model.unload", "budget.set", "denied", "config.load", "service.start", "service.stop"}


def iso_now(t: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(time.time() if t is None else t).astimezone()
    return dt.isoformat(timespec="milliseconds")


def prompt_fields(prompt: str, store: bool) -> dict:
    raw = prompt.encode("utf-8")
    out = {"promptSha256": hashlib.sha256(raw).hexdigest(), "promptLen": len(raw)}
    if store:
        out["prompt"] = prompt
    return out


class AuditLog:
    def __init__(self, path: str, store_prompts: bool = False, max_bytes: int = MAX_BYTES,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.store_prompts = store_prompts
        self.max_bytes = max_bytes
        self.clock = clock
        self._lock = threading.Lock()

    def _rotate(self) -> None:
        for i in range(KEEP - 1, 0, -1):
            src = self.path if i == 1 else "%s.%d" % (self.path, i - 1)
            dst = "%s.%d" % (self.path, i)
            if os.path.exists(src):
                os.replace(src, dst)

    def write(self, ev: str, **fields) -> None:
        """Never raises (the audit log must not break the connector)."""
        rec = {"ts": iso_now(self.clock()), "ev": ev}
        prompt = fields.pop("prompt", None)
        if isinstance(prompt, str):
            rec.update(prompt_fields(prompt, self.store_prompts))
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        line = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            try:
                try:
                    size = os.path.getsize(self.path)
                except OSError:
                    size = 0
                if size and size + len(line) > self.max_bytes:
                    self._rotate()
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(fd, line)
                finally:
                    os.close(fd)
            except OSError:
                pass

    def read(self) -> list:
        out = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
        return out
