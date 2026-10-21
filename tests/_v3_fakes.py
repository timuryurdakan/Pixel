"""Fakes for the connector tests (Phase 13): a fake `claude` executable that replays stream-json fixtures, and
fake Ollama / OpenAI-compatible HTTP servers that stream realistic NDJSON / SSE. Stdlib only, no network."""

import json
import os
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FAKE_CLAUDE = r'''#!{python}
"""Fake claude for tests. Plan file ($CLAUDE_FAKE_DIR/plan.json): list of steps, one consumed per run:
{"fixture": "claude_stream_x.jsonl", "delay": 0.0, "stderr": "...", "exit": 0, "ignore_int": false, "ignore_term": false,
 "hang": 0}. Every run is logged to $CLAUDE_FAKE_DIR/calls.jsonl (argv, stdin, cwd, env flags)."""
import json, os, signal, sys, time
d = os.environ["CLAUDE_FAKE_DIR"]
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print(os.environ.get("CLAUDE_FAKE_VERSION", "2.1.301 (Claude Code)"))
    sys.exit(0)
if argv[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": os.environ.get("CLAUDE_FAKE_AUTH", "claude.ai"),
                      "apiProvider": "firstParty"}}))
    sys.exit(0)
stdin = sys.stdin.read()
plan_path = os.path.join(d, "plan.json")
with open(plan_path) as f:
    plan = json.load(f)
step = plan.pop(0) if plan else {{"fixture": "claude_stream_success_tools.jsonl"}}
with open(plan_path, "w") as f:
    json.dump(plan, f)
sid = argv[argv.index("--session-id") + 1] if "--session-id" in argv else argv[argv.index("--resume") + 1]
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps({{"argv": argv, "stdin": stdin, "cwd": os.getcwd(), "pid": os.getpid(),
                        "disable": os.environ.get("PIXEL_OFFICE_DISABLE"),
                        "office_env": sorted(k for k in os.environ if k.startswith("PIXEL_OFFICE_")),
                        "env_keys": sorted(os.environ)}}) + "\n")
if step.get("ignore_int"):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
else:
    def _int(*a):
        sys.stdout.flush()
        os._exit(130)
    signal.signal(signal.SIGINT, _int)
if step.get("ignore_term"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if step.get("stderr"):
    sys.stderr.write(step["stderr"] + "\n")
    sys.stderr.flush()
fx = step.get("fixture")
if fx:
    with open(os.path.join({fixtures!r}, fx)) as f:
        for line in f:
            sys.stdout.write(line.replace("{{SESSION}}", sid).replace("{{CWD}}", os.getcwd()))
            sys.stdout.flush()
            if step.get("delay"):
                time.sleep(step["delay"])
if step.get("hang"):
    end = time.time() + step["hang"]
    while time.time() < end:
        time.sleep(0.05)
sys.exit(step.get("exit", 0))
'''


# The fake's settings use the CLAUDE_* prefix because the connector hands Claude Code only an allow-listed environment
# (Phase 20, child_env), exactly as a real Claude Code setting such as CLAUDE_CONFIG_DIR would pass.
def write_fake_claude(dirpath, plan=None) -> str:
    d = Path(dirpath)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "claude"
    text = FAKE_CLAUDE.replace("{python}", sys.executable).replace("{fixtures!r}", repr(str(FIXTURES)))
    p.write_text(text.replace("{{", "{").replace("}}", "}"))
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    set_plan(d, plan or [])
    return str(p)


def set_plan(dirpath, plan) -> None:
    (Path(dirpath) / "plan.json").write_text(json.dumps(plan))
    try:
        (Path(dirpath) / "calls.jsonl").unlink()
    except FileNotFoundError:
        pass


def calls(dirpath) -> list:
    try:
        return [json.loads(l) for l in (Path(dirpath) / "calls.jsonl").read_text().splitlines() if l.strip()]
    except FileNotFoundError:
        return []


