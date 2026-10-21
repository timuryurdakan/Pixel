"""Phase 13 integration: the real connector against dev_relay + the dev server, driven by FakeBrowser over E2E
channels. Fake engines only (fake `claude`, fake Ollama). Covers task streaming, Claude tools/usage/resume, cancel,
budgets, workspace denial, local confirmation, runtimes, busy/queued, watch, office status with `control`, the
privacy canary, the loopback control API and `pixel-connector pair` (CLI) through the running service.
"""

import http.client
import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
import _v3_fakes as F

from pixel_connector import __main__ as CLI
from pixel_connector.approvals import ctl_request

PROMPT_CANARY = "CANARY-v3-9d1e"
OUT_CANARY = "CANARY-OUT-77aa"


class Base(unittest.TestCase):
    connector_overrides = {}
    start_ctl = False

    def setUp(self):
        self.env = E.V3Env()
        self.llm = F.FakeLLM(words=["Merhaba ", OUT_CANARY, " — ", "tamam."])
        self.fake_dir = os.path.join(self.env.tmp, "fakeclaude")
        self.claude = F.write_fake_claude(self.fake_dir)
        os.environ["CLAUDE_FAKE_DIR"] = self.fake_dir
        ov = {"engines": {"claude": {"path": self.claude, "enabled": True, "maxTurns": 50, "maxBudgetUsdPerTask": 2.0},
                          "ollama": {"url": self.llm.url, "enabled": True, "keepAlive": "10m", "historyMessages": 40},
                          "openai": []},
              "runtimes": [{"id": "dummy", "name": "Deneme", "start": ["true"], "stop": ["sleep", "0.05"]}]}
        ov.update(self.connector_overrides)
        self.home = self.env.connector_home(**ov)
        self.conn = self.env.connector(self.home, start_ctl=self.start_ctl)
        self.br = E.FakeBrowser(self.env)
        self.dev, _, _ = E.pair(self.env, self.conn, self.br)
        self.br.connect_device(self.dev)

    def tearDown(self):
        self.br.close()
        self.conn.stop()
        self.llm.stop()
        self.env.stop()
        os.environ.pop("CLAUDE_FAKE_DIR", None)

    def req(self, obj, timeout=15.0):
        return self.br.request(self.dev, obj, timeout)

    def create(self, engine="ollama", name="Yerel", model="qwen2.5-coder:7b", ws=None, profile="edit", budget=None):
        r = self.req({"type": "agent.create", "name": name, "engine": engine, "model": model,
                      "workspace": ws if ws is not None else {"id": "pixel", "sub": None}, "profile": profile,
                      "budget": budget})
        self.assertEqual(r["type"], "agent.created", r)
        return r["agent"]["key"]

    def run_task(self, key, prompt="Merhaba " + PROMPT_CANARY, fresh=False):
        ack = self.req({"type": "task.start", "agent": key, "prompt": prompt, "fresh": fresh})
        self.assertEqual(ack["type"], "task.ack", ack)
        chunks, end = self.br.task_events(ack["task"])
        return ack, chunks, end

    def office(self, key, pred, timeout=8.0):
        end = time.time() + timeout
        last = None
        while time.time() < end:
            for a in self.env.state()["agents"]:
                if (a.get("control") or {}).get("agent") == key:
                    last = a
                    if pred(a):
                        return a
            time.sleep(0.2)
        raise AssertionError("office state not reached: %r" % (last,))


