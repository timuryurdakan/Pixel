"""A minimal QR Code encoder (ISO/IEC 18004): byte mode, error correction level M, versions 1–10, stdlib only.

Used by `pixel-connector pair` to show the pairing link as a terminal QR (G4). Only what that needs: one segment of
bytes, the smallest version that fits, the mask with the lowest penalty. Layout and constants follow the standard
(and Project Nayuki's reference implementation for the module placement order).
"""

from typing import List, Optional, Tuple

ECL_M_FORMAT_BITS = 0            # the 2-bit error correction level indicator of level M
MIN_VERSION, MAX_VERSION = 1, 10
# Level M per version: (EC codewords per block, [(number of blocks, data codewords per block), …])
EC_M = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
        6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
        10: (26, [(4, 43), (1, 44)])}
ALIGNMENT = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
             9: [6, 26, 46], 10: [6, 28, 50]}
MODE_BYTE = 0b0100
PAD_BYTES = (0xEC, 0x11)
FORMAT_GEN, FORMAT_MASK = 0x537, 0x5412
VERSION_GEN = 0x1F25
QUIET_ZONE = 4                   # modules of light border the standard asks for
PENALTY_N1, PENALTY_N2, PENALTY_N3, PENALTY_N4 = 3, 3, 40, 10
FINDER_LIKE = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])


class QrError(ValueError):
    pass


# -- GF(256), polynomial x^8 + x^4 + x^3 + x^2 + 1 ----------------------------------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def gf_mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def rs_generator(degree: int) -> List[int]:
    """Coefficients (highest power first, leading 1 dropped) of ∏(x − α^i), i = 0 … degree − 1."""
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= gf_mul(c, _EXP[i])
        poly = nxt
    return poly[1:]


def rs_remainder(data: List[int], degree: int) -> List[int]:
    """The `degree` Reed–Solomon error correction codewords of `data`."""
    gen = rs_generator(degree)
    rem = [0] * degree
    for b in data:
        factor = b ^ rem.pop(0)
        rem.append(0)
        for i, g in enumerate(gen):
            rem[i] ^= gf_mul(g, factor)
    return rem


# -- codewords --------------------------------------------------------------------------------------------------
def data_capacity(version: int) -> int:
    return sum(n * k for n, k in EC_M[version][1])


def _count_bits(version: int) -> int:
    return 8 if version <= 9 else 16


def pick_version(length: int) -> int:
    for v in range(MIN_VERSION, MAX_VERSION + 1):
        if 4 + _count_bits(v) + 8 * length <= 8 * data_capacity(v):
            return v
    raise QrError("too long for a version %d-M QR code" % MAX_VERSION)


def data_codewords(payload: bytes, version: int) -> List[int]:
    """Mode, count, bytes, terminator, then pad bytes up to the version's data capacity."""
    bits: List[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> i) & 1 for i in range(n - 1, -1, -1))

    put(MODE_BYTE, 4)
    put(len(payload), _count_bits(version))
    for b in payload:
        put(b, 8)
    cap = 8 * data_capacity(version)
    if len(bits) > cap:
        raise QrError("payload does not fit")
    bits.extend([0] * min(4, cap - len(bits)))
    bits.extend([0] * (-len(bits) % 8))
    out = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    i = 0
    while len(out) < cap // 8:
        out.append(PAD_BYTES[i % 2])
        i += 1
    return out


