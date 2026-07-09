"""Browser-capture provider (home computers only).

Ticket sites' seat-map data endpoints reject plain HTTP clients — bot
detection looks at far more than the IP address. This provider sidesteps that
legitimately by *being* a real browser: it opens each event's public SeatGeek
page in Chromium (Playwright) and captures the seat-listing JSON the page
itself downloads, then parses it with the same tolerant parser the seatgeek
provider uses. Section, row, quantity, splits, and price all come through —
enough for true "N seats together in sections X–Y" matching.

Setup (once, on the machine that runs the monitor):

    pip install playwright
    python -m playwright install chromium

Then give each watch the event-page URL(s) to read, keyed by date
(watches.json -> "seatgeek_page_urls"). Just copy the address bar from the
event page in your own browser.

If the site shows a human-verification challenge to the invisible browser,
run with a visible one instead (often passes):

    PowerShell:  $env:BROWSER_HEADED="1"; python start_monitor.py
    Mac/Linux:   BROWSER_HEADED=1 python3 start_monitor.py

The provider reports itself as not-configured when Playwright isn't installed
(e.g. in GitHub Actions), so cloud runs skip it cleanly.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import List
from urllib.parse import urlsplit

from .base import TicketProvider
from .seatgeek import _listing_from_consumer_row

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

CHALLENGE_MARKERS = ("press & hold", "access to this page has been denied",
                     "are you a human", "verify you are", "unusual activity")

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

    def __init__(self, page_load_timeout_ms: int = 45000, settle_ms: int = 12000):
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

        headed = os.getenv("BROWSER_HEADED", "").strip().lower() in ("1", "true", "yes")
        listings: List = []
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=not headed,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                viewport={"width": 1366, "height": 900},
                locale="en-US",
                user_agent=USER_AGENT,  # Playwright's default UA says "Headless"
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
        json_urls: List[str] = []
        page = context.new_page()

        def on_response(response):
            try:
                content_type = response.headers.get("content-type", "")
            except Exception:  # noqa: BLE001
                return
            if "json" not in content_type:
                return
            json_urls.append(response.url)
            try:
                body = response.json()
            except Exception:  # noqa: BLE001 - not actually JSON / stream gone
                return
            # Schema-agnostic: any JSON response carrying a "listings" array is
            # treated as seat-listing data, whatever the endpoint is named.
            if isinstance(body, dict) and isinstance(body.get("listings"), list):
                payloads.append(body)

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.page_load_timeout_ms)
            page.wait_for_timeout(2500)
            # Nudge lazy-loaded seat maps into fetching.
            for _ in range(3):
                try:
                    page.mouse.wheel(0, 900)
                except Exception:  # noqa: BLE001
                    break
                page.wait_for_timeout(800)
            page.wait_for_timeout(self.settle_ms)
            probe = self._probe_page(page)
        finally:
            page.close()
        challenge = probe.get("challenge", "")

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

        if out:
            print(f"[browser] {ev_date}: captured {len(out)} seat-level listing(s) "
                  f"from the live page")
        elif rows:
            print(f"[browser] {ev_date}: captured {len(rows)} listing rows but could "
                  f"not parse them; sample keys: {sorted(rows[0])[:20]}")
        elif challenge:
            print(f"[browser] {ev_date}: the site showed a human-verification "
                  f"challenge ({challenge!r}). Try a visible browser: set "
                  f"BROWSER_HEADED=1 and run again")
        else:
            seen = []
            for u in json_urls:
                s = urlsplit(u)
                path = s.netloc + s.path
                if path not in seen:
                    seen.append(path)
            print(f"[browser] {ev_date}: no listings JSON captured; JSON endpoints "
                  f"seen: {seen[:8] or 'none'}; page title={probe.get('title', '')!r}; "
                  f"page text starts: {probe.get('snippet', '')[:160]!r}")
        return out

    @staticmethod
    def _probe_page(page) -> dict:
        """Grab the page's title/text and flag known bot-wall phrasing."""
        try:
            title = page.title() or ""
            snippet = page.evaluate(
                "document.body ? document.body.innerText.slice(0, 600) : ''") or ""
        except Exception:  # noqa: BLE001
            return {"title": "", "snippet": "", "challenge": ""}
        haystack = f"{title} {snippet}".lower()
        challenge = next((m for m in CHALLENGE_MARKERS if m in haystack), "")
        return {"title": title, "snippet": snippet, "challenge": challenge}
