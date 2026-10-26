"""`pixel-connector update` (V3.md §5.4 "Güncelleme"): replace the installed connector with the build the server
publishes, only when the user runs the command on this computer.

Trust model (docs/SECURITY_AUDIT.md S16-09): the files are checked against `connector/SHA256SUMS` from the same server
(`connector.json` → `server`, https only; plain http only for a loopback development server). That catches corruption
and truncation, not a compromised site; the digest is also published in README (git). There is deliberately no
automatic or background update and no message a browser could send to trigger one: a compromised site must never be
able to push code to every paired computer.

Fail closed: every file is downloaded (time-out, size limit) and verified before anything is written; a failure leaves
every file as it was. Then each changed file is written next to its target and moved over it (`os.replace`, mode 0600).
Settings, keys, pairings and agents are never touched. The connector service (and the Antigravity watcher service when
its files changed) is restarted when it is installed. Windows: the task is stopped before the files are replaced and
started afterwards (a running program's file may be locked there). Not yet tried on a real Windows machine.

Everything this module needs after the `.pyz` has been replaced is imported up front: the CLI itself may run from that
archive, and zipimport would read later imports from the new file at the old offsets.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Callable, Dict, List, Optional, Tuple

from . import service
from .localhttp import is_loopback_url

SUMS_PATH = "/connector/SHA256SUMS"
PYZ_NAME = "pixel-connector.pyz"
HOOK_SUMS_PREFIX = "../hook/"           # SHA256SUMS names are relative to /connector/, as `sha256sum -c` reads them
HOOK_FILES = ("pixel_hook.py", "pixel_agent.py", "pixel_antigravity.py")   # in ~/.pixel-office/ when installed
ANTIGRAVITY_FILES = frozenset({"pixel_antigravity.py", "pixel_agent.py"})  # what the watcher service runs
FETCH_TIMEOUT_S = 30.0
MAX_SUMS_BYTES = 64 * 1024
MAX_PYZ_BYTES = 8 * 1024 * 1024         # the 3.5.0 archive is ~0.5 MB
MAX_HOOK_BYTES = 1024 * 1024            # the largest hook file is ~60 KB
FILE_MODE = 0o600
SHA_RE = re.compile(r"^[0-9a-f]{64}\Z")
VERSION_RE = re.compile(r'^__version__\s*=\s*"([0-9A-Za-z.+-]{1,32})"', re.M)
BUILD_RE = re.compile(r'^BUILD\s*=\s*"([0-9a-z]{1,32})"', re.M)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AVAILABLE = 2                      # `--check`: an update is available

Opener = Callable[[str, float], object]


class UpdateError(Exception):
    """A refusal or failure; nothing has been changed when it is raised before the write phase."""


# -- server and download --------------------------------------------------------------------------------------------

def check_server(server: str) -> str:
    """The base URL, or UpdateError: https, or plain http only to a loopback development server."""
    base = (server or "").rstrip("/")
    parts = urllib.parse.urlsplit(base)
    if not parts.hostname or parts.query or parts.fragment or parts.username or parts.password:
        raise UpdateError("sunucu adresi geçersiz: %s" % base[:80])
    if parts.scheme == "https" or (parts.scheme == "http" and is_loopback_url(base)):
        return base
    raise UpdateError("sunucu https:// olmalı (http yalnızca bu bilgisayardaki geliştirme sunucusu için): %s"
                      % base[:80])


def redirect_allowed(old_url: str, new_url: str) -> bool:
    """A redirect may go to https, or from a loopback http server to loopback http. Never a downgrade to plain http."""
    target = urllib.parse.urljoin(old_url, new_url)
    scheme = urllib.parse.urlsplit(target).scheme
    if scheme == "https":
        return True
    return scheme == "http" and is_loopback_url(old_url) and is_loopback_url(target)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not redirect_allowed(req.full_url, newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_opener(url: str, timeout: float):
    handlers: list = [_SafeRedirect()]
    if is_loopback_url(url):
        handlers.append(urllib.request.ProxyHandler({}))     # loopback never goes through a proxy (localhttp, H8)
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "pixel-connector-update"})
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


def fetch(url: str, limit: int, opener: Opener, timeout: float = FETCH_TIMEOUT_S) -> bytes:
    """GET with a time-out and a size limit (Content-Length and the bytes actually read)."""
    try:
        with opener(url, timeout) as r:
            length = r.headers.get("Content-Length") if getattr(r, "headers", None) is not None else None
            if length is not None and str(length).isdigit() and int(length) > limit:
                raise UpdateError("dosya çok büyük (%s bayt, en fazla %d): %s" % (length, limit, url))
            data = r.read(limit + 1)
    except UpdateError:
        raise
    except Exception as e:                  # URLError, HTTPError, socket.timeout, ssl errors, …
        if isinstance(e, urllib.error.HTTPError):
            e.close()
        raise UpdateError("indirilemedi (%s): %s" % (type(e).__name__, url))
    if len(data) > limit:
        raise UpdateError("dosya çok büyük (en fazla %d bayt): %s" % (limit, url))
    return data


def parse_sums(text: str) -> Dict[str, str]:
    """{file name: sha256} for the names `update` knows (allow-list); other or malformed lines are ignored."""
    known = {PYZ_NAME} | {HOOK_SUMS_PREFIX + n for n in HOOK_FILES}
    out: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split("  ", 1)
        if len(parts) == 2 and SHA_RE.match(parts[0]) and parts[1] in known:
            out[parts[1]] = parts[0]
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> Optional[str]:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 16), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def describe_pyz(data: bytes) -> str:
    """"3.5.0 (build abc…)" read from the archive bytes (never executed); "?" when it can't be read."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            init = z.read("pixel_connector/__init__.py").decode("utf-8", "replace")
            try:
                build_src = z.read("pixel_connector/_build.py").decode("utf-8", "replace")
            except KeyError:
                build_src = ""
    except (zipfile.BadZipFile, KeyError, OSError, ValueError):
        return "?"
    v, b = VERSION_RE.search(init), BUILD_RE.search(build_src)
    return "%s (build %s)" % (v.group(1) if v else "?", b.group(1) if b else "?")