def interleave(data: List[int], version: int) -> List[int]:
    """Split into blocks, add each block's EC codewords, interleave data then EC (§7.6)."""
    ec_len, groups = EC_M[version]
    blocks, pos = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(data[pos:pos + size])
            pos += size
    ecs = [rs_remainder(b, ec_len) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out.extend(b[i] for b in blocks if i < len(b))
    for i in range(ec_len):
        out.extend(e[i] for e in ecs)
    return out


# -- format and version information ------------------------------------------------------------------------------
def format_bits(mask: int) -> int:
    data = (ECL_M_FORMAT_BITS << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * FORMAT_GEN)
    return ((data << 10) | rem) ^ FORMAT_MASK


def version_bits(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * VERSION_GEN)
    return (version << 12) | rem


# -- the matrix ------------------------------------------------------------------------------------------------------
class _Matrix:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = 17 + 4 * version
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def set(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def function_patterns(self) -> None:
        n = self.size
        for i in range(n):
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        d = max(abs(dx), abs(dy))
                        self.set(x, y, d not in (2, 4))
        pos = ALIGNMENT[self.version]
        last = len(pos) - 1
        for i, cx in enumerate(pos):
            for j, cy in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.format_info(0)                       # reserve the areas; drawn for real after masking
        if self.version >= 7:
            bits = version_bits(self.version)
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = n - 11 + i % 3, i // 3
                self.set(a, b, bit)
                self.set(b, a, bit)

    def format_info(self, mask: int) -> None:
        bits = format_bits(mask)
        n = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, n - 15 + i, bit(i))
        self.set(8, n - 8, True)                  # the dark module

    def place(self, codewords: List[int]) -> None:
        n, i, total = self.size, 0, len(codewords) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vert in range(n):
                y = n - 1 - vert if upward else vert
                for x in (right, right - 1):
                    if not self.fixed[y][x] and i < total:
                        self.dark[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if not self.fixed[y][x] and mask_bit(mask, y, x):
                    self.dark[y][x] = not self.dark[y][x]


def mask_bit(mask: int, i: int, j: int) -> bool:
    """Mask condition for row i, column j."""
    if mask == 0:
        return (i + j) % 2 == 0
    if mask == 1:
        return i % 2 == 0
    if mask == 2:
        return j % 3 == 0
    if mask == 3:
        return (i + j) % 3 == 0
    if mask == 4:
        return (i // 2 + j // 3) % 2 == 0
    if mask == 5:
        return (i * j) % 2 + (i * j) % 3 == 0
    if mask == 6:
        return ((i * j) % 2 + (i * j) % 3) % 2 == 0
    return ((i + j) % 2 + (i * j) % 3) % 2 == 0


def penalty(dark: List[List[bool]]) -> int:
    """The four penalty rules of §7.8.3 (the lowest score picks the mask)."""
    n = len(dark)
    lines = [list(r) for r in dark] + [[dark[y][x] for y in range(n)] for x in range(n)]
    score = 0
    for line in lines:
        run = 1
        for k in range(1, n + 1):
            if k < n and line[k] == line[k - 1]:
                run += 1
                continue
            if run >= 5:
                score += PENALTY_N1 + run - 5
            run = 1
        bits = [1 if v else 0 for v in line]
        for k in range(n - 10):
            if bits[k:k + 11] in FINDER_LIKE:
                score += PENALTY_N3
    for y in range(n - 1):
        for x in range(n - 1):
            if dark[y][x] == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                score += PENALTY_N2
    total = sum(sum(r) for r in dark)
    score += PENALTY_N4 * (abs(total * 20 - n * n * 10) // (n * n))
    return score


def encode(payload: bytes, mask: Optional[int] = None) -> List[List[bool]]:
    """The QR matrix (rows of booleans, True = dark, no quiet zone) of `payload` in byte mode, level M."""
    if not isinstance(payload, (bytes, bytearray)):
        raise QrError("payload must be bytes")
    version = pick_version(len(payload))
    codewords = interleave(data_codewords(bytes(payload), version), version)
    best: Optional[Tuple[int, List[List[bool]]]] = None
    for m in (range(8) if mask is None else [mask]):
        mx = _Matrix(version)
        mx.function_patterns()
        mx.place(codewords)
        mx.apply_mask(m)
        mx.format_info(m)
        score = penalty(mx.dark)
        if best is None or score < best[0]:
            best = (score, mx.dark)
    return best[1]


def render_half_blocks(matrix: List[List[bool]], ansi: bool = True) -> str:
    """Two module rows per text line with ▀ ▄ █. With `ansi`, black on white whatever the terminal's colours are
    (an inverted QR does not scan everywhere); the quiet zone is included."""
    n = len(matrix)
    size = n + 2 * QUIET_ZONE

    def dark(x: int, y: int) -> bool:
        x, y = x - QUIET_ZONE, y - QUIET_ZONE
        return 0 <= x < n and 0 <= y < n and matrix[y][x]

    glyph = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}
    lines = []
    for y in range(0, size, 2):
        row = "".join(glyph[(dark(x, y), dark(x, y + 1) if y + 1 < size else False)] for x in range(size))
        lines.append("\x1b[30;47m%s\x1b[0m" % row if ansi else row)
    return "\n".join(lines)


__all__ = ["encode", "render_half_blocks", "rs_remainder", "format_bits", "version_bits", "QrError"]
