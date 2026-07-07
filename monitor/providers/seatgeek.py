"""SeatGeek provider.

Uses SeatGeek's official Platform API (https://platform.seatgeek.com). A free
`client_id` (SEATGEEK_CLIENT_ID) unlocks event discovery and event-level price
stats. Per-seat listing data (section/row/quantity) is only returned to
approved partner accounts; when it is present in the response we parse it into
seat-level listings, otherwise we log the event's lowest price for visibility
and return nothing (so we never fire a false alert on a section-less price).
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from ..models import Listing
from .base import TicketProvider
from .http import session

EVENTS_URL = "https://api.seatgeek.com/2/events"


class SeatGeekProvider(TicketProvider):
    name = "seatgeek"

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.http = session()

    def is_configured(self) -> bool:
        return bool(self.client_id)

    def fetch(self, config) -> List[Listing]:
        params = {
            "client_id": self.client_id,
            "performers.slug": _slug(config.artist),
            "venue.city": config.city,
            "per_page": 50,
        }
        resp = self.http.get(EVENTS_URL, params=params, timeout=30)
        resp.raise_for_status()
        events = resp.json().get("events", [])

        target_dates = set(config.dates)
        listings: List[Listing] = []
        for ev in events:
            local = ev.get("datetime_local") or ev.get("datetime_utc") or ""
            ev_date = _to_date(local)
            if ev_date is None or ev_date not in target_dates:
                continue
            venue = (ev.get("venue") or {}).get("name", config.venue)
            ev_id = str(ev.get("id", ""))
            ev_url = ev.get("url", "")

            raw_listings = ev.get("listings") or []
            if raw_listings:
                for row in raw_listings:
                    listings.append(_listing_from_row(row, ev_id, config.artist, ev_date, venue, ev_url))
            else:
                low = (ev.get("stats") or {}).get("lowest_price")
                print(f"[seatgeek] {ev_date} lowest listed price=${low} "
                      f"(event-level only; no seat detail on this key)")
                # Public keys don't get per-seat listings, but the lowest listed
                # price is still an official, useful signal — surface it as a
                # clearly-labeled price-level result (see monitor/filters.py).
                if low is not None:
                    listings.append(Listing(
                        source="seatgeek",
                        event_id=ev_id,
                        event_name=config.artist,
                        event_date=ev_date,
                        venue=venue,
                        section="",
                        quantity=0,
                        price_per_ticket=float(low),
                        notes="lowest listed price on SeatGeek (any section)",
                        url=ev_url,
                        listing_id=f"{ev_id}:price-range",
                        is_price_level=True,
                    ))
        return listings


def _listing_from_row(row, ev_id, artist, ev_date, venue, ev_url) -> Listing:
    price = float(row.get("price") or row.get("dq_bucket_price") or 0.0)
    return Listing(
        source="seatgeek",
        event_id=ev_id,
        event_name=artist,
        event_date=ev_date,
        venue=venue,
        section=str(row.get("section", "")),
        row=str(row.get("row")) if row.get("row") is not None else None,
        quantity=int(row.get("quantity", 1)),
        price_per_ticket=price,
        split_options=[int(x) for x in row.get("splits", []) if str(x).isdigit()],
        is_obstructed=bool(row.get("obstructed_view") or row.get("obstructed")),
        notes=row.get("notes", "") or row.get("deal_description", ""),
        url=row.get("url", ev_url),
        listing_id=str(row.get("id", "")),
    )


def _slug(artist: str) -> str:
    return artist.strip().lower().replace(" ", "-")


def _to_date(value: str):
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:19] if "T" in value else value[:10], fmt).date()
        except (ValueError, TypeError):
            continue
    return None
