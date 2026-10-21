"""Phase 20 / Package 1 (v2.md H1, H7, H8, H13, H30; docs/SECURITY_AUDIT.md S20-01 … S20-05).

S20-01 (H1)  Edit rules also cover a `.git` *file* (gitdir redirection), the whole `.claude/` tree, `.idea/`,
             `*.code-workspace`, `.devcontainer/` and ~/.npmrc; git in the Claude child runs with command-scope pins
             (core.fsmonitor, core.hooksPath, core.pager) that beat a planted repository config.
S20-02 (H7)  The Claude child env is an allow-list; configured `apiKeyEnv` names are removed as well.
S20-03 (H8)  Loopback HTTP calls (CLI → service, local-mode server, hooks) never go through http_proxy.
S20-04 (H30) More credential folders are read-denied and denied as workspaces, consistently.
S20-05 (H13) `dev_server.py --relay` never prints a RELAY_SECRET that came from the environment.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401  (sys.path)
import _v3_fakes as F

import dev_server
from pixel_connector import config as CFG
from pixel_connector import localhttp
from pixel_connector import relay as R
from pixel_connector import workspaces as W
from pixel_connector.approvals import ctl_request
from pixel_connector.engines import claude as CL
from pixel_connector.engines.base import TaskContext

ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PA = _load("pixel_agent_phase20", "web/hook/pixel_agent.py")
PH = _load("pixel_hook_phase20", "web/hook/pixel_hook.py")
SID = "11111111-2222-4333-8444-555555555555"
PROFILES = {"readonly": {"deny": []}, "edit": {"deny": ["Read(./secrets/**)"]},
            "dev": {"allow": ["Bash(git status *)", "Bash(git diff *)"], "deny": []}, "auto": {"enabled": True}}


def deny_args(profile):
    a = CL.build_argv("/opt/claude", profile, PROFILES, SID, False, None, 50, None, (2, 1, 300))
    j = a.index("--disallowedTools")
    end = next((i for i in range(j + 1, len(a)) if a[i].startswith("--")), len(a))
    return a[j + 1:end]


# -- S20-01 (H1) ----------------------------------------------------------------------------------------------

class EditToExecDeny(unittest.TestCase):
    NEW_RULES = ["Edit(//**/.git)", "Edit(**/.git)", "Edit(//**/.claude/**)", "Edit(**/.claude/**)",
                 "Edit(//**/.idea/**)", "Edit(**/.idea/**)", "Edit(//**/*.code-workspace)", "Edit(**/*.code-workspace)",
                 "Edit(//**/.devcontainer/**)", "Edit(**/.devcontainer/**)", "Edit(~/.npmrc)"]
    KEPT_RULES = ["Edit(//**/.git/**)", "Edit(**/.git/**)", "Edit(**/.vscode/**)", "Edit(**/.mcp.json)",
                  "Edit(**/.envrc)", "Edit(~/.claude/**)", "Edit(~/.config/**)"]

    def test_every_profile_gets_the_rules_in_disallowed_tools(self):
        for prof in ("readonly", "edit", "dev", "auto"):
            deny = deny_args(prof)
            with self.subTest(profile=prof):
                for rule in self.NEW_RULES + self.KEPT_RULES:
                    self.assertIn(rule, deny)
                self.assertEqual(deny[:len(CL.BUILTIN_DENY)], CL.BUILTIN_DENY)
        self.assertIn("Read(./secrets/**)", deny_args("edit"))          # profile rules are appended, not replacing

    def test_editing_profiles_allow_edit_tools_yet_carry_the_deny_rules(self):
        for prof in ("edit", "dev"):
            a = CL.build_argv("/opt/claude", prof, PROFILES, SID, True, "opus", 10, 1.0, (2, 1, 300), bare=True)
            self.assertIn("Write", a[a.index("--allowedTools") + 1:a.index("--disallowedTools")])
            self.assertIn("Edit(**/.git)", a[a.index("--disallowedTools") + 1:])

    def test_workspace_relative_rules_have_an_absolute_twin(self):
        rel = [r for r in CL.EXEC_SENSITIVE_DENY if r.startswith("Edit(**/")]
        absolute = [r for r in CL.EXEC_SENSITIVE_DENY if r.startswith("Edit(//**/")]
        self.assertEqual(sorted(r.replace("Edit(**/", "Edit(//**/") for r in rel), sorted(absolute))
        self.assertEqual(len(CL.BUILTIN_DENY), len(set(CL.BUILTIN_DENY)))
        for r in CL.BUILTIN_DENY:
            self.assertRegex(r, r"^(Bash|Read|Edit)\([^()\s]+( \*)?\)\Z")


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitPinsInChildEnv(unittest.TestCase):
    """Real git: a config planted through a `.git` file must not run code under the child env's pins."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.marker = os.path.join(self.tmp, "pwned")
        self.evil = os.path.join(self.tmp, "evil.sh")
        with open(self.evil, "w") as f:
            f.write("#!/bin/sh\necho \"$0\" >> '%s'\ncat >/dev/null\n" % self.marker)
        os.chmod(self.evil, 0o755)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)
        self.base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home}
        # The attacker's directory and the `.git` *file* that points git at it (what Edit(**/.git) now denies).
        gitdir = os.path.join(self.tmp, "planted")
        self.git(["init", "-q", "--bare", gitdir], self.base)
        self.git(["config", "--file", os.path.join(gitdir, "config"), "core.bare", "false"], self.base)
        for key in ("core.fsmonitor", "core.pager"):
            self.git(["config", "--file", os.path.join(gitdir, "config"), key, self.evil], self.base)
        hooks = os.path.join(gitdir, "hooks")
        os.makedirs(hooks, exist_ok=True)
        shutil.copy(self.evil, os.path.join(hooks, "pre-commit"))
        with open(os.path.join(self.ws, ".git"), "w") as f:
            f.write("gitdir: %s\n" % gitdir)
        with open(os.path.join(self.ws, "a.txt"), "w") as f:
            f.write("x\n")

    def git(self, args, env, check=True):
        return subprocess.run(["git"] + args, cwd=self.ws, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=check)

    def exercise(self, env):
        env = dict(env, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.git(["status"], env)
        self.git(["-p", "log", "--oneline", "-1"], env, check=False)
        self.git(["add", "a.txt"], env)
        self.git(["commit", "-qm", "m"], env, check=False)

    def test_planted_config_runs_code_without_the_pins(self):
        self.exercise(self.base)                                   # control: the attack is real
        with open(self.marker) as f:
            ran = f.read()
        self.assertIn("evil.sh", ran)                              # fsmonitor / pager
        self.assertIn("pre-commit", ran)                           # hook from the planted gitdir

    def test_pins_stop_fsmonitor_pager_and_hooks(self):
        self.exercise(CL.child_env(self.base))
        self.assertFalse(os.path.exists(self.marker))
        log = self.git(["log", "--oneline"], CL.child_env(self.base)).stdout
        self.assertEqual(len(log.splitlines()), 1)                 # the commit itself went through

    def test_inherited_git_variables_are_replaced(self):
        env = CL.child_env(dict(self.base, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor",
                                GIT_CONFIG_VALUE_0=self.evil, GIT_SSH_COMMAND="evil", GIT_DIR="/x"))
        self.assertEqual(env["GIT_CONFIG_COUNT"], str(len(CL.GIT_CONFIG_PINS)))
        self.assertNotIn("GIT_SSH_COMMAND", env)
        self.assertNotIn("GIT_DIR", env)
        self.assertEqual(dict((env["GIT_CONFIG_KEY_%d" % i], env["GIT_CONFIG_VALUE_%d" % i])
                              for i in range(len(CL.GIT_CONFIG_PINS))),
                         {"core.fsmonitor": "false", "core.hooksPath": os.devnull, "core.pager": "cat"})


# -- S20-02 (H7) ----------------------------------------------------------------------------------------------

SECRETS = {"GH_TOKEN": "s", "GITHUB_TOKEN": "s", "AWS_SECRET_ACCESS_KEY": "s", "AWS_ACCESS_KEY_ID": "s",
           "OPENAI_API_KEY": "s", "MY_LLM_KEY": "s", "NPM_TOKEN": "s", "DATABASE_URL": "s", "PIXEL_OFFICE_KEY": "s",
           "PIXEL_SECRET": "s", "RELAY_SECRET": "s", "UPSTASH_REDIS_REST_TOKEN": "s", "KV_REST_API_TOKEN": "s",
           "LD_PRELOAD": "/x.so", "NODE_OPTIONS": "--require /x.js", "BASH_ENV": "/x", "PYTHONPATH": "/x",
           "SSH_AUTH_SOCK": "/s", "CLAUDECODE": "1", "GIT_SSH_COMMAND": "x"}
NEEDED = {"PATH": "/bin", "HOME": "/h", "USER": "u", "LOGNAME": "u", "SHELL": "/bin/sh", "LANG": "tr_TR.UTF-8",
          "LC_ALL": "C.UTF-8", "TERM": "xterm", "TMPDIR": "/t", "TZ": "Europe/Istanbul", "XDG_RUNTIME_DIR": "/r",
          "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "https://a", "CLAUDE_CONFIG_DIR": "/c",
          "CLAUDE_CODE_USE_BEDROCK": "0", "http_proxy": "http://p:1", "HTTPS_PROXY": "http://p:1", "no_proxy": "x",
          "NODE_EXTRA_CA_CERTS": "/ca.pem", "SSL_CERT_FILE": "/ca.pem",
          "SystemRoot": "C:\\Windows", "ComSpec": "cmd.exe", "PATHEXT": ".EXE", "APPDATA": "a", "LOCALAPPDATA": "l",
          "USERPROFILE": "C:\\Users\\u", "ProgramData": "p", "windir": "C:\\Windows", "TEMP": "t", "TMP": "t"}


class ChildEnvAllowList(unittest.TestCase):
    def test_secrets_are_not_passed_and_required_vars_are(self):
        env = CL.child_env(dict(SECRETS, **NEEDED), drop=["MY_LLM_KEY"])
        for k in SECRETS:
            self.assertNotIn(k, env)
        for k, v in NEEDED.items():
            self.assertEqual(env.get(k), v, k)
        self.assertEqual(env["PIXEL_OFFICE_DISABLE"], "1")

    def test_api_key_env_is_dropped_even_when_allow_listed(self):
        env = CL.child_env({"PATH": "/bin", "ANTHROPIC_AUTH_TOKEN": "x", "Claude_Proxy_Key": "y"},
                           drop=["ANTHROPIC_AUTH_TOKEN", "CLAUDE_PROXY_KEY"])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("Claude_Proxy_Key", env)                     # case-insensitive like Windows
        self.assertIn("PATH", env)

    def test_secret_prefix_wins_over_allowed_prefix(self):
        self.assertFalse(CL._child_env_allowed("PIXEL_OFFICE_HOME"))
        self.assertTrue(CL._child_env_allowed("lc_ctype"))
        self.assertFalse(CL._child_env_allowed("XDGX"))
        self.assertFalse(CL._child_env_allowed("PATHX"))

    def test_real_process_gets_the_filtered_env(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fake_dir = os.path.join(tmp, "fake")
        path = F.write_fake_claude(fake_dir, [{"fixture": "claude_stream_success_tools.jsonl"}])
        ws = os.path.join(tmp, "ws")
        os.makedirs(ws)
        base = dict(os.environ, CLAUDE_FAKE_DIR=fake_dir, **SECRETS)
        cfg = {"path": path, "enabled": True, "maxTurns": 5, "cancelGraceS": 5.0, "cancelTermS": 10.0}
        eng = CL.ClaudeEngine(cfg, PROFILES, env=base, drop_env=["MY_LLM_KEY"])
        done = threading.Event()

        class Ev:
            def part(self, p):
                pass

            def live(self, **u):
                pass

            def office(self, state, tool=None, label=None):
                pass

            def end(self, *a, **k):
                done.set()

        agent = {"key": "a1", "name": "x", "engine": "claude", "claudeSession": None, "claudeSessionStarted": False,
                 "claudeTotals": None}
        eng.start(TaskContext("t_0123456789", agent, "p", ws, None, "edit", False, None, save_agent=lambda a: None), Ev())
        self.assertTrue(done.wait(30))
        keys = set(F.calls(fake_dir)[0]["env_keys"])
        self.assertFalse(keys & set(SECRETS), keys & set(SECRETS))
        self.assertIn("PATH", keys)
        self.assertIn("GIT_CONFIG_COUNT", keys)

    def test_connector_passes_configured_api_key_envs(self):
        from pixel_connector.app import Connector
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        home = CFG.ensure_dir(os.path.join(tmp, "connector"))
        cfg = CFG.initial_config("http://127.0.0.1:9", "abcdefghijklmnopqrstuvwxyz", "k" * 43, "T", "D", None)
        cfg["approvalPort"] = 0
        cfg["engines"]["openai"] = [{"id": "lm", "url": "http://127.0.0.1:1234/v1", "apiKeyEnv": "MY_LLM_KEY"},
                                    {"id": "lm2", "url": "http://127.0.0.1:1235/v1"}]
        CFG.write_json(os.path.join(home, CFG.CONFIG_FILE), cfg)
        loaded = CFG.load_config(home, user_home=tmp)
        self.assertTrue(loaded.ok, loaded.errors)
        conn = Connector(home, loaded, start_ctl=False, user_home=tmp)
        self.assertEqual(conn.claude.drop_env, ("MY_LLM_KEY",))
        conn.claude.env = {"PATH": "/bin", "MY_LLM_KEY": "s", "ANTHROPIC_API_KEY": "k"}
        self.assertEqual(sorted(k for k in conn.claude.child_env() if not k.startswith("GIT_CONFIG_")),
                         ["ANTHROPIC_API_KEY", "PATH", "PIXEL_OFFICE_DISABLE"])


# -- S20-03 (H8) ----------------------------------------------------------------------------------------------

class _Recorder(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        self.server.seen.append((self.command, self.path, dict(self.headers), body))
        out = json.dumps(self.server.reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _answer


def _server(reply):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    srv.seen, srv.reply = [], reply
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _dead_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LoopbackBypassesProxy(unittest.TestCase):
    def setUp(self):
        self.srv = _server({"ok": True})
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        dead = "http://127.0.0.1:%d" % _dead_port()               # an unreachable proxy
        env = {k: v for k, v in os.environ.items() if k.lower() not in ("no_proxy", "http_proxy", "https_proxy",
                                                                          "all_proxy")}
        env.update(http_proxy=dead, HTTP_PROXY=dead, https_proxy=dead, HTTPS_PROXY=dead)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        o = mock.patch.object(urllib.request, "_opener", None)      # the global opener caches proxies from env
        o.start()
        self.addCleanup(o.stop)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def test_control_the_proxy_env_really_breaks_plain_urllib(self):
        with self.assertRaises(urllib.error.URLError):
            urllib.request.build_opener().open(self.base + "/x", timeout=5)

    def test_ctl_request_reaches_the_local_service(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        CFG.write_json(os.path.join(home, CFG.CONTROL_FILE), {"port": self.srv.server_address[1], "token": "T" * 32})
        st, obj = ctl_request(home, "POST", "/ctl/ping", {"a": 1})
        self.assertEqual((st, obj), (200, {"ok": True}))
        method, path, headers, _ = self.srv.seen[-1]
        self.assertEqual((method, path, headers.get("X-Pixel-Ctl")), ("POST", "/ctl/ping", "T" * 32))

    def test_local_server_calls_and_hooks(self):
        for url in (self.base, self.base.replace("127.0.0.1", "localhost")):
            with localhttp.urlopen(url + "/api/health", timeout=5) as r:
                self.assertEqual(r.status, 200)
        self.assertEqual(PA.post_updates({"url": self.base, "room": None, "key": "k"}, [{"id": "a"}], retries=0),
                         200)
        self.assertEqual(PH.post_updates({"server": self.base, "room": None, "key": "k"}, [{"id": "a"}], retries=0),
                         200)
        self.srv.reply = {"url": "ws://x", "ticket": "t"}
        with self.assertRaises(R.TicketError) as cm:                # reached the server: its answer was judged
            R.fetch_ticket(self.base, "abcdefghijklmnopqrstuvwxyz", "k", "d", timeout=5)
        self.assertEqual(cm.exception.status, 0)
        self.assertTrue(all(h.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost") for _, _, h, _ in self.srv.seen))

    def test_non_loopback_still_uses_the_proxy(self):
        proxy = _server({"via": "proxy"})
        self.addCleanup(proxy.server_close)
        self.addCleanup(proxy.shutdown)
        purl = "http://127.0.0.1:%d" % proxy.server_address[1]
        os.environ.update(http_proxy=purl, HTTP_PROXY=purl)
        with localhttp.urlopen("http://pixel.example.invalid/api/health", timeout=5) as r:
            self.assertEqual(json.loads(r.read()), {"via": "proxy"})
        self.assertEqual(proxy.seen[-1][1], "http://pixel.example.invalid/api/health")

    def test_is_loopback_url(self):
        for u in ("http://127.0.0.1:1/x", "http://127.5.5.5/", "http://[::1]:8/", "http://localhost:8765",
                  "http://LOCALHOST./", "http://app.localhost/"):
            self.assertTrue(localhttp.is_loopback_url(u), u)
            self.assertTrue(PA._is_loopback_url(u), u)
            self.assertTrue(PH._is_loopback_url(u), u)
        for u in ("http://10.0.0.1/", "https://example.com/", "http://localhost.example.com/",
                  "http://127.0.0.1.nip.io/", "http://[::2]/", "not a url"):
            self.assertFalse(localhttp.is_loopback_url(u), u)
            self.assertFalse(PA._is_loopback_url(u), u)
            self.assertFalse(PH._is_loopback_url(u), u)

    def test_no_plain_urlopen_left_in_connector_and_hooks(self):
        for p in list((ROOT / "pixel_connector").rglob("*.py")) + [ROOT / "web/hook/pixel_hook.py",
                                                                     ROOT / "web/hook/pixel_agent.py"]:
            src = p.read_text(encoding="utf-8")
            calls = re.findall(r"urllib\.request\.urlopen\(", src)
            allowed = 1 if p.name in ("localhttp.py", "pixel_hook.py", "pixel_agent.py") else 0
            self.assertEqual(len(calls), allowed, p.name)


# -- S20-04 (H30) ---------------------------------------------------------------------------------------------

class SecretReadDeny(unittest.TestCase):
    MUST = ["Read(~/.gemini/**)", "Read(~/.codex/**)", "Read(~/.npmrc)", "Read(~/.pypirc)", "Read(~/.cargo/credentials)",
            "Read(~/.cargo/credentials.toml)", "Read(~/.netrc)", "Read(~/.git-credentials)", "Read(~/.config/gh/**)",
            "Read(~/.docker/config.json)", "Read(~/.kube/**)", "Read(~/.local/share/keyrings/**)",
            "Read(~/Library/Keychains/**)", "Read(~/.mozilla/**)", "Read(~/.config/google-chrome/**)",
            "Read(~/.config/chromium/**)", "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.claude/.credentials.json)"]

    def test_every_profile_denies_reading_them(self):
        for prof in ("readonly", "edit", "dev", "auto"):
            deny = deny_args(prof)
            for rule in self.MUST:
                self.assertIn(rule, deny, (prof, rule))

    def test_workspace_deny_list_matches_the_read_rules(self):
        home = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, True)
        homed = [r[len("Read(~/"):-1] for r in CL.BUILTIN_DENY if r.startswith("Read(~/")]
        self.assertGreater(len(homed), 15)
        for rel in homed:
            folder = rel[:-3] if rel.endswith("/**") else os.path.dirname(rel)
            if not folder:
                continue                                   # a file directly in home (~/.npmrc …): never a workspace
            path = os.path.join(home, *folder.split("/"))
            with self.subTest(rule=rel):
                self.assertIsNotNone(W.check_denied(path, home))
                self.assertIsNotNone(W.check_denied(os.path.join(path, "sub"), home))

    def test_new_workspace_roots_are_denied(self):
        home = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, True)
        for d in (".gemini", ".codex", ".docker", ".kube", ".azure", ".cargo", ".mozilla", ".local/share/keyrings",
                  ".local/bin", "Library/Keychains"):
            self.assertIsNotNone(W.check_denied(os.path.join(home, *d.split("/")), home), d)
        self.assertIsNone(W.check_denied(os.path.join(home, "projects", "gemini"), home))
        self.assertIsNone(W.check_denied(os.path.join(home, ".local", "src"), home))


# -- S20-05 (H13) ---------------------------------------------------------------------------------------------

class DevServerRelaySecret(unittest.TestCase):
    def test_secret_from_env_is_never_printed(self):
        real = "R" * 44
        with mock.patch.dict(os.environ, {"RELAY_SECRET": real}):
            srv, url, secret, generated = dev_server.start_relay(0, 8787)
            try:
                self.assertFalse(generated)
                self.assertEqual(secret, real)
                line = dev_server.relay_secret_line(secret, generated)
                self.assertNotIn(real, line)
                self.assertNotIn("RRRR", line)
                self.assertIn("ortamdan alındı", line)
            finally:
                srv.shutdown()
                srv.server_close()

    def test_generated_secret_is_shown(self):
        with mock.patch.dict(os.environ, {"RELAY_SECRET": "too-short"}):
            srv, url, secret, generated = dev_server.start_relay(0, 8787)
            try:
                self.assertTrue(generated)
                self.assertNotEqual(secret, "too-short")
                self.assertIn(secret, dev_server.relay_secret_line(secret, generated))
            finally:
                srv.shutdown()
                srv.server_close()

    def test_main_prints_only_the_line_helper(self):
        src = (ROOT / "dev_server.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"print\([^)]*relay_secret\}")
        self.assertIn("print(relay_secret_line(relay_secret, relay_secret_generated)", src)


if __name__ == "__main__":
    unittest.main()
