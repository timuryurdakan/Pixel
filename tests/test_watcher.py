import json
import os
import tempfile
import unittest
from pathlib import Path

from _helpers import T0, assistant_text, dumps, tool_result, tool_use, user_prompt

from pixel_office import watcher as watcher_mod
from pixel_office.store import AgentStore
from pixel_office.watcher import FileTail, Watcher


class FileTailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.jsonl"
        self.path.write_bytes(b"")

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, data: bytes) -> None:
        with open(self.path, "ab") as f:
            f.write(data)

    def test_partial_lines_are_buffered(self):
        tail = FileTail(self.path)
        self.assertEqual(tail.read_new_lines(), [])
        self.append(b'{"a":1}\n{"b":')
        self.assertEqual(tail.read_new_lines(), ['{"a":1}'])
        self.assertEqual(tail.read_new_lines(), [])
        self.append(b'2}\n')
        self.assertEqual(tail.read_new_lines(), ['{"b":2}'])
        self.append(b'\n\r\n{"c":3}\r\n')
        self.assertEqual(tail.read_new_lines(), ['{"c":3}'])

    def test_utf8_split_across_reads(self):
        tail = FileTail(self.path)
        data = '{"t":"çay ☕"}\n'.encode()
        cut = data.index("☕".encode()) + 1
        self.append(data[:cut])
        self.assertEqual(tail.read_new_lines(), [])
        self.append(data[cut:])
        self.assertEqual(tail.read_new_lines(), ['{"t":"çay ☕"}'])

    def test_invalid_utf8_is_replaced(self):
        tail = FileTail(self.path)
        self.append(b'{"x":"\xff\xfe"}\n')
        lines = tail.read_new_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("�", lines[0])

    def test_initial_tail_drops_partial_first_line(self):
        self.append(b"A" * 100 + b"\n" + b"B" * 10 + b"\nC\n")
        tail = FileTail(self.path, initial_tail_bytes=8)
        self.assertEqual(tail.read_new_lines(), ["C"])     # "BBB…" was cut → dropped
        self.assertTrue(tail.skipped_head)

    def test_initial_tail_at_line_boundary_keeps_first_line(self):
        self.append(b"AAAA\nBBB\n")          # tail of 4 bytes starts exactly at "BBB\n"
        tail = FileTail(self.path, initial_tail_bytes=4)
        self.assertEqual(tail.read_new_lines(), ["BBB"])

    def test_truncation_restarts(self):
        tail = FileTail(self.path)
        self.append(b"one\ntwo\n")
        self.assertEqual(tail.read_new_lines(), ["one", "two"])
        self.path.write_bytes(b"x\n")
        self.assertEqual(tail.read_new_lines(), ["x"])

    def test_replacement_restarts(self):
        tail = FileTail(self.path)
        self.append(b"one\ntwo\nthree\n")
        tail.read_new_lines()
        new = Path(self.tmp.name) / "new"
        new.write_bytes(b"fresh line that is long enough\n")
        os.replace(new, self.path)
        self.assertEqual(tail.read_new_lines(), ["fresh line that is long enough"])

    def test_huge_line_is_dropped(self):
        old = watcher_mod.MAX_LINE_BYTES
        watcher_mod.MAX_LINE_BYTES = 50
        try:
            tail = FileTail(self.path)
            self.append(b"ok\n" + b"X" * 40)
            self.assertEqual(tail.read_new_lines(), ["ok"])
            self.append(b"X" * 40)                       # buffer > 50 → discarded, skip to newline
            self.assertEqual(tail.read_new_lines(), [])
            self.append(b"X" * 10 + b"\nnext\n")
            self.assertEqual(tail.read_new_lines(), ["next"])
            self.append(b"Y" * 60 + b"\nafter\n")        # complete oversized line in one read
            self.assertEqual(tail.read_new_lines(), ["after"])
        finally:
            watcher_mod.MAX_LINE_BYTES = old

    def test_missing_file_raises(self):
        tail = FileTail(Path(self.tmp.name) / "nope.jsonl")
        with self.assertRaises(FileNotFoundError):
            tail.read_new_lines()