class OllamaFlowTests(Base):
    def test_stream_usage_office_audit_and_privacy(self):
        self.llm.delay = 0.4                 # long enough for the v2 debounce to publish "working" first
        key = self.create()
        a = self.office(key, lambda a: True)
        self.assertEqual(a["control"], {"device": self.conn.key.device_id, "agent": key})
        self.assertEqual((a["name"], a["project"], a["source"], a["owner"]), ("Yerel", "Pixel", "connector", "Timur"))
        ack, chunks, end = self.run_task(key)
        self.assertEqual((ack["confirm"], ack["queued"]), (None, False))
        self.assertEqual(E.text_of(chunks), "Merhaba %s — tamam." % OUT_CANARY)
        self.assertEqual([c["n"] for c in chunks], list(range(len(chunks))))
        self.assertEqual(end["status"], "success")
        self.assertEqual(end["usage"]["inTok"], 26)
        self.assertEqual(end["usage"]["outTok"], 8)
        self.assertEqual((end["costUsd"], end["costBasis"]), (0.0, "local"))
        bodies = [json.loads(b) for b in self.env.status_bodies if b.strip()]
        flat = [x for b in bodies for x in (b.get("agents") or [b])]
        self.assertIn(("working", "llm"), [(x.get("state"), x.get("tool")) for x in flat])
        self.office(key, lambda a: a["state"] == "idle" and a.get("detail") == "Görev bitti")
        # usage and task history
        u = self.req({"type": "usage.get", "period": "day", "agent": None})
        self.assertEqual((u["totals"]["tasks"], u["totals"]["tokens"]), (1, 34))
        tl = self.req({"type": "task.list", "agent": key, "limit": 5})
        self.assertEqual(tl["tasks"][0]["task"], ack["task"])
        tg = self.req({"type": "task.get", "task": ack["task"]})
        self.assertIn(PROMPT_CANARY, tg["prompt"])            # local ledger, delivered E2E only
        self.assertIn(OUT_CANARY, tg["result"])
        # audit: hash + length, never the text
        recs = self.conn.audit.read()
        start = [r for r in recs if r["ev"] == "task.start"][0]
        self.assertEqual((start["agent"], start["engine"], start["ws"], start["profile"]), (key, "ollama", "pixel", "edit"))
        self.assertEqual(start["promptLen"], len(("Merhaba " + PROMPT_CANARY).encode()))
        self.assertIn("task.end", [r["ev"] for r in recs])
        self.assertIn("agent.create", [r["ev"] for r in recs])
        for ev in ("pair.ok", "hs.ok", "config.load"):
            self.assertIn(ev, [r["ev"] for r in recs])
        # privacy canary: not in any relay frame, status body or the audit log
        time.sleep(1.2)
        blob = "\n".join(self.env.relay_frames)
        self.assertGreater(len(self.env.relay_frames), 5)
        for canary in (PROMPT_CANARY, OUT_CANARY, "Pixel", "Yerel", "qwen2.5"):
            self.assertNotIn(canary, blob, canary)
        status = b"\n".join(self.env.status_bodies).decode()
        self.assertIn('"ctl"', status)
        for canary in (PROMPT_CANARY, OUT_CANARY):
            self.assertNotIn(canary, status)
            with open(os.path.join(self.home, "audit.log"), encoding="utf-8") as f:
                self.assertNotIn(canary, f.read())

    def test_cancel_mid_stream(self):
        self.llm.words = ["kelime "] * 300
        self.llm.delay = 0.02
        key = self.create()
        ack = self.req({"type": "task.start", "agent": key, "prompt": "uzun anlat"})
        self.br.next_msg(lambda m: m.get("type") == "task.chunk" and m["task"] == ack["task"])
        time.sleep(1.3)                      # let the office show "working" (v2 debounce) before the cancel
        self.br.send_e(self.dev, {"type": "task.cancel", "task": ack["task"]})
        chunks, end = self.br.task_events(ack["task"])
        self.assertEqual(end["status"], "cancelled")
        self.assertTrue(self.llm.disconnected.wait(3))
        self.office(key, lambda a: a.get("detail") == "Durduruldu")
        r = self.req({"type": "task.cancel", "task": ack["task"]})
        self.assertEqual((r["type"], r["code"]), ("error", "unknown_task"))

    def test_busy_queue_and_watch(self):
        self.llm.words = ["a "] * 30
        self.llm.delay = 0.02
        key = self.create()
        other = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, other)
        other.connect_device(self.dev)
        other.send_e(self.dev, {"type": "agent.watch", "agent": key, "on": True})
        a1 = self.req({"type": "task.start", "agent": key, "prompt": "bir"})
        a2 = self.req({"type": "task.start", "agent": key, "prompt": "iki"})
        self.assertEqual((a1["queued"], a2["queued"]), (False, True))
        a3 = self.req({"type": "task.start", "agent": key, "prompt": "üç"})
        self.assertEqual((a3["type"], a3["code"]), ("error", "busy"))
        for ack in (a1, a2):
            _, end = self.br.task_events(ack["task"])
            self.assertEqual(end["status"], "success")
        # the watching browser saw both tasks, including chunks it did not start
        _, end_w = other.task_events(a1["task"])
        self.assertEqual(end_w["status"], "success")
        st = other.next_msg(lambda m: m.get("type") == "agent.state")
        self.assertEqual(st["agent"], key)
        other.close()

    def test_rate_limit_and_prompt_size(self):
        key = self.create()
        r = self.req({"type": "task.start", "agent": key, "prompt": "x" * 32769})
        self.assertEqual(r["code"], "too_large")
        r = self.req({"type": "task.start", "agent": key, "prompt": "   "})
        self.assertEqual(r["code"], "bad_request")
        self.conn.scheduler.limits["tasksPerMinute"] = 1
        self.run_task(key, "bir")
        r = self.req({"type": "task.start", "agent": key, "prompt": "iki"})
        self.assertEqual(r["code"], "rate_limited")
        self.assertIn("denied", [x["ev"] for x in self.conn.audit.read()])

    def test_budget_exceeded(self):
        key = self.create()
        r = self.req({"type": "budget.set", "scope": "engine:ollama", "period": "day", "unit": "tokens", "limit": 20,
                      "action": "block"})
        self.assertEqual(r["type"], "usage")
        b = [x for x in r["budgets"] if x["scope"] == "engine:ollama"][0]
        self.assertEqual((b["used"], b["state"]), (0, "ok"))
        self.run_task(key, "ilk")                               # 34 tokens > 20 → now over
        r = self.req({"type": "task.start", "agent": key, "prompt": "ikinci"})
        self.assertEqual((r["type"], r["code"]), ("error", "budget_exceeded"))
        u = self.req({"type": "usage.get", "period": "day", "agent": key})
        b = [x for x in u["budgets"] if x["scope"] == "engine:ollama"][0]
        self.assertEqual((b["state"], b["pct"]), ("over", 170))

    def test_workspace_denied(self):
        for ws in ({"id": "nope", "sub": None}, {"id": "pixel", "sub": "../.."}, {"id": "pixel", "sub": "/etc"},
                   {"id": "pixel", "sub": "missing"}):
            r = self.req({"type": "agent.create", "name": "X", "engine": "claude", "model": None, "workspace": ws,
                          "profile": "edit", "budget": None})
            self.assertEqual((r["type"], r["code"]), ("error", "workspace_denied"), ws)
        ok = self.create(engine="claude", name="Alt", model=None, ws={"id": "pixel", "sub": "sub"})
        lst = self.req({"type": "workspace.list"})
        self.assertEqual(lst["workspaces"], [{"id": "pixel", "name": "Pixel", "children": ["sub"]}])
        self.assertNotIn(self.env.tmp, json.dumps(lst))
        r = self.req({"type": "agent.create", "name": "X", "engine": "claude", "model": None,
                      "workspace": {"id": "pixel"}, "profile": "auto", "budget": None})
        self.assertEqual(r["code"], "not_allowed")
        del ok

    def test_runtimes_and_agents_admin(self):
        lst = self.req({"type": "runtime.list"})
        self.assertEqual([r["id"] for r in lst["runtimes"]], ["dummy"])
        r = self.req({"type": "runtime.start", "runtime": "dummy", "argv": ["rm", "-rf", "/"], "args": "x"})
        self.assertEqual((r["type"], r["ok"], r["exitCode"]), ("runtime.result", True, 0))
        r = self.req({"type": "runtime.stop", "runtime": "dummy"})
        self.assertTrue(r["ok"])
        r = self.req({"type": "runtime.start", "runtime": "rm"})
        self.assertEqual((r["type"], r["code"]), ("error", "runtime_unknown"))
        audit = [x for x in self.conn.audit.read() if x["ev"].startswith("runtime.")]
        self.assertEqual([(x["ev"], x["runtime"], x["exitCode"]) for x in audit],
                         [("runtime.start", "dummy", 0), ("runtime.stop", "dummy", 0)])
        key = self.create()
        upd = self.req({"type": "agent.update", "agent": key, "name": "Yeni Ad",
                        "budget": {"period": "day", "unit": "tokens", "limit": 5000, "action": "warn"}})
        self.assertEqual((upd["agent"]["name"], upd["agent"]["budget"]["limit"]), ("Yeni Ad", 5000))
        self.assertEqual([a["key"] for a in self.req({"type": "agent.list"})["agents"]], [key])
        self.office(key, lambda a: a["name"] == "Yeni Ad")
        d = self.req({"type": "agent.delete", "agent": key})
        self.assertEqual(d, {"type": "agent.deleted", "agent": key, "re": d["re"]})
        end = time.time() + 8
        while time.time() < end and any((a.get("control") or {}).get("agent") == key for a in self.env.state()["agents"]):
            time.sleep(0.2)
        self.assertFalse(any((a.get("control") or {}).get("agent") == key for a in self.env.state()["agents"]))

    def test_wall_time_timeout_and_reset(self):
        self.llm.words = ["x "] * 400
        self.llm.delay = 0.02
        key = self.create()
        self.conn.scheduler.limits["taskTimeoutS"] = 1
        ack, chunks, end = self.run_task(key, "çok uzun")
        self.assertEqual((end["status"], end["error"]["code"]), ("timeout", "timeout"))
        self.assertLess(end["durationMs"], 4000)
        self.conn.scheduler.limits["taskTimeoutS"] = 3600
        self.llm.words = ["kısa"]
        self.llm.delay = 0
        self.run_task(key, "bir")
        hist = os.path.join(self.home, "history", key + ".jsonl")
        self.assertTrue(os.path.exists(hist))
        r = self.req({"type": "agent.reset", "agent": key})
        self.assertEqual(r["type"], "agent.updated")
        self.assertFalse(os.path.exists(hist))

    def test_peer_revoke_from_other_browser(self):
        phone = E.FakeBrowser(self.env, label="Telefon")
        E.pair(self.env, self.conn, phone)
        phone.connect_device(self.dev)
        r = self.req({"type": "peer.revoke", "bid": phone.bid})
        self.assertEqual(r["type"], "peer.revoked")
        time.sleep(0.3)
        self.assertIsNone(self.conn.peers.active(phone.bid))
        with self.assertRaises(AssertionError):
            phone.request(self.dev, {"type": "agent.list"}, timeout=1.5)
        phone.close()


