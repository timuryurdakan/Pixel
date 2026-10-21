"""`connector.json` load/validate, connector file locations and safe file writes (V3.md §5.2, §9.2).

The connector directory is `~/.pixel-office/connector/` (0700); `PIXEL_OFFICE_HOME` moves `~/.pixel-office` (tests,
portable installs) and `--home` overrides the connector directory itself. Every file is written atomically
(temp file + `os.replace`) with mode 0600.
"""

from __future__ import annotations

import copy
import glob
import hashlib
import ipaddress
import json
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from . import workspaces as W

CONFIG_FILE = "connector.json"
KEYS_FILE = "keys.json"
PEERS_FILE = "peers.json"
AGENTS_FILE = "agents.json"
BUDGETS_FILE = "budgets.json"
CONTROL_FILE = "control.json"
AUDIT_FILE = "audit.log"
LEDGER_FILE = "usage.sqlite"
LOCK_FILE = "run.lock"
LOG_FILE = "connector.log"

ID_RE = re.compile(r"^[a-z0-9_-]{1,24}\Z")
ROOM_RE = re.compile(r"^[a-z2-7]{26}\Z")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}\Z")      # T12; never starts with '-'
RULE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}(\([^\x00-\x1f]{1,200}\))?\Z")
FORBIDDEN_RE = re.compile(r"bypass\s*permissions|dangerously|skip-permissions", re.I)
PROFILES = ("readonly", "edit", "dev", "auto")
ENGINES = ("claude", "ollama", "openai")

DEFAULT_LIMITS = {"maxConcurrent": 2, "taskTimeoutS": 3600, "maxPromptBytes": 32768, "tasksPerMinute": 6,
                  "tasksPerHour": 60, "chunkMs": 250, "retainDays": 30, "runtimePerMinute": 10,
                  "agentCreatePerHour": 10, "maxAgents": 12, "handshakesPerHour": 20}
DEFAULT_CONFIRM = {"task": "never", "agentCreate": False, "runtime": False, "peerRevoke": False, "timeoutS": 120}
DEFAULT_CLAUDE = {"path": None, "enabled": True, "bare": False, "showThinking": False, "maxTurns": 50,
                  "maxBudgetUsdPerTask": 2.0, "defaultModel": None, "cancelGraceS": 5.0, "cancelTermS": 10.0}
DEFAULT_OLLAMA = {"url": "http://127.0.0.1:11434", "enabled": True, "keepAlive": "10m", "numCtx": None,
                  "historyMessages": 40, "allowRemote": False, "showThinking": False}


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------------------------------

def office_home() -> str:
    env = os.environ.get("PIXEL_OFFICE_HOME")
    return os.path.abspath(os.path.expanduser(env)) if env else os.path.join(os.path.expanduser("~"), ".pixel-office")


def connector_home(override: Optional[str] = None) -> str:
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(office_home(), "connector")


