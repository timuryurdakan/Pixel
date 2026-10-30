"""E3 (docs/FAZ3_TASARIM.md §2): file tools for the Ollama and OpenAI-compatible engines.

Unit tests on `engines/tools.py` against a real temporary folder (path escapes, symlinks, denied names, size caps,
binary files, the profile's write right), then the tool turns of both engines against a scripted fake server (the
model's tool calls, results going back as data, the caps D7, opt-in), and the provider presets in config.
"""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import _helpers  # noqa: F401  (sys.path)
from test_connector_claude import Events

from pixel_connector import config as CFG
from pixel_connector.engines import tools as T
from pixel_connector.engines.base import TaskContext
from pixel_connector.engines.ollama import OllamaEngine
from pixel_connector.engines.openai_compat import OpenAIEngine


class FilesBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "ws")
        os.makedirs(os.path.join(self.root, "docs"))
        with open(os.path.join(self.root, "docs", "a.md"), "w", encoding="utf-8") as f:
            f.write("# Başlık\nmerhaba")
        self.outside = os.path.join(self.tmp.name, "gizli.txt")
        with open(self.outside, "w") as f:
            f.write("SIR-CANARY")

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, profile="edit"):
        return T.WorkspaceFiles(self.root, profile)


class PathTests(unittest.TestCase):
    def test_relative_paths_only(self):
        self.assertEqual(T.split_path("docs/a.md"), ["docs", "a.md"])
        self.assertEqual(T.split_path("docs/"), ["docs"])
        self.assertEqual(T.split_path(None, allow_empty=True), [])
        self.assertEqual(T.split_path(".", allow_empty=True), [])
        for bad in ("", " ", "/etc/passwd", "../x", "docs/../../x", "a//b", "./a", "C:\\x", "C:/x", "a\\b", "~/x",
                    "a\u202eb", "a\nb", "a\0b", "x" * (T.PATH_MAX + 1), 1, None, ["a"]):
            with self.assertRaises(T.ToolError, msg=repr(bad)) as cm:
                T.split_path(bad)
            self.assertEqual(cm.exception.code, "bad_path", repr(bad))

    def test_denied_names_anywhere_in_the_path(self):
        for bad in (".git/config", "src/.git/hooks/pre-commit", ".claude/settings.json", ".mcp.json", ".vscode/x.json",
                    "a.code-workspace", ".envrc", ".env", ".env.local", "conf/.ENV", "keys/server.pem", "id_rsa",
                    "id_ed25519.pub", ".npmrc", ".bashrc", ".IDEA/x", "x.KEY"):
            with self.assertRaises(T.ToolError, msg=bad) as cm:
                T.split_path(bad)
            self.assertEqual(cm.exception.code, "denied", bad)
        T.split_path("docs/environment.md")          # not ".env…"
        T.split_path("keyboard.md")