# --------------------------------------------------------------------------------------------------
# Fake Ollama / OpenAI-compatible servers
# --------------------------------------------------------------------------------------------------

class FakeLLM(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, words=None, delay=0.0, with_counts=True, models=("qwen2.5-coder:7b", "llama3.2:3b")):
        self.words = list(words or ["Merhaba", " dünya", "!", " Nasıl", " yardımcı", " olabilirim?"])
        self.delay = delay
        self.with_counts = with_counts
        self.models = list(models)
        self.loaded = {}
        self.requests = []
        self.disconnected = threading.Event()
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _LLMHandler)
        self.url = "http://127.0.0.1:%d" % self.server_address[1]
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()


class _LLMHandler(BaseHTTPRequestHandler):
    server: FakeLLM
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        s = self.server
        if self.path == "/api/version":
            return self._json(200, {"version": "0.12.3"})
        if self.path == "/api/tags":
            return self._json(200, {"models": [{"name": m, "size": 1} for m in s.models]})
        if self.path == "/api/ps":
            return self._json(200, {"models": [{"name": m, "size_vram": 5100000000, "expires_at": "2026-10-17T12:00:00Z"}
                                               for m in s.loaded]})
        if self.path == "/v1/models":
            return self._json(200, {"object": "list", "data": [{"id": m} for m in s.models]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        s = self.server
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        with s.lock:
            s.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        if self.path == "/api/generate":
            m = body.get("model")
            if m not in s.models:
                return self._json(404, {"error": "model '%s' not found" % m})
            if body.get("keep_alive") == 0:
                s.loaded.pop(m, None)
            else:
                s.loaded[m] = True
            return self._json(200, {"model": m, "done": True, "done_reason": "load" if body.get("keep_alive") else "unload"})
        if self.path == "/api/chat":
            if body.get("model") not in s.models:
                return self._json(404, {"error": "model '%s' not found, try pulling it first" % body.get("model")})
            return self._stream_ollama(body)
        if self.path == "/v1/chat/completions":
            return self._stream_openai(body)
        self._json(404, {"error": "not found"})

    def _chunked(self, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _write(self, data: bytes) -> bool:
        try:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()
            return True
        except OSError:
            self.server.disconnected.set()
            return False

    def _stream_ollama(self, body):
        s = self.server
        self._chunked("application/x-ndjson")
        t = "2026-10-17T10:00:00.000Z"
        for w in s.words:
            if not self._write((json.dumps({"model": body["model"], "created_at": t,
                                            "message": {"role": "assistant", "content": w}, "done": False}) + "\n").encode()):
                return
            if s.delay:
                time.sleep(s.delay)
        final = {"model": body["model"], "created_at": t, "message": {"role": "assistant", "content": ""},
                 "done": True, "done_reason": "stop", "total_duration": 1543000000, "load_duration": 12000000,
                 "prompt_eval_duration": 95000000, "eval_duration": 1200000000}
        if s.with_counts:
            final.update({"prompt_eval_count": 26 + 3 * (len(body.get("messages") or []) - 1),
                          "eval_count": len(s.words) * 2})
        self._write((json.dumps(final) + "\n").encode())
        self._write(b"")

    def _stream_openai(self, body):
        s = self.server
        self._chunked("text/event-stream")
        for w in s.words:
            ch = {"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": w}}]}
            if not self._write(("data: %s\n\n" % json.dumps(ch)).encode()):
                return
            if s.delay:
                time.sleep(s.delay)
        self._write(("data: %s\n\n" % json.dumps({"id": "c1", "choices": [{"index": 0, "delta": {},
                                                                              "finish_reason": "stop"}]})).encode())
        if s.with_counts:
            self._write(("data: %s\n\n" % json.dumps({"id": "c1", "choices": [], "usage": {
                "prompt_tokens": 31, "completion_tokens": 12, "total_tokens": 43}})).encode())
        self._write(b"data: [DONE]\n\n")
        self._write(b"")
