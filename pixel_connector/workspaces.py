"""Workspace allow-list (V3.md §6.4).

`connector.json → workspaces: [{id, name, path, children}]`. Paths: `~` is expanded, environment variables are NOT,
then `os.path.realpath`. A request names a workspace only by `{id, sub}`; `sub` is a relative path below a
workspace with `children: true`. Resolution happens again at every task start, so a directory that was swapped for a
symlink in the meantime is caught. The browser only ever sees ids, names and child names.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from typing import Dict, List, Optional

ID_RE = re.compile(r"^[a-z0-9_-]{1,24}\Z")
MAX_SUB = 200
MAX_CHILDREN = 50

# Phase 17: the default workspace that `setup` creates (SECURITY_AUDIT.md §5). On Windows `~` is %USERPROFILE%.
DEFAULT_DIR = "PixelOfficeIsler"
DEFAULT_ENTRY = {"id": "isler", "name": "Pixel Office İşleri", "path": "~/" + DEFAULT_DIR, "children": True}


class WorkspaceDenied(Exception):
    """Workspace or sub-path not allowed (error code `workspace_denied`)."""


def _fold(p: str) -> str:
    return os.path.normcase(p).lower() if sys.platform in ("darwin", "win32") else p


# Folders under home that may never be (or contain) a workspace: credentials, agent logins, keyrings, browser profiles,
# and places where an edit becomes code execution. Phase 20 (v2.md H30) added everything below ".claude"; every
# `Read(~/<dir>/**)` rule in engines/claude.py has its folder here (test-enforced). ".config" covers gh, gcloud,
# google-chrome and chromium.
HOME_DENY_DIRS = (".ssh", ".gnupg", ".aws", ".config", ".pixel-office", ".claude",
                  ".gemini", ".codex", ".docker", ".kube", ".azure", ".cargo", ".mozilla",
                  os.path.join(".local", "share"), os.path.join(".local", "bin"),
                  os.path.join("Library", "Keychains"), os.path.join("Library", "LaunchAgents"))


def deny_list(home: Optional[str] = None) -> List[str]:
    """Built-in denied roots (always applied, even if configured)."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    out = [os.path.join(home, d) for d in HOME_DENY_DIRS]
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
    if _under(home_r, real):
        # Phase 16 (SECURITY_AUDIT.md S16-06): /home, /Users, C:\Users … contain the whole home directory,
        # including every denied folder below it.
        return "an ancestor of the home directory"
    for d in deny_list(home):
        if _under(real, d):
            return "denied path " + d
    if sys.platform == "win32" and re.match(r"^[A-Za-z]:\\Program Files", real, re.I):
        return "denied path Program Files"
    return None


def ensure_default_dir(home: Optional[str] = None) -> str:
    """Create `~/PixelOfficeIsler` with mode 0700, or reuse it if it already is a plain directory (Phase 17).

    Refused (WorkspaceDenied): a symlink or junction, a non-directory, an existing git repository, or anything the
    deny/ancestor rules reject. Returns the realpath.
    """
    home_r = os.path.realpath(home or os.path.expanduser("~"))
    path = os.path.join(home_r, DEFAULT_DIR)
    created = False
    try:
        os.mkdir(path, 0o700)
        created = True
    except FileExistsError:
        pass
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            raise WorkspaceDenied("%s is a symbolic link" % path)
        if not stat.S_ISDIR(st.st_mode):
            raise WorkspaceDenied("%s exists and is not a directory" % path)
        real = os.path.realpath(path)
        if _fold(real) != _fold(path):                  # Windows junctions, reparse points
            raise WorkspaceDenied("%s resolves to %s" % (path, real))
        if not created and os.path.lexists(os.path.join(path, ".git")):
            raise WorkspaceDenied("%s is an existing git repository" % path)
        reason = check_denied(real, home)
        if reason:
            raise WorkspaceDenied("%s is not allowed (%s)" % (path, reason))
    except BaseException:
        if created:
            try:
                os.rmdir(path)
            except OSError:
                pass
        raise
    if created:
        try:
            os.chmod(path, 0o700)                       # mkdir's mode is masked by the umask; make it exact
        except OSError:
            pass
    return real


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


# S26-03: on POSIX each component is created and opened relative to the previous one's descriptor with O_NOFOLLOW, so
# a component swapped for a symlink after it was checked cannot redirect the next mkdir outside the workspace.
_DIR_FD_OK = (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
              and os.mkdir in os.supports_dir_fd and os.open in os.supports_dir_fd)


def _make_dirs_at(base: str, parts: List[str]) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(base, flags)
    try:
        for part in parts:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                nxt = os.open(part, flags, dir_fd=fd)       # ELOOP for a symlink, ENOTDIR for anything else
            except OSError:
                raise WorkspaceDenied("sub path is not a plain directory") from None
            os.close(fd)
            fd = nxt
    finally:
        os.close(fd)


def _make_dirs_by_path(base: str, parts: List[str]) -> None:
    """Fallback without dir_fd (Windows): check every component after creating it. A local process racing between
    the check and the next mkdir is not covered here; resolve() still refuses the result."""
    cur = base
    for part in parts:
        cur = os.path.join(cur, part)
        try:
            os.mkdir(cur, 0o700)
        except FileExistsError:
            pass
        st = os.lstat(cur)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise WorkspaceDenied("sub path is not a plain directory")


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

    def ensure_sub(self, wid, sub=None) -> str:
        """Like resolve(), but creates a missing sub-path first (v4-C workflows write into a new folder). Every component
        is created below the resolved workspace with mode 0700 and must be a plain directory, never a symlink, so a
        planted link cannot redirect the creation; the result is checked again by resolve(). Raises WorkspaceDenied."""
        base = self.resolve(wid)
        s = validate_sub(sub)
        if s is None:
            return base
        if not self.get(wid).get("children"):
            raise WorkspaceDenied("sub folders not allowed here")
        try:
            if _DIR_FD_OK:
                _make_dirs_at(base, s.split("/"))
            else:
                _make_dirs_by_path(base, s.split("/"))
        except WorkspaceDenied:
            raise
        except OSError:
            raise WorkspaceDenied("cannot create sub folder") from None
        return self.resolve(wid, s)

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
