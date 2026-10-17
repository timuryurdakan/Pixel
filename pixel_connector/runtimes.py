"""Allow-listed runtimes (V3.md §9): start/stop argv copied verbatim from connector.json, status probes, and Ollama
model load/unload over its HTTP API. The browser sends only a runtime id (and a model name for model.load/unload,
which must be in the runtime's `models` list or, with allowAnyInstalledModel, installed per /api/tags). No request
field ever reaches an argv; there is no shell."""

from __future__ import annotations

import http.client
import os
import subprocess
import sys
from typing import Callable, List, Optional

from .config import MODEL_RE
from .engines.ollama import get_json

RUN_TIMEOUT = 30.0


class RuntimeError_(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _process_running(name: str) -> Optional[bool]:
    if not sys.platform.startswith("linux"):
        return None
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % pid, "r") as f:
                    if f.read().strip() == name[:15]:
                        return True
            except OSError:
                continue
    except OSError:
        return None
    return False


class Runtimes:
    def __init__(self, entries: List[dict], run=subprocess.run, home: Optional[str] = None) -> None:
        self.entries = {e["id"]: e for e in entries}
        self.run = run
        self.home = home or os.path.expanduser("~")

    def get(self, rid) -> dict:
        e = self.entries.get(rid) if isinstance(rid, str) else None
        if e is None:
            raise RuntimeError_("runtime_unknown", "unknown runtime")
        return e

    # -- status --------------------------------------------------------------------------------
    def state(self, e: dict) -> dict:
        st = e.get("status") or {}
        state, detail = "unknown", None
        try:
            if "http" in st:
                d = get_json(st["http"], "", 2.0)
                state = "running"
                if isinstance(d, dict) and d.get("version"):
                    detail = "v" + str(d["version"])[:20]
            elif "systemd" in st:
                argv = ["systemctl"] + (["--user"] if st.get("user") else []) + ["is-active", st["systemd"]]
                r = self.run(argv, capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
                out = (r.stdout or "").strip()
                state = "running" if out == "active" else ("starting" if out in ("activating", "reloading")
                                                           else "stopped")
            elif "process" in st:
                pr = _process_running(st["process"])
                state = "unknown" if pr is None else ("running" if pr else "stopped")
            elif e["kind"] == "ollama" and e.get("url"):
                d = get_json(e["url"], "/api/version", 2.0)
                state = "running"
                detail = "v" + str((d or {}).get("version", ""))[:20]
            elif e["kind"] == "openai" and e.get("url"):
                get_json(e["url"], "/models", 2.0)
                state = "running"
        except (OSError, ValueError, http.client.HTTPException, subprocess.SubprocessError):
            state = "stopped"
        return {"state": state, "detail": detail}

    def models(self, e: dict, running: bool) -> List[dict]:
        if e["kind"] != "ollama" or not e.get("url"):
            return []
        loaded = {}
        installed: List[str] = []
        if running:
            try:
                ps = get_json(e["url"], "/api/ps", 2.0) or {}
                for m in ps.get("models") or []:
                    if isinstance(m, dict) and m.get("name"):
                        loaded[m["name"]] = m
                tags = get_json(e["url"], "/api/tags", 2.0) or {}
                installed = [m.get("name") for m in tags.get("models") or [] if isinstance(m, dict) and m.get("name")]
            except (OSError, ValueError, http.client.HTTPException):
                pass
        names = list(e.get("models") or [])
        if e.get("allowAnyInstalledModel"):
            names += [n for n in installed if n not in names]
        out = []
        for n in names[:50]:
            m = loaded.get(n)
            out.append({"name": n, "loaded": m is not None, "installed": n in installed if installed else None,
                        "vramBytes": m.get("size_vram") if m else None, "expiresAt": m.get("expires_at") if m else None})
        return out

    def describe(self, e: dict) -> dict:
        s = self.state(e)
        return {"id": e["id"], "name": e["name"], "kind": e["kind"], "state": s["state"], "detail": s["detail"],
                "canStart": e.get("start") is not None, "canStop": e.get("stop") is not None,
                "models": self.models(e, s["state"] == "running")}

    def list(self) -> List[dict]:
        return [self.describe(e) for e in self.entries.values()]

    # -- actions -------------------------------------------------------------------------------
    def start_stop(self, rid, op: str) -> dict:
        """op ∈ start|stop. Runs exactly the configured argv. → {runtime, ok, state, exitCode, message}."""
        e = self.get(rid)
        if op not in ("start", "stop"):
            raise RuntimeError_("bad_request")
        argv = e.get(op)
        if not argv:
            raise RuntimeError_("not_allowed", "no %s command configured" % op)
        try:
            r = self.run(list(argv), shell=False, timeout=RUN_TIMEOUT, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, cwd=self.home)
            code, err = r.returncode, (r.stderr or "")[:500]
        except subprocess.TimeoutExpired:
            code, err = None, "timeout"
        except OSError as ex:
            code, err = None, str(ex)[:500]
        ok = code == 0
        return {"runtime": e["id"], "ok": ok, "state": self.state(e)["state"], "exitCode": code,
                "message": err.strip() or None}

    def model_op(self, rid, model, op: str, keep_alive: str = "10m") -> dict:
        e = self.get(rid)
        if e["kind"] != "ollama":
            raise RuntimeError_("not_allowed", "not an ollama runtime")
        if not isinstance(model, str) or not MODEL_RE.match(model):
            raise RuntimeError_("model_not_found", "invalid model name")
        if model not in (e.get("models") or []):
            if not e.get("allowAnyInstalledModel"):
                raise RuntimeError_("not_allowed", "model not in the allow-list")
            try:
                tags = get_json(e["url"], "/api/tags", 3.0) or {}
            except (OSError, ValueError, http.client.HTTPException):
                raise RuntimeError_("engine_unavailable", "Ollama çalışmıyor") from None
            if model not in [m.get("name") for m in tags.get("models") or [] if isinstance(m, dict)]:
                raise RuntimeError_("model_not_found", "model not installed")
        body = {"model": model, "keep_alive": keep_alive if op == "load" else 0, "stream": False}
        try:
            get_json(e["url"], "/api/generate", 300.0 if op == "load" else 30.0, method="POST", body=body)
            ok, msg = True, None
        except http.client.HTTPException as ex:
            ok, msg = False, str(ex)[:500]
        except (OSError, ValueError) as ex:
            ok, msg = False, str(ex)[:500] or "Ollama çalışmıyor"
        return {"runtime": e["id"], "ok": ok, "state": self.state(e)["state"], "exitCode": None, "message": msg,
                "model": model}