class WatcherScanTests(unittest.TestCase):
    """Drives Watcher.scan(now) with fake time against a temporary projects dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proj = self.root / "-home-x-proj"
        self.proj.mkdir()
        self.store = AgentStore("live")
        self.w = Watcher(self.root, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path: Path, text: str, mtime: float, append: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a" if append else "w", encoding="utf-8") as f:
            f.write(text)
        os.utime(path, (mtime, mtime))

    def test_discovery_states_and_removal(self):
        main = self.proj / "sess1.jsonl"
        self.write(main, dumps(user_prompt(T0), tool_use(T0 + 1, "t1", "Bash", {"command": "make"})), T0 + 1)
        old = self.proj / "old.jsonl"
        self.write(old, dumps(user_prompt(T0 - 5000)), T0 - 5000)
        self.write(self.proj / "garbage.jsonl", "{not json\n\x00\x01\n[1]\n", T0)

        self.w.scan(T0 + 2)
        self.assertIn("sess1", self.store.ids())
        self.assertNotIn("old", self.store.ids())
        a = self.store.get("sess1")
        self.assertEqual((a.state, a.tool, a.detail, a.project), ("working", "Bash", "make", "proj"))

        self.write(main, dumps(tool_result(T0 + 2.5, "t1"), assistant_text(T0 + 3, "done", "end_turn")), T0 + 3)
        self.w.scan(T0 + 4)
        self.assertEqual(self.store.get("sess1").state, "idle")
        self.w.scan(T0 + 3 + 180)
        self.assertEqual(self.store.get("sess1").state, "sleeping")
        self.w.scan(T0 + 3 + 1800)
        self.assertNotIn("sess1", self.store.ids())
        # not re-added while unchanged…
        self.w.scan(T0 + 3 + 1810)
        self.assertNotIn("sess1", self.store.ids())
        # …but re-added once written again
        self.write(main, dumps(user_prompt(T0 + 4000, "back")), T0 + 4000)
        self.w.scan(T0 + 4001)
        self.assertEqual(self.store.get("sess1").state, "thinking")

    def test_removed_memo_forgets_deleted_files(self):
        main = self.proj / "sess1.jsonl"
        self.write(main, dumps(user_prompt(T0)), T0)
        self.w.scan(T0 + 1)
        self.w.scan(T0 + 1800)                     # silent → removed, memo kept
        self.assertNotIn("sess1", self.store.ids())
        self.assertIn(main, self.w._removed)
        main.unlink()
        self.w.scan(T0 + 1810)                     # next discovery prunes the memo
        self.assertNotIn(main, self.w._removed)

    def test_file_disappears(self):
        main = self.proj / "sess1.jsonl"
        self.write(main, dumps(user_prompt(T0)), T0)
        self.w.scan(T0 + 1)
        self.assertIn("sess1", self.store.ids())
        main.unlink()
        self.w.scan(T0 + 2)
        self.assertNotIn("sess1", self.store.ids())

    def test_subagents(self):
        main = self.proj / "sess1.jsonl"
        self.write(main, dumps(user_prompt(T0), tool_use(T0 + 1, "t1", "Agent", {"description": "Explore"})), T0 + 1)
        sub = self.proj / "sess1" / "subagents" / "agent-abc.jsonl"
        self.write(sub, dumps(user_prompt(T0 + 1, "explore stuff"), tool_use(T0 + 2, "u1", "Grep", {"pattern": "x"})), T0 + 2)
        (sub.parent / "agent-abc.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Explore"}))
        (sub.parent / "agent-bad.meta.json").write_text("{nope")
        self.write(sub.parent / "agent-bad.jsonl", dumps(user_prompt(T0 + 1)), T0 + 1)

        self.w.scan(T0 + 3)
        s = self.store.get("sess1:abc")
        self.assertIsNotNone(s)
        self.assertEqual((s.is_subagent, s.parent_id, s.agent_type, s.title, s.state),
                         (True, "sess1", "Explore", "Explore", "working"))
        self.assertIsNotNone(self.store.get("sess1:bad"))          # bad meta → still tracked

        # effective_last_write: parent quiet but subagent active → parent not asleep
        self.write(sub, dumps(tool_result(T0 + 650, "u1")), T0 + 650)
        self.w.scan(T0 + 655)
        self.assertEqual(self.store.get("sess1").state, "working")
        self.assertEqual(self.store.get("sess1").updated_at, int((T0 + 650) * 1000))

        # subagent finishes → sleeping, removed after 120 s of sleep
        self.write(sub, dumps(assistant_text(T0 + 660, "report", "end_turn")), T0 + 660)
        self.w.scan(T0 + 661)
        self.assertEqual(self.store.get("sess1:abc").state, "sleeping")
        self.w.scan(T0 + 660 + 121)
        self.assertNotIn("sess1:abc", self.store.ids())
        self.assertIn("sess1", self.store.ids())

    def test_subagent_finished_via_parent_tool_result(self):
        """Subagent files lack the final report; the parent's tool_result marks them done."""
        main = self.proj / "sess1.jsonl"
        self.write(main, dumps(user_prompt(T0), tool_use(T0 + 1, "toolu_A", "Agent", {"description": "Explore"})), T0 + 1)
        sub = self.proj / "sess1" / "subagents" / "agent-abc.jsonl"
        self.write(sub, dumps(user_prompt(T0 + 1, "go"), tool_use(T0 + 2, "u1", "Read"), tool_result(T0 + 3, "u1")), T0 + 3)
        (sub.parent / "agent-abc.meta.json").write_text(json.dumps({"agentType": "Explore", "toolUseId": "toolu_A"}))
        self.w.scan(T0 + 4)
        self.assertEqual(self.store.get("sess1:abc").state, "thinking")
        self.write(main, dumps(tool_result(T0 + 5, "toolu_A")), T0 + 5)
        self.w.scan(T0 + 6)
        s = self.store.get("sess1:abc")
        self.assertEqual(s.state, "sleeping")
        self.assertEqual(s.state_since, int((T0 + 5) * 1000))
        self.assertEqual(self.store.get("sess1").state, "thinking")
        # resumed later (e.g. SendMessage) → awake again
        self.write(sub, dumps(user_prompt(T0 + 30, "one more thing")), T0 + 30)
        self.w.scan(T0 + 31)
        self.assertEqual(self.store.get("sess1:abc").state, "thinking")

    def test_subagent_removed_with_parent(self):
        main = self.proj / "sess1.jsonl"
        self.write(main, dumps(user_prompt(T0)), T0)
        sub = self.proj / "sess1" / "subagents" / "agent-abc.jsonl"
        self.write(sub, dumps(user_prompt(T0)), T0)
        self.w.scan(T0 + 1)
        self.assertEqual(self.store.ids(), {"sess1", "sess1:abc"})
        main.unlink()
        self.w.scan(T0 + 2)
        self.assertEqual(self.store.ids(), set())

    def test_initial_tail_of_big_file(self):
        main = self.proj / "big.jsonl"
        filler = dumps(*[assistant_text(T0 + i * 0.001, "x" * 1000) for i in range(400)])  # ~500 KB
        self.write(main, dumps(user_prompt(T0 - 100, "first")) + filler + dumps(tool_use(T0 + 5, "t", "Read")), T0 + 5)
        self.w.scan(T0 + 6)
        a = self.store.get("big")
        self.assertEqual(a.state, "working")
        self.assertEqual(a.started_at, int((T0 - 100) * 1000))   # from file head probe

    def test_missing_projects_dir(self):
        w = Watcher(self.root / "nope", self.store)
        with self.assertLogs("pixel_office", "WARNING"):
            w.scan(T0)
        w.scan(T0 + 10)
        self.assertEqual(self.store.ids(), set())


if __name__ == "__main__":
    unittest.main()
