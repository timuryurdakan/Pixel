"""urlopen that never sends loopback requests through a proxy (v2.md H8).

urllib honours `http_proxy`/`https_proxy` for every host that `no_proxy` doesn't list, and an unset `no_proxy` doesn't
exempt 127.0.0.1 on Linux/macOS. The CLI → service control call (`X-Pixel-Ctl` token) and a local-mode server URL would
then go to the proxy: the token leaks to it and the call fails. Non-loopback URLs keep the normal proxy handling.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.request
from typing import Union

_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def urlopen(req: Union[str, urllib.request.Request], timeout: float):
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    if is_loopback_url(url):
        return _DIRECT.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)
