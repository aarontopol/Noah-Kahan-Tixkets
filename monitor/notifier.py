"""SMS notification via TextBelt (https://textbelt.com).

TextBelt is a single-HTTP-call SMS API: no account or phone-number setup, just
an API key. Set TEXTBELT_KEY and ALERT_PHONE in the environment. Use the key
`textbelt_test` for a free no-op that verifies wiring without sending.
"""
from __future__ import annotations

import re
from typing import List

import requests

_URL_RE = re.compile(r"https?://\S+")


def strip_urls(message: str) -> str:
    """Remove links (TextBelt blocks URLs for unverified accounts)."""
    stripped = _URL_RE.sub("", message)
    return "\n".join(line.rstrip() for line in stripped.splitlines() if line.strip())

from .models import Listing

TEXTBELT_URL = "https://textbelt.com/text"
TEXTBELT_QUOTA_URL = "https://textbelt.com/quota/{key}"

TEST_MESSAGE = ("✅ Ticket monitor test: texting works! You'll get alerts at this "
                "number when tickets matching your criteria are found.")


def check_quota(api_key: str, timeout: int = 10):
    """Return the remaining TextBelt quota for this key, or None if unknown."""
    if not api_key:
        return None
    try:
        resp = requests.get(TEXTBELT_QUOTA_URL.format(key=api_key), timeout=timeout)
        payload = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not payload.get("success"):
        return None
    try:
        return int(payload.get("quotaRemaining"))
    except (TypeError, ValueError):
        return None


def build_message(matches: List[Listing], max_matches: int, buy_hint: str = "") -> str:
    """Compose a concise SMS body from the cheapest matching listings."""
    if not matches:
        return ""
    ev = matches[0]
    header = f"🎫 Noah Kahan {ev.event_date:%b %-d} @ {ev.venue}: {len(matches)} seat(s) under target!"
    lines = [header]
    for lst in matches[:max_matches]:
        lines.append("• " + lst.summary())
    if len(matches) > max_matches:
        lines.append(f"…and {len(matches) - max_matches} more")
    # Include a direct link to the cheapest listing if we have one.
    link = next((l.url for l in matches if l.url), buy_hint)
    if link:
        lines.append(link)
    return "\n".join(lines)


class TextBeltNotifier:
    def __init__(self, api_key: str, phone: str, dry_run: bool = False, timeout: int = 30):
        self.api_key = api_key or ""
        self.phone = phone or ""
        self.dry_run = dry_run
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.phone) and not self.dry_run

    def send(self, message: str) -> bool:
        """Send an SMS. Returns True on success (or in dry-run).

        Unverified TextBelt accounts can't send messages containing URLs; if
        that's why the send was rejected, retry once with the links stripped —
        an alert without a link still beats no alert.
        """
        if not message:
            return False
        if self.dry_run or not self.api_key or not self.phone:
            print("[dry-run] would text %s:\n%s" % (self.phone or "<no phone>", message))
            return True
        if self._post(message):
            return True
        without_links = strip_urls(message)
        if self._last_error_was_url_block and without_links != message:
            print("[notifier] retrying without links (account not verified for URLs — "
                  "visit https://textbelt.com/whitelist to verify)")
            return self._post(without_links)
        return False

    def _post(self, message: str) -> bool:
        self._last_error_was_url_block = False
        try:
            resp = requests.post(
                TEXTBELT_URL,
                data={"phone": self.phone, "message": message, "key": self.api_key},
                timeout=self.timeout,
            )
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"[notifier] send failed: {exc}")
            return False
        if not payload.get("success"):
            error = str(payload.get("error", payload))
            print(f"[notifier] TextBelt error: {error}")
            self._last_error_was_url_block = "URL" in error
            return False
        remaining = payload.get("quotaRemaining")
        print(f"[notifier] sent to {self.phone} (quota remaining: {remaining})")
        return True
