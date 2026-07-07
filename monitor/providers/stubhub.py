"""StubHub provider (partner API).

StubHub's inventory API returns rich seat-level resale listings (section, row,
quantity, seat numbers, and a "hasObstructedView" flag) — exactly what we want.
It requires a partner OAuth token (STUBHUB_TOKEN) from an approved StubHub
developer account, which is why this provider is disabled by default in
config.yaml. Once you have a token and the numeric event IDs, flip it on.

If StubHub isn't configured this provider reports itself as not-configured and
the orchestrator skips it cleanly.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from ..models import Listing
from .base import TicketProvider
from .http import session

INVENTORY_URL = "https://api.stubhub.com/sellers/search/inventory/v2"


class StubHubProvider(TicketProvider):
    name = "stubhub"

    def __init__(self, token: str, event_ids: dict | None = None):
        self.token = token
        # optional {iso_date: stubhub_event_id}
        self.event_ids = event_ids or {}
        self.http = session()

    def is_configured(self) -> bool:
        return bool(self.token and self.event_ids)

    def fetch(self, config) -> List[Listing]:
        self.http.headers.update({"Authorization": f"Bearer {self.token}"})
        listings: List[Listing] = []
        target_dates = {d.isoformat(): d for d in config.dates}
        for iso, ev_id in self.event_ids.items():
            if iso not in target_dates or not ev_id:
                continue
            resp = self.http.get(INVENTORY_URL, params={"eventid": ev_id, "rows": 500}, timeout=30)
            resp.raise_for_status()
            for row in resp.json().get("listing", []) or []:
                listings.append(_listing_from_row(row, str(ev_id), config, target_dates[iso]))
        return listings


def _listing_from_row(row, ev_id, config, ev_date) -> Listing:
    seats = _parse_seats(row.get("seatNumbers", ""))
    return Listing(
        source="stubhub",
        event_id=ev_id,
        event_name=config.artist,
        event_date=ev_date,
        venue=config.venue,
        section=str(row.get("sectionName", "")),
        row=str(row.get("row")) if row.get("row") is not None else None,
        quantity=int(row.get("quantity", 1)),
        price_per_ticket=float((row.get("currentPrice") or {}).get("amount", row.get("faceValue", 0)) or 0),
        seat_numbers=seats,
        split_options=[int(x) for x in (row.get("splitOption") or "").split(",") if x.strip().isdigit()],
        is_obstructed=bool(row.get("hasObstructedView") or row.get("isObstructed")),
        notes=row.get("sellerNotes", "") or row.get("listingAttributeList", ""),
        url=row.get("listingUrl", ""),
        listing_id=str(row.get("listingId", "")),
    )


def _parse_seats(raw: str) -> List[int]:
    seats = []
    for token in str(raw).replace(";", ",").split(","):
        token = token.strip()
        if token.isdigit():
            seats.append(int(token))
    return seats
