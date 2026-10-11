import unittest
from pathlib import Path

from _helpers import T0, assistant_text, iso, tool_result, tool_use, user_prompt, user_text_block

from pixel_office.models import State
from pixel_office.transcript import SessionTracker, parse_line, parse_ts, snippet, summarize_tool_input


def make(sub: bool = False) -> SessionTracker:
    return SessionTracker("sess-1:agent-x" if sub else "sess-1", Path("/nonexistent.jsonl"), is_subagent=sub,
                          parent_id="sess-1" if sub else None, project_dir_name="-home-x-proj")


def feed(tr: SessionTracker, *objs: dict) -> None:
    for o in objs:
        tr.feed(o, T0)


class HelperTests(unittest.TestCase):
    def test_parse_line(self):
        self.assertEqual(parse_line('{"a": 1}'), {"a": 1})
        self.assertIsNone(parse_line("{broken"))
        self.assertIsNone(parse_line("[1,2]"))
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("   "))

    def test_parse_ts(self):
        self.assertAlmostEqual(parse_ts("2026-10-11T12:46:50.208Z"), 1791722810.208, places=3)
        self.assertAlmostEqual(parse_ts(iso(T0)), T0)
        self.assertIsNone(parse_ts("yesterday"))
        self.assertIsNone(parse_ts(None))
        self.assertIsNone(parse_ts(12))  # type: ignore[arg-type]

    def test_snippet(self):
        self.assertEqual(snippet("  a \n\n b\tc "), "a b c")
        s = snippet("x" * 500)
        self.assertEqual(len(s), 140)
        self.assertTrue(s.endswith("…"))

    def test_summarize_tool_input(self):
        self.assertEqual(summarize_tool_input("Bash", {"command": "ls -la\npwd", "description": "List files"}), "List files")
        self.assertEqual(summarize_tool_input("Bash", {"command": "ls -la\npwd"}), "ls -la")
        self.assertEqual(summarize_tool_input("Read", {"file_path": "/a/b/office.js"}), "office.js")
        self.assertEqual(summarize_tool_input("NotebookEdit", {"notebook_path": "/a/n.ipynb"}), "n.ipynb")
        self.assertEqual(summarize_tool_input("Grep", {"pattern": "TODO"}), "TODO")
        self.assertEqual(summarize_tool_input("Agent", {"description": "Explore", "prompt": "..."}), "Explore")
        self.assertEqual(summarize_tool_input("WebFetch", {"url": "https://docs.python.org/3/x"}), "docs.python.org")
        self.assertEqual(summarize_tool_input("WebSearch", {"query": "sse"}), "sse")
        self.assertEqual(summarize_tool_input("TodoWrite", {"todos": []}), "todos")
        self.assertEqual(summarize_tool_input("Custom", {"n": 1, "q": "hello\nworld"}), "hello")
        self.assertEqual(summarize_tool_input("Custom", {"n": 1}), "")
        self.assertLessEqual(len(summarize_tool_input("Bash", {"command": "x" * 300})), 60)
        self.assertEqual(summarize_tool_input("Read", "garbage"), "")  # type: ignore[arg-type]


