"""Incremental, read-only transcript tailing and agent discovery (ARCHITECTURE.md §2.3, §7.4)."""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    DISCOVERY_INTERVAL,
    DISCOVERY_WINDOW,
    INITIAL_TAIL_BYTES,
    MAX_LINE_BYTES,
    POLL_INTERVAL,
    REMOVE_AFTER,
    SUBAGENT_REMOVE_AFTER,
)
from .models import AgentInfo, State
from .store import AgentStore
from .transcript import SessionTracker, parse_line, parse_ts

log = logging.getLogger("pixel_office")

READ_CHUNK = 1 << 20          # read appended data in 1 MiB chunks
HEAD_PROBE_BYTES = 65_536     # bytes read from the file head to find the first timestamp


class FileTail:
    """Incremental line reader for an append-only file. Never writes; robust to partial
    lines, truncation/replacement and absurdly long lines."""

    def __init__(self, path: Path, initial_tail_bytes: int = INITIAL_TAIL_BYTES) -> None:
        self.path = Path(path)
        self.initial_tail_bytes = initial_tail_bytes
        self.offset: int = 0
        self.skipped_head: bool = False      # True if the first read started mid-file
        self._buf = bytearray()
        self._skipping = False               # discarding bytes up to the next newline
        self._started = False
        self._ino: int | None = None
        self._dev: int | None = None

    def _reset(self) -> None:
        self.offset = 0
        self._buf.clear()
        self._skipping = False

    def read_new_lines(self) -> list[str]:
        """Return complete new lines (decoded, without the newline). Raises OSError if the
        file cannot be stat'ed/opened (e.g. it disappeared)."""
        st = os.stat(self.path)
        size = st.st_size
        if self._ino is not None and (st.st_ino != self._ino or st.st_dev != self._dev):
            log.info("transcript replaced, re-reading: %s", self.path)
            self._reset()
        elif size < self.offset:
            log.info("transcript truncated, re-reading: %s", self.path)
            self._reset()
        self._ino, self._dev = st.st_ino, st.st_dev

        lines: list[str] = []
        with open(self.path, "rb") as f:
            if not self._started:
                self._started = True
                if size > self.initial_tail_bytes:
                    start = size - self.initial_tail_bytes
                    f.seek(start - 1)
                    # Only drop the first line if we really landed in the middle of one.
                    self._skipping = f.read(1) != b"\n"
                    self.offset = start
                    self.skipped_head = True
            if size == self.offset:
                return lines
            f.seek(self.offset)
            while True:
                chunk = f.read(READ_CHUNK)
                if not chunk:
                    break
                self.offset += len(chunk)
                self._consume(chunk, lines)
        return lines

    def _consume(self, chunk: bytes, out: list[str]) -> None:
        parts = chunk.split(b"\n")
        for part in parts[:-1]:
            if self._skipping:
                self._skipping = False
                self._buf.clear()
                continue
            if self._buf:
                self._buf += part
                data = bytes(self._buf)
                self._buf.clear()
            else:
                data = part
            if len(data) > MAX_LINE_BYTES:
                log.warning("skipping oversized line (%d bytes) in %s", len(data), self.path)
                continue
            if data.endswith(b"\r"):
                data = data[:-1]
            if data:
                out.append(data.decode("utf-8", errors="replace"))
        tail = parts[-1]
        if self._skipping or not tail:
            return
        self._buf += tail
        if len(self._buf) > MAX_LINE_BYTES:
            log.warning("partial line exceeds %d bytes, discarding it: %s", MAX_LINE_BYTES, self.path)
            self._buf.clear()
            self._skipping = True


def read_first_timestamp(path: Path) -> float | None:
    """Best effort: first timestamp found in the first few lines of the file."""
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_PROBE_BYTES)
    except OSError:
        return None
    for raw in head.split(b"\n")[:-1]:
        obj = parse_line(raw.decode("utf-8", errors="replace"))
        if obj is not None:
            ts = parse_ts(obj.get("timestamp"))
            if ts is not None:
                return ts
    return None