# -- plan -------------------------------------------------------------------------------------------------------------

class Item:
    """One file `update` manages: where it comes from, where it goes, what it must hash to."""

    def __init__(self, name: str, url: str, dest: str, expected: str, limit: int) -> None:
        self.name, self.url, self.dest, self.expected, self.limit = name, url, dest, expected, limit
        self.current = sha256_file(dest)
        self.data: Optional[bytes] = None

    @property
    def changed(self) -> bool:
        return self.current != self.expected


def plan(base: str, sums: Dict[str, str], pyz_path: str, office_dir: str) -> List[Item]:
    """The connector archive (must be listed) and every hook file that is installed here AND listed on the server."""
    if PYZ_NAME not in sums:
        raise UpdateError("sunucudaki SHA256SUMS dosyasında %s satırı yok" % PYZ_NAME)
    items = [Item(PYZ_NAME, base + "/connector/" + PYZ_NAME, pyz_path, sums[PYZ_NAME], MAX_PYZ_BYTES)]
    for name in HOOK_FILES:
        dest = os.path.join(office_dir, name)
        key = HOOK_SUMS_PREFIX + name
        if key in sums and os.path.isfile(dest):
            items.append(Item(name, base + "/hook/" + name, dest, sums[key], MAX_HOOK_BYTES))
    return items


def download_all(items: List[Item], opener: Opener) -> None:
    """Fill `data` for every changed item; UpdateError (nothing written) on any failure or digest mismatch."""
    for it in items:
        if not it.changed:
            continue
        data = fetch(it.url, it.limit, opener)
        got = sha256_bytes(data)
        if got != it.expected:
            raise UpdateError("SHA-256 doğrulanamadı (%s): beklenen %s…, inen %s…. Hiçbir dosya değiştirilmedi."
                              % (it.name, it.expected[:12], got[:12]))
        it.data = data


# -- write --------------------------------------------------------------------------------------------------------------

def stage(dest: str, data: bytes) -> str:
    """Write `data` to a new 0600 file in dest's folder (so os.replace stays on one file system). → its path."""
    folder = os.path.dirname(os.path.abspath(dest))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="." + os.path.basename(dest) + ".", suffix=".yeni")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, FILE_MODE)
    except BaseException:
        _unlink(tmp)
        raise
    return tmp


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def write_all(items: List[Item]) -> List[Item]:
    """Stage every changed file, then move them into place. A staging failure removes the staged files and leaves
    the targets untouched. → the items that were replaced."""
    todo = [it for it in items if it.changed and it.data is not None]
    staged: List[Tuple[Item, str]] = []
    try:
        for it in todo:
            staged.append((it, stage(it.dest, it.data)))
    except OSError as e:
        for _, tmp in staged:
            _unlink(tmp)
        raise UpdateError("yazılamadı (%s); hiçbir dosya değiştirilmedi." % type(e).__name__)
    done: List[Item] = []
    for it, tmp in staged:
        try:
            os.replace(tmp, it.dest)
        except OSError as e:
            for _, rest in staged[len(done) + 1:]:
                _unlink(rest)
            _unlink(tmp)
            names = ", ".join(x.name for x in done) or "yok"
            raise UpdateError("%s yerine konamadı (%s). Değişen dosyalar: %s" % (it.name, type(e).__name__, names))
        done.append(it)
    return done


# -- services ---------------------------------------------------------------------------------------------------------

