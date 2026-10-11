#!/usr/bin/env python3
"""Render the Pixel Office app icons as PNGs (CLOUD.md §10.1). Stdlib only (zlib + struct).

    python3 tools/make_icons.py            # writes web/icons/*.png

The art is the same 8×8 pixel image as the page favicon (a monitor on a wooden desk), upscaled with
nearest neighbour. The maskable variant keeps the art inside the central safe zone (a circle with 80 %
of the icon's diameter), so the art square is ≤ 56 % of the side; the rest is background.
The generated PNGs are committed; re-run this script only when the art changes.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

BG = (0x1E, 0x1E, 0x2A)
# Same rectangles as the favicon in web/index.html: (x, y, w, h, colour)
RECTS = [
    (1, 1, 6, 4, (0x3F, 0x9A, 0x8F)),   # screen
    (2, 2, 3, 1, (0xD6, 0xFF, 0xF4)),   # screen glare / text line
    (3, 5, 2, 1, (0x6B, 0x71, 0x80)),   # monitor stand
    (1, 6, 6, 1, (0xA0, 0x70, 0x3C)),   # desk
]
ART = 8
OUT = Path(__file__).resolve().parent.parent / "web" / "icons"

# (file name, size, art pixels in the output)
ICONS = [
    ("icon-192.png", 192, 192),
    ("icon-512.png", 512, 512),
    ("icon-maskable-512.png", 512, 288),   # 36 px per art pixel = 56.25 % of the side
    ("apple-touch-icon.png", 180, 160),    # 20 px per art pixel, 10 px margin (iOS rounds corners)
]


def art_pixels() -> list[list[tuple[int, int, int]]]:
    grid = [[BG for _ in range(ART)] for _ in range(ART)]
    for x0, y0, w, h, col in RECTS:
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                grid[y][x] = col
    return grid


def render(size: int, art_size: int) -> list[bytes]:
    if art_size % ART:
        raise ValueError("art size must be a multiple of 8")
    scale = art_size // ART
    off = (size - art_size) // 2
    grid = art_pixels()
    rows = []
    for y in range(size):
        row = bytearray()
        ay = (y - off) // scale if off <= y < off + art_size else None
        for x in range(size):
            if ay is not None and off <= x < off + art_size:
                row += bytes(grid[ay][(x - off) // scale])
            else:
                row += bytes(BG)
        rows.append(bytes(row))
    return rows


def png_bytes(rows: list[bytes], width: int, height: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + r for r in rows)   # filter type 0 per scanline
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)   # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def main(argv: list[str] | None = None) -> int:
    out = Path(argv[0]) if argv else OUT
    out.mkdir(parents=True, exist_ok=True)
    for name, size, art in ICONS:
        data = png_bytes(render(size, art), size, size)
        (out / name).write_bytes(data)
        print(f"{out / name}  {size}x{size}  {len(data)} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