def ensure_dir(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def atomic_write(path: str, data: bytes) -> None:
    d = os.path.dirname(path) or "."
    ensure_dir(d)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def write_json(path: str, obj) -> None:
    atomic_write(path, (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(default)


# --------------------------------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------------------------------

def is_loopback_url(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _http_url(url, field: str, errors: List[str], allow_remote: bool) -> Optional[str]:
    if not isinstance(url, str) or not re.match(r"^https?://[^\s/]+", url) or "\x00" in url:
        errors.append("%s: must be an http(s) URL" % field)
        return None
    if not allow_remote and not is_loopback_url(url):
        errors.append("%s: must be a loopback address (127.0.0.1/localhost) unless allowRemote is true" % field)
        return None
    return url.rstrip("/")


def resolve_argv(argv, field: str, errors: List[str], which=shutil.which) -> Optional[List[str]]:
    """argv rule (§9.2): non-empty list of strings, no NUL; argv[0] absolute or resolved with which()."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        errors.append("%s: must be a non-empty list of strings" % field)
        return None
    if any("\x00" in a for a in argv) or not argv[0]:
        errors.append("%s: contains NUL or an empty program" % field)
        return None
    prog = argv[0]
    if not os.path.isabs(prog):
        if os.sep in prog or (os.altsep and os.altsep in prog):
            errors.append("%s: program must be an absolute path or a bare name" % field)
            return None
        found = which(prog)
        if not found:
            errors.append("%s: program %r not found on PATH" % (field, prog))
            return None
        prog = os.path.abspath(found)
    return [prog] + list(argv[1:])


def _scan_forbidden(obj, path: str, errors: List[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and FORBIDDEN_RE.search(k):
                errors.append("%s.%s: bypassPermissions / --dangerously-skip-permissions can never be configured" % (path, k))
            _scan_forbidden(v, "%s.%s" % (path, k), errors)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_forbidden(v, "%s[%d]" % (path, i), errors)
    elif isinstance(obj, str) and FORBIDDEN_RE.search(obj):
        errors.append("%s: bypassPermissions / --dangerously-skip-permissions can never be configured" % path)


def _bool(v, default: bool) -> bool:
    return v if isinstance(v, bool) else default


def _num(v, default, lo, hi, integer=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    if integer and not float(v).is_integer():
        return default
    v = int(v) if integer else float(v)
    return min(hi, max(lo, v))


def validate_budget(b, errors: List[str], where: str, need_scope: bool = True) -> Optional[dict]:
    if not isinstance(b, dict):
        errors.append("%s: budget must be an object" % where)
        return None
    scope = b.get("scope")
    if need_scope:
        if not isinstance(scope, str) or not (scope == "device" or re.match(r"^agent:[A-Za-z0-9_-]{1,24}\Z", scope)
                                              or scope in ("engine:claude", "engine:ollama", "engine:openai")):
            errors.append("%s: scope must be device, agent:<key> or engine:claude|ollama|openai" % where)
            return None
    period, unit, action, limit = b.get("period"), b.get("unit"), b.get("action", "warn"), b.get("limit")
    if period not in ("day", "week", "month") or unit not in ("tokens", "usd") or action not in ("warn", "block") \
            or isinstance(limit, bool) or not isinstance(limit, (int, float)) or not 0 < limit < 1e12:
        errors.append("%s: needs period day|week|month, unit tokens|usd, limit > 0, action warn|block" % where)
        return None
    out = {"period": period, "unit": unit, "limit": float(limit) if unit == "usd" else int(limit), "action": action}
    if need_scope:
        out = {"scope": scope, **out}
    return out


# --------------------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------------------

KNOWN_TOP = {"v", "server", "room", "key", "owner", "deviceName", "workspaces", "engines", "profiles", "runtimes",
             "confirm", "budgets", "limits", "remote", "audit", "approvalPort"}


class Config:
    def __init__(self, data: dict, errors: List[str], warnings: List[str], raw_hash: str, path: Optional[str]) -> None:
        self.data = data
        self.errors = errors
        self.warnings = warnings
        self.hash = raw_hash
        self.path = path

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(raw: Any, home: Optional[str] = None, which=shutil.which) -> Tuple[dict, List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(raw, dict):
        return {}, ["connector.json must contain a JSON object"], []
    _scan_forbidden(raw, "connector", errors)
    for k in raw:
        if k not in KNOWN_TOP:
            warnings.append("unknown key %r ignored" % k)
    d: Dict[str, Any] = {"v": 1}
    server = raw.get("server")
    if not isinstance(server, str) or not re.match(r"^https?://[^\s/]+\Z", server.rstrip("/")):
        errors.append("server: must be https://<host> (or http://127.0.0.1:<port> for development)")
    elif server.startswith("http://") and not is_loopback_url(server):
        errors.append("server: plain http is only allowed for loopback development servers")
    d["server"] = (server or "").rstrip("/") if isinstance(server, str) else ""
    room = raw.get("room")
    if not isinstance(room, str) or not ROOM_RE.match(room):
        errors.append("room: must be a 26-character room id")
    d["room"] = room if isinstance(room, str) else ""
    key = raw.get("key")
    if not isinstance(key, str) or not 8 <= len(key) <= 200 or not re.match(r"^[A-Za-z0-9_.~-]+\Z", key):
        errors.append("key: the room write key is missing or malformed")
    d["key"] = key if isinstance(key, str) else ""
    owner = raw.get("owner")
    d["owner"] = (owner.strip()[:24] if isinstance(owner, str) and owner.strip() else None) or "local"
    dn = raw.get("deviceName")
    d["deviceName"] = (dn.strip()[:32] if isinstance(dn, str) and dn.strip() else None) or _default_device_name()

    # workspaces
    wss = []
    seen = set()
    for i, w in enumerate(raw.get("workspaces") or []):
        where = "workspaces[%d]" % i
        if not isinstance(w, dict) or not isinstance(w.get("id"), str) or not ID_RE.match(w["id"]):
            errors.append("%s: id must match [a-z0-9_-]{1,24}" % where)
            continue
        if w["id"] in seen:
            errors.append("%s: duplicate id %r" % (where, w["id"]))
            continue
        seen.add(w["id"])
        name = w.get("name") if isinstance(w.get("name"), str) and w.get("name").strip() else w["id"]
        try:
            real = W.expand_path(w.get("path"), home)
        except ValueError as e:
            errors.append("%s: %s" % (where, e))
            continue
        reason = W.check_denied(real, home)
        if reason:
            errors.append("%s: %s is not allowed (%s)" % (where, w.get("path"), reason))
            continue
        if not os.path.isdir(real):
            warnings.append("%s: %s does not exist (yet)" % (where, real))
        wss.append({"id": w["id"], "name": name.strip()[:40], "path": w.get("path"), "real": real,
                    "children": _bool(w.get("children"), False)})
    d["workspaces"] = wss

    # engines
    eng = raw.get("engines") if isinstance(raw.get("engines"), dict) else {}
    c_raw = eng.get("claude") if isinstance(eng.get("claude"), dict) else {}
    c = dict(DEFAULT_CLAUDE)
    c["enabled"] = _bool(c_raw.get("enabled"), True)
    c["bare"] = _bool(c_raw.get("bare"), False)
    c["showThinking"] = _bool(c_raw.get("showThinking"), False)
    c["maxTurns"] = _num(c_raw.get("maxTurns"), 50, 1, 500, integer=True)
    c["maxBudgetUsdPerTask"] = _num(c_raw.get("maxBudgetUsdPerTask"), 2.0, 0.01, 1000.0)
    c["cancelGraceS"] = _num(c_raw.get("cancelGraceS"), 5.0, 0.05, 60.0)
    c["cancelTermS"] = _num(c_raw.get("cancelTermS"), 10.0, 0.05, 60.0)
    dm = c_raw.get("defaultModel")
    if dm is not None and (not isinstance(dm, str) or not MODEL_RE.match(dm)):
        errors.append("engines.claude.defaultModel: invalid model name")
        dm = None
    c["defaultModel"] = dm
    cp = c_raw.get("path")
    if cp is not None:
        r = resolve_argv([cp], "engines.claude.path", errors, which) if isinstance(cp, str) else None
        if cp is not None and not isinstance(cp, str):
            errors.append("engines.claude.path: must be a string")
        c["path"] = r[0] if r else None
    else:
        found = which("claude")
        c["path"] = os.path.abspath(found) if found else None
        if c["enabled"] and not found:
            warnings.append("engines.claude: `claude` not found on PATH; set engines.claude.path")
    d["engines"] = {"claude": c}

    o_raw = eng.get("ollama") if isinstance(eng.get("ollama"), dict) else {}
    o = dict(DEFAULT_OLLAMA)
    o["allowRemote"] = _bool(o_raw.get("allowRemote"), False)
    o["enabled"] = _bool(o_raw.get("enabled"), True)
    o["showThinking"] = _bool(o_raw.get("showThinking"), False)
    if "url" in o_raw:
        o["url"] = _http_url(o_raw.get("url"), "engines.ollama.url", errors, o["allowRemote"]) or o["url"]
    ka = o_raw.get("keepAlive", "10m")
    o["keepAlive"] = ka if isinstance(ka, str) and re.match(r"^-?\d{1,6}[smh]?\Z", ka) else "10m"
    nc = o_raw.get("numCtx")
    o["numCtx"] = _num(nc, None, 256, 1048576, integer=True) if nc is not None else None
    o["historyMessages"] = _num(o_raw.get("historyMessages"), 40, 0, 1000, integer=True)
    d["engines"]["ollama"] = o

    oa = []
    for i, e in enumerate(eng.get("openai") or []):
        where = "engines.openai[%d]" % i
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not ID_RE.match(e["id"]):
            errors.append("%s: id must match [a-z0-9_-]{1,24}" % where)
            continue
        allow_remote = _bool(e.get("allowRemote"), False)
        url = _http_url(e.get("url"), where + ".url", errors, allow_remote)
        if not url:
            continue
        env_name = e.get("apiKeyEnv")
        if env_name is not None and (not isinstance(env_name, str) or not re.match(r"^[A-Z_][A-Z0-9_]{0,63}\Z", env_name)):
            errors.append("%s.apiKeyEnv: must be an environment variable name" % where)
            env_name = None
        oa.append({"id": e["id"], "name": str(e.get("name") or e["id"])[:40], "url": url, "allowRemote": allow_remote,
                   "apiKeyEnv": env_name})
    d["engines"]["openai"] = oa

    # profiles
    pr = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
    profiles = {}
    for name in ("readonly", "edit", "dev"):
        p = pr.get(name) if isinstance(pr.get(name), dict) else {}
        allow, deny = [], []
        for field, out in (("allow", allow), ("deny", deny)):
            for j, rule in enumerate(p.get(field) or []):
                if not isinstance(rule, str) or not RULE_RE.match(rule):
                    errors.append("profiles.%s.%s[%d]: invalid permission rule" % (name, field, j))
                    continue
                if field == "allow" and rule == "Bash":
                    warnings.append("profiles.%s.allow: a bare \"Bash\" rule allows every shell command" % name)
                out.append(rule)
        if name != "dev" and allow:
            warnings.append("profiles.%s.allow is ignored (only the dev profile takes allow rules)" % name)
            allow = []
        profiles[name] = {"allow": allow, "deny": deny}
    auto = pr.get("auto") if isinstance(pr.get("auto"), dict) else {}
    profiles["auto"] = {"enabled": _bool(auto.get("enabled"), False), "allow": [], "deny": []}
    d["profiles"] = profiles

    # runtimes
    rts = []
    rseen = set()
    for i, r in enumerate(raw.get("runtimes") or []):
        where = "runtimes[%d]" % i
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not ID_RE.match(r["id"]):
            errors.append("%s: id must match [a-z0-9_-]{1,24}" % where)
            continue
        if r["id"] in rseen:
            errors.append("%s: duplicate id %r" % (where, r["id"]))
            continue
        rseen.add(r["id"])
        kind = r.get("kind", "service")
        if kind not in ("service", "ollama", "openai"):
            errors.append("%s: kind must be service, ollama or openai" % where)
            continue
        allow_remote = _bool(r.get("allowRemote"), False)
        rt = {"id": r["id"], "name": str(r.get("name") or r["id"])[:40], "kind": kind, "allowRemote": allow_remote,
              "start": None, "stop": None, "status": None, "url": None, "models": [],
              "allowAnyInstalledModel": _bool(r.get("allowAnyInstalledModel"), False)}
        for op in ("start", "stop"):
            if r.get(op) is not None:
                rt[op] = resolve_argv(r.get(op), "%s.%s" % (where, op), errors, which)
        if r.get("url") is not None:
            rt["url"] = _http_url(r.get("url"), where + ".url", errors, allow_remote)
        elif kind == "ollama":
            rt["url"] = DEFAULT_OLLAMA["url"]
        st = r.get("status")
        if isinstance(st, dict):
            if "http" in st:
                u = _http_url(st.get("http"), where + ".status.http", errors, allow_remote)
                rt["status"] = {"http": u} if u else None
            elif "systemd" in st:
                unit = st.get("systemd")
                if not isinstance(unit, str) or not re.match(r"^[A-Za-z0-9@._:-]{1,100}\Z", unit):
                    errors.append("%s.status.systemd: invalid unit name" % where)
                else:
                    rt["status"] = {"systemd": unit, "user": _bool(st.get("user"), False)}
            elif "process" in st:
                pn = st.get("process")
                if not isinstance(pn, str) or not re.match(r"^[A-Za-z0-9._-]{1,64}\Z", pn):
                    errors.append("%s.status.process: invalid process name" % where)
                else:
                    rt["status"] = {"process": pn}
            else:
                errors.append("%s.status: use {\"http\": url}, {\"systemd\": unit} or {\"process\": name}" % where)
        elif st is not None:
            errors.append("%s.status: must be an object" % where)
        for j, m in enumerate(r.get("models") or []):
            if not isinstance(m, str) or not MODEL_RE.match(m):
                errors.append("%s.models[%d]: invalid model name" % (where, j))
            else:
                rt["models"].append(m)
        rts.append(rt)
    d["runtimes"] = rts

    # confirm / limits / remote / audit
    cf = raw.get("confirm") if isinstance(raw.get("confirm"), dict) else {}
    confirm = dict(DEFAULT_CONFIRM)
    if cf.get("task") is not None:
        if cf.get("task") in ("never", "always", "new_session"):
            confirm["task"] = cf["task"]
        else:
            errors.append("confirm.task: must be never, always or new_session")
    for k in ("agentCreate", "runtime", "peerRevoke"):
        confirm[k] = _bool(cf.get(k), DEFAULT_CONFIRM[k])
    confirm["timeoutS"] = _num(cf.get("timeoutS"), 120, 5, 3600, integer=True)
    d["confirm"] = confirm
    lim = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
    limits = dict(DEFAULT_LIMITS)
    bounds = {"maxConcurrent": (1, 16), "taskTimeoutS": (10, 86400), "maxPromptBytes": (1, 32768),
              "tasksPerMinute": (1, 600), "tasksPerHour": (1, 10000), "chunkMs": (50, 5000), "retainDays": (1, 3650),
              "runtimePerMinute": (1, 600), "agentCreatePerHour": (1, 1000), "maxAgents": (1, 100),
              "handshakesPerHour": (1, 1000)}
    for k, (lo, hi) in bounds.items():
        limits[k] = _num(lim.get(k), DEFAULT_LIMITS[k], lo, hi, integer=True)
    d["limits"] = limits
    rm = raw.get("remote") if isinstance(raw.get("remote"), dict) else {}
    d["remote"] = {"agentCreate": _bool(rm.get("agentCreate"), True), "budgetEdit": _bool(rm.get("budgetEdit"), True)}
    au = raw.get("audit") if isinstance(raw.get("audit"), dict) else {}
    d["audit"] = {"storePrompts": _bool(au.get("storePrompts"), False)}
    d["approvalPort"] = _num(raw.get("approvalPort"), 8766, 0, 65535, integer=True)

    budgets = []
    for i, b in enumerate(raw.get("budgets") or []):
        v = validate_budget(b, errors, "budgets[%d]" % i)
        if v:
            v["locked"] = True
            budgets.append(v)
    d["budgets"] = budgets
    return d, errors, warnings


def _default_device_name() -> str:
    try:
        import socket
        return (socket.gethostname() or "Cihaz")[:32]
    except OSError:
        return "Cihaz"


def load_config(home: str, which=shutil.which, user_home: Optional[str] = None) -> Config:
    path = os.path.join(home, CONFIG_FILE)
    try:
        with open(path, "rb") as f:
            raw_bytes = f.read()
    except FileNotFoundError:
        return Config({}, ["%s not found: run `pixel-connector setup` first" % path], [], "", path)
    h = hashlib.sha256(raw_bytes).hexdigest()
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return Config({}, ["connector.json is not valid JSON: %s" % e], [], h, path)
    data, errors, warnings = validate(raw, user_home, which)
    return Config(data, errors, warnings, h, path)


def _version_key(text: str) -> Tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else (-1,)


def _is_exe(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def claude_candidates(user_home: str) -> List[str]:
    """Usual install locations of `claude` that a minimal service PATH misses (v2.md H9), in preference order: the
    native installer (~/.local/bin, ~/.claude/local), npm prefixes, the newest nvm Node, Homebrew and /usr/local, and
    last the binary inside the newest Claude Code VS Code extension."""
    h = user_home
    out = [os.path.join(h, ".local", "bin", "claude"), os.path.join(h, ".claude", "local", "claude"),
           os.path.join(h, ".npm-global", "bin", "claude")]
    nvm = sorted(glob.glob(os.path.join(h, ".nvm", "versions", "node", "*", "bin", "claude")),
                 key=lambda p: _version_key(os.path.basename(os.path.dirname(os.path.dirname(p)))), reverse=True)
    out += nvm
    out += ["/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    ext = glob.glob(os.path.join(h, ".vscode", "extensions", "anthropic.claude-code-*", "resources", "native-binary",
                                 "claude"))
    out += sorted(ext, key=lambda p: _version_key(p.split(os.sep + "extensions" + os.sep, 1)[-1]), reverse=True)
    return out


def find_claude(user_home: Optional[str] = None, which=shutil.which) -> Optional[str]:
    """Absolute path of an executable `claude`: PATH first, then claude_candidates(). None when there is none."""
    found = which("claude")
    if found:
        return os.path.abspath(found)
    for p in claude_candidates(user_home or os.path.expanduser("~")):
        if _is_exe(p):
            return p
    return None


def claude_path_ok(path, which=shutil.which) -> bool:
    """A configured engines.claude.path that still works: an executable absolute path or a bare name on PATH."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    if os.path.isabs(path):
        return _is_exe(path)
    return os.sep not in path and bool(which(path))


def initial_config(server: str, room: str, key: str, owner: str, device_name: Optional[str] = None,
                   claude_path: Optional[str] = None) -> dict:
    """The connector.json that `setup` writes (no workspaces yet; the user adds them by hand)."""
    return {
        "v": 1, "server": server.rstrip("/"), "room": room, "key": key, "owner": owner,
        "deviceName": device_name or _default_device_name(),
        "workspaces": [],
        "engines": {"claude": {"path": claude_path, "enabled": True, "bare": False, "showThinking": False,
                               "maxTurns": 50, "maxBudgetUsdPerTask": 2.0, "defaultModel": None},
                    "ollama": {"url": "http://127.0.0.1:11434", "enabled": True, "keepAlive": "10m",
                               "historyMessages": 40},
                    "openai": []},
        "profiles": {"dev": {"allow": ["Bash(git status *)", "Bash(git diff *)"], "deny": []},
                     "auto": {"enabled": False}},
        "runtimes": [],
        "confirm": dict(DEFAULT_CONFIRM),
        "budgets": [],
        "limits": {k: DEFAULT_LIMITS[k] for k in ("maxConcurrent", "taskTimeoutS", "maxPromptBytes",
                                                   "tasksPerMinute", "tasksPerHour", "chunkMs", "retainDays")},
        "remote": {"agentCreate": True, "budgetEdit": True},
        "audit": {"storePrompts": False},
        "approvalPort": 8766,
    }
