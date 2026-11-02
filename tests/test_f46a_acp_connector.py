"""F4-6a through the real connector (dev relay, E2E browser): device.info lists the ACP agents, agent.create for one
is always confirmed on the computer (A4) and only with readonly/edit and no model (A2), and a task runs through the fake
ACP agent (tests/_fake_acp_agent.py) like any other engine."""

import sys
import threading
import time
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)
from test_connector_tasks import Base

FAKE = str(Path(__file__).resolve().parent / "_fake_acp_agent.py")
WS = {"id": "pixel", "sub": None}


class AcpConnectorTests(Base):
    connector_overrides = {"engines": {
        "claude": {"enabled": False}, "ollama": {"enabled": False}, "openai": [],
        "acp": [{"id": "sahte", "name": "Sahte ACP", "command": [sys.executable, FAKE, "text"], "enabled": True},
                {"id": "kapali", "command": [sys.executable, FAKE, "text"]}]}}

    def create_confirmed(self, msg, approve=True):
        box = {}
        t = threading.Thread(target=lambda: box.update(r=self.req(msg, timeout=20)))
        t.start()
        end = time.time() + 10
        pend = []
        while time.time() < end and not pend:
            pend = [p for p in self.conn.approvals.pending.values() if p.kind == "agentCreate"]
            time.sleep(0.05)
        self.assertTrue(pend, "agent.create for an ACP agent was not held for the computer's confirmation")
        self.conn.approvals.answer(pend[0].id, approve, None, by="local")
        t.join(20)
        return box["r"]

    def test_device_info_lists_acp_agents(self):
        info = self.conn.device_info()
        self.assertIn("acp", info["features"])
        self.assertEqual(info["engines"]["acp"], [{"id": "sahte", "name": "Sahte ACP", "available": True},
                                                  {"id": "kapali", "name": "kapali", "available": False}])

    def test_create_is_checked_and_always_confirmed_then_a_task_runs(self):
        base = {"type": "agent.create", "name": "Kodcu", "engine": "acp", "engineId": "sahte", "model": None,
                "workspace": WS, "profile": "edit"}
        for change, code in [({"profile": "dev"}, "not_allowed"), ({"profile": "auto"}, "not_allowed"),
                             ({"model": "gpt-5"}, "bad_request"), ({"engineId": "kapali"}, "engine_unavailable"),
                             ({"engineId": "yok"}, "engine_unavailable"), ({"workspace": None}, "workspace_denied")]:
            r = self.req(dict(base, **change))
            self.assertEqual((r["type"], r.get("code")), ("error", code), change)
        r = self.create_confirmed(base, approve=False)
        self.assertEqual(r.get("code"), "confirm_denied")
        r = self.create_confirmed(base)
        self.assertEqual(r["type"], "agent.created", r)
        key = r["agent"]["key"]
        self.assertEqual((r["agent"]["engine"], r["agent"]["engineId"]), ("acp", "sahte"))
        up = self.req({"type": "agent.update", "agent": key, "profile": "dev"})
        self.assertEqual(up.get("code"), "not_allowed")
        up = self.req({"type": "agent.update", "agent": key, "model": "x"})
        self.assertEqual(up.get("code"), "bad_request")
        _, chunks, end = self.run_task(key, prompt="Merhaba")
        self.assertEqual(end["status"], "success", end)
        self.assertEqual(end["result"], "Merhaba dünya")
        self.assertEqual(end.get("costBasis"), "unknown")


if __name__ == "__main__":
    unittest.main()
