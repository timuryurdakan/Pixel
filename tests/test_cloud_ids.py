import base64
import hashlib
import hmac
import re
import unittest

import _helpers  # noqa: F401  (sys.path)

from pixel_cloud import ids


class IdsTests(unittest.TestCase):
    def test_room_id_format(self):
        seen = set()
        for _ in range(200):
            r = ids.new_room_id()
            self.assertRegex(r, r"^[a-z2-7]{26}$")
            self.assertTrue(ids.valid_room_id(r))
            seen.add(r)
        self.assertEqual(len(seen), 200)
        for bad in ("", "A" * 26, "a" * 25, "a" * 27, "0" * 26, "abcdefghijklmnopqrstuvwxy1", None, 5, "a" * 25 + "\n"):
            self.assertFalse(ids.valid_room_id(bad), bad)

    def test_write_key_hmac(self):
        secret = b"s" * 40
        room = ids.new_room_id()
        k = ids.write_key(secret, room)
        mac = hmac.new(secret, b"pixel-write:v1:" + room.encode(), hashlib.sha256).digest()
        self.assertEqual(k, base64.urlsafe_b64encode(mac).decode()[:32])
        self.assertRegex(k, r"^[A-Za-z0-9_-]{32}$")
        self.assertTrue(ids.check_write_key(secret, room, k))
        self.assertFalse(ids.check_write_key(secret, room, k[:-1] + ("A" if k[-1] != "A" else "B")))
        self.assertFalse(ids.check_write_key(b"t" * 40, room, k))          # other secret → rejected
        self.assertFalse(ids.check_write_key(secret, ids.new_room_id(), k))  # other room
        self.assertFalse(ids.check_write_key(secret, room, None))
        self.assertFalse(ids.check_write_key(secret, room, ""))
        self.assertFalse(ids.check_write_key(None, room, k))
        self.assertFalse(ids.check_write_key(secret, room, "ğ" * 32))      # non-ASCII never raises

    def test_bearer(self):
        self.assertEqual(ids.bearer({"Authorization": "Bearer abc"}), "abc")
        self.assertEqual(ids.bearer({"authorization": "bearer   abc  "}), "abc")
        self.assertIsNone(ids.bearer({"Authorization": "Basic abc"}))
        self.assertIsNone(ids.bearer({"Authorization": "Bearer "}))
        self.assertIsNone(ids.bearer({}))

    def test_agent_ids(self):
        a = ids.agent_id("r" * 26, "o_1234", "s1", None)
        self.assertRegex(a, r"^a[0-9a-f]{16}$")
        self.assertEqual(a, ids.agent_id("r" * 26, "o_1234", "s1", ""))
        self.assertNotEqual(a, ids.agent_id("r" * 26, "o_1234", "s1", "x"))
        self.assertNotEqual(a, ids.agent_id("q" * 26, "o_1234", "s1", None))
        self.assertEqual(a, "a" + hashlib.sha256(("r" * 26 + "|o_1234|s1|").encode()).hexdigest()[:16])
        self.assertEqual(ids.local_agent_id("o_1", "s", None),
                         "r" + hashlib.sha256(b"local|o_1|s|").hexdigest()[:16])
        self.assertEqual(ids.default_owner_id("Timur"), ids.default_owner_id("TIMUR"))
        self.assertTrue(re.match(r"^n_[0-9a-f]{10}$", ids.default_owner_id("Timur")))

    def test_constant_time_eq(self):
        self.assertTrue(ids.constant_time_eq("abc", "abc"))
        self.assertFalse(ids.constant_time_eq("abc", "abd"))
        self.assertFalse(ids.constant_time_eq(None, "abc"))


if __name__ == "__main__":
    unittest.main()
