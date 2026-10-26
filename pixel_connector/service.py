"""Background service files (V3.md §12): systemd user unit (Linux), launchd agent (macOS), Task Scheduler (Windows).

Every function that touches the system takes `dry_run`; tests only use the text generators and dry runs.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as _xml_escape

UNIT_NAME = "pixel-connector.service"
LAUNCHD_LABEL = "tr.pixeloffice.connector"
SCHTASK_NAME = "PixelOffice\\Connector"
# The Antigravity watcher's service (web/hook/pixel_antigravity.py AG_*; that file can't be imported from here).
# `pixel-connector update` restarts it after replacing its files; tests/test_connector_update.py keeps them equal.
AG_UNIT_NAME = "pixel-antigravity.service"
AG_LAUNCHD_LABEL = "tr.pixeloffice.antigravity"
AG_SCHTASK_NAME = "PixelOffice\\Antigravity"
SYSTEM_PATH = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin", "/usr/sbin", "/sbin")
TASK_XML_FILE = "pixel-connector.task.xml"
TASK_RESTART_COUNT = 3              # Task Scheduler: restart a failed task 3× …
TASK_RESTART_INTERVAL = "PT1M"      # … one minute apart (ISO 8601 duration; 1 min is the minimum it accepts)
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def service_home(connector_dir: str, user_home: str) -> Tuple[Dict[str, str], List[str]]:
    """How the service finds a connector directory that isn't the default `~/.pixel-office/connector` (v2.md H10).
    → (environment, extra CLI arguments placed before `run`).

    `<dir>/connector` → PIXEL_OFFICE_HOME=<dir> (config.connector_home() appends "connector"); any other name →
    `--home <dir>`, because PIXEL_OFFICE_HOME=<parent> would point the service at <parent>/connector instead (the
    `--home` parent-dir bug of v2.md H10)."""
    default = os.path.join(user_home, ".pixel-office", "connector")
    if os.path.realpath(connector_dir) == os.path.realpath(default):
        return {}, []
    if os.path.basename(os.path.normpath(connector_dir)) == "connector":
        return {"PIXEL_OFFICE_HOME": os.path.dirname(os.path.normpath(connector_dir))}, []
    return {}, ["--home", connector_dir]


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


def systemd_unit(python: str, pyz: str, home_override: Optional[str] = None, path_env: Optional[str] = None,
                 extra_args: Sequence[str] = ()) -> str:
    """The unit text. `pyz` may be "%h/…" (kept as a specifier) or an absolute path.

    No `network-online.target` (v2.md H24): a *user* manager can't order against that system target, so the lines only
    looked useful. The connector's own reconnect loop handles a network that comes up late."""
    pyz_arg = pyz if pyz.startswith("%h/") else _quote_systemd(pyz)
    exec_start = " ".join([_quote_systemd(python), pyz_arg] + [_quote_systemd(a) for a in extra_args] + ["run"])
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


