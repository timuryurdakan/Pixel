"""Phase 13: allow-listed runtimes (V3.md §9): only configured ids, argv exactly as configured (harmless commands),
no request field reaches argv, timeouts, status probes, Ollama load/unload bodies and the model allow-list."""

import os
import subprocess
import tempfile
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_fakes as F

from pixel_connector import config as CFG
from pixel_connector.runtimes import RuntimeError_, Runtimes


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.llm = F.FakeLLM()
        self.marker = os.path.join(self.tmp.name, "started")
        raw = CFG.initial_config("https://x.example", "a" * 26, "k" * 40, "T")
        raw["runtimes"] = [
            {"id": "toucher", "name": "Dokun", "start": ["touch", self.marker], "stop": ["rm", "-f", self.marker]},
            {"id": "sleeper", "start": ["sleep", "0.1"], "stop": ["true"], "status": {"process": "sleep"}},
            {"id": "failer", "start": ["false"]},
            {"id": "ollama", "name": "Ollama sunucusu", "kind": "ollama", "url": self.llm.url,
             "status": {"http": self.llm.url + "/api/version"}, "models": ["qwen2.5-coder:7b"]},
            {"id": "ollama-any", "kind": "ollama", "url": self.llm.url, "allowAnyInstalledModel": True}]
        d, errors, _ = CFG.validate(raw)
        self.assertEqual(errors, [])
        self.calls = []

        def run(argv, **kw):
            self.calls.append((list(argv), kw))
            return subprocess.run(argv, **kw)
        self.rt = Runtimes(d["runtimes"], run=run, home=self.tmp.name)

    def tearDown(self):
        self.llm.stop()
        self.tmp.cleanup()

    def test_start_stop_exact_argv(self):
        r = self.rt.start_stop("toucher", "start")
        self.assertTrue(r["ok"])
        self.assertEqual(r["exitCode"], 0)
        self.assertTrue(os.path.exists(self.marker))
        argv, kw = self.calls[-1]
        self.assertEqual(argv[1:], [self.marker])
        self.assertTrue(os.path.isabs(argv[0]))
        self.assertEqual(kw["shell"], False)
        self.assertEqual(kw["timeout"], 30.0)
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual(kw["cwd"], self.tmp.name)
        self.assertTrue(self.rt.start_stop("toucher", "stop")["ok"])
        self.assertFalse(os.path.exists(self.marker))
        r = self.rt.start_stop("sleeper", "start")
        self.assertEqual(r["exitCode"], 0)

    def test_failure_and_unknown(self):
        r = self.rt.start_stop("failer", "start")
        self.assertEqual((r["ok"], r["exitCode"]), (False, 1))
        with self.assertRaises(RuntimeError_) as cm:
            self.rt.start_stop("failer", "stop")
        self.assertEqual(cm.exception.code, "not_allowed")
        for bad in ("rm", "toucher; rm -rf /", None, 5, "../toucher", "TOUCHER"):
            with self.subTest(rid=bad):
                with self.assertRaises(RuntimeError_) as cm:
                    self.rt.start_stop(bad, "start")
                self.assertEqual(cm.exception.code, "runtime_unknown")
        self.assertEqual(len(self.calls), 1)                 # only the "failer" start ran

    def test_timeout(self):
        def slow(argv, **kw):
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        rt = Runtimes(list(self.rt.entries.values()), run=slow)
        r = rt.start_stop("sleeper", "start")
        self.assertEqual((r["ok"], r["exitCode"], r["message"]), (False, None, "timeout"))

    def test_list_status_and_models(self):
        lst = {r["id"]: r for r in self.rt.list()}
        self.assertEqual(lst["ollama"]["state"], "running")
        self.assertEqual(lst["ollama"]["detail"], "v0.12.3")
        self.assertEqual(lst["ollama"]["models"][0]["name"], "qwen2.5-coder:7b")
        self.assertFalse(lst["ollama"]["models"][0]["loaded"])
        self.assertEqual([m["name"] for m in lst["ollama-any"]["models"]], ["qwen2.5-coder:7b", "llama3.2:3b"])
        self.assertTrue(lst["toucher"]["canStart"])
        self.assertFalse(lst["failer"]["canStop"])
        self.assertEqual(lst["toucher"]["state"], "unknown")

    def test_model_load_unload(self):
        r = self.rt.model_op("ollama", "qwen2.5-coder:7b", "load", "10m")
        self.assertTrue(r["ok"])
        self.assertEqual(self.llm.requests[-1], {"path": "/api/generate", "auth": None,
                                                 "body": {"model": "qwen2.5-coder:7b", "keep_alive": "10m",
                                                          "stream": False}})
        lst = {r["id"]: r for r in self.rt.list()}
        self.assertTrue(lst["ollama"]["models"][0]["loaded"])
        self.assertEqual(lst["ollama"]["models"][0]["vramBytes"], 5100000000)
        self.assertTrue(self.rt.model_op("ollama", "qwen2.5-coder:7b", "unload")["ok"])
        self.assertEqual(self.llm.requests[-1]["body"]["keep_alive"], 0)
        with self.assertRaises(RuntimeError_) as cm:
            self.rt.model_op("ollama", "llama3.2:3b", "load")              # installed but not allow-listed
        self.assertEqual(cm.exception.code, "not_allowed")
        self.assertTrue(self.rt.model_op("ollama-any", "llama3.2:3b", "load")["ok"])
        with self.assertRaises(RuntimeError_) as cm:
            self.rt.model_op("ollama-any", "notinstalled:1b", "load")      # never pulls
        self.assertEqual(cm.exception.code, "model_not_found")
        with self.assertRaises(RuntimeError_):
            self.rt.model_op("ollama", "--evil", "load")
        with self.assertRaises(RuntimeError_):
            self.rt.model_op("toucher", "x", "load")
        self.assertFalse(any("generate" in str(c) for c in self.calls))     # no process ever ran for models


if __name__ == "__main__":
    unittest.main()
