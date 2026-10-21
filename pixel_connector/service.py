"""Background service files (V3.md §12): systemd user unit (Linux), launchd agent (macOS), Task Scheduler (Windows).

Every function that touches the system takes `dry_run`; tests only use the text generators and dry runs.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from typing import Callable, List, Optional, Tuple

UNIT_NAME = "pixel-connector.service"
LAUNCHD_LABEL = "tr.pixeloffice.connector"
SCHTASK_NAME = "PixelOffice\\Connector"
SYSTEM_PATH = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin", "/usr/sbin", "/sbin")


def _quote_systemd(arg: str) -> str:
    """Quote one ExecStart argument (systemd.syntax): spaces/quotes/backslashes escaped inside double quotes;
    `%` doubled so it is not taken as a specifier, `$` doubled so it is not expanded as an environment variable."""
    a = arg.replace("%", "%%").replace("$", "$$")
    if a and all(c.isalnum() or c in "/._-+:=@," for c in a):
        return a
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"'


def service_path(user_path: Optional[str], claude_path: Optional[str] = None, which=shutil.which,
                 isdir: Callable[[str], bool] = os.path.isdir) -> str:
    """PATH recorded in the service definition (v2.md H9). A systemd user service or launchd agent starts with a
    minimal PATH, so `claude` installed with npm/nvm (a Node script: `#!/usr/bin/env node`) and the tools it calls (git,
    node) are not found. Kept from the installing user's PATH: absolute, existing directories without control
    characters or `:`/`;` inside a name, in order, without duplicates. The directories of claude and of `node` (when
    claude is a Node script) come first, the system directories last."""
    sep = os.pathsep
    out: List[str] = []

    def add(d: Optional[str]) -> None:
        if not d or not os.path.isabs(d) or any(not ch.isprintable() for ch in d) or sep in d:
            return
        d = os.path.normpath(d)
        if d not in out and isdir(d):
            out.append(d)

    if claude_path and os.path.isabs(claude_path):
        add(os.path.dirname(claude_path))
        if _is_node_script(claude_path):
            node = which("node", path=(user_path or "") + sep + os.path.dirname(claude_path))
            if node:
                add(os.path.dirname(os.path.abspath(node)))
    for d in (user_path or "").split(sep):
        add(d)
    for d in SYSTEM_PATH:
        add(d)
    return sep.join(out)


def _is_node_script(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(128)
    except OSError:
        return False
    return head.startswith(b"#!") and b"node" in head.split(b"\n", 1)[0]


def systemd_unit(python: str, pyz: str, home_override: Optional[str] = None, path_env: Optional[str] = None) -> str:
    """The unit text. `pyz` may be "%h/…" (kept as a specifier) or an absolute path.

    No `network-online.target` (v2.md H24): a *user* manager can't order against that system target, so the lines only
    looked useful. The connector's own reconnect loop handles a network that comes up late."""
    pyz_arg = pyz if pyz.startswith("%h/") else _quote_systemd(pyz)
    exec_start = "%s %s run" % (_quote_systemd(python), pyz_arg)
    env = ["Environment=PYTHONUNBUFFERED=1"]
    if home_override:
        env.append("Environment=%s" % _quote_systemd("PIXEL_OFFICE_HOME=" + home_override))
    if path_env:
        env.append("Environment=%s" % _quote_systemd("PATH=" + path_env))
    return "\n".join([
        "[Unit]",
        "Description=Pixel Office bağlayıcı",
        "",
        "[Service]",
        "Type=simple",
        "ExecStart=" + exec_start,
        "Restart=on-failure",
        "RestartSec=5",
        "UMask=0077",
        "NoNewPrivileges=yes",
        *env,
        "# PATH: the installing user's PATH at setup time (+ claude/node directories); re-run the installer to refresh.",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def launchd_plist(python: str, pyz: str, log_dir: str, path_env: Optional[str] = None) -> bytes:
    extra = {"EnvironmentVariables": {"PATH": path_env}} if path_env else {}
    return plistlib.dumps(dict({
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [python, pyz, "run"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": os.path.join(log_dir, "connector.out.log"),
        "StandardErrorPath": os.path.join(log_dir, "connector.err.log"),
        "ProcessType": "Background",
    }, **extra))


def schtasks_create_argv(pythonw: str, pyz: str) -> List[str]:
    tr = '"%s" "%s" run' % (pythonw, pyz)
    return ["schtasks", "/Create", "/TN", SCHTASK_NAME, "/SC", "ONLOGON", "/RL", "LIMITED", "/F", "/TR", tr]


def default_pyz(connector_dir: str) -> str:
    return os.path.join(connector_dir, "pixel-connector.pyz")


def _run(argv: List[str], dry_run: bool, log: Callable[[str], None], runner=subprocess.run) -> int:
    log("$ " + " ".join(argv))
    if dry_run:
        return 0
    try:
        return runner(argv, stdin=subprocess.DEVNULL).returncode
    except OSError as e:
        log("  hata: %s" % e)
        return 127


def install(connector_dir: str, pyz: Optional[str] = None, python: Optional[str] = None, dry_run: bool = False,
            log: Callable[[str], None] = print, platform: Optional[str] = None, user_home: Optional[str] = None,
            runner=subprocess.run, path_env: Optional[str] = None) -> Tuple[int, Optional[str]]:
    """Write the service definition and enable it. → (exit code, path of the written file). `path_env`: the PATH for
    the service (service_path()); Windows tasks inherit the user's environment and ignore it."""
    platform = platform or sys.platform
    python = python or sys.executable
    home = user_home or os.path.expanduser("~")
    pyz = pyz or default_pyz(connector_dir)
    if platform.startswith("linux"):
        unit_dir = os.path.join(home, ".config", "systemd", "user")
        path = os.path.join(unit_dir, UNIT_NAME)
        rel = os.path.relpath(pyz, home) if pyz.startswith(home + os.sep) else None
        office = os.path.dirname(connector_dir)
        override = None if os.path.realpath(office) == os.path.realpath(os.path.join(home, ".pixel-office")) else office
        text = systemd_unit(python, "%h/" + rel if rel else pyz, override, path_env)
        log("systemd kullanıcı birimi: %s" % path)
        if dry_run:
            log(text)
        else:
            os.makedirs(unit_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        rc = _run(["systemctl", "--user", "daemon-reload"], dry_run, log, runner)
        if rc == 0:
            rc = _run(["systemctl", "--user", "enable", "pixel-connector"], dry_run, log, runner)
        if rc == 0:
            # `restart` also starts a stopped unit; after an update (install.sh again) the new .pyz must replace the
            # running process, which `enable --now` would leave untouched (Phase 15).
            rc = _run(["systemctl", "--user", "restart", "pixel-connector"], dry_run, log, runner)
        return rc, path
    if platform == "darwin":
        path = os.path.join(home, "Library", "LaunchAgents", LAUNCHD_LABEL + ".plist")
        data = launchd_plist(python, pyz, connector_dir, path_env)
        log("launchd ajanı: %s" % path)
        if dry_run:
            log(data.decode())
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        uid = os.getuid() if hasattr(os, "getuid") else 0
        # bootstrap fails while the agent is loaded: unload a previous version first (result ignored; Phase 15).
        _run(["launchctl", "bootout", "gui/%d/%s" % (uid, LAUNCHD_LABEL)], dry_run, log, runner)
        rc = _run(["launchctl", "bootstrap", "gui/%d" % uid, path], dry_run, log, runner)
        return rc, path
    if platform == "win32":
        pythonw = python.replace("python.exe", "pythonw.exe")
        rc = _run(schtasks_create_argv(pythonw, pyz), dry_run, log, runner)
        if rc == 0:
            _run(["schtasks", "/End", "/TN", SCHTASK_NAME], dry_run, log, runner)   # stop an old instance (ignored)
            rc = _run(["schtasks", "/Run", "/TN", SCHTASK_NAME], dry_run, log, runner)
        return rc, None
    log("Bu işletim sistemi için servis kurulumu yok: %s" % platform)
    return 2, None


def uninstall(dry_run: bool = False, log: Callable[[str], None] = print, platform: Optional[str] = None,
              user_home: Optional[str] = None, runner=subprocess.run) -> int:
    platform = platform or sys.platform
    home = user_home or os.path.expanduser("~")
    if platform.startswith("linux"):
        path = os.path.join(home, ".config", "systemd", "user", UNIT_NAME)
        _run(["systemctl", "--user", "disable", "--now", "pixel-connector"], dry_run, log, runner)
        log("siliniyor: %s" % path)
        if not dry_run:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        return _run(["systemctl", "--user", "daemon-reload"], dry_run, log, runner)
    if platform == "darwin":
        path = os.path.join(home, "Library", "LaunchAgents", LAUNCHD_LABEL + ".plist")
        uid = os.getuid() if hasattr(os, "getuid") else 0
        rc = _run(["launchctl", "bootout", "gui/%d/%s" % (uid, LAUNCHD_LABEL)], dry_run, log, runner)
        if not dry_run:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        return rc
    if platform == "win32":
        return _run(["schtasks", "/Delete", "/TN", SCHTASK_NAME, "/F"], dry_run, log, runner)
    return 2


def status(runner=subprocess.run, platform: Optional[str] = None) -> str:
    platform = platform or sys.platform
    try:
        if platform.startswith("linux"):
            r = runner(["systemctl", "--user", "is-active", "pixel-connector"], capture_output=True, text=True,
                       timeout=5, stdin=subprocess.DEVNULL)
            return (r.stdout or "").strip() or "unknown"
        if platform == "darwin":
            r = runner(["launchctl", "list", LAUNCHD_LABEL], capture_output=True, text=True, timeout=5,
                       stdin=subprocess.DEVNULL)
            return "active" if r.returncode == 0 else "inactive"
        if platform == "win32":
            r = runner(["schtasks", "/Query", "/TN", SCHTASK_NAME], capture_output=True, text=True, timeout=5,
                       stdin=subprocess.DEVNULL)
            return "active" if r.returncode == 0 else "inactive"
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"