def launchd_plist(python: str, pyz: str, log_dir: str, path_env: Optional[str] = None,
                  home_override: Optional[str] = None, extra_args: Sequence[str] = ()) -> bytes:
    """launchd agent. launchd starts agents with a minimal PATH and none of the login shell's variables, so PATH
    (service_path()) and a non-default PIXEL_OFFICE_HOME go into EnvironmentVariables (v2.md H9/H10)."""
    env = {}
    if path_env:
        env["PATH"] = path_env
    if home_override:
        env["PIXEL_OFFICE_HOME"] = home_override
    extra = {"EnvironmentVariables": env} if env else {}
    return plistlib.dumps(dict({
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [python, pyz] + list(extra_args) + ["run"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": os.path.join(log_dir, "connector.out.log"),
        "StandardErrorPath": os.path.join(log_dir, "connector.err.log"),
        "ProcessType": "Background",
    }, **extra))


def windows_quote(arg: str) -> str:
    """One argument for a Windows command line (CommandLineToArgvW / the MSVC runtime rules, as
    subprocess.list2cmdline): always in double quotes, backslashes before a quote and at the end doubled."""
    out, slashes = [], 0
    for ch in arg:
        if ch == "\\":
            slashes += 1
            continue
        if ch == '"':
            out.append("\\" * (slashes * 2 + 1) + '"')
        else:
            out.append("\\" * slashes + ch)
        slashes = 0
    return '"' + "".join(out) + "\\" * (slashes * 2) + '"'


# Sets environment variables inside the started Python and then runs the target like `python <target> …` would.
# Used instead of a .cmd wrapper / `cmd /c set …`: cmd.exe would open a console window next to pythonw, and its
# quoting and code-page rules are easy to get wrong with paths such as C:\Users\Ömer Şahin. No double quotes inside,
# so it survives windows_quote() unchanged. argv: -c CODE <n> NAME1 VALUE1 … <target> <args…>
_WIN_ENV_LAUNCHER = ("import os,runpy,sys;a=sys.argv;n=int(a[1]);"
                     "os.environ.update(zip(a[2:2+2*n:2],a[3:3+2*n:2]));"
                     "sys.argv=a[2+2*n:];runpy.run_path(sys.argv[0],run_name='__main__')")


def windows_action(pythonw: str, target: str, args: Sequence[str], env: Optional[Dict[str, str]] = None
                   ) -> Tuple[str, str]:
    """→ (Command, Arguments) of the task's Exec action: `pythonw <target> <args…>`, or with `env` the in-process
    launcher above (Task Scheduler has no per-task environment variables)."""
    argv: List[str] = []
    if env:
        argv = ["-c", _WIN_ENV_LAUNCHER, str(len(env))]
        for k in sorted(env):
            argv += [k, env[k]]
    argv += [target] + list(args)
    return pythonw, " ".join(windows_quote(a) for a in argv)


def windows_user_id(environ: Optional[Dict[str, str]] = None) -> str:
    """DOMAIN\\user of the installing user (logon trigger + principal of the task)."""
    env = os.environ if environ is None else environ
    user = env.get("USERNAME") or ""
    if not user:
        import getpass
        user = getpass.getuser()
    dom = env.get("USERDOMAIN") or ""
    return dom + "\\" + user if dom else user


def task_scheduler_xml(command: str, arguments: str, user_id: str, description: str,
                       working_dir: Optional[str] = None) -> str:
    """Task Scheduler 1.2 definition (v2.md H10): at logon of this user only, with the user's normal (limited)
    rights and an interactive token, so registering it needs no administrator and stores no password. Unlike the
    `schtasks /Create /SC ONLOGON` defaults it runs on battery and keeps running when the laptop unplugs, has no
    72-hour execution limit, ignores a second start while running and is restarted 3× one minute apart if it fails.
    The element order follows what Windows itself exports."""
    e = _xml_escape
    wd = "      <WorkingDirectory>%s</WorkingDirectory>\n" % e(working_dir) if working_dir else ""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="%s">\n'
        "  <RegistrationInfo>\n"
        "    <Description>%s</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        "      <UserId>%s</UserId>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        "      <UserId>%s</UserId>\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <IdleSettings>\n"
        "      <StopOnIdleEnd>false</StopOnIdleEnd>\n"
        "      <RestartOnIdle>false</RestartOnIdle>\n"
        "    </IdleSettings>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        "    <WakeToRun>false</WakeToRun>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>%s</Interval>\n"
        "      <Count>%d</Count>\n"
        "    </RestartOnFailure>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        "      <Command>%s</Command>\n"
        "      <Arguments>%s</Arguments>\n"
        "%s"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    ) % (TASK_NS, e(description), e(user_id), e(user_id), TASK_RESTART_INTERVAL, TASK_RESTART_COUNT, e(command),
         e(arguments), wd)


def task_xml_bytes(xml: str) -> bytes:
    """schtasks /XML reads UTF-16 with a byte order mark (the encoding Windows itself exports)."""
    return xml.encode("utf-16")


def schtasks_create_argv(xml_path: str, task_name: str = SCHTASK_NAME) -> List[str]:
    return ["schtasks", "/Create", "/TN", task_name, "/XML", xml_path, "/F"]


def pythonw_for(python: str) -> str:
    """pythonw.exe next to python.exe (no console window). Unchanged when the name isn't python.exe."""
    cut = max(python.rfind("\\"), python.rfind("/")) + 1
    return python[:cut] + "pythonw.exe" if python[cut:].lower() == "python.exe" else python


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
            runner=subprocess.run, path_env: Optional[str] = None,
            environ: Optional[Dict[str, str]] = None) -> Tuple[int, Optional[str]]:
    """Write the service definition and enable it. → (exit code, path of the written file). `path_env`: the PATH for
    the service (service_path()); Windows tasks inherit the user's environment and ignore it. `environ`: where the
    Windows user name comes from (tests)."""
    platform = platform or sys.platform
    python = python or sys.executable
    home = user_home or os.path.expanduser("~")
    pyz = pyz or default_pyz(connector_dir)
    env, extra_args = service_home(connector_dir, home)
    office = env.get("PIXEL_OFFICE_HOME")
    if platform.startswith("linux"):
        unit_dir = os.path.join(home, ".config", "systemd", "user")
        path = os.path.join(unit_dir, UNIT_NAME)
        rel = os.path.relpath(pyz, home) if pyz.startswith(home + os.sep) else None
        text = systemd_unit(python, "%h/" + rel if rel else pyz, office, path_env, extra_args)
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
        data = launchd_plist(python, pyz, connector_dir, path_env, office, extra_args)
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
        command, arguments = windows_action(pythonw_for(python), pyz, extra_args + ["run"], env)
        xml = task_scheduler_xml(command, arguments, windows_user_id(environ), "Pixel Office bağlayıcı",
                                 connector_dir)
        path = os.path.join(connector_dir, TASK_XML_FILE)
        log("Görev Zamanlayıcı tanımı: %s" % path)
        if dry_run:
            log(xml)
        else:
            os.makedirs(connector_dir, exist_ok=True)
            with open(path, "wb") as f:
                f.write(task_xml_bytes(xml))
        rc = _run(schtasks_create_argv(path), dry_run, log, runner)
        if rc == 0:
            _run(["schtasks", "/End", "/TN", SCHTASK_NAME], dry_run, log, runner)   # stop an old instance (ignored)
            rc = _run(["schtasks", "/Run", "/TN", SCHTASK_NAME], dry_run, log, runner)
        return rc, path
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