class Services:
    """Restart helpers for the connector and Antigravity watcher services (the names of service.py and of
    web/hook/pixel_antigravity.py). `runner` runs argv (tests pass a fake)."""

    def __init__(self, platform: Optional[str] = None, user_home: Optional[str] = None, runner=subprocess.run,
                 log: Callable[[str], None] = print, environ: Optional[Dict[str, str]] = None) -> None:
        self.platform = platform or sys.platform
        self.home = user_home or os.path.expanduser("~")
        self.runner = runner
        self.log = log
        self.environ = os.environ if environ is None else environ

    def _run(self, argv: List[str]) -> int:
        self.log("$ " + " ".join(argv))
        try:
            return self.runner(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL).returncode
        except (OSError, subprocess.SubprocessError) as e:
            self.log("  hata: %s" % type(e).__name__)
            return 127

    def installed(self, which: str) -> bool:
        """`which` = "connector" | "antigravity": is its service definition present?"""
        p = self.platform
        if p.startswith("linux"):
            if which == "connector":
                unit_dir = os.path.join(self.home, ".config", "systemd", "user")
            else:
                xdg = self.environ.get("XDG_CONFIG_HOME") or os.path.join(self.home, ".config")
                unit_dir = os.path.join(xdg, "systemd", "user")
            unit = service.UNIT_NAME if which == "connector" else service.AG_UNIT_NAME
            return os.path.isfile(os.path.join(unit_dir, unit))
        if p == "darwin":
            label = service.LAUNCHD_LABEL if which == "connector" else service.AG_LAUNCHD_LABEL
            return os.path.isfile(os.path.join(self.home, "Library", "LaunchAgents", label + ".plist"))
        if p == "win32":
            return self._run(["schtasks", "/Query", "/TN", self._task(which)]) == 0
        return False

    @staticmethod
    def _task(which: str) -> str:
        return service.SCHTASK_NAME if which == "connector" else service.AG_SCHTASK_NAME

    def stop_before_replace(self, which: str) -> None:
        """Windows only: end the task so its files are not in use while they are replaced."""
        if self.platform == "win32":
            self._run(["schtasks", "/End", "/TN", self._task(which)])

    def restart(self, which: str) -> int:
        p = self.platform
        if p.startswith("linux"):
            unit = "pixel-connector" if which == "connector" else service.AG_UNIT_NAME
            return self._run(["systemctl", "--user", "restart", unit])
        if p == "darwin":
            uid = os.getuid() if hasattr(os, "getuid") else 0
            label = service.LAUNCHD_LABEL if which == "connector" else service.AG_LAUNCHD_LABEL
            return self._run(["launchctl", "kickstart", "-k", "gui/%d/%s" % (uid, label)])
        if p == "win32":
            return self._run(["schtasks", "/Run", "/TN", self._task(which)])
        return 2


def running_archive() -> Optional[str]:
    """Path of the zipapp this process runs from, None from a source checkout."""
    import pixel_connector
    archive = getattr(getattr(pixel_connector, "__loader__", None), "archive", None)
    return archive if archive and os.path.isfile(archive) else None


# -- the command --------------------------------------------------------------------------------------------------------

def run_update(server: str, pyz_path: Optional[str], office_dir: str, check_only: bool = False,
               opener: Opener = default_opener, services: Optional[Services] = None,
               out: Callable[[str], None] = print) -> int:
    """→ EXIT_OK (up to date / updated), EXIT_AVAILABLE (`check_only` and something differs) or EXIT_ERROR."""
    try:
        return _run_update(server, pyz_path, office_dir, check_only, opener, services or Services(log=out), out)
    except UpdateError as e:
        out("Hata: %s" % e)
        return EXIT_ERROR


def _run_update(server, pyz_path, office_dir, check_only, opener, services, out) -> int:
    if not pyz_path or not os.path.isfile(pyz_path):
        raise UpdateError("bu komut yalnızca kurulu pixel-connector.pyz ile çalışır (kaynak koddan çalışıyor). "
                          "Kurulum komutunu kullanın: Cihazlar → Cihaz ekle.")
    base = check_server(server)
    sums = parse_sums(fetch(base + SUMS_PATH, MAX_SUMS_BYTES, opener).decode("ascii", "replace"))
    items = plan(base, sums, pyz_path, office_dir)
    changed = [it for it in items if it.changed]
    if not changed:
        out("Zaten güncel: sunucudaki sürümle aynı (%s…)." % items[0].expected[:12])
        return EXIT_OK
    for it in changed:
        out("Güncelleme var: %s (bu bilgisayar %s…, sunucu %s…)" % (it.name, (it.current or "yok")[:12],
                                                                    it.expected[:12]))
    if check_only:
        out("Güncellemek için: pixel-connector update")
        return EXIT_AVAILABLE
    download_all(items, opener)
    pyz = items[0]
    restart = []
    if pyz.changed and services.installed("connector"):
        restart.append("connector")
    if any(it.changed and it.name in ANTIGRAVITY_FILES for it in items[1:]) and services.installed("antigravity"):
        restart.append("antigravity")
    for which in restart:
        services.stop_before_replace(which)
    try:
        done = write_all(items)
    finally:
        rc = 0
        for which in restart:                    # also after a failed write: the old files are back in service
            rc = services.restart(which) or rc
    for it in done:
        out("Yenilendi: %s" % it.dest)
    if pyz in done:
        out("Bağlayıcı güncellendi: %s" % describe_pyz(pyz.data or b""))
    if not restart and pyz in done:
        out("Servis kurulu değil: çalışan `pixel-connector run` sürecini yeniden başlatın.")
    if rc:
        out("Uyarı: servis yeniden başlatılamadı (çıkış kodu %d). Elle başlatın ya da bilgisayarı yeniden başlatın." % rc)
        return EXIT_ERROR
    return EXIT_OK