class ClaudeFlowTests(Base):
    def test_tools_usage_resume(self):
        key = self.create(engine="claude", name="Kâtip", model=None)
        F.set_plan(self.fake_dir, [{"fixture": "claude_stream_success_tools.jsonl"},
                                   {"fixture": "claude_stream_resume.jsonl"}])
        ack, chunks, end = self.run_task(key, "Hatayı düzelt " + PROMPT_CANARY)
        parts = [p for c in chunks for p in c["parts"]]
        self.assertIn({"k": "tool", "name": "Read", "v": "src/app.py"}, parts)
        self.assertIn({"k": "tool_done", "name": "Read", "ok": True}, parts)
        self.assertEqual(end["status"], "success")
        self.assertEqual(end["usage"]["cacheRead"], 30000)
        self.assertEqual((end["costUsd"], end["costBasis"], end["turns"]), (0.0421, "claude-estimate", 3))
        self.assertTrue(chunks[-1]["live"]["inTok"] > 0)
        ack2, chunks2, end2 = self.run_task(key, "devam et")
        self.assertAlmostEqual(end2["costUsd"], 0.01, places=6)
        cs = F.calls(self.fake_dir)
        self.assertIn("--session-id", cs[0]["argv"])
        self.assertEqual(cs[1]["argv"][cs[1]["argv"].index("--resume") + 1],
                         cs[0]["argv"][cs[0]["argv"].index("--session-id") + 1])
        self.assertEqual(cs[0]["stdin"], "Hatayı düzelt " + PROMPT_CANARY)
        self.assertEqual(cs[0]["cwd"], os.path.realpath(os.path.join(self.env.tmp, "dev", "projects", "pixel")))
        self.assertEqual(cs[0]["argv"][cs[0]["argv"].index("--max-budget-usd") + 1], "2.00")
        info = self.req({"type": "peer.hello", "label": "x"})
        self.assertEqual(info["engines"]["claude"], {"available": True, "version": "2.1.301", "auth": "subscription"})
        a = self.office(key, lambda a: a["state"] == "idle")
        self.assertEqual(a["control"]["agent"], key)
        time.sleep(1.2)
        self.assertNotIn(PROMPT_CANARY, "\n".join(self.env.relay_frames))
        self.assertNotIn("src/app.py", "\n".join(self.env.relay_frames))
        self.assertNotIn(PROMPT_CANARY, b"".join(self.env.status_bodies).decode())
        # Phase 15: Claude output, tool arguments and tool results never reach the relay or Vercel either
        relay_blob = "\n".join(self.env.relay_frames)
        status_blob = b"".join(self.env.status_bodies).decode()
        for canary in ("okuyorum", "CANARY-TOOL-RESULT", "src/app.py", "Hatayı düzelt"):
            self.assertNotIn(canary, relay_blob, canary)
            self.assertNotIn(canary, status_blob, canary)

    def test_usd_budget_caps_max_budget(self):
        key = self.create(engine="claude", name="Kâtip", model=None)
        self.req({"type": "budget.set", "scope": "device", "period": "day", "unit": "usd", "limit": 0.5, "action": "block"})
        F.set_plan(self.fake_dir, [{"fixture": "claude_stream_max_budget.jsonl"}])
        ack, chunks, end = self.run_task(key, "büyük iş")
        argv = F.calls(self.fake_dir)[0]["argv"]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.50")
        self.assertEqual(end["status"], "budget")
        r = self.req({"type": "task.start", "agent": key, "prompt": "bir daha"})
        self.assertEqual(r["code"], "budget_exceeded")


