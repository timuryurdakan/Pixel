#!/usr/bin/env python3
"""Build the connector zipapp (V3.md §2, §5.4): web/connector/pixel-connector.pyz + web/connector/SHA256SUMS.

    python3 tools/build_connector.py            # build and write
    python3 tools/build_connector.py --check    # exit 1 if the committed files differ from a fresh build

The archive is reproducible: entries sorted, fixed timestamps (1980-01-01), fixed modes, stored (no compression, so
the bytes don't depend on the local zlib). Contents: pixel_connector/**.py, pixel_agent.py (from web/hook/, used for
office presence) and a root __main__.py. Stdlib only, Python ≥ 3.8 at run time.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "connector"
PYZ = "pixel-connector.pyz"
SUMS = "SHA256SUMS"
SHEBANG = b"#!/usr/bin/env python3\n"
MAIN = b'''"""pixel-connector zipapp entry point."""
import sys

if sys.version_info < (3, 8):
    sys.stderr.write("pixel-connector: Python 3.8 or newer is required\\n")
    raise SystemExit(2)

from pixel_connector.__main__ import main

raise SystemExit(main())
'''


def sources() -> list:
    files = []
    for p in sorted((ROOT / "pixel_connector").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        files.append((p.relative_to(ROOT).as_posix(), p.read_bytes()))
    files.append(("pixel_agent.py", (ROOT / "web" / "hook" / "pixel_agent.py").read_bytes()))
    files.append(("__main__.py", MAIN))
    return sorted(files)


def build_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for name, data in sources():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.create_system = 3
            z.writestr(info, data)
    return SHEBANG + buf.getvalue()


def sums_text(files: dict) -> str:
    return "".join("%s  %s\n" % (hashlib.sha256(data).hexdigest(), name) for name, data in sorted(files.items()))


def outputs() -> dict:
    pyz = build_bytes()
    files = {PYZ: pyz}
    for name in ("install.sh", "install.ps1"):
        files[name] = (OUT_DIR / name).read_bytes()
    return {PYZ: pyz, SUMS: sums_text(files).encode("ascii")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true", help="verify the committed files instead of writing them")
    args = p.parse_args(argv)
    out = outputs()
    if args.check:
        bad = [n for n, data in out.items() if not (OUT_DIR / n).exists() or (OUT_DIR / n).read_bytes() != data]
        if bad:
            print("out of date: %s (run python3 tools/build_connector.py)" % ", ".join(bad), file=sys.stderr)
            return 1
        print("web/connector is up to date")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, data in out.items():
        (OUT_DIR / name).write_bytes(data)
    digest = hashlib.sha256(out[PYZ]).hexdigest()
    print("wrote web/connector/%s (%d bytes, sha256 %s) and %s" % (PYZ, len(out[PYZ]), digest, SUMS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
