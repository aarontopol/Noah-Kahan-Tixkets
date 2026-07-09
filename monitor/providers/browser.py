"""Browser-capture provider (home computers only).

Ticket sites' seat-map data endpoints reject plain HTTP clients — bot
detection looks at far more than the IP address. This provider sidesteps that
legitimately by *being* a real browser: it opens each event's public SeatGeek
page in headless Chromium (Playwright) and captures the seat-listing JSON the
page itself downloads, then parses it with the same tolerant parser the
seatgeek provider uses. Section, row, quantity, splits, and price all come
through — enough for true "N seats together in sections X–Y" matching.

Setup (once, on the machine that runs the monitor):

    pip install playwright
    playwright install chromium

Then give each watch the event-page URL(s) to read, keyed by date, e.g. in
watches.json:

    "seatgeek_page_urls": {
      "2026-08-08": "https://seatgeek.com/noah-kahan-...-tickets/.../18046704",
      "2026-08-09": "https://seatgeek.com/noah-kahan-...-tickets/.../18066156"
    }

(Just copy the address bar from the event page in your own browser.)

The provider reports itself as not-configured when Playwright isn't installed
(e.g. in GitHub Actions), so cloud runs skip it cleanly.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import List

from .base import TicketProvider
from .seatgeek import _listing_from_consumer_row

# Substrings identifying the XHR responses that carry seat listings.
LISTING_ENDPOINT_MARKERS = ("event_listings",)

_EVENT_ID_RE = re.compile(r"/(\d{6,})(?:[/?#]|$)")


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - any import problem means "not available"
        return False


def _event_id_from_url(url: str) -> str:
    m = _EVENT_ID_RE.search(url)
    return m.group(1) if m else url


def _to_date(value: str):
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


class BrowserProvider(TicketProvider):
    name = "browser"

    def __init__(self, page_load_timeout_ms: int = 45000, settle_ms: int = 8000):
        self.page_load_timeout_ms = page_load_timeout_ms
        self.settle_ms = settle_ms

    def is_configured(self) -> bool:
        return _playwright_available()

    def fetch(self, config) -> List:
        page_urls = getattr(config, "seatgeek_page_urls", None) or {}
        targets = []
        for iso, url in page_urls.items():
            ev_date = _to_date(iso)
            if ev_date is not None and url and ev_date in config.dates:
                targets.append((ev_date, url))
        if not targets:
            print("[browser] no seatgeek_page_urls configured for this watch — "
                  "paste the event page address(es) into watches.json to enable "
                  "browser capture")
            return []

        from playwright.sync_api import sync_playwright

        listings: List = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            try:
                for ev_date, url in targets:
                    try:
                        listings.extend(self._capture_event(context, url, ev_date, config))
                    except Exception as exc:  # noqa: BLE001 - one bad page shouldn't stop the rest
                        print(f"[browser] {ev_date}: capture failed for {url}: {exc}")
            finally:
                browser.close()
        return listings

    def _capture_event(self, context, url: str, ev_date, config) -> List:
        payloads: List[dict] = []
        page = context.new_page()

        def on_response(response):
            if any(marker in response.url for marker in LISTING_ENDPOINT_MARKERS):
                try:
                    payloads.append(response.json())
                except Exception:  # noqa: BLE001 - non-JSON body, ignore
                    pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.page_load_timeout_ms)
            # Give the seat map time to fetch its listings.
            page.wait_for_timeout(self.settle_ms)
        finally:
            page.close()

        ev_id = _event_id_from_url(url)
        rows = []
        for payload in payloads:
            rows.extend(payload.get("listings") or [])
        out = []
        for row in rows:
            lst = _listing_from_consumer_row(row, ev_id, config.artist, ev_date,
                                             config.venue, url)
            if lst is not None:
                lst.source = "browser"
                out.append(lst)
        if rows and not out:
            print(f"[browser] {ev_date}: captured {len(rows)} listing rows but could "
                  f"not parse them; sample keys: {sorted(rows[0])[:20]}")
        elif not payloads:
            print(f"[browser] {ev_date}: page loaded but no listings response was "
                  f"captured (URL right? seat map may need scrolling into view)")
        else:
            print(f"[browser] {ev_date}: captured {len(out)} seat-level listing(s) "
                  f"from the live page")
        return out


def _debug_dump(payloads, path="browser_capture_debug.json"):  # pragma: no cover
    """Handy while diagnosing schema drift on a home machine."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payloads, fh, indent=2)
