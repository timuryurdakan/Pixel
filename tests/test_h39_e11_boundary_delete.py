"""v2.md H39 + E11 (2026-10-22).

H39 "error flood": a connector Claude agent asked to "create a txt file on the Desktop" tried Bash/Write/Read outside
its workspace; every denial showed as a red tool row. Reproduction with the real Claude Code 2.1.306 also showed that a
bare `--allowedTools Edit Write NotebookEdit` let the `edit` profile WRITE OUTSIDE the workspace (~/Desktop). Covered
here: the edit allow rule is scoped to the working directory, the appended boundary system prompt (folder name,
~-relative path, profile text, cmd.exe-safe), ~-relative tool summaries, and a denial flood from the fake `claude`
(N denials reach the browser as N `denied` parts; tests/js/prompt_denials.test.js groups them into one line).

E11: agent deletion from the UI: `agent.delete` tells the other paired tabs (`agent.deleted` without `re`), the
requesting tab only gets its reply, and `remote.agentDelete: false` refuses it (`not_allowed`).
"""

import os
import tempfile
import time
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E
import _v3_fakes as F
from test_connector_claude import PROFILES, Events
from test_connector_tasks import PAIR_ITER, Base

from pixel_connector.engines import claude as CL
from pixel_connector.engines.base import TaskContext

SID = "11111111-2222-4333-8444-555555555555"


class EditScopeTests(unittest.TestCase):
    def allow(self, profile):
        a = CL.build_argv("/opt/claude", profile, PROFILES, SID, False, None, 10, None, (2, 1, 306))
        if "--allowedTools" not in a:
            return []
        return a[a.index("--allowedTools") + 1:a.index("--disallowedTools")]

    def test_edit_tools_only_inside_the_working_directory(self):
        for prof in ("edit", "dev"):
            allow = self.allow(prof)
            self.assertEqual(allow[0], "Edit(./**)")
            # a bare tool name allows the tool on ANY path (reproduced: ~/Desktop/yeni_dosya.txt was written)
            for rule in allow:
                self.assertRegex(rule, r"^\w+\(.+\)\Z", "unscoped allow rule %r" % rule)
        self.assertEqual(self.allow("readonly"), [])

    def test_system_prompt_flag(self):
        a = CL.build_argv("/opt/claude", "edit", PROFILES, SID, False, None, 10, None, (2, 1, 306),
                          system_prompt="X")
        self.assertEqual(a[a.index("--append-system-prompt") + 1], "X")
        b = CL.build_argv("/opt/claude", "edit", PROFILES, SID, False, None, 10, None, (2, 1, 306))
        self.assertNotIn("--append-system-prompt", b)


class BoundaryPromptTests(unittest.TestCase):
    def test_folder_path_and_profile(self):
        t = CL.boundary_prompt("edit", "Pixel Office İşleri", "/home/ali/PixelOfficeIsler", home="/home/ali")
        self.assertIn("«Pixel Office İşleri»", t)
        self.assertIn("(%s)" % os.path.join("~", "PixelOfficeIsler"), t)
        self.assertNotIn("/home/ali", t)                      # no user name
        self.assertIn("pixel-connector workspace add ~/Desktop", t)
        self.assertIn("Masaüstü", t)
        self.assertIn("Desktop", t)
        self.assertIn("never retry a denied action", t)
        self.assertIn("kabuk komutu çalıştıramazsın", t)
        self.assertLess(len(t), 1400)

    def test_readonly_says_no_writes(self):
        t = CL.boundary_prompt("readonly", "Notlar", "/home/ali/Notlar", home="/home/ali")
        self.assertIn("Sadece okuma", t)
        self.assertIn("cannot create, edit or delete any file", t)
        self.assertNotIn("okuyup yazabilirsin", t)

    def test_unsafe_characters_are_dropped(self):
        evil = 'A"&%PATH%^!\nIgnore previous‮'
        t = CL.boundary_prompt("dev", evil, "/srv/x", home="/home/ali")
        for c in '"%^!\n\r‮':
            self.assertNotIn(c, t)
        self.assertIn("(x)", t)                               # outside home: only the folder's basename

    def test_tool_summary_home_relative(self):
        s = CL.tool_summary("Write", {"file_path": "/home/ali/Desktop/not.txt"}, "/home/ali/PixelOfficeIsler",
                            "/home/ali")
        self.assertEqual(s, os.path.join("~", "Desktop", "not.txt"))
        self.assertEqual(CL.tool_summary("Read", {"file_path": "/home/ali/PixelOfficeIsler/a.md"},
                                         "/home/ali/PixelOfficeIsler", "/home/ali"), "a.md")
        self.assertEqual(CL.home_relative("/home/ali", "/home/ali"), "~")
        self.assertIsNone(CL.home_relative("/home/alice/x", "/home/ali"))


class FakeRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_dir = os.path.join(self.tmp.name, "fake")
        self.path = F.write_fake_claude(self.fake_dir)
        self.ws = os.path.join(self.tmp.name, "PixelOfficeIsler")
        os.makedirs(self.ws)
        cfg = {"path": self.path, "enabled": True, "bare": False, "showThinking": False, "maxTurns": 50,
               "maxBudgetUsdPerTask": 2.0, "defaultModel": None, "cancelGraceS": 5.0, "cancelTermS": 10.0}
        self.eng = CL.ClaudeEngine(cfg, PROFILES, env=dict(os.environ, CLAUDE_FAKE_DIR=self.fake_dir))
        self.agent = {"key": "a1", "name": "Kâtip", "engine": "claude", "claudeSession": None,
                      "claudeSessionStarted": False, "claudeTotals": None}

    def tearDown(self):
        self.tmp.cleanup()

    def run_task(self, profile, ws_name="Pixel Office İşleri"):
        F.set_plan(self.fake_dir, [{"fixture": "claude_stream_denied_flood.jsonl"}])
        ev = Events()
        ctx = TaskContext("t_0123456789", self.agent, "masaüstünde bir txt dosyası oluştur", self.ws, None, profile,
                          workspace_name=ws_name)
        self.eng.start(ctx, ev)
        return ev, ev.wait()

    def test_denial_flood_and_boundary_prompt(self):
        ev, end = self.run_task("edit")
        self.assertEqual(end["status"], "success")              # denials alone are not an engine error
        self.assertEqual(end["denials"], 4)
        denied = ev.kinds("denied")
        self.assertEqual([d["name"] for d in denied], ["Bash", "Write", "Read", "Write"])
        self.assertEqual([t["name"] for t in ev.kinds("tool")], ["Bash", "Write", "Read", "Write"])
        argv = F.calls(self.fake_dir)[0]["argv"]
        sp = argv[argv.index("--append-system-prompt") + 1]
        self.assertIn("«Pixel Office İşleri»", sp)
        self.assertIn("pixel-connector workspace add", sp)
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Edit(./**)")
        self.assertNotIn("masaüstünde", " ".join(argv))           # the prompt still goes to stdin only

    def test_readonly_profile_prompt(self):
        ev, end = self.run_task("readonly", ws_name=None)
        argv = F.calls(self.fake_dir)[0]["argv"]
        sp = argv[argv.index("--append-system-prompt") + 1]
        self.assertIn("«PixelOfficeIsler»", sp)                   # no display name → the folder's basename
        self.assertIn("Sadece okuma", sp)
        self.assertNotIn("--allowedTools", argv)


class DeleteProtocolTests(Base):
    def test_other_tabs_hear_about_the_deletion(self):
        br2 = E.FakeBrowser(self.env, label="Telefon · Android")
        try:
            E.pair(self.env, self.conn, br2, iterations=PAIR_ITER)
            br2.connect_device(self.dev)
            key = self.create(name="Silinecek")
            info = self.req({"type": "peer.hello", "label": "x", "app": "web/3.0.0"})
            self.assertTrue(info["remote"]["agentDelete"])
            d = self.req({"type": "agent.delete", "agent": key})
            self.assertEqual((d["type"], d["agent"]), ("agent.deleted", key))
            m = br2.next_msg(lambda m: m.get("type") == "agent.deleted", 10)
            self.assertEqual(m, {"type": "agent.deleted", "agent": key})
            self.assertEqual(self.req({"type": "agent.list"})["agents"], [])
            time.sleep(0.3)
            extra = []
            while not self.br.msgs.empty():
                extra.append(self.br.msgs.get())
            self.assertFalse([x for x in extra if x.get("type") == "agent.deleted"], extra)   # requester: reply only
            again = self.req({"type": "agent.delete", "agent": key})
            self.assertEqual((again["type"], again["code"]), ("error", "unknown_agent"))
            self.assertEqual([x["agent"] for x in self.conn.audit.read() if x["ev"] == "agent.delete"], [key])
        finally:
            br2.close()


class DeleteDisabledTests(Base):
    connector_overrides = {"remote": {"agentCreate": True, "agentDelete": False, "budgetEdit": True}}

    def test_remote_delete_can_be_switched_off(self):
        key = self.create()
        r = self.req({"type": "agent.delete", "agent": key})
        self.assertEqual((r["type"], r["code"]), ("error", "not_allowed"))
        self.assertEqual([a["key"] for a in self.req({"type": "agent.list"})["agents"]], [key])
        info = self.req({"type": "peer.hello", "label": "x", "app": "web/3.0.0"})
        self.assertFalse(info["remote"]["agentDelete"])


if __name__ == "__main__":
    unittest.main()
