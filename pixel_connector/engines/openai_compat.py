"""OpenAI-compatible local servers (LM Studio, llama.cpp `llama-server`, vLLM …), V3.md §8.3.

`POST {url}/chat/completions` with `stream: true` and `stream_options.include_usage`; SSE `data:` lines;
`choices[0].delta.content` → text; the usage chunk → counts; `data: [DONE]` ends. No usage chunk → chars/4 estimate.
History, cancel and office states as for Ollama. `apiKeyEnv` names an environment variable on the device whose value
is sent as a Bearer token (a deliberately configured remote endpoint); then costBasis = "unknown".
"""

from __future__ import annotations

import json
import os
import http.client
from typing import Optional

from .base import Cached, TaskContext, empty_usage
from .ollama import ChatRun, get_json


class OpenAIEngine:
    name = "openai"

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg                       # one entry of engines.openai
        self._avail = Cached(self._probe, 60)

    @property
    def url(self) -> str:
        return self.cfg["url"]

    def headers(self) -> dict:
        env = self.cfg.get("apiKeyEnv")
        if env and os.environ.get(env):
            return {"Authorization": "Bearer " + os.environ[env]}
        return {}

    def _probe(self) -> dict:
        try:
            d = get_json(self.url, "/models", 3.0, headers=self.headers())
            models = [m.get("id") for m in (d or {}).get("data") or [] if isinstance(m, dict) and m.get("id")]
            return {"available": True, "models": models[:100]}
        except (OSError, ValueError, http.client.HTTPException):
            return {"available": False, "models": []}

    def available(self, force: bool = False) -> dict:
        return self._avail.get(force)

    def start(self, ctx: TaskContext, events) -> "OpenAIRun":
        r = OpenAIRun(self, ctx, events)
        r.start()
        return r


class OpenAIRun(ChatRun):
    unavailable_text = "Yerel model sunucusu çalışmıyor"

    def __init__(self, engine: OpenAIEngine, ctx: TaskContext, events) -> None:
        super().__init__(engine, ctx, events)
        self.cost_basis = "unknown" if engine.cfg.get("apiKeyEnv") else "local"

    def _request(self):
        body = {"model": self.ctx.model, "messages": self._messages(), "stream": True,
                "stream_options": {"include_usage": True}}
        return self.stream.request("POST", "/chat/completions", body, self.engine.headers())

    def _parse(self, resp) -> Optional[str]:
        while True:
            line = resp.readline(4 * 1024 * 1024)
            if not line:
                break
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            try:
                o = json.loads(data)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            if o.get("error"):
                e = o["error"]
                return str(e.get("message") if isinstance(e, dict) else e)
            for ch in o.get("choices") or []:
                if isinstance(ch, dict) and isinstance(ch.get("delta"), dict) and ch["delta"].get("content"):
                    self._content(str(ch["delta"]["content"]))
            u = o.get("usage")
            if isinstance(u, dict) and (isinstance(u.get("prompt_tokens"), int) or isinstance(u.get("completion_tokens"), int)):
                us = empty_usage(False)
                us["inTok"] = int(u.get("prompt_tokens") or 0)
                us["outTok"] = int(u.get("completion_tokens") or 0)
                self.usage = us
                self.events.live(inTok=us["inTok"], outTok=us["outTok"], cacheRead=0, cacheWrite=0)
        return None