class FileToolTests(FilesBase):
    def test_list_read_write_inside_the_folder(self):
        f = self.files()
        self.assertEqual(f.list_dir(), "docs/")
        self.assertEqual(f.list_dir("docs"), "a.md")
        self.assertEqual(f.read_file("docs/a.md"), "# Başlık\nmerhaba")
        self.assertEqual(f.write_file("yeni/alt/b.md", "içerik"), "%d bayt yazıldı" % len("içerik".encode()))
        with open(os.path.join(self.root, "yeni", "alt", "b.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "içerik")
        f.write_file("docs/a.md", "yeni")
        self.assertEqual(f.read_file("docs/a.md"), "yeni")

    def test_list_hides_denied_names(self):
        os.makedirs(os.path.join(self.root, ".git"))
        open(os.path.join(self.root, ".env"), "w").close()
        open(os.path.join(self.root, "ok.txt"), "w").close()
        self.assertEqual(self.files().list_dir(), "docs/\nok.txt")

    def test_symlinks_never_lead_out(self):
        os.symlink(self.outside, os.path.join(self.root, "link.txt"))
        os.symlink(self.tmp.name, os.path.join(self.root, "dis"))
        f = self.files()
        for call in (lambda: f.read_file("link.txt"), lambda: f.read_file("dis/gizli.txt"),
                     lambda: f.list_dir("dis"), lambda: f.write_file("link.txt", "x"),
                     lambda: f.write_file("dis/yeni.txt", "x")):
            with self.assertRaises(T.ToolError):
                call()
        with open(self.outside) as fh:
            self.assertEqual(fh.read(), "SIR-CANARY")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "yeni.txt")))

    def test_a_fifo_never_hangs_the_task(self):
        os.mkfifo(os.path.join(self.root, "boru"))
        f = self.files()
        for call in (lambda: f.read_file("boru"), lambda: f.write_file("boru", "x")):
            with self.assertRaises(T.ToolError):
                call()

    def test_readonly_profile_cannot_write(self):
        with self.assertRaises(T.ToolError) as cm:
            self.files("readonly").write_file("x.md", "x")
        self.assertEqual(cm.exception.code, "read_only")
        self.assertFalse(os.path.exists(os.path.join(self.root, "x.md")))
        self.assertNotIn("write_file", [s["function"]["name"] for s in T.tool_specs("readonly")])
        self.assertIn("write_file", [s["function"]["name"] for s in T.tool_specs("edit")])

    def test_size_binary_and_content_limits(self):
        f = self.files()
        with open(os.path.join(self.root, "buyuk.txt"), "wb") as fh:
            fh.write(b"a" * (T.READ_MAX + 1))
        with open(os.path.join(self.root, "resim.png"), "wb") as fh:
            fh.write(b"\x89PNG\0\0\0")
        for path, code in (("buyuk.txt", "too_large"), ("resim.png", "binary"), ("yok.md", "not_found"),
                           ("docs", "not_a_file")):
            with self.assertRaises(T.ToolError, msg=path) as cm:
                f.read_file(path)
            self.assertEqual(cm.exception.code, code, path)
        with open(os.path.join(self.root, "sinirda.txt"), "wb") as fh:
            fh.write(b"a" * T.READ_MAX)
        self.assertEqual(len(f.read_file("sinirda.txt")), T.READ_MAX)
        for content, code in (("a" * (T.WRITE_MAX + 1), "too_large"), ("ş" * (T.WRITE_MAX // 2 + 1), "too_large"),
                              ("gizli \u202e yön", "bad_content"), ("nul\0", "bad_content"), (5, "bad_content")):
            with self.assertRaises(T.ToolError) as cm:
                f.write_file("w.txt", content)
            self.assertEqual(cm.exception.code, code)
        f.write_file("w.txt", "a" * T.WRITE_MAX)

    def test_run_checks_names_and_arguments(self):
        f = self.files()
        self.assertEqual(f.run("read_file", {"path": "docs/a.md"}), (True, "# Başlık\nmerhaba"))
        self.assertEqual(f.run("bash", {"cmd": "ls"}), (False, "hata: unknown_tool"))
        self.assertEqual(f.run("read_file", {"path": "docs/a.md", "x": 1}), (False, "hata: bad_arguments"))
        self.assertEqual(f.run("read_file", "docs/a.md"), (False, "hata: unknown_tool"))
        self.assertEqual(f.run("read_file", {"path": "../gizli.txt"}), (False, "hata: bad_path"))
        self.assertEqual(f.run("read_file", {"path": ".env"}), (False, "hata: denied"))
        self.assertEqual(T.parse_arguments('{"path": "a"}'), {"path": "a"})
        for bad in ("{", "[1]", 3, None, "x" * (T.WRITE_MAX + 5000)):
            self.assertIsNone(T.parse_arguments(bad))

    def test_tools_are_opt_in_and_never_for_auto(self):
        self.assertFalse(T.tools_enabled({}, "edit", self.root))
        self.assertTrue(T.tools_enabled({"tools": True}, "edit", self.root))
        self.assertFalse(T.tools_enabled({"tools": True}, "auto", self.root))
        self.assertFalse(T.tools_enabled({"tools": True}, "edit", None))


# -- the engines' tool turns ----------------------------------------------------------------------------------------
class ScriptedLLM:
    """Answers each POST with the next scripted JSON body; records the request bodies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({"path": self.path, "body": json.loads(self.rfile.read(n)),
                                       "auth": self.headers.get("Authorization")})
                status, body = outer.replies.pop(0) if outer.replies else (500, {"error": "no reply"})
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def ollama_reply(content="", calls=(), pe=10, ec=5):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in calls]
    return 200, {"message": msg, "done": True, "prompt_eval_count": pe, "eval_count": ec}


def openai_reply(content="", calls=(), pt=10, ct=5):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": "c%d" % i, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                             for i, (n, a) in enumerate(calls)]
    return 200, {"choices": [{"message": msg}], "usage": {"prompt_tokens": pt, "completion_tokens": ct}}


class EngineToolTests(FilesBase):
    def ctx(self, profile="edit", prompt="Bir not yaz", max_turns=None):
        hist = os.path.join(self.tmp.name, "history", "a1.jsonl")
        return TaskContext("t_0123456789", {"key": "a1"}, prompt, self.root, "m", profile=profile, history_path=hist,
                           max_turns=max_turns)

    def run_ollama(self, replies, profile="edit", tools=True, **kw):
        srv = ScriptedLLM(replies)
        self.addCleanup(srv.stop)
        eng = OllamaEngine({"url": srv.url, "enabled": True, "tools": tools})
        ev = Events()
        eng.start(self.ctx(profile, **kw), ev)
        return ev.wait(), ev, srv

    def test_ollama_reads_writes_and_answers(self):
        end, ev, srv = self.run_ollama([
            ollama_reply(calls=[("read_file", {"path": "docs/a.md"})]),
            ollama_reply(calls=[("write_file", {"path": "notlar/ozet.md", "content": "özet"})]),
            ollama_reply("Bitti: özet yazıldı.")])
        self.assertEqual(end["status"], "success")
        self.assertEqual(end["result"], "Bitti: özet yazıldı.")
        with open(os.path.join(self.root, "notlar", "ozet.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "özet")
        self.assertEqual(end["usage"]["inTok"], 30)
        self.assertEqual(end["usage"]["outTok"], 15)
        first = srv.requests[0]["body"]
        self.assertEqual((first["stream"], first["options"]["num_predict"]), (False, T.MAX_OUTPUT_TOKENS))
        self.assertEqual([t["function"]["name"] for t in first["tools"]], ["list_dir", "read_file", "write_file"])
        self.assertEqual(first["messages"][0]["role"], "system")
        tool_msg = srv.requests[1]["body"]["messages"][-1]
        self.assertEqual((tool_msg["role"], tool_msg["tool_name"]), ("tool", "read_file"))
        self.assertTrue(tool_msg["content"].startswith(T.RESULT_HEAD))
        self.assertIn("merhaba", tool_msg["content"])
        self.assertEqual([p["name"] for p in ev.kinds("tool")], ["Read", "Write"])
        self.assertEqual([p["ok"] for p in ev.kinds("tool_done")], [True, True])
        self.assertIn(("working", "Read"), ev.offices)
        self.assertIn(("working", "Write"), ev.offices)

    def test_refused_calls_go_back_as_errors_and_nothing_leaks(self):
        end, ev, srv = self.run_ollama([
            ollama_reply(calls=[("read_file", {"path": "../gizli.txt"}), ("read_file", {"path": ".env"}),
                                ("bash", {"cmd": "rm -rf /"}), ("write_file", {"path": "x.md", "content": "x"})]),
            ollama_reply("Yapamadım.")], profile="readonly")
        self.assertEqual(end["status"], "success")
        results = [m["content"] for m in srv.requests[1]["body"]["messages"] if m["role"] == "tool"]
        self.assertEqual([r[len(T.RESULT_HEAD):] for r in results],
                         ["hata: bad_path", "hata: denied", "hata: unknown_tool", "hata: read_only"])
        self.assertNotIn("SIR-CANARY", json.dumps(srv.requests))
        self.assertFalse(os.path.exists(os.path.join(self.root, "x.md")))
        self.assertNotIn("write_file", [t["function"]["name"] for t in srv.requests[0]["body"]["tools"]])
        self.assertEqual([p["ok"] for p in ev.kinds("tool_done")], [False, False, False, False])

    def test_caps_end_the_task(self):
        loop = [ollama_reply(calls=[("list_dir", {})])] * (T.MAX_TOOL_TURNS + 1)
        end, _, srv = self.run_ollama(loop)
        self.assertEqual((end["status"], end["error"]["code"]), ("error", "max_turns"))
        self.assertEqual(len(srv.requests), T.MAX_TOOL_TURNS)
        end, _, srv = self.run_ollama(loop, max_turns=3)
        self.assertEqual(len(srv.requests), 3)
        many = [ollama_reply(calls=[("list_dir", {})] * (T.MAX_TOOL_CALLS + 1))]
        end, _, _ = self.run_ollama(many)
        self.assertEqual(end["error"]["code"], "tool_limit")
        big = [ollama_reply(calls=[("list_dir", {})], ec=T.MAX_OUTPUT_TOKENS)] * 2
        end, _, srv = self.run_ollama(big)
        self.assertEqual(end["error"]["code"], "max_tokens")
        self.assertEqual(len(srv.requests), 1)

    def test_without_opt_in_the_old_streaming_chat_is_used(self):
        srv = ScriptedLLM([])
        self.addCleanup(srv.stop)
        eng = OllamaEngine({"url": srv.url, "enabled": True})
        ev = Events()
        run = eng.start(self.ctx(), ev)
        ev.wait()
        self.assertIsNone(run.files)
        self.assertTrue(srv.requests[0]["body"]["stream"])
        self.assertNotIn("tools", srv.requests[0]["body"])

    def test_http_error_in_a_tool_turn(self):
        end, _, _ = self.run_ollama([ollama_reply(calls=[("list_dir", {})]), (404, {"error": "model not found"})])
        self.assertEqual((end["status"], end["error"]["code"]), ("error", "model_not_found"))

    def test_openai_tool_turns_and_bearer_key(self):
        srv = ScriptedLLM([openai_reply(calls=[("list_dir", {"path": "docs"})]), openai_reply("Tamam.")])
        self.addCleanup(srv.stop)
        os.environ["E3_TEST_KEY"] = "sk-test-canary"
        self.addCleanup(os.environ.pop, "E3_TEST_KEY", None)
        eng = OpenAIEngine({"id": "x", "url": srv.url + "/v1", "apiKeyEnv": "E3_TEST_KEY", "tools": True})
        ev = Events()
        eng.start(self.ctx(), ev)
        end = ev.wait()
        self.assertEqual((end["status"], end["result"]), ("success", "Tamam."))
        self.assertEqual(srv.requests[0]["path"], "/v1/chat/completions")
        self.assertEqual(srv.requests[0]["auth"], "Bearer sk-test-canary")
        self.assertEqual(srv.requests[0]["body"]["max_tokens"], T.MAX_OUTPUT_TOKENS)
        msgs = srv.requests[1]["body"]["messages"]
        self.assertEqual(msgs[-2]["tool_calls"][0]["id"], "c0")
        self.assertEqual((msgs[-1]["role"], msgs[-1]["tool_call_id"]), ("tool", "c0"))
        self.assertIn("a.md", msgs[-1]["content"])
        self.assertNotIn("sk-test-canary", json.dumps([p for p in ev.parts]))


class PresetTests(unittest.TestCase):
    def validate(self, entry):
        raw = CFG.initial_config("https://x.example", "a" * 26, "k" * 20, "Timur", "Cihaz", None)
        raw["engines"]["openai"] = [entry]
        d, errors, _ = CFG.validate(raw)
        return d["engines"]["openai"], errors

    def test_presets_fix_an_https_url_and_need_a_key_variable(self):
        oa, errors = self.validate({"id": "gem", "preset": "gemini", "apiKeyEnv": "GEMINI_API_KEY", "tools": True})
        self.assertEqual(errors, [])
        self.assertEqual(oa[0]["url"], CFG.PROVIDER_PRESETS["gemini"]["url"])
        self.assertEqual((oa[0]["name"], oa[0]["tools"], oa[0]["allowRemote"]), ("Google Gemini", True, True))
        oa, errors = self.validate({"id": "gem", "preset": "gemini", "url": "https://saldirgan.example/v1",
                                    "apiKeyEnv": "K"})
        self.assertEqual(oa[0]["url"], CFG.PROVIDER_PRESETS["gemini"]["url"])        # the preset wins
        for p in CFG.PROVIDER_PRESETS.values():
            self.assertTrue(p["url"].startswith("https://"))
        oa, errors = self.validate({"id": "gem", "preset": "gemini"})
        self.assertEqual(oa, [])
        self.assertTrue(any("apiKeyEnv" in e for e in errors))
        oa, errors = self.validate({"id": "x", "preset": "evil", "apiKeyEnv": "K"})
        self.assertEqual(oa, [])
        self.assertTrue(any("preset" in e for e in errors))

    def test_tools_default_off(self):
        oa, errors = self.validate({"id": "lm", "url": "http://127.0.0.1:1234/v1"})
        self.assertEqual((oa[0]["tools"], oa[0]["preset"], errors), (False, None, []))


if __name__ == "__main__":
    unittest.main()
