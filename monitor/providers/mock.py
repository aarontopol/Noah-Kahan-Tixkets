"""Mock provider — reads recorded sample listings from data/sample_listings.json.

Used by the test suite and for local demos / dry-runs so the whole pipeline can
be exercised end-to-end without any network access or API keys.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List

from ..models import Listing
from .base import TicketProvider

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sample_listings.json")


class MockProvider(TicketProvider):
    name = "mock"

    def __init__(self, path: str = _DEFAULT_PATH, venue: str = "Coors Field", artist: str = "Noah Kahan"):
        self.path = path
        self.venue = venue
        self.artist = artist

    def is_configured(self) -> bool:
        return os.path.exists(self.path)

    def fetch(self, config) -> List[Listing]:
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        listings: List[Listing] = []
        for row in raw:
            listings.append(
                Listing(
                    source=self.name,
                    event_id=row.get("event_date", ""),
                    event_name=self.artist,
                    event_date=datetime.strptime(row["event_date"], "%Y-%m-%d").date(),
                    venue=self.venue,
                    section=str(row["section"]),
                    row=str(row.get("row")) if row.get("row") is not None else None,
                    quantity=int(row["quantity"]),
                    price_per_ticket=float(row["price_per_ticket"]),
                    seat_numbers=list(row.get("seat_numbers", [])),
                    split_options=list(row.get("split_options", [])),
                    is_obstructed=bool(row.get("is_obstructed", False)),
                    notes=row.get("notes", ""),
                    url=row.get("url", ""),
                    listing_id=row.get("listing_id", ""),
                )
            )
        return listings
