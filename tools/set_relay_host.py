#!/usr/bin/env python3
"""Set the realtime relay host in vercel.json's Content-Security-Policy (`connect-src`). V3.md §20, Phase 15.

Vercel serves vercel.json `headers` as static values: environment variables are NOT substituted there, so the
relay host that `PIXEL_RELAY_URL` names must also be written into the CSP once. This tool makes that a single,
validated command (no wildcard such as `*.workers.dev`):

    python3 tools/set_relay_host.py timur                                  # → wss://pixel-relay.timur.workers.dev
    python3 tools/set_relay_host.py wss://pixel-relay.timur.workers.dev    # the same, as a URL
    python3 tools/set_relay_host.py wss://relay.example.com                # custom domain
    python3 tools/set_relay_host.py --show                                 # print the current value

Afterwards: commit + push (Vercel redeploys), and set PIXEL_RELAY_URL in Vercel to the printed value.
If the two differ, the browser blocks the relay socket and the devices page says so ("CSP").
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERCEL_JSON = ROOT / "vercel.json"
LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOST_RE = re.compile(r"^%s(?:\.%s)+\Z" % (LABEL, LABEL))
SUB_RE = re.compile(r"^%s\Z" % LABEL)
CONNECT_RE = re.compile(r"connect-src [^;\"]*")


def relay_url(value: str) -> str:
    """'timur' | 'wss://host[/]' → 'wss://host'. Raises ValueError."""
    v = (value or "").strip().lower().rstrip("/")
    if SUB_RE.match(v):
        v = "wss://pixel-relay.%s.workers.dev" % v
    if v.startswith("https://"):
        v = "wss://" + v[len("https://"):]
    if not v.startswith("wss://"):
        raise ValueError("adres wss:// ile başlamalı (ya da yalnızca workers.dev alt alan adınızı yazın)")
    host = v[len("wss://"):]
    if not HOST_RE.match(host) or "*" in host:
        raise ValueError("geçersiz alan adı: %r" % host)
    return v


def current(text: str):
    m = CONNECT_RE.search(text)
    return m.group(0) if m else None


def apply(text: str, url: str) -> str:
    """Replace only the relay (wss://) source; the browser device's https:// provider hosts (docs/FAZ3_TASARIM.md
    §3.3) stay as they are."""
    found = CONNECT_RE.findall(text)
    if len(found) != 1:
        raise ValueError("vercel.json içinde tam olarak bir connect-src bekleniyordu")
    providers = [s for s in found[0].split()[1:] if s.startswith("https://")]
    out = CONNECT_RE.sub(lambda m: " ".join(["connect-src", "'self'", url] + providers), text)
    json.loads(out)                     # still valid JSON
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="vercel.json CSP connect-src röle adresini ayarlar")
    p.add_argument("relay", nargs="?", help="workers.dev alt alan adı (örn. timur) ya da wss://… adresi")
    p.add_argument("--show", action="store_true", help="yalnızca mevcut değeri yaz")
    p.add_argument("--file", default=str(VERCEL_JSON), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    path = Path(a.file)
    text = path.read_text(encoding="utf-8")
    if a.show or not a.relay:
        print(current(text) or "(connect-src bulunamadı)")
        return 0 if a.show else 2
    try:
        url = relay_url(a.relay)
        new = apply(text, url)
    except ValueError as e:
        print("Hata: %s" % e, file=sys.stderr)
        return 2
    if new != text:
        path.write_text(new, encoding="utf-8")
        print("vercel.json güncellendi: connect-src 'self' %s" % url)
    else:
        print("Değişiklik yok: connect-src zaten 'self' %s" % url)
    print("Vercel'de PIXEL_RELAY_URL = %s olmalı. Sonra: git commit + git push (Vercel yeniden dağıtır)." % url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
