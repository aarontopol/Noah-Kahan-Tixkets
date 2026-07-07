"""Shared HTTP session with sane defaults for all live providers.

`requests` automatically honors the HTTPS_PROXY and CA-bundle environment
variables, so this works both locally and inside GitHub Actions.
"""
from __future__ import annotations

import requests

_USER_AGENT = (
    "Mozilla/5.0 (compatible; NoahKahanTicketMonitor/1.0; "
    "+https://github.com/aarontopol/noah-kahan-tixkets)"
)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _USER_AGENT, "Accept": "application/json"})
    return s