class StateTests(unittest.TestCase):
    def test_no_lines_is_idle(self):
        self.assertEqual(make().derive_state(T0, T0), State.IDLE)

    def test_prompt_is_thinking(self):
        tr = make()
        feed(tr, user_prompt(T0, "Do it"))
        self.assertEqual(tr.derive_state(T0 + 1, T0), State.THINKING)
        self.assertEqual(tr.last_prompt, "Do it")
        self.assertEqual(tr.cwd, "/home/x/proj")

    def test_open_tool_use_is_working(self):
        tr = make()
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "Edit", {"file_path": "/p/a.py"}))
        self.assertEqual(tr.derive_state(T0 + 2, T0 + 1), State.WORKING)
        a = tr.to_agent(T0 + 2, T0 + 1)
        self.assertEqual((a.tool, a.detail, a.tool_count), ("Edit", "a.py", 1))

    def test_tool_result_back_to_thinking(self):
        tr = make()
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "Read"), tool_result(T0 + 2, "t1"))
        self.assertEqual(tr.derive_state(T0 + 3, T0 + 2), State.THINKING)
        a = tr.to_agent(T0 + 3, T0 + 2)
        self.assertIsNone(a.tool)
        self.assertIsNone(a.detail)

    def test_interactive_tool_is_waiting(self):
        tr = make()
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "AskUserQuestion", {"questions": []}))
        self.assertEqual(tr.derive_state(T0 + 1.5, T0 + 1), State.WAITING)
        tr2 = make()
        feed(tr2, user_prompt(T0), tool_use(T0 + 1, "t1", "ExitPlanMode", {"plan": "x"}))
        self.assertEqual(tr2.derive_state(T0 + 1.5, T0 + 1), State.WAITING)

    def test_unanswered_fast_tool_becomes_waiting_after_8s(self):
        tr = make()
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "Edit", {"file_path": "/x/y.py"}))
        self.assertEqual(tr.derive_state(T0 + 8.9, T0 + 1), State.WORKING)       # 7.9 s
        self.assertEqual(tr.derive_state(T0 + 9.0, T0 + 1), State.WAITING)       # 8 s
        # file still being written (silence < 8 s) → still working
        self.assertEqual(tr.derive_state(T0 + 20, T0 + 15), State.WORKING)
        a = tr.to_agent(T0 + 9, T0 + 1)
        self.assertEqual((a.state, a.tool, a.detail), (State.WAITING, "Edit", "y.py"))

    def test_long_running_tool_stays_working(self):
        for name in ("Bash", "Agent", "Task", "WebFetch", "WebSearch", "Monitor", "BashOutput"):
            tr = make()
            feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", name, {"command": "sleep 100"}))
            self.assertEqual(tr.derive_state(T0 + 120, T0 + 1), State.WORKING, name)

    def test_most_recent_pending_tool_decides(self):
        tr = make()
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "Bash"), tool_use(T0 + 1, "t2", "AskUserQuestion"))
        self.assertEqual(tr.derive_state(T0 + 2, T0 + 1), State.WAITING)
        feed(tr, tool_result(T0 + 3, "t2"))
        self.assertEqual(tr.derive_state(T0 + 4, T0 + 3), State.WORKING)
        self.assertEqual(tr.to_agent(T0 + 4, T0 + 3).tool, "Bash")

    def test_end_turn_idle_then_sleeping(self):
        tr = make()
        feed(tr, user_prompt(T0), assistant_text(T0 + 1, "thinking aloud"), assistant_text(T0 + 2, "Done!", "end_turn"))
        self.assertEqual(tr.derive_state(T0 + 3, T0 + 2), State.IDLE)
        self.assertEqual(tr.derive_state(T0 + 2 + 179, T0 + 2), State.IDLE)
        self.assertEqual(tr.derive_state(T0 + 2 + 180, T0 + 2), State.SLEEPING)
        self.assertEqual(tr.last_message, "Done!")
        self.assertEqual(tr.model, "claude-opus-5-5")

    def test_streaming_assistant_is_thinking(self):
        tr = make()
        feed(tr, user_prompt(T0), assistant_text(T0 + 1, "Let me look", None))
        self.assertEqual(tr.derive_state(T0 + 2, T0 + 1), State.THINKING)

    def test_other_end_reasons_and_api_error(self):
        for stop in ("stop_sequence", "max_tokens"):
            tr = make()
            feed(tr, user_prompt(T0), assistant_text(T0 + 1, "x", stop))
            self.assertEqual(tr.derive_state(T0 + 2, T0 + 1), State.IDLE, stop)
        tr = make()
        err = assistant_text(T0 + 1, "API Error: overloaded", None)
        err["isApiErrorMessage"] = True
        err["message"]["model"] = "<synthetic>"
        feed(tr, user_prompt(T0), err)
        self.assertEqual(tr.derive_state(T0 + 2, T0 + 1), State.IDLE)
        self.assertIsNone(tr.model)

    def test_subagent_end_turn_is_sleeping(self):
        tr = make(sub=True)
        feed(tr, user_prompt(T0), assistant_text(T0 + 1, "report", "end_turn"))
        self.assertEqual(tr.derive_state(T0 + 2, T0 + 1), State.SLEEPING)

    def test_cost_state_is_sleeping_until_new_activity(self):
        tr = make()
        feed(tr, user_prompt(T0), assistant_text(T0 + 1, "bye", "end_turn"), {"type": "cost-state", "costUSD": 1})
        self.assertEqual(tr.derive_state(T0 + 2, T0 + 1), State.SLEEPING)
        feed(tr, user_prompt(T0 + 10, "again"))
        self.assertEqual(tr.derive_state(T0 + 11, T0 + 10), State.THINKING)

    def test_silence_is_sleeping(self):
        tr = make()
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "Bash"))
        self.assertEqual(tr.derive_state(T0 + 599, T0), State.WORKING)
        self.assertEqual(tr.derive_state(T0 + 600, T0), State.SLEEPING)

    def test_interrupt(self):
        tr = make()
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "Edit"),
             user_text_block(T0 + 2, "[Request interrupted by user for tool use]"))
        self.assertEqual(tr.pending, {})
        self.assertEqual(tr.derive_state(T0 + 3, T0 + 2), State.IDLE)
        self.assertEqual(tr.derive_state(T0 + 200, T0 + 2), State.SLEEPING)
        # Interrupt as a plain string content too; must not overwrite last_prompt
        tr2 = make()
        feed(tr2, user_prompt(T0, "real prompt"), tool_use(T0 + 1, "t1", "Bash"),
             user_prompt(T0 + 2, "[Request interrupted by user]"))
        self.assertEqual(tr2.derive_state(T0 + 3, T0 + 2), State.IDLE)
        self.assertEqual(tr2.last_prompt, "real prompt")

    def test_new_prompt_clears_pending(self):
        tr = make()
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "Edit"), user_prompt(T0 + 5, "never mind"))
        self.assertEqual(tr.pending, {})
        self.assertEqual(tr.derive_state(T0 + 30, T0 + 5), State.THINKING)

    def test_meta_and_ignored_lines(self):
        tr = make()
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "Bash"))
        feed(tr, user_prompt(T0 + 2, "<system-reminder>", isMeta=True),
             {"type": "attachment", "timestamp": iso(T0 + 2)},
             {"type": "queue-operation", "timestamp": iso(T0 + 2)},
             {"type": "file-history-snapshot"}, {"type": "mode"}, {"type": "weird-new-type"})
        self.assertEqual(tr.derive_state(T0 + 3, T0 + 2), State.WORKING)
        self.assertEqual(tr.last_prompt, "Fix the bug please")

    def test_metadata_lines(self):
        tr = make()
        feed(tr, {"type": "ai-title", "aiTitle": "Pixel Office"}, {"type": "last-prompt", "lastPrompt": "hello\nthere"})
        a = tr.to_agent(T0, T0)
        self.assertEqual(a.title, "Pixel Office")
        self.assertEqual(a.last_prompt, "hello there")
        self.assertEqual(a.state, State.IDLE)
        self.assertEqual(a.project, "proj")         # fallback from project dir name
        self.assertIsNone(a.project_path)

    def test_weird_input_never_crashes(self):
        tr = make()
        junk = [{}, {"type": "user"}, {"type": "user", "message": None}, {"type": "user", "message": {"content": 5}},
                {"type": "assistant", "message": {"content": [1, None, {"type": "tool_use"}]}},
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": 3, "name": None, "input": []}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": None}]}},
                {"type": "user", "timestamp": "nope", "message": {"content": "hi"}}, {"type": 7}]
        for j in junk:
            tr.feed(j, T0)
        tr.to_agent(T0 + 1, T0)

    def test_to_agent_fields_and_state_since(self):
        tr = SessionTracker("sess-1:abc", Path("/x"), is_subagent=True, parent_id="sess-1",
                            project_dir_name="-home-x-proj", meta={"agentType": "Explore", "description": "Find it"})
        feed(tr, user_prompt(T0), tool_use(T0 + 1, "t1", "Grep", {"pattern": "foo"}))
        a = tr.to_agent(T0 + 2, T0 + 1)
        d = a.to_dict()
        self.assertEqual(d["state"], "working")
        self.assertEqual(d["agentType"], "Explore")
        self.assertEqual(d["title"], "Find it")
        self.assertEqual(d["parentId"], "sess-1")
        self.assertTrue(d["isSubagent"])
        self.assertEqual(d["project"], "proj")
        self.assertEqual(d["projectPath"], "/home/x/proj")
        self.assertEqual(d["startedAt"], int(T0 * 1000))
        self.assertEqual(d["stateSince"], int((T0 + 1) * 1000))
        self.assertEqual(d["updatedAt"], int((T0 + 1) * 1000))
        # same state later → stateSince unchanged
        self.assertEqual(tr.to_agent(T0 + 5, T0 + 1).state_since, int((T0 + 1) * 1000))
        expected_keys = {"id", "name", "project", "projectPath", "title", "state", "tool", "detail", "lastMessage",
                         "lastPrompt", "isSubagent", "parentId", "agentType", "model", "colorSeed", "toolCount",
                         "startedAt", "stateSince", "updatedAt"}
        self.assertEqual(set(d), expected_keys)


if __name__ == "__main__":
    unittest.main()
