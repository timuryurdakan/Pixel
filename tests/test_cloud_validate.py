import json
import unittest

import _helpers  # noqa: F401  (sys.path)

from pixel_cloud import settings as S
from pixel_cloud.validate import (ValidationError, clean_name, clean_room_name, parse_cloud_rename,
                                  parse_local_rename, parse_status_body, parse_update, sanitize)

BASE = {"session": "s1", "owner": "Timur", "state": "working"}


def upd(**kw):
    d = dict(BASE)
    d.update(kw)
    return parse_update(d)


class SanitizeTests(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(sanitize("  a\t\nb   c ", 40), "a b c")
        self.assertEqual(sanitize("a\x00b\x07c\x7fd\x9fe", 40), "abcde")
        self.assertEqual(sanitize("ev\u202eil\u2066x\u2069\u200by\ufeff", 40), "evilxy")
        self.assertEqual(sanitize("e\u0301", 40), "\u00e9")                  # NFC
        self.assertEqual(sanitize("🙂" * 30, 24), "🙂" * 24)                  # code points, not bytes
        self.assertEqual(sanitize("abc   def", 4), "abc")                    # truncate, then trim
        self.assertEqual(sanitize("\u200b\u200b", 5), "")
        # H22: Cf category, Hangul fillers, tags
        self.assertEqual(sanitize("a\u3164b\u115Fc\u1160d\uFFA0e", 40), "abcde")
        self.assertEqual(sanitize("x\U000e0001\U000e0020y\U000e007Fz", 40), "xyz")
        self.assertEqual(sanitize("\u3164\u115F", 40), "")

    def test_clean_name(self):
        self.assertIsNone(clean_name(None))
        self.assertIsNone(clean_name(""))
        self.assertEqual(clean_name("  Dilek "), "Dilek")
        self.assertEqual(len(clean_name("x" * 100)), 24)
        with self.assertRaises(ValidationError):
            clean_name("\u200b\u202e ")
        with self.assertRaises(ValidationError):
            clean_name("\u3164\u115F")
        with self.assertRaises(ValidationError):
            clean_name("\U000e0020\U000e007F")
        with self.assertRaises(ValidationError):
            clean_name(5)
        self.assertEqual(clean_room_name(None), S.DEFAULT_ROOM_NAME)
        self.assertEqual(clean_room_name("   "), S.DEFAULT_ROOM_NAME)
        self.assertEqual(len(clean_room_name("k" * 99)), 32)


class UpdateTests(unittest.TestCase):
    def test_minimal_and_defaults(self):
        u = upd()
        self.assertEqual((u.session, u.owner, u.state, u.source, u.final), ("s1", "Timur", "working", "api", False))
        self.assertIsNone(u.owner_id)
        self.assertIsNone(u.sub)

    def test_required(self):
        for missing in ("session", "owner", "state"):
            d = dict(BASE)
            del d[missing]
            with self.assertRaises(ValidationError, msg=missing):
                parse_update(d)
        with self.assertRaises(ValidationError):
            upd(owner="\u200b")
        self.assertEqual(parse_update({"session": "s", "state": "idle"}, default_owner="me").owner, "me")

    def test_field_limits(self):
        u = upd(owner="o" * 50, project="p" * 50, label="l" * 80, agentType="t" * 50, name="n" * 50)
        self.assertEqual((len(u.owner), len(u.project), len(u.label), len(u.agent_type), len(u.name)),
                         (24, 32, 40, 24, 24))
        upd(tool="t" * 32)
        for bad in ({"tool": "t" * 33}, {"tool": "rm -rf"}, {"session": "s" * 65}, {"session": "a b"},
                    {"sub": "x/y"}, {"ownerId": "abc"}, {"ownerId": "o" * 33}, {"source": "Cursor"},
                    {"source": "s" * 17}, {"state": "dancing"}, {"state": None}, {"color": 16}, {"color": -1},
                    {"color": True}, {"color": "3"}, {"stateAgeMs": "1"}, {"toolCount": True},
                    {"final": "yes"}, {"final": True}, {"owner": 5}, {"label": ["x"]}, {"name": 3},
                    {"stateAgeMs": float("inf")}):
            with self.assertRaises(ValidationError, msg=bad):
                upd(**bad)
        self.assertTrue(upd(state="sleeping", final=True).final)

    def test_project_paths_rejected(self):
        for p in ("a/b", "C:\\x", "/abs"):
            with self.assertRaises(ValidationError):
                upd(project=p)
        self.assertEqual(upd(project=" Pixel ").project, "Pixel")

    def test_numbers_clamped(self):
        u = upd(stateAgeMs=10**12, startedAgeMs=-5, toolCount=2.7)
        self.assertEqual((u.state_age_ms, u.started_age_ms, u.tool_count), (86_400_000, 0, 2))

    def test_strings_sanitised_everywhere(self):
        u = upd(owner="Ti\u202emur\x00", label="Der\u200bleme\n yapıyor", agentType="Özel\u2066 ajan", name="Kâ\x1btip")
        self.assertEqual((u.owner, u.label, u.agent_type, u.name), ("Timur", "Derleme yapıyor", "Özel ajan", "Kâtip"))

    def test_body(self):
        self.assertEqual(len(parse_status_body(json.dumps(BASE).encode())), 1)
        batch = {"agents": [dict(BASE, session=f"s{i}") for i in range(S.MAX_BATCH)]}
        self.assertEqual(len(parse_status_body(json.dumps(batch).encode())), S.MAX_BATCH)
        batch["agents"].append(dict(BASE))
        for bad in (json.dumps(batch).encode(), b"[]", b"null", b"{", b"\xff\xfe", json.dumps({"agents": []}).encode(),
                    json.dumps({"agents": {"a": 1}}).encode(), json.dumps({"agents": [1]}).encode()):
            with self.assertRaises(ValidationError, msg=bad[:30]):
                parse_status_body(bad)

    def test_unknown_fields_dropped(self):
        u = upd(prompt="secret", tool_input={"x": 1}, extra=1)
        self.assertFalse(any("secret" in str(getattr(u, f)) for f in u.__dataclass_fields__))

    def test_rename_parsers(self):
        r = parse_cloud_rename(b'{"scope":"agent","agentId":"a0123456789abcdef","name":" Dilek "}')
        self.assertEqual((r.scope, r.target, r.name), ("agent", "a0123456789abcdef", "Dilek"))
        r = parse_cloud_rename(b'{"scope":"owner","ownerId":"o_Xk3abc","name":null}')
        self.assertEqual((r.scope, r.name), ("owner", None))
        for bad in (b'{"scope":"agent","agentId":"A0123456789abcdef","name":"x"}',
                    b'{"scope":"agent","agentId":"a0123","name":"x"}', b'{"scope":"owner","ownerId":"x","name":"x"}',
                    b'{"scope":"project","agentId":"a0123456789abcdef"}', b'{"scope":"agent","agentId":"a0123456789abcdef","name":"\\u200b"}',
                    b'[]'):
            with self.assertRaises(ValidationError, msg=bad):
                parse_cloud_rename(bad)
        r = parse_local_rename(b'{"id":"abc","name":"Dilek","scope":"project"}')
        self.assertEqual((r.scope, r.target, r.name), ("project", "abc", "Dilek"))
        self.assertEqual(parse_local_rename(b'{"id":"abc","name":""}').name, None)
        for bad in (b'{"name":"x"}', b'{"id":"a","scope":"owner"}', b'{"id":""}'):
            with self.assertRaises(ValidationError):
                parse_local_rename(bad)


if __name__ == "__main__":
    unittest.main()
