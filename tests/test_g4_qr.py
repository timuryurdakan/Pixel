"""G4: the terminal QR of `pixel-connector pair` (docs/GOREV_PANOSU.md G4, docs/V3.md §7.3).

The stdlib QR encoder (`pixel_connector/qr.py`) is checked against published vectors (Reed–Solomon codewords of the
ISO/IEC 18004 "HELLO WORLD" 1-M example, the level M format strings, the version 7–10 information strings) and by a
small independent decoder written here from the standard: it finds the format information, unmasks, reads the
codewords in the zigzag order, de-interleaves the blocks, checks every block's Reed–Solomon syndromes (evaluating the
codeword polynomial at α^0 … α^(n−1), a different computation from the encoder's division) and parses the byte
segment. Then the link: what the QR carries (room, code in the fragment; the write key only with `--qr-key`), that a
bad code or a missing config prints no QR and never breaks pairing, and the CLI output.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import __main__ as CLI
from pixel_connector import qr as Q

# -- independent reference decoder -----------------------------------------------------------------------------------
EXP, LOG = [0] * 512, [0] * 256
_v = 1
for _i in range(255):
    EXP[_i], LOG[_v] = _v, _i
    _v = (_v << 1) ^ (0x11D if _v & 0x80 else 0)
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def mul(a, b):
    return 0 if not a or not b else EXP[LOG[a] + LOG[b]]


def syndromes(block, count):
    """Codeword polynomial (first byte = highest power) evaluated at α^0 … α^(count−1); all zero for a valid RS
    codeword with `count` EC codewords."""
    out = []
    for i in range(count):
        acc = 0
        for c in block:
            acc = mul(acc, EXP[i]) ^ c
        out.append(acc)
    return out


# Thonky / ISO tables, typed in from the standard (not derived from the encoder)
FORMAT_M = {0: "101010000010010", 1: "101000100100101", 2: "101111001111100", 3: "101101101001011",
            4: "100010111111001", 5: "100000011001110", 6: "100111110010111", 7: "100101010100000"}
VERSION_INFO = {7: "000111110010010100", 8: "001000010110111100", 9: "001001101010011001",
                10: "001010010011010011"}
BLOCKS_M = {1: (10, [16]), 2: (16, [28]), 3: (26, [44]), 4: (18, [32] * 2), 5: (24, [43] * 2), 6: (16, [27] * 4),
            7: (18, [31] * 4), 8: (22, [38, 38, 39, 39]), 9: (22, [36] * 3 + [37] * 2),
            10: (26, [43] * 4 + [44])}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
         9: [6, 26, 46], 10: [6, 28, 50]}
MASKS = [lambda i, j: (i + j) % 2 == 0, lambda i, j: i % 2 == 0, lambda i, j: j % 3 == 0,
         lambda i, j: (i + j) % 3 == 0, lambda i, j: (i // 2 + j // 3) % 2 == 0,
         lambda i, j: (i * j) % 2 + (i * j) % 3 == 0, lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
         lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0]


def reserved(version):
    n = 17 + 4 * version
    r = [[False] * n for _ in range(n)]
    for y in range(n):
        for x in range(n):
            if (x < 9 and y < 9) or (x >= n - 8 and y < 9) or (x < 9 and y >= n - 8) or x == 6 or y == 6:
                r[y][x] = True
    pos = ALIGN[version]
    for cy in pos:
        for cx in pos:
            if (cx < 9 and cy < 9) or (cx > n - 9 and cy < 9) or (cx < 9 and cy > n - 9):
                continue
            for y in range(cy - 2, cy + 3):
                for x in range(cx - 2, cx + 3):
                    r[y][x] = True
    if version >= 7:
        for a in range(n - 11, n - 8):
            for b in range(6):
                r[b][a] = r[a][b] = True
    return r


def decode(m):
    n = len(m)
    version = (n - 17) // 4
    bits = "".join("1" if m[8][x] else "0" for x in (0, 1, 2, 3, 4, 5, 7, 8))
    bits += "".join("1" if m[y][8] else "0" for y in (7, 5, 4, 3, 2, 1, 0))
    mask = next(k for k, v in FORMAT_M.items() if v == bits)          # StopIteration = not level M / broken
    second = "".join("1" if m[y][8] else "0" for y in range(n - 1, n - 8, -1))
    second += "".join("1" if m[8][x] else "0" for x in range(n - 8, n))
    assert second == bits, "format copies differ"
    assert m[n - 8][8], "dark module"
    if version >= 7:
        got = "".join("1" if m[i // 3][n - 11 + i % 3] else "0" for i in range(17, -1, -1))
        assert got == VERSION_INFO[version], (version, got)
    res = reserved(version)
    stream, col, up = [], n - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if up else range(n)
        for y in rows:
            for x in (col, col - 1):
                if not res[y][x]:
                    stream.append(int(m[y][x] ^ MASKS[mask](y, x)))
        col -= 2
        up = not up
    words = [int("".join(map(str, stream[i:i + 8])), 2) for i in range(0, len(stream) - 7, 8)]
    ec_len, sizes = BLOCKS_M[version]
    blocks = [[] for _ in sizes]
    k = 0
    for i in range(max(sizes)):
        for b, size in enumerate(sizes):
            if i < size:
                blocks[b].append(words[k])
                k += 1
    for i in range(ec_len):
        for b in blocks:
            b.append(words[k])
            k += 1
    for b in blocks:
        assert syndromes(b, ec_len) == [0] * ec_len, "RS check failed"
    data = [w for b, size in zip(blocks, sizes) for w in b[:size]]
    bitstr = "".join("{:08b}".format(w) for w in data)
    assert bitstr[:4] == "0100", "not byte mode"
    cbits = 8 if version <= 9 else 16
    length = int(bitstr[4:4 + cbits], 2)
    body = bitstr[4 + cbits:4 + cbits + 8 * length]
    return bytes(int(body[i:i + 8], 2) for i in range(0, len(body), 8)), version, mask


# -- tests -------------------------------------------------------------------------------------------------------
class VectorTests(unittest.TestCase):
    def test_reed_solomon_hello_world_1m(self):
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        self.assertEqual(Q.rs_remainder(data, 10), [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])
        self.assertEqual(syndromes(data + Q.rs_remainder(data, 10), 10), [0] * 10)
        self.assertNotEqual(syndromes(data + [0] * 10, 10), [0] * 10)

    def test_format_and_version_strings(self):
        for mask, want in FORMAT_M.items():
            self.assertEqual("{:015b}".format(Q.format_bits(mask)), want)
        for v, want in VERSION_INFO.items():
            self.assertEqual("{:018b}".format(Q.version_bits(v)), want)

    def test_byte_mode_codewords(self):
        # 1-M, "hi": 0100 00000010 01101000 01101001 0000 then pad bytes
        self.assertEqual(Q.data_codewords(b"hi", 1)[:5], [0x40, 0x26, 0x86, 0x90, 0xEC])
        self.assertEqual(len(Q.data_codewords(b"hi", 1)), 16)


class RoundTripTests(unittest.TestCase):
    def test_every_version_and_mask_decodes(self):
        for length in (1, 14, 26, 42, 62, 84, 106, 122, 152, 180, 213):
            payload = bytes((i * 37 + length) % 256 for i in range(length))
            v = Q.pick_version(length)
            for mask in range(8):
                got, version, m = decode(Q.encode(payload, mask=mask))
                self.assertEqual((got, version, m), (payload, v, mask))

    def test_versions_capacity_and_too_long(self):
        self.assertEqual([Q.pick_version(n) for n in (14, 15, 26, 84, 85, 213)], [1, 2, 2, 5, 6, 10])
        with self.assertRaises(Q.QrError):
            Q.pick_version(214)
        with self.assertRaises(Q.QrError):
            Q.encode("str")

    def test_render_has_quiet_zone_and_two_rows_per_line(self):
        m = Q.encode(b"https://example.com/")
        plain = Q.render_half_blocks(m, ansi=False).split("\n")
        size = len(m) + 2 * Q.QUIET_ZONE
        self.assertEqual(len(plain), (size + 1) // 2)
        self.assertTrue(all(len(line) == size for line in plain))
        self.assertEqual(plain[0].strip(), "")
        self.assertTrue(set("".join(plain)) <= set(" ▀▄█"))
        back = [[False] * size for _ in range(size + 1)]
        for y, line in enumerate(plain):
            for x, ch in enumerate(line):
                back[2 * y][x] = ch in "▀█"
                back[2 * y + 1][x] = ch in "▄█"
        q = Q.QUIET_ZONE
        self.assertEqual([r[q:q + len(m)] for r in back[q:q + len(m)]], m)
        coloured = Q.render_half_blocks(m, ansi=True).split("\n")
        self.assertTrue(all(c.startswith("\x1b[30;47m") and c.endswith("\x1b[0m") for c in coloured))


ROOM = "abcdefghijklmnopqrstuvwxyz"
KEY = "K" * 32
CFG = {"server": "https://www.pixeloffice.com.tr", "room": ROOM, "key": KEY}


class LinkTests(unittest.TestCase):
    def test_link_carries_room_and_code_in_the_fragment_only(self):
        link = CLI.pair_link(CFG, "k7q2-m4x5-z6a9")
        self.assertEqual(link, "https://www.pixeloffice.com.tr/?room=%s#pair=K7Q2M4X5Z6A9" % ROOM)
        self.assertNotIn(KEY, link)
        before, frag = link.split("#", 1)
        self.assertNotIn("K7Q2", before)                       # never in the part a server receives
        got, version, _ = decode(Q.encode(link.encode("ascii")))
        self.assertEqual((got.decode(), version), (link, 5))

    def test_key_only_on_request(self):
        link = CLI.pair_link(CFG, "K7Q2M4X5Z6A9", with_key=True)
        self.assertEqual(link.split("#", 1)[1], "key=%s&pair=K7Q2M4X5Z6A9" % KEY)
        self.assertLessEqual(Q.pick_version(len(link)), 7)

    def test_malformed_code_is_refused(self):
        for bad in ("", "K7Q2", "K7Q2M4X5Z6A9X", "K7Q2-M4X5-Z6A!", "K7Q2M4X5Z6A9&key=x", "<script>"):
            with self.assertRaises(ValueError):
                CLI.pair_link(CFG, bad)


def _write_cfg(home):
    os.makedirs(home, exist_ok=True)
    from pixel_connector import config as C
    raw = C.initial_config(CFG["server"], ROOM, KEY, "Timur", "Laptop")
    with open(os.path.join(home, C.CONFIG_FILE), "w", encoding="utf-8") as f:
        json.dump(raw, f)


class CliTests(unittest.TestCase):
    START = (200, {"state": "open", "code": "K7Q2-M4X5-Z6A9"})

    def run_pair(self, args):
        seq = [self.START, (200, {"state": "expired"})]
        out = io.StringIO()
        with mock.patch.object(CLI, "ctl_request", side_effect=lambda *a, **k: seq.pop(0)), redirect_stdout(out):
            rc = CLI.cmd_pair(args, ask=lambda q: True)
        return rc, out.getvalue()

    def qr_lines(self, text):
        return [line for line in text.split("\n") if line and set(line) <= set(" ▀▄█") and len(line) > 30]

    def parse_args(self, home, *extra):
        return CLI.build_parser().parse_args(["--home", home, "pair"] + list(extra))

    def test_qr_printed_and_it_decodes_to_the_link(self):
        with tempfile.TemporaryDirectory() as home:
            _write_cfg(home)
            rc, out = self.run_pair(self.parse_args(home))
        self.assertEqual(rc, 1)                                 # expired: unchanged result handling
        self.assertIn("K7Q2-M4X5-Z6A9", out)
        self.assertIn("kamerasıyla okutun", out)
        self.assertNotIn(KEY, out)
        self.assertNotIn("\x1b[", out)                          # not a terminal: no colour codes
        lines = self.qr_lines(out)
        size = len(lines[0])
        grid = [[False] * size for _ in range(2 * len(lines))]
        for y, line in enumerate(lines):
            for x, ch in enumerate(line):
                grid[2 * y][x] = ch in "▀█"
                grid[2 * y + 1][x] = ch in "▄█"
        q, n = Q.QUIET_ZONE, size - 2 * Q.QUIET_ZONE
        got, _, _ = decode([r[q:q + n] for r in grid[q:q + n]])
        self.assertEqual(got.decode(), CLI.pair_link(CFG, "K7Q2M4X5Z6A9"))

    def test_qr_key_warns_and_no_qr_prints_none(self):
        with tempfile.TemporaryDirectory() as home:
            _write_cfg(home)
            _, out = self.run_pair(self.parse_args(home, "--qr-key"))
            self.assertIn("oda anahtarını da içerir", out)
            self.assertTrue(self.qr_lines(out))
            _, out = self.run_pair(self.parse_args(home, "--no-qr"))
            self.assertEqual(self.qr_lines(out), [])
            self.assertIn("K7Q2-M4X5-Z6A9", out)

    def test_no_config_or_bad_code_prints_the_code_only(self):
        with tempfile.TemporaryDirectory() as home:
            _, out = self.run_pair(SimpleNamespace(home=home))           # older callers: no flags at all
            self.assertEqual(self.qr_lines(out), [])
            _write_cfg(home)
            self.START = (200, {"state": "open", "code": "bozuk&key=x"})
            _, out = self.run_pair(self.parse_args(home))
            self.assertEqual(self.qr_lines(out), [])
            self.assertIn("bozuk", out)


if __name__ == "__main__":
    unittest.main()
