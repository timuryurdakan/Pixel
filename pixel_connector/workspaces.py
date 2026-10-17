"""Workspace allow-list (V3.md §6.4).

`connector.json → workspaces: [{id, name, path, children}]`. Paths: `~` is expanded, environment variables are NOT,
then `os.path.realpath`. A request names a workspace only by `{id, sub}`; `sub` is a relative path below a
workspace with `children: true`. Resolution happens again at every task start, so a directory that was swapped for a
symlink in the meantime is caught. The browser only ever sees ids, names and child names.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Dict, List, Optional

ID_RE = re.compile(r"^[a-z0-9_-]{1,24}\Z")
MAX_SUB = 200
MAX_CHILDREN = 50


class WorkspaceDenied(Exception):
    """Workspace or sub-path not allowed (error code `workspace_denied`)."""


def _fold(p: str) -> str:
    return os.path.normcase(p).lower() if sys.platform in ("darwin", "win32") else p


def deny_list(home: Optional[str] = None) -> List[str]:
    """Built-in denied roots (always applied, even if configured)."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    out = [os.path.join(home, d) for d in (".ssh", ".gnupg", ".aws", ".config", ".pixel-office", ".claude")]
    out += ["/etc", "/usr", "/bin", "/sbin", "/System"]
    if sys.platform == "win32":
        sysdrive = os.environ.get("SystemDrive", "C:")
        out += [sysdrive + "\\Windows", sysdrive + "\\Program Files", sysdrive + "\\Program Files (x86)"]
    real = []
    for p in out:
        real.append(p)
        try:
            rp = os.path.realpath(p)
            if rp != p:
                real.append(rp)
        except OSError:
            pass
    return real


def _under(path: str, root: str) -> bool:
    path, root = _fold(path), _fold(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def check_denied(real: str, home: Optional[str] = None) -> Optional[str]:
    """Reason string if `real` (a realpath) is a forbidden workspace, else None."""
    home_r = os.path.realpath(home or os.path.expanduser("~"))
    if _fold(real) == _fold(home_r):
        return "the home directory itself"
    if _fold(real) == _fold(os.path.realpath(os.sep)) or re.match(r"^[A-Za-z]:\\?\Z", real):
        return "the filesystem root"
    for d in deny_list(home):
        if _under(real, d):
            return "denied path " + d
    if sys.platform == "win32" and re.match(r"^[A-Za-z]:\\Program Files", real, re.I):
        return "denied path Program Files"
    return None


def expand_path(path: str, home: Optional[str] = None) -> str:
    """`~` expansion only (no env vars), then realpath."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("bad path")
    if path == "~" or path.startswith("~/") or path.startswith("~" + os.sep):
        path = (home or os.path.expanduser("~")) + path[1:]
    if not os.path.isabs(path):
        raise ValueError("workspace path must be absolute or start with ~/")
    return os.path.realpath(path)


def validate_sub(sub) -> Optional[str]:
    """Normalised relative sub-path or None. Raises WorkspaceDenied."""
    if sub is None or sub == "":
        return None
    if not isinstance(sub, str) or len(sub) > MAX_SUB or "\x00" in sub:
        raise WorkspaceDenied("bad sub path")
    s = sub.replace("\\", "/")
    if s.startswith("/") or s.startswith("//") or re.match(r"^[A-Za-z]:", s):
        raise WorkspaceDenied("absolute sub path")
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise WorkspaceDenied("parent components")
    for p in parts:
        if any(ord(c) < 0x20 for c in p):
            raise WorkspaceDenied("control characters")
    return "/".join(parts)


class Workspaces:
    def __init__(self, entries: List[dict], home: Optional[str] = None) -> None:
        self.home = home
        self.entries: Dict[str, dict] = {}
        for e in entries:
            self.entries[e["id"]] = e

    def get(self, wid) -> dict:
        e = self.entries.get(wid) if isinstance(wid, str) else None
        if e is None:
            raise WorkspaceDenied("unknown workspace")
        return e

    def resolve(self, wid, sub=None) -> str:
        """Absolute real directory for {id, sub}. Raises WorkspaceDenied."""
        e = self.get(wid)
        try:
            base = expand_path(e["path"], self.home)
        except ValueError:
            raise WorkspaceDenied("bad workspace path") from None
        if check_denied(base, self.home):
            raise WorkspaceDenied("workspace is on the deny list")
        if not os.path.isdir(base):
            raise WorkspaceDenied("workspace missing")
        s = validate_sub(sub)
        if s is None:
            return base
        if not e.get("children"):
            raise WorkspaceDenied("sub folders not allowed here")
        cand = os.path.realpath(os.path.join(base, *s.split("/")))
        if not _under(cand, base):
            raise WorkspaceDenied("escapes the workspace")
        if check_denied(cand, self.home):
            raise WorkspaceDenied("denied path")
        if not os.path.isdir(cand):
            raise WorkspaceDenied("not a directory")
        return cand

    def children(self, wid) -> Optional[List[str]]:
        """Direct child directory names (≤ 50, depth 1, no dot-dirs, no symlinks out) or None."""
        e = self.get(wid)
        if not e.get("children"):
            return None
        try:
            base = self.resolve(wid)
            names = sorted(os.listdir(base))
        except (WorkspaceDenied, OSError):
            return []
        out = []
        for n in names:
            if n.startswith("."):
                continue
            try:
                self.resolve(wid, n)
            except WorkspaceDenied:
                continue
            out.append(n)
            if len(out) >= MAX_CHILDREN:
                break
        return out

    def public_list(self) -> List[dict]:
        return [{"id": e["id"], "name": e["name"], "children": self.children(e["id"])} for e in self.entries.values()]
