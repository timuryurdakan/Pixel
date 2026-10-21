"""Phase 20 / Package 7 (v2.md H10, H31): Windows Task Scheduler settings, service environment on macOS/Windows,
process-tree kill on Windows and the Windows hook state lock. None of this can run on real Windows/macOS here: the
tests check the generated XML/plist, the commands that would run (fake runners) and the lock through a fake msvcrt
module. Linux behaviour is checked to be unchanged."""

import importlib.util
import os
import plistlib
import shutil
import signal
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import service as S
from pixel_connector.engines import claude as CL

ROOT = Path(__file__).resolve().parent.parent
NS = {"t": S.TASK_NS}
OMER = "C:\\Users\\Ömer Şahin\\.pixel-office\\connector"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ph = _load("pixel_hook_under_test_p7", ROOT / "web" / "hook" / "pixel_hook.py")
ag = _load("pixel_antigravity_under_test_p7", ROOT / "web" / "hook" / "pixel_antigravity.py")


def parse_windows_cmdline(s):
    """CommandLineToArgvW / MSVC rules (the parser pythonw.exe uses), enough to check windows_quote round-trips."""
    out, cur, i, inq, have = [], [], 0, False, False
    while i < len(s):
        c = s[i]
        if c == "\\":
            n = 0
            while i < len(s) and s[i] == "\\":
                n += 1
                i += 1
            if i < len(s) and s[i] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i += 1
            else:
                cur.append("\\" * n)
            have = True
            continue
        if c == '"':
            inq = not inq
            have = True
        elif c in " \t" and not inq:
            if have:
                out.append("".join(cur))
            cur, have = [], False
        else:
            cur.append(c)
            have = True
        i += 1
    if have:
        out.append("".join(cur))
    return out


def task_settings(xml):
    root = ET.fromstring(xml.split("\n", 1)[1])          # ElementTree refuses the UTF-16 declaration on a str
    return root, {el.tag.split("}")[1]: el for el in root.find("t:Settings", NS)}


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)


# ------------------------------------------------------------------------------------------------------------------
# H10a — Task Scheduler settings
# ------------------------------------------------------------------------------------------------------------------

