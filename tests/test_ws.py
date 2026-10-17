"""pixel_connector/ws.py: RFC 6455 framing details not covered by the relay conformance suite (V3.md §2, Phase 12)."""

import ast
import socket
import struct
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import ws as W

ROOT = Path(__file__).resolve().parent.parent


def pair(max_message=W.DEFAULT_MAX_MESSAGE):
    s1, s2 = socket.socketpair()
    return W.WebSocket(s1, client=True, max_message=max_message), W.WebSocket(s2, client=False, max_message=max_message)


class FramingTests(unittest.TestCase):
    def test_accept_key_rfc_example(self):
        self.assertEqual(W.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_text_binary_and_lengths(self):
        c, s = pair()
        for n in (0, 1, 125, 126, 65535, 65536, 200_000):
            c.send_text("é" * (n // 2))
            self.assertEqual(s.recv(timeout=2), "é" * (n // 2))
            s.send_bytes(b"\x01" * n)
            self.assertEqual(c.recv(timeout=2), b"\x01" * n)

    def test_client_frames_are_masked_and_server_frames_are_not(self):
        c, s = pair()
        c.send_text("hi")
        raw = s.sock.recv(100)
        self.assertTrue(raw[1] & 0x80)
        s.send_text("yo")
        raw = c.sock.recv(100)
        self.assertEqual(raw, b"\x81\x02yo")

    def test_unmasked_client_frame_is_a_protocol_error(self):
        c, s = pair()
        c.sock.sendall(b"\x81\x02hi")             # a client must mask
        with self.assertRaises(W.ConnectionClosed) as cm:
            s.recv(timeout=2)
        self.assertEqual(cm.exception.code, W.CLOSE_PROTOCOL)

    def test_ping_is_answered_and_pong_skipped(self):
        c, s = pair()
        s.ping(b"abc")
        with self.assertRaises(socket.timeout):
            c.recv(timeout=0.2)                    # answers the ping, no data message
        fin, op, payload = s._read_frame()
        self.assertEqual((op, payload), (W.OP_PONG, b"abc"))
        c.send_text("after")
        self.assertEqual(s.recv(timeout=2), "after")

    def test_fragments_and_interleaved_ping(self):
        c, s = pair()
        c._send_frame(W.OP_TEXT, b"he", fin=False)
        c._send_frame(W.OP_PING, b"")
        c._send_frame(W.OP_CONT, b"llo", fin=True)
        self.assertEqual(s.recv(timeout=2), "hello")

    def test_too_big_closes_1009(self):
        c, s = pair(max_message=10)
        c.send_text("x" * 11)
        with self.assertRaises(W.ConnectionClosed) as cm:
            s.recv(timeout=2)
        self.assertEqual(cm.exception.code, W.CLOSE_TOO_BIG)
        with self.assertRaises(W.ConnectionClosed) as cm:
            c.recv(timeout=2)
        self.assertEqual(cm.exception.code, W.CLOSE_TOO_BIG)

    def test_close_handshake_and_codes(self):
        c, s = pair()
        s.close(4009, "replaced")
        with self.assertRaises(W.ConnectionClosed) as cm:
            c.recv(timeout=2)
        self.assertEqual((cm.exception.code, cm.exception.reason), (4009, "replaced"))
        with self.assertRaises(W.ConnectionClosed) as cm:
            s.recv(timeout=2)                      # the client's echo
        self.assertEqual(cm.exception.code, 4009)
        with self.assertRaises(W.ConnectionClosed):
            c.send_text("late")

    def test_invalid_utf8_is_1007(self):
        c, s = pair()
        c._send_frame(W.OP_TEXT, b"\xff\xfe")
        with self.assertRaises(W.ConnectionClosed) as cm:
            s.recv(timeout=2)
        self.assertEqual(cm.exception.code, W.CLOSE_INVALID_DATA)

    def test_timeout_mid_frame_keeps_the_stream(self):
        c, s = pair()
        data = b"\x81" + bytes([0x80 | 5]) + b"\x00\x00\x00\x00" + b"hello"
        c.sock.sendall(data[:4])
        with self.assertRaises(socket.timeout):
            s.recv(timeout=0.1)
        c.sock.sendall(data[4:])
        self.assertEqual(s.recv(timeout=2), "hello")

    def test_python38_syntax_and_stdlib_only(self):
        src = (ROOT / "pixel_connector" / "ws.py").read_text(encoding="utf-8")
        tree = ast.parse(src, feature_version=(3, 8))
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertLessEqual(mods, {"__future__", "base64", "hashlib", "os", "socket", "ssl", "struct", "threading",
                                    "time", "typing", "urllib"})


if __name__ == "__main__":
    unittest.main()
