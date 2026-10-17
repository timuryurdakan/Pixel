"""Phase 13: Ollama (/api/chat NDJSON) and OpenAI-compatible (SSE) adapters against fake stdlib servers (V3.md §8.2,
§8.3): streamed text, final token counts, missing counts → estimated, history appended only on success, cancel =
disconnect, connection refused → engine_unavailable, unknown model → model_not_found."""

import json
import os
import socket
import tempfile
import time
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_fakes as F
from test_connector_claude import Events

from pixel_connector.engines.base import TaskContext, read_history
from pixel_connector.engines.ollama import OllamaEngine
from pixel_connector.engines.openai_compat import OpenAIEngine


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hist = os.path.join(self.tmp.name, "history", "a1.jsonl")
        self.agent = {"key": "a1", "engine": "ollama", "model": "qwen2.5-coder:7b"}

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self, prompt="Merhaba", model="qwen2.5-coder:7b"):
        return TaskContext("t_0123456789", self.agent, prompt, None, model, history_path=self.hist)


class OllamaTests(Base):
    def setUp(self):
        super().setUp()
        self.srv = F.FakeLLM()
        self.cfg = {"url": self.srv.url, "enabled": True, "keepAlive": "10m", "numCtx": 4096, "historyMessages": 40,
                    "showThinking": False}
        self.eng = OllamaEngine(self.cfg)

    def tearDown(self):
        self.srv.stop()
        super().tearDown()

    def test_available(self):
        self.assertEqual(self.eng.available(), {"available": True, "version": "0.12.3",
                                                "models": ["qwen2.5-coder:7b", "llama3.2:3b"]})
        dead = OllamaEngine({"url": "http://127.0.0.1:%d" % free_port(), "enabled": True})
        self.assertFalse(dead.available()["available"])

    def test_stream_counts_history(self):
        ev = Events()
        self.eng.start(self.ctx("Selam"), ev)
        end = ev.wait()
        self.assertEqual(end["status"], "success")
        self.assertEqual(end["result"], "Merhaba dünya! Nasıl yardımcı olabilirim?")
        self.assertEqual("".join(p["v"] for p in ev.kinds("text")), end["result"])
        self.assertEqual(end["usage"], {"inTok": 26, "outTok": 12, "cacheRead": 0, "cacheWrite": 0, "thinkTok": None,
                                        "estimated": False})
        self.assertEqual((end["cost"], end["basis"]), (0.0, "local"))
        self.assertEqual(ev.offices, [("thinking", None), ("working", "llm")])
        self.assertEqual(ev.kinds("note"), [{"k": "note", "v": "10.0 token/sn"}])
        req = self.srv.requests[-1]["body"]
        self.assertEqual(req["stream"], True)
        self.assertEqual(req["keep_alive"], "10m")
        self.assertEqual(req["options"], {"num_ctx": 4096})
        self.assertEqual(req["messages"], [{"role": "user", "content": "Selam"}])
        self.assertEqual(read_history(self.hist, 40), [{"role": "user", "content": "Selam"},
                                                       {"role": "assistant", "content": end["result"]}])
        self.assertEqual(oct(os.stat(self.hist).st_mode & 0o777), "0o600")
        ev2 = Events()
        self.eng.start(self.ctx("Devam"), ev2)
        ev2.wait()
        self.assertEqual([m["content"] for m in self.srv.requests[-1]["body"]["messages"]],
                         ["Selam", end["result"], "Devam"])

    def test_missing_counts_are_estimated(self):
        self.srv.with_counts = False
        ev = Events()
        self.eng.start(self.ctx("1234567890ab"), ev)
        end = ev.wait()
        self.assertTrue(end["usage"]["estimated"])
        self.assertEqual(end["usage"]["inTok"], 3)
        self.assertEqual(end["usage"]["outTok"], (len(end["result"]) + 3) // 4)

    def test_cancel_disconnects_and_skips_history(self):
        self.srv.words = ["kelime "] * 200
        self.srv.delay = 0.02
        ev = Events()
        run = self.eng.start(self.ctx(), ev)
        time.sleep(0.4)
        t0 = time.time()
        run.cancel()
        end = ev.wait()
        self.assertLess(time.time() - t0, 2)
        self.assertEqual(end["status"], "cancelled")
        self.assertTrue(self.srv.disconnected.wait(3))
        self.assertFalse(os.path.exists(self.hist))

    def test_unknown_model(self):
        ev = Events()
        self.eng.start(self.ctx(model="yok:1b"), ev)
        end = ev.wait()
        self.assertEqual((end["status"], end["error"]["code"]), ("error", "model_not_found"))
        self.assertIn("not found", end["error"]["message"])

    def test_connection_refused(self):
        eng = OllamaEngine(dict(self.cfg, url="http://127.0.0.1:%d" % free_port()))
        ev = Events()
        eng.start(self.ctx(), ev)
        end = ev.wait()
        self.assertEqual(end["error"], {"code": "engine_unavailable", "message": "Ollama çalışmıyor"})


class OpenAITests(Base):
    def setUp(self):
        super().setUp()
        self.srv = F.FakeLLM()
        self.cfg = {"id": "lmstudio", "name": "LM Studio", "url": self.srv.url + "/v1", "apiKeyEnv": None}

    def tearDown(self):
        self.srv.stop()
        super().tearDown()

    def test_stream_and_usage(self):
        eng = OpenAIEngine(self.cfg)
        self.assertEqual(eng.available(), {"available": True, "models": ["qwen2.5-coder:7b", "llama3.2:3b"]})
        ev = Events()
        eng.start(self.ctx(), ev)
        end = ev.wait()
        self.assertEqual(end["status"], "success")
        self.assertEqual(end["result"], "Merhaba dünya! Nasıl yardımcı olabilirim?")
        self.assertEqual((end["usage"]["inTok"], end["usage"]["outTok"], end["usage"]["estimated"]), (31, 12, False))
        self.assertEqual(end["basis"], "local")
        body = self.srv.requests[-1]["body"]
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertIsNone(self.srv.requests[-1]["auth"])

    def test_no_usage_chunk_and_api_key_env(self):
        self.srv.with_counts = False
        os.environ["PIXEL_TEST_LLM_KEY"] = "sk-test-123"
        try:
            eng = OpenAIEngine(dict(self.cfg, apiKeyEnv="PIXEL_TEST_LLM_KEY"))
            ev = Events()
            eng.start(self.ctx(), ev)
            end = ev.wait()
        finally:
            del os.environ["PIXEL_TEST_LLM_KEY"]
        self.assertTrue(end["usage"]["estimated"])
        self.assertEqual(end["basis"], "unknown")
        self.assertIsNone(end["cost"])
        self.assertEqual(self.srv.requests[-1]["auth"], "Bearer sk-test-123")

    def test_cancel(self):
        self.srv.words = ["x "] * 200
        self.srv.delay = 0.02
        ev = Events()
        run = OpenAIEngine(self.cfg).start(self.ctx(), ev)
        time.sleep(0.3)
        run.cancel()
        self.assertEqual(ev.wait()["status"], "cancelled")
        self.assertFalse(os.path.exists(self.hist))


if __name__ == "__main__":
    unittest.main()