class TaskSchedulerXml(unittest.TestCase):
    def xml(self, **kw):
        cmd, args = S.windows_action("C:\\Py\\pythonw.exe", OMER + "\\pixel-connector.pyz", ["run"], kw.get("env"))
        return S.task_scheduler_xml(cmd, args, "PC\\Ömer", "Pixel Office bağlayıcı", OMER)

    def test_battery_time_limit_restart_logon(self):
        root, st = task_settings(self.xml())
        self.assertEqual(root.get("version"), "1.2")
        self.assertEqual(st["DisallowStartIfOnBatteries"].text, "false")
        self.assertEqual(st["StopIfGoingOnBatteries"].text, "false")
        self.assertEqual(st["ExecutionTimeLimit"].text, "PT0S")             # no 72 h limit
        self.assertEqual(st["MultipleInstancesPolicy"].text, "IgnoreNew")
        self.assertEqual(st["RestartOnFailure"].find("t:Count", NS).text, "3")
        self.assertEqual(st["RestartOnFailure"].find("t:Interval", NS).text, "PT1M")
        self.assertEqual(st["IdleSettings"].find("t:StopOnIdleEnd", NS).text, "false")
        trig = root.find("t:Triggers/t:LogonTrigger", NS)
        self.assertEqual(trig.find("t:UserId", NS).text, "PC\\Ömer")              # this user only: no admin needed
        pr = root.find("t:Principals/t:Principal", NS)
        self.assertEqual(pr.find("t:LogonType", NS).text, "InteractiveToken")      # no stored password
        self.assertEqual(pr.find("t:RunLevel", NS).text, "LeastPrivilege")
        ex = root.find("t:Actions/t:Exec", NS)
        self.assertEqual(ex.find("t:Command", NS).text, "C:\\Py\\pythonw.exe")
        self.assertEqual(ex.find("t:WorkingDirectory", NS).text, OMER)
        self.assertEqual(parse_windows_cmdline(ex.find("t:Arguments", NS).text),
                         [OMER + "\\pixel-connector.pyz", "run"])

    def test_xml_escaping_and_utf16(self):
        cmd, args = S.windows_action("C:\\A&B <x>\\pythonw.exe", "C:\\T&T\\a.pyz", ["--home", "C:\\x\"y"])
        xml = S.task_scheduler_xml(cmd, args, "D\\u", "d<&>", None)
        root, _ = task_settings(xml)
        ex = root.find("t:Actions/t:Exec", NS)
        self.assertEqual(ex.find("t:Command", NS).text, "C:\\A&B <x>\\pythonw.exe")
        self.assertIsNone(ex.find("t:WorkingDirectory", NS))
        self.assertEqual(parse_windows_cmdline(ex.find("t:Arguments", NS).text),
                         ["C:\\T&T\\a.pyz", "--home", "C:\\x\"y"])
        data = S.task_xml_bytes(self.xml())
        self.assertTrue(data.startswith(b"\xff\xfe"))                          # UTF-16 LE with BOM
        self.assertIn("Ömer Şahin", data.decode("utf-16"))
        self.assertTrue(data.decode("utf-16").startswith('<?xml version="1.0" encoding="UTF-16"?>'))

    def test_windows_quote_round_trip(self):
        for a in ["plain", "a b", "", "C:\\a b\\", "C:\\x\\\\", 'q"q', 'q\\"q', "Ömer Şahin", "tab\there",
                  "\\\\server\\share\\a b", "end\\\\\\"]:
            self.assertEqual(parse_windows_cmdline(S.windows_quote(a)), [a], a)
        self.assertEqual(S.windows_quote("C:\\a b\\"), '"C:\\a b\\\\"')

    def test_env_launcher_sets_variables_and_runs_target(self):
        tmp = tempfile.mkdtemp(prefix="p7-launch-")
        self.addCleanup(shutil.rmtree, tmp, True)
        target = os.path.join(tmp, "t ö.py")
        Path(target).write_text("import os,sys\nprint(os.environ['PIXEL_OFFICE_HOME']+'|'+os.environ['B']+'|'+"
                                "'|'.join(sys.argv)+'|'+__name__)\n", encoding="utf-8")
        cmd, args = S.windows_action("python", target, ["--home", "x y", "run"],
                                     {"PIXEL_OFFICE_HOME": "C:\\Users\\Ömer Şahin\\o", "B": 'a"b'})
        argv = parse_windows_cmdline(args)
        self.assertEqual(argv[0], "-c")
        import sys
        r = subprocess.run([sys.executable] + argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode("utf-8").strip(),
                         "C:\\Users\\Ömer Şahin\\o|a\"b|%s|--home|x y|run|__main__" % target)

    def test_user_id_and_pythonw(self):
        self.assertEqual(S.windows_user_id({"USERNAME": "Ömer", "USERDOMAIN": "PC"}), "PC\\Ömer")
        self.assertEqual(S.windows_user_id({"USERNAME": "u"}), "u")
        self.assertEqual(S.pythonw_for("C:\\Py 3\\Python.EXE"), "C:\\Py 3\\pythonw.exe")
        self.assertEqual(S.pythonw_for("C:\\Py\\pythonw.exe"), "C:\\Py\\pythonw.exe")
        self.assertEqual(S.pythonw_for("C:\\Py\\py.exe"), "C:\\Py\\py.exe")

    def test_windows_install_writes_xml_and_runs_schtasks(self):
        h = tempfile.mkdtemp(prefix="p7-win-")
        self.addCleanup(shutil.rmtree, h, True)
        conn = os.path.join(h, ".pixel-office", "connector")
        run = Runner()
        rc, path = S.install(conn, platform="win32", user_home=h, python="C:\\Py\\python.exe", log=lambda s: None,
                             runner=run, environ={"USERNAME": "Ömer", "USERDOMAIN": "PC"}, path_env="/ignored")
        self.assertEqual(rc, 0)
        self.assertEqual(path, os.path.join(conn, "pixel-connector.task.xml"))
        self.assertEqual(run.calls, [["schtasks", "/Create", "/TN", "PixelOffice\\Connector", "/XML", path, "/F"],
                                     ["schtasks", "/End", "/TN", "PixelOffice\\Connector"],
                                     ["schtasks", "/Run", "/TN", "PixelOffice\\Connector"]])
        xml = Path(path).read_bytes().decode("utf-16")
        root, st = task_settings(xml)
        self.assertEqual(st["StopIfGoingOnBatteries"].text, "false")
        ex = root.find("t:Actions/t:Exec", NS)
        self.assertEqual(ex.find("t:Command", NS).text, "C:\\Py\\pythonw.exe")
        self.assertEqual(parse_windows_cmdline(ex.find("t:Arguments", NS).text),
                         [os.path.join(conn, "pixel-connector.pyz"), "run"])        # default home: no env, no flag
        self.assertNotIn("PIXEL_OFFICE_HOME", xml)
        self.assertNotIn("/ignored", xml)

    def test_windows_install_failure_stops(self):
        h = tempfile.mkdtemp(prefix="p7-win-")
        self.addCleanup(shutil.rmtree, h, True)
        calls = []
        rc, _ = S.install(os.path.join(h, "c"), platform="win32", user_home=h, python="C:\\Py\\python.exe",
                          log=lambda s: None, environ={"USERNAME": "u"},
                          runner=lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 1))
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1)


