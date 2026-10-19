"""Ollama adapter (V3.md §8.2): `POST /api/chat` with NDJSON streaming; usage from the final object
(`prompt_eval_count`, `eval_count`, durations in ns). Cancel = close the HTTP connection (Ollama stops generating).
Chat history (local only) lives in history/<agent>.jsonl and is appended only after a successful answer."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlsplit

from .base import Cached, Running, TaskContext, append_history, empty_usage, estimate_tokens, read_history, short

READ_TIMEOUT = 600.0


class HttpStream:
    """One streaming HTTP request whose socket can be shut down from another thread (cancel)."""

    def __init__(self, base_url: str, timeout: float = READ_TIMEOUT) -> None:
        u = urlsplit(base_url)
        self.scheme = u.scheme
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.prefix = (u.path or "").rstrip("/")
        self.timeout = timeout
        self.conn: Optional[http.client.HTTPConnection] = None
        self.closed = False
        self._lock = threading.Lock()

    def request(self, method: str, path: str, body=None, headers=None) -> http.client.HTTPResponse:
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        with self._lock:
            if self.closed:
                raise ConnectionAbortedError("cancelled")
            self.conn = cls(self.host, self.port, timeout=self.timeout)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        hdrs = {"Content-Type": "application/json", "Accept": "application/x-ndjson, text/event-stream"}
        hdrs.update(headers or {})
        self.conn.request(method, self.prefix + path, body=data, headers=hdrs)
        return self.conn.getresponse()

    def close(self) -> None:
        with self._lock:
            self.closed = True
            c = self.conn
        if c is not None:
            try:
                if c.sock is not None:
                    c.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass


def get_json(base_url: str, path: str, timeout: float = 3.0, method: str = "GET", body=None, headers=None):
    s = HttpStream(base_url, timeout)
    try:
        r = s.request(method, path, body, headers)
        data = r.read(4 * 1024 * 1024)
        if r.status >= 400:
            raise http.client.HTTPException("HTTP %d" % r.status)
        return json.loads(data or b"null")
    finally:
        s.close()


class OllamaEngine:
    name = "ollama"

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self._avail = Cached(self._probe, 60)

    @property
    def url(self) -> str:
        return self.cfg.get("url") or "http://127.0.0.1:11434"

    def _probe(self) -> dict:
        if not self.cfg.get("enabled", True):
            return {"available": False, "version": None, "models": []}
        try:
            v = get_json(self.url, "/api/version", 2.0)
            tags = get_json(self.url, "/api/tags", 3.0)
            models = [m.get("name") for m in (tags or {}).get("models") or [] if isinstance(m, dict) and m.get("name")]
            return {"available": True, "version": (v or {}).get("version"), "models": models[:100]}
        except (OSError, ValueError, http.client.HTTPException):
            return {"available": False, "version": None, "models": []}

    def available(self, force: bool = False) -> dict:
        return self._avail.get(force)

    def start(self, ctx: TaskContext, events) -> "ChatRun":
        r = ChatRun(self, ctx, events)
        r.start()
        return r


class ChatRun(Running):
    """Shared by Ollama and the OpenAI-compatible adapter (`_request` / `_parse` differ)."""

    cost_basis = "local"

    def __init__(self, engine, ctx: TaskContext, events) -> None:
        self.engine = engine
        self.ctx = ctx
        self.events = events
        self.stream: Optional[HttpStream] = None
        self.cancelled = False
        self._ended = False
        self._started_content = False
        self.text = []
        self.usage = None
        self.note = None

    def start(self) -> None:
        threading.Thread(target=self._main, daemon=True, name="%s-run" % self.engine.name).start()

    def cancel(self) -> None:
        self.cancelled = True
        s = self.stream
        if s is not None:
            s.close()

    def _end(self, *a, **kw) -> None:
        if not self._ended:
            self._ended = True
            self.events.end(*a, **kw)

    def _messages(self) -> list:
        cfg = self.engine.cfg
        hist = read_history(self.ctx.history_path, int(cfg.get("historyMessages", 40)), cfg.get("numCtx"))
        return hist + [{"role": "user", "content": self.ctx.prompt}]

    def _content(self, text: str) -> None:
        if not text:
            return
        if not self._started_content:
            self._started_content = True
            self.events.office("working", tool="llm")
        self.text.append(text)
        self.events.part({"k": "text", "v": text})

    def _main(self) -> None:
        self.events.office("thinking")
        t0 = time.monotonic()
        try:
            self.stream = HttpStream(self.engine.url)
            if self.cancelled:
                raise ConnectionAbortedError("cancelled")
            resp = self._request()
            if resp.status >= 400:
                body = resp.read(65536)
                try:
                    msg = json.loads(body).get("error") or ""
                    if isinstance(msg, dict):
                        msg = msg.get("message") or ""
                except (ValueError, AttributeError):
                    msg = body.decode("utf-8", "replace")
                code = "model_not_found" if resp.status == 404 else "engine_error"
                self._end("error", None, empty_usage(), 0.0 if self.cost_basis == "local" else None, self.cost_basis,
                          error={"code": code, "message": short(msg or "HTTP %d" % resp.status, 300)})
                return
            err = self._parse(resp)
            if err:
                raise RuntimeError(err)
        except Exception as e:
            if self.cancelled:
                self._finish_cancelled()
                return
            if isinstance(e, ConnectionRefusedError) or (isinstance(e, OSError) and not isinstance(e, socket.timeout)
                                                            and not self._started_content and not self.text):
                self._end("error", None, empty_usage(), 0.0 if self.cost_basis == "local" else None, self.cost_basis,
                          error={"code": "engine_unavailable", "message": self.unavailable_text})
            else:
                self._end("error", "".join(self.text) or None, self._usage_or_estimate(),
                          0.0 if self.cost_basis == "local" else None, self.cost_basis,
                          error={"code": "engine_error", "message": short(str(e), 300)})
            return
        finally:
            if self.stream is not None:
                self.stream.close()
        if self.cancelled:
            self._finish_cancelled()
            return
        result = "".join(self.text)
        usage = self._usage_or_estimate()
        if self.note:
            self.events.part({"k": "note", "v": self.note})
        try:
            append_history(self.ctx.history_path, [{"role": "user", "content": self.ctx.prompt},
                                                    {"role": "assistant", "content": result}])
        except OSError:
            pass
        self._end("success", result, usage, 0.0 if self.cost_basis == "local" else None, self.cost_basis, turns=1)

    unavailable_text = "Ollama çalışmıyor"

    def _finish_cancelled(self) -> None:
        self._end("cancelled", "".join(self.text) or None, self._usage_or_estimate(),
                  0.0 if self.cost_basis == "local" else None, self.cost_basis)

    def _usage_or_estimate(self) -> dict:
        if self.usage is not None:
            return self.usage
        u = empty_usage(True)
        u["inTok"] = sum(estimate_tokens(m["content"]) for m in self._messages())
        u["outTok"] = estimate_tokens("".join(self.text))
        return u

    # -- Ollama specifics --------------------------------------------------------------------------
    def _request(self):
        cfg = self.engine.cfg
        body = {"model": self.ctx.model, "messages": self._messages(), "stream": True,
                "keep_alive": cfg.get("keepAlive", "10m")}
        if cfg.get("numCtx"):
            body["options"] = {"num_ctx": cfg["numCtx"]}
        return self.stream.request("POST", "/api/chat", body)

    def _parse(self, resp) -> Optional[str]:
        show_thinking = bool(self.engine.cfg.get("showThinking"))
        while True:
            line = resp.readline(4 * 1024 * 1024)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            if o.get("error"):
                return str(o["error"])
            msg = o.get("message") or {}
            if isinstance(msg, dict):
                if msg.get("thinking") and show_thinking:
                    self.events.part({"k": "thinking", "v": str(msg["thinking"])})
                if msg.get("content"):
                    self._content(str(msg["content"]))
            if o.get("done"):
                pe, ec = o.get("prompt_eval_count"), o.get("eval_count")
                if isinstance(pe, int) or isinstance(ec, int):
                    u = empty_usage(False)
                    u["inTok"] = pe if isinstance(pe, int) else estimate_tokens(self.ctx.prompt)
                    u["outTok"] = ec if isinstance(ec, int) else estimate_tokens("".join(self.text))
                    u["estimated"] = not (isinstance(pe, int) and isinstance(ec, int))
                    self.usage = u
                    self.events.live(inTok=u["inTok"], outTok=u["outTok"], cacheRead=0, cacheWrite=0)
                    ed = o.get("eval_duration")
                    if isinstance(ec, int) and isinstance(ed, int) and ed > 0:
                        self.note = ("%.1f token/sn" % (ec / ed * 1e9)).replace(".", ",")   # tr-TR decimal comma
                break
        return None