class ConfirmTests(Base):
    connector_overrides = {"confirm": {"task": "always", "agentCreate": False, "runtime": False, "peerRevoke": False,
                                       "timeoutS": 120}}
    start_ctl = True

    def test_confirm_approve_and_deny(self):
        key = self.create()
        ack = self.req({"type": "task.start", "agent": key, "prompt": "onaylı görev"})
        self.assertEqual(ack["confirm"], "pending")
        self.office(key, lambda a: a["state"] == "waiting_for_user" and a.get("detail") == "Onay bekliyor")
        st, d = ctl_request(self.home, "GET", "/ctl/approvals")
        self.assertEqual(st, 200)
        pend = d["pending"][0]
        self.assertEqual((pend["kind"], pend["preview"]), ("task", "onaylı görev"))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(CLI.main(["--home", self.home, "approve", pend["id"]]), 0)
        self.assertIn("Onaylandı", out.getvalue())
        self.assertEqual(self.env.notified[-1], "Yerel")          # fixed text + agent name, never the prompt
        cr = self.br.next_msg(lambda m: m.get("type") == "confirm.result")
        self.assertEqual((cr["task"], cr["ok"]), (ack["task"], True))
        _, end = self.br.task_events(ack["task"])
        self.assertEqual(end["status"], "success")
        ack2 = self.req({"type": "task.start", "agent": key, "prompt": "reddedilecek"})
        pid = ctl_request(self.home, "GET", "/ctl/approvals")[1]["pending"][0]["id"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(["--home", self.home, "deny", pid]), 0)
        _, end2 = self.br.task_events(ack2["task"])
        self.assertEqual((end2["status"], end2["error"]["code"]), ("denied", "confirm_denied"))
        confirms = [r for r in self.conn.audit.read() if r["ev"] == "confirm"]
        self.assertEqual([c["result"] for c in confirms], ["ok", "denied"])

    def test_confirm_timeout_and_page(self):
        self.conn.approvals.timeout_s = 0.5
        key = self.create()
        ack = self.req({"type": "task.start", "agent": key, "prompt": "zaman aşımı <script>"})
        # the approval page: Host check, cookie login, escaped preview, CSRF
        port = self.conn.ctl.port
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/onay", headers={"Host": "evil.example:%d" % port})
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 403)
        c.close()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/onay")
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 401)
        c.request("GET", "/onay?t=" + self.conn.ctl.token)       # the control token is not a page token
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 401)
        c.request("GET", "/onay?t=" + self.conn.ctl.page_token)
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 303)
        cookie = r.getheader("Set-Cookie").split(";")[0]
        self.assertIn("HttpOnly", r.getheader("Set-Cookie"))
        c.request("GET", "/onay", headers={"Cookie": cookie})
        r = c.getresponse()
        page = r.read().decode()
        self.assertIn("zaman aşımı &lt;script&gt;", page)
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        c.request("POST", "/onay/karar", body="id=x&karar=onayla&csrf=wrong",
                  headers={"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 403)
        c.close()
        _, end = self.br.task_events(ack["task"])
        self.assertEqual((end["status"], end["error"]["code"]), ("denied", "confirm_timeout"))

    def test_ctl_api_auth(self):
        port = self.conn.ctl.port
        for headers, want in (({}, 401), ({"X-Pixel-Ctl": "wrong"}, 401),
                              ({"X-Pixel-Ctl": self.conn.ctl.token, "Host": "attacker.example"}, 403),
                              ({"X-Pixel-Ctl": self.conn.ctl.token}, 200)):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", "/ctl/status", headers=headers)
            r = c.getresponse()
            body = r.read()
            self.assertEqual(r.status, want, headers)
            c.close()
        self.assertEqual(json.loads(body)["deviceId"], self.conn.key.device_id)
        mode = os.stat(os.path.join(self.home, "control.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)


class CliPairTests(Base):
    start_ctl = True

    def test_pair_through_service_cli(self):
        new = E.FakeBrowser(self.env, label="Firefox · Linux")
        result = {}
        import threading

        def browser_side():
            for _ in range(100):
                p = self.conn.router.pairing
                if p is not None and p.state == "open" and p.code != result.get("old"):
                    break
                time.sleep(0.05)
            result["dev"], result["sas"] = new.pair_start(self.conn.router.pairing.code)
            result["p4"] = new.pair_finish()

        result["old"] = self.conn.router.pairing.code
        th = threading.Thread(target=browser_side)
        th.start()
        asked = []

        def ask(text):
            asked.append(text)
            return True
        out = io.StringIO()
        with redirect_stdout(out):
            rc = CLI.cmd_pair(CLI.build_parser().parse_args(["--home", self.home, "pair"]), ask=ask)
        th.join(20)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("Eşleştirme kodu:", out.getvalue())
        self.assertIn(result["sas"], asked[0])
        self.assertIn("Firefox · Linux", asked[0])
        self.assertTrue(result["p4"]["ok"])
        self.assertIsNotNone(self.conn.peers.active(new.bid))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(CLI.main(["--home", self.home, "peers", "revoke", new.bid]), 0)
        self.assertIsNone(self.conn.peers.active(new.bid))
        new.close()


if __name__ == "__main__":
    unittest.main()