# ------------------------------------------------------------------------------------------------------------------
# H10b — PIXEL_OFFICE_HOME / --home / PATH
# ------------------------------------------------------------------------------------------------------------------

class ServiceHome(unittest.TestCase):
    def setUp(self):
        self.h = tempfile.mkdtemp(prefix="p7-home-")
        self.addCleanup(shutil.rmtree, self.h, True)

    def test_service_home_cases(self):
        h = self.h
        self.assertEqual(S.service_home(os.path.join(h, ".pixel-office", "connector"), h), ({}, []))
        self.assertEqual(S.service_home(os.path.join(h, "Ömer Şahin", "connector"), h),
                         ({"PIXEL_OFFICE_HOME": os.path.join(h, "Ömer Şahin")}, []))
        other = os.path.join(h, ".pixel-office", "conn2")         # parent is the default office: was dropped before
        self.assertEqual(S.service_home(other, h), ({}, ["--home", other]))
        self.assertEqual(S.service_home(os.path.join(h, "x", "connector") + os.sep, h)[0],
                         {"PIXEL_OFFICE_HOME": os.path.join(h, "x")})

    def test_linux_home_parent_dir_bug_fixed(self):
        logs = []
        conn = os.path.join(self.h, "my conn")
        S.install(conn, dry_run=True, log=logs.append, platform="linux", user_home=self.h, python="/usr/bin/python3",
                  pyz="/opt/p.pyz")
        text = "\n".join(logs)
        self.assertIn('ExecStart=/usr/bin/python3 /opt/p.pyz --home "%s" run' % conn, text)
        self.assertNotIn("PIXEL_OFFICE_HOME", text)

    def test_linux_unchanged_for_default_and_connector_dirs(self):
        logs = []
        S.install(os.path.join(self.h, ".pixel-office", "connector"), dry_run=True, log=logs.append, platform="linux",
                  user_home=self.h, python="/usr/bin/python3", path_env="/usr/bin")
        text = "\n".join(logs)
        self.assertIn("ExecStart=/usr/bin/python3 %h/.pixel-office/connector/pixel-connector.pyz run\n", text)
        self.assertNotIn("PIXEL_OFFICE_HOME", text)
        self.assertIn("Environment=PATH=/usr/bin", text)
        logs.clear()
        office = os.path.join(self.h, "po")
        S.install(os.path.join(office, "connector"), dry_run=True, log=logs.append, platform="linux",
                  user_home=self.h, python="/usr/bin/python3")
        self.assertIn("Environment=PIXEL_OFFICE_HOME=%s" % office, "\n".join(logs))

    def test_launchd_env_and_home_flag(self):
        office = os.path.join(self.h, "Ömer Şahin")
        conn = os.path.join(office, "connector")
        run = Runner()
        rc, path = S.install(conn, platform="darwin", user_home=self.h, python="/usr/local/bin/python3",
                             log=lambda s: None, runner=run, path_env="/opt/homebrew/bin:/usr/bin")
        self.assertEqual(rc, 0)
        d = plistlib.loads(Path(path).read_bytes())
        self.assertEqual(d["EnvironmentVariables"], {"PATH": "/opt/homebrew/bin:/usr/bin", "PIXEL_OFFICE_HOME": office})
        self.assertEqual(d["ProgramArguments"], ["/usr/local/bin/python3", os.path.join(conn, "pixel-connector.pyz"),
                                                 "run"])
        other = os.path.join(self.h, "elsewhere")
        rc, path = S.install(other, platform="darwin", user_home=self.h, python="/usr/bin/python3",
                             log=lambda s: None, runner=run)
        d = plistlib.loads(Path(path).read_bytes())
        self.assertEqual(d["ProgramArguments"][2:], ["--home", other, "run"])
        self.assertNotIn("EnvironmentVariables", d)
        default = os.path.join(self.h, ".pixel-office", "connector")
        rc, path = S.install(default, platform="darwin", user_home=self.h, python="/usr/bin/python3",
                             log=lambda s: None, runner=run, path_env="/usr/bin")
        self.assertEqual(plistlib.loads(Path(path).read_bytes())["EnvironmentVariables"], {"PATH": "/usr/bin"})

    def test_windows_env_via_launcher_and_home_flag(self):
        office = os.path.join(self.h, "Ömer Şahin")
        conn = os.path.join(office, "connector")
        logs = []
        S.install(conn, dry_run=True, log=logs.append, platform="win32", user_home=self.h,
                  python="C:\\Py\\python.exe", environ={"USERNAME": "u"})
        xml = "\n".join(logs).split("Görev Zamanlayıcı tanımı:", 1)[1].split("\n", 1)[1]
        xml = xml[:xml.index("</Task>") + len("</Task>")]
        root, _ = task_settings(xml)
        argv = parse_windows_cmdline(root.find("t:Actions/t:Exec/t:Arguments", NS).text)
        self.assertEqual(argv[0], "-c")
        self.assertEqual(argv[2:], ["1", "PIXEL_OFFICE_HOME", office, os.path.join(conn, "pixel-connector.pyz"),
                                    "run"])
        logs.clear()
        other = os.path.join(self.h, "x y")
        S.install(other, dry_run=True, log=logs.append, platform="win32", user_home=self.h,
                  python="C:\\Py\\python.exe", environ={"USERNAME": "u"})
        text = "\n".join(logs)
        self.assertIn('"--home" "%s" "run"</Arguments>' % other, text)
        self.assertEqual(parse_windows_cmdline(text.split("<Arguments>")[1].split("</Arguments>")[0]),
                         [os.path.join(other, "pixel-connector.pyz"), "--home", other, "run"])

    def test_launcher_runs_the_connector_zipapp(self):
        import sys
        pyz = ROOT / "web" / "connector" / "pixel-connector.pyz"
        office = os.path.join(self.h, "Ömer Şahin")
        cmd, args = S.windows_action(sys.executable, str(pyz), ["--version"], {"PIXEL_OFFICE_HOME": office})
        r = subprocess.run([sys.executable] + parse_windows_cmdline(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"pixel-connector 3.", r.stdout)


# ------------------------------------------------------------------------------------------------------------------
# Antigravity watcher task: the same settings
# ------------------------------------------------------------------------------------------------------------------

class AntigravityTask(unittest.TestCase):
    def test_copies_match_the_connector(self):
        for a in ["", "a b", "C:\\a b\\", 'q"q', "Ömer Şahin\\", "x\\\\\"y"]:
            self.assertEqual(ag.windows_quote(a), S.windows_quote(a), a)
        env = {"PIXEL_OFFICE_HOME": "C:\\Users\\Ömer Şahin\\o"}
        self.assertEqual(ag.windows_action("p", "t", ["--x"], env), S.windows_action("p", "t", ["--x"], env))
        self.assertEqual(ag.task_scheduler_xml("c", "a b", "D\\u", "d&", "w"),
                         S.task_scheduler_xml("c", "a b", "D\\u", "d&", "w"))
        self.assertEqual(ag.task_scheduler_xml("c", "a", "u", "d"), S.task_scheduler_xml("c", "a", "u", "d"))
        self.assertEqual(ag.windows_user_id({"USERNAME": "u", "USERDOMAIN": "D"}), "D\\u")
        self.assertEqual(ag.pythonw_for("C:\\P\\python.exe"), S.pythonw_for("C:\\P\\python.exe"))
        self.assertEqual(ag.task_xml_bytes("x"), S.task_xml_bytes("x"))

    def test_install_with_non_default_home(self):
        tmp = tempfile.mkdtemp(prefix="p7-ag-")
        self.addCleanup(shutil.rmtree, tmp, True)
        office = os.path.join(tmp, "Ömer Şahin")
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        run = Runner()
        with mock.patch.dict(os.environ, {"PIXEL_OFFICE_HOME": office, "HOME": home}):
            rc = ag.install_antigravity_service(["--name", "A b"], platform="win32", python="C:\\Py\\python.exe",
                                                user_home=home, runner=run, log=lambda s: None,
                                                environ={"USERNAME": "u", "USERDOMAIN": "D"})
        self.assertEqual(rc, 0)
        xml_path = os.path.join(office, "pixel-antigravity.task.xml")
        self.assertEqual(run.calls[0], ["schtasks", "/Create", "/TN", "PixelOffice\\Antigravity", "/XML", xml_path,
                                        "/F"])
        root, st = task_settings(Path(xml_path).read_bytes().decode("utf-16"))
        self.assertEqual(st["DisallowStartIfOnBatteries"].text, "false")
        self.assertEqual(st["ExecutionTimeLimit"].text, "PT0S")
        argv = parse_windows_cmdline(root.find("t:Actions/t:Exec/t:Arguments", NS).text)
        self.assertEqual(argv[2:], ["1", "PIXEL_OFFICE_HOME", office, os.path.join(office, "pixel_antigravity.py"),
                                    "--name", "A b"])
        self.assertEqual(root.find("t:Actions/t:Exec/t:Command", NS).text, "C:\\Py\\pythonw.exe")


# ------------------------------------------------------------------------------------------------------------------
# H10c — Windows cancel kills the process tree
# ------------------------------------------------------------------------------------------------------------------

class P:
    pid = 4242

    def __init__(self, alive=True):
        self.alive = alive
        self.killed = 0

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.killed += 1


class WindowsCancel(unittest.TestCase):
    def test_no_console_skips_ctrl_break_and_kills_tree(self):
        sent, trees, waits = [], [], []
        c = CL.Canceller(P(), 5.0, 10.0, signal_fn=sent.append, wait_fn=lambda t: waits.append(t) or False,
                         platform="win32", tree_kill_fn=trees.append, has_console=lambda: False)
        c.run()
        self.assertEqual(c.sent, ["TREE_KILL", "KILL"])
        self.assertEqual(trees, [4242])
        self.assertEqual(waits, [10.0])
        self.assertEqual(sent, [signal.SIGTERM])                  # TerminateProcess on the root as a last resort

    def test_with_console_tries_ctrl_break_first(self):
        if not hasattr(signal, "CTRL_BREAK_EVENT"):
            brk = mock.patch.object(signal, "CTRL_BREAK_EVENT", 1, create=True)
            brk.start()
            self.addCleanup(brk.stop)
        sent, trees, waits = [], [], []
        c = CL.Canceller(P(), 5.0, 10.0, signal_fn=sent.append, wait_fn=lambda t: waits.append(t) or False,
                         platform="win32", tree_kill_fn=trees.append, has_console=lambda: True)
        c.run()
        self.assertEqual(c.sent, ["CTRL_BREAK", "TREE_KILL", "KILL"])
        self.assertEqual(waits, [5.0, 10.0])
        self.assertEqual(sent, [signal.CTRL_BREAK_EVENT, signal.SIGTERM])
        trees.clear()
        c2 = CL.Canceller(P(), 5.0, 10.0, signal_fn=sent.append, wait_fn=lambda t: True, platform="win32",
                          tree_kill_fn=trees.append, has_console=lambda: True)
        c2.run()
        self.assertEqual(c2.sent, ["CTRL_BREAK"])
        self.assertEqual(trees, [])

    def test_linux_escalation_unchanged(self):
        sent = []
        c = CL.Canceller(P(), 5.0, 10.0, signal_fn=sent.append, wait_fn=lambda t: False, platform="linux",
                         tree_kill_fn=lambda pid: self.fail("no taskkill on Linux"))
        c.run()
        self.assertEqual(sent, [signal.SIGINT, signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(c.sent, ["INT", "TERM", "KILL"])

    def test_taskkill_command(self):
        self.assertEqual(CL.taskkill_argv(77, {"SystemRoot": "C:\\WINDOWS"}),
                         ["C:\\WINDOWS\\System32\\taskkill.exe", "/T", "/F", "/PID", "77"])
        self.assertEqual(CL.taskkill_argv(1, {})[0], "C:\\Windows\\System32\\taskkill.exe")
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.dict(os.environ, {"SystemRoot": "D:\\Win"}):
            self.assertTrue(CL.win_kill_tree(55, run=run))
        argv, kw = calls[0]
        self.assertEqual(argv, ["D:\\Win\\System32\\taskkill.exe", "/T", "/F", "/PID", "55"])
        self.assertEqual(kw["creationflags"] & 0x08000000, 0x08000000)          # no console window under pythonw
        self.assertIn("timeout", kw)
        self.assertFalse(CL.win_kill_tree(55, run=lambda a, **k: subprocess.CompletedProcess(a, 128)))

        def boom(a, **k):
            raise FileNotFoundError(a[0])
        self.assertFalse(CL.win_kill_tree(55, run=boom))

    def test_creationflags(self):
        self.assertEqual(CL.win_creationflags(True), 0x200)
        self.assertEqual(CL.win_creationflags(False), 0x200 | 0x08000000)
        self.assertIs(CL.win_has_console(), False)                   # no ctypes.windll here → False, never raises

    def test_run_kill_on_windows_kills_tree_then_root(self):
        run = CL.ClaudeRun.__new__(CL.ClaudeRun)
        run.proc = P()
        trees = []
        with mock.patch.object(CL.sys, "platform", "win32"), \
                mock.patch.object(CL, "win_kill_tree", side_effect=lambda pid: trees.append(pid) or True):
            run.kill()
        self.assertEqual(trees, [4242])
        self.assertEqual(run.proc.killed, 1)                     # still "alive" in the fake → TerminateProcess too
        run.proc = P(alive=False)
        with mock.patch.object(CL.sys, "platform", "win32"), \
                mock.patch.object(CL, "win_kill_tree", side_effect=lambda pid: self.fail("already exited")):
            run.kill()


# ------------------------------------------------------------------------------------------------------------------
# H31 — Windows hook state lock (fake msvcrt)
# ------------------------------------------------------------------------------------------------------------------

class FakeMsvcrt:
    LK_UNLCK, LK_LOCK, LK_NBLCK = 0, 1, 2

    def __init__(self, busy_times=0):
        self.busy = busy_times
        self.calls = []

    def locking(self, fd, mode, n):
        self.calls.append((mode, n, os.lseek(fd, 0, os.SEEK_CUR)))
        if mode == self.LK_NBLCK and self.busy:
            if self.busy > 0:
                self.busy -= 1
            raise PermissionError(13, "Permission denied")


class WindowsHookLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p7-lock-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def patch(self, fake):
        for p in (mock.patch.object(ph, "fcntl", None), mock.patch.object(ph, "msvcrt", fake)):
            p.start()
            self.addCleanup(p.stop)

    def test_locks_and_unlocks_first_byte(self):
        fake = FakeMsvcrt()
        self.patch(fake)
        r = ph.HookRunner({}, self.tmp)
        with r.locked():
            self.assertEqual(fake.calls, [(FakeMsvcrt.LK_NBLCK, 1, 0)])
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, "state", ".lock")))
        self.assertEqual(fake.calls[-1], (FakeMsvcrt.LK_UNLCK, 1, 0))

    def test_retries_while_busy(self):
        fake = FakeMsvcrt(busy_times=3)
        self.patch(fake)
        t = [0.0]
        slept = []

        def sleep(s):
            slept.append(s)
            t[0] += s
        fd = ph._win_lock_acquire(os.path.join(self.tmp, "state"), clock=lambda: t[0], sleep=sleep)
        self.assertIsNotNone(fd)
        self.assertEqual(slept, [ph.WIN_LOCK_POLL] * 3)
        ph._win_lock_release(fd)

    def test_timeout_fails_open_and_never_hangs(self):
        fake = FakeMsvcrt(busy_times=-1)                       # always busy
        self.patch(fake)
        t = [0.0]

        def sleep(s):
            t[0] += s
        self.assertIsNone(ph._win_lock_acquire(os.path.join(self.tmp, "state"), clock=lambda: t[0], sleep=sleep))
        self.assertLessEqual(t[0], ph.WIN_LOCK_WAIT + ph.WIN_LOCK_POLL)
        self.assertLessEqual(ph.WIN_LOCK_WAIT, 2.0)
        import time
        with mock.patch.object(ph, "WIN_LOCK_WAIT", 0.2):               # real clock, shortened wait
            ran = []
            t0 = time.monotonic()
            with ph.HookRunner({}, self.tmp).locked():
                ran.append(1)
            self.assertEqual(ran, [1])
            self.assertLess(time.monotonic() - t0, 1.5)
        self.assertIn("state lock unavailable", Path(self.tmp, "hook.log").read_text())
        self.assertFalse([c for c in fake.calls if c[0] == FakeMsvcrt.LK_UNLCK])   # nothing to release

    def test_unopenable_lock_file_fails_open(self):
        self.patch(FakeMsvcrt())
        Path(self.tmp, "state").write_text("not a dir")                   # makedirs/open fail
        ran = []
        with ph.HookRunner({}, self.tmp).locked():
            ran.append(1)
        self.assertEqual(ran, [1])

    def test_body_exception_propagates_and_releases(self):
        fake = FakeMsvcrt()
        self.patch(fake)
        with self.assertRaises(ValueError):
            with ph.HookRunner({}, self.tmp).locked():
                raise ValueError("x")
        self.assertEqual(fake.calls[-1][0], FakeMsvcrt.LK_UNLCK)

    def test_full_hook_event_with_fake_msvcrt(self):
        fake = FakeMsvcrt()
        self.patch(fake)
        sent = []
        r = ph.HookRunner({"salt": "s"}, self.tmp, clock=lambda: 1000.0, sleep=lambda s: None,
                          post=lambda updates, timeout, retries: sent.append(updates) or 200)
        r.handle({"hook_event_name": "SessionStart", "session_id": "abc", "cwd": self.tmp, "source": "startup"})
        modes = [c[0] for c in fake.calls]
        self.assertIn(FakeMsvcrt.LK_NBLCK, modes)
        self.assertEqual(modes.count(FakeMsvcrt.LK_NBLCK), modes.count(FakeMsvcrt.LK_UNLCK))

    def test_posix_path_unchanged(self):
        if ph.fcntl is None:
            self.skipTest("POSIX only")
        with mock.patch.object(ph, "msvcrt", mock.Mock(side_effect=AssertionError)) as m:
            with ph.HookRunner({}, self.tmp).locked():
                pass
        m.locking.assert_not_called()


if __name__ == "__main__":
    unittest.main()