def _read_meta(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("cannot read %s: %s", path, e)
        return {}
    return obj if isinstance(obj, dict) else {}


@dataclass
class _Tracked:
    tracker: SessionTracker
    tail: FileTail
    sig: tuple[int, int, int] | None = None     # (size, mtime_ns, ino) at last read
    mtime: float = 0.0
    meta_path: Path | None = None
    children: set[Path] = field(default_factory=set)


class Watcher(threading.Thread):
    """Polls ~/.claude/projects, tails transcripts and pushes AgentInfo into the store."""

    def __init__(self, projects_dir: Path, store: AgentStore, poll_interval: float = POLL_INTERVAL) -> None:
        super().__init__(name="pixel-office-watcher", daemon=True)
        self.projects_dir = Path(projects_dir).expanduser()
        self.store = store
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._tracked: dict[Path, _Tracked] = {}
        self._removed: dict[Path, float] = {}     # path → mtime at removal
        self._last_discovery: float | None = None
        self._dir_missing_logged = False

    # -- thread -----------------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan(time.time())
            except Exception:
                log.exception("watcher scan failed")
            self._stop_event.wait(self.poll_interval)

    def stop(self) -> None:
        self._stop_event.set()

    # -- one iteration ----------------------------------------------------------------------
    def scan(self, now: float) -> None:
        if self._last_discovery is None or now - self._last_discovery >= DISCOVERY_INTERVAL:
            self._last_discovery = now
            try:
                self._discover(now)
            except Exception:
                log.exception("discovery failed")

        for path in list(self._tracked):
            entry = self._tracked.get(path)
            if entry is None:
                continue
            try:
                self._poll(path, entry, now)
            except FileNotFoundError:
                self._drop(path, forget=True)
            except Exception:
                log.exception("error while reading %s", path)

        self._publish(now)

    # -- discovery --------------------------------------------------------------------------
    def _discover(self, now: float) -> None:
        if not self.projects_dir.is_dir():
            if not self._dir_missing_logged:
                log.warning("projects dir %s does not exist (yet); still watching", self.projects_dir)
                self._dir_missing_logged = True
            return
        if self._dir_missing_logged:
            log.info("projects dir %s appeared", self.projects_dir)
            self._dir_missing_logged = False

        cutoff = now - DISCOVERY_WINDOW
        mains = sorted(self.projects_dir.glob("*/*.jsonl"))
        subs = sorted(self.projects_dir.glob("*/*/subagents/agent-*.jsonl"))
        # Forget removal memos of files that no longer exist (keeps _removed bounded).
        if self._removed:
            present = set(mains).union(subs)
            for gone in [p for p in self._removed if p not in present]:
                del self._removed[gone]
        for path in mains:
            self._maybe_track(path, cutoff, subagent=False)
        for path in subs:
            if self._maybe_track(path, cutoff, subagent=True):
                # A recently active subagent means its session is active too: make sure the
                # parent is shown even if its own file has been quiet (or was removed).
                parent = path.parent.parent.parent / (path.parent.parent.name + ".jsonl")
                if parent not in self._tracked and parent.is_file():
                    self._maybe_track(parent, float("-inf"), subagent=False, force=True)

    def _maybe_track(self, path: Path, cutoff: float, *, subagent: bool, force: bool = False) -> bool:
        """Start tracking path if eligible. Returns True if it is tracked afterwards."""
        if path in self._tracked:
            return True
        try:
            st = path.stat()
        except OSError:
            return False
        if not path.is_file():
            return False
        removed_mtime = self._removed.get(path)
        if removed_mtime is not None:
            if st.st_mtime == removed_mtime and not force:
                return False
            del self._removed[path]
        if st.st_mtime < cutoff:
            return False

        if subagent:
            session_dir = path.parent.parent
            parent_id = session_dir.name
            project_dir_name = session_dir.parent.name
            agent_stem = path.stem.removeprefix("agent-")
            agent_id = f"{parent_id}:{agent_stem}"
            meta_path = path.with_name(path.stem + ".meta.json")
            meta = _read_meta(meta_path)
        else:
            parent_id = None
            project_dir_name = path.parent.name
            agent_id = path.stem
            meta_path = None
            meta = None

        tracker = SessionTracker(agent_id, path, is_subagent=subagent, parent_id=parent_id,
                                 project_dir_name=project_dir_name, meta=meta)
        entry = _Tracked(tracker=tracker, tail=FileTail(path), meta_path=meta_path)
        self._tracked[path] = entry
        if subagent:
            parent_path = path.parent.parent.parent / (parent_id + ".jsonl")
            parent_entry = self._tracked.get(parent_path)
            if parent_entry is not None:
                parent_entry.children.add(path)
        else:
            # adopt already tracked children (subagents discovered before the parent)
            for p, e in self._tracked.items():
                if e.tracker.is_subagent and e.tracker.parent_id == agent_id and \
                        p.parent.parent.parent == path.parent:
                    entry.children.add(p)
        log.info("tracking %s %s", "subagent" if subagent else "session", agent_id)
        return True

    # -- polling ----------------------------------------------------------------------------
    def _poll(self, path: Path, entry: _Tracked, now: float) -> None:
        st = os.stat(path)
        sig = (st.st_size, st.st_mtime_ns, st.st_ino)
        tracker = entry.tracker
        if tracker.is_subagent and tracker.meta is None and entry.meta_path is not None:
            tracker.meta = _read_meta(entry.meta_path)
        if sig == entry.sig:
            return
        first = entry.sig is None
        entry.sig = sig
        entry.mtime = st.st_mtime
        tracker.last_write = st.st_mtime
        lines = entry.tail.read_new_lines()
        if first and entry.tail.skipped_head:
            tracker.first_ts_hint = read_first_timestamp(path)
        bad = 0
        for raw in lines:
            obj = parse_line(raw)
            if obj is None:
                bad += 1
                continue
            try:
                tracker.feed(obj, now)
            except Exception:
                log.exception("failed to process a line of %s", path)
        if bad:
            log.warning("%d malformed line(s) ignored in %s", bad, path)

    # -- publishing & removal ---------------------------------------------------------------
    def _effective_last_write(self, entry: _Tracked) -> float:
        elw = entry.tracker.last_write
        for child in entry.children:
            ce = self._tracked.get(child)
            if ce is not None:
                elw = max(elw, ce.tracker.last_write)
        return elw

    def _publish(self, now: float) -> None:
        mains = [(p, e) for p, e in self._tracked.items() if not e.tracker.is_subagent]
        subs = [(p, e) for p, e in self._tracked.items() if e.tracker.is_subagent]

        for path, entry in mains:
            if entry.sig is None:
                continue  # never read successfully
            elw = self._effective_last_write(entry)
            if now - elw >= REMOVE_AFTER:
                log.info("session %s silent for %ds, removing", entry.tracker.agent_id, int(now - elw))
                self._drop(path)
                continue
            self._upsert(entry, now, elw)

        for path, entry in subs:
            if path not in self._tracked or entry.sig is None:
                continue
            tr = entry.tracker
            # Subagent transcripts do not record the final report; the parent's tool_result for
            # the spawning Agent/Task call is the reliable "finished" signal.
            if tr.finished_ts is None and tr.tool_use_id and tr.parent_id:
                pe = self._tracked.get(path.parent.parent.parent / (tr.parent_id + ".jsonl"))
                if pe is not None and tr.tool_use_id in pe.tracker.tool_results:
                    tr.finished_ts = pe.tracker.tool_results[tr.tool_use_id]
            agent = tr.to_agent(now, tr.last_write)
            if agent.state == State.SLEEPING and now - agent.state_since / 1000 >= SUBAGENT_REMOVE_AFTER:
                self._drop(path)
                continue
            if now - tr.last_write >= REMOVE_AFTER:
                self._drop(path)
                continue
            self._safe_upsert(agent)

    def _upsert(self, entry: _Tracked, now: float, elw: float) -> None:
        try:
            agent = entry.tracker.to_agent(now, elw)
        except Exception:
            log.exception("cannot build agent for %s", entry.tracker.path)
            return
        self._safe_upsert(agent)

    def _safe_upsert(self, agent: AgentInfo) -> None:
        try:
            self.store.upsert(agent)
        except Exception:
            log.exception("store upsert failed for %s", agent.id)

    def _drop(self, path: Path, *, forget: bool = False) -> None:
        """Stop tracking path (and children of a main session) and remove it from the store.
        forget=True: the file disappeared, so there is nothing to remember."""
        entry = self._tracked.pop(path, None)
        if entry is None:
            return
        if forget:
            self._removed.pop(path, None)
        else:
            self._removed[path] = entry.mtime
        self.store.remove(entry.tracker.agent_id)
        for child in list(entry.children):
            if child in self._tracked:
                self._drop(child, forget=not child.exists())
        if entry.tracker.is_subagent and entry.tracker.parent_id:
            parent_path = path.parent.parent.parent / (entry.tracker.parent_id + ".jsonl")
            pe = self._tracked.get(parent_path)
            if pe is not None:
                pe.children.discard(path)
