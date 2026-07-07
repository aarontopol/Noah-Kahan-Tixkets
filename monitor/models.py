"""Core data model shared across ticket providers and the filter engine."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

# A single number, optionally prefixed by letters (e.g. "120", "RF 141", "141FL").
_SECTION_DIGITS = re.compile(r"(\d+)")


@dataclass
class Listing:
    """A normalized ticket listing from any source.

    Every provider converts its raw payload into one of these so the filter
    engine, dedupe store, and notifier never have to know where it came from.
    """

    source: str                 # e.g. "seatgeek", "ticketmaster", "mock"
    event_id: str
    event_name: str
    event_date: date
    venue: str
    section: str                # raw section label as shown by the source
    quantity: int               # number of seats in the listing block
    price_per_ticket: float     # per-seat price (all-in when available)

    row: Optional[str] = None
    total_price: Optional[float] = None
    seat_numbers: List[int] = field(default_factory=list)   # parsed seat numbers, if known
    split_options: List[int] = field(default_factory=list)  # purchasable group sizes; empty = any
    is_obstructed: bool = False
    notes: str = ""
    url: str = ""
    listing_id: str = ""
    # True for event-level price info (no section/seat detail) used as a
    # fallback when seat-map data is unavailable. Matched on date+price only
    # and labeled honestly in the alert text.
    is_price_level: bool = False

    @property
    def section_number(self) -> Optional[int]:
        """Numeric portion of the section label, or None if not parseable."""
        m = _SECTION_DIGITS.search(self.section or "")
        return int(m.group(1)) if m else None

    @property
    def dedup_key(self) -> str:
        """Stable identity for a listing, so we don't re-alert on the same one."""
        ident = self.listing_id or f"{self.section}|{self.row}|{self.quantity}"
        return f"{self.source}:{self.event_id}:{ident}"

    def summary(self) -> str:
        """One-line human-readable description used in the text message."""
        if self.is_price_level:
            return (
                f"{self.event_date:%b %-d}: tickets from ${self.price_per_ticket:,.0f} "
                f"— lowest listed price, seat details unavailable ({self.source})"
            )
        row = f" Row {self.row}" if self.row else ""
        price = f"${self.price_per_ticket:,.0f}/ea"
        return (
            f"Sec {self.section}{row} x{self.quantity} @ {price} "
            f"({self.source})"
        )
