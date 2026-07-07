"""The matching engine: decide whether a listing satisfies the search criteria."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

from .models import Listing

# Words that indicate a view the user explicitly does not want.
OBSTRUCTED_RE = re.compile(
    r"obstruct|limited\s+view|partial(?:ly)?\s+view|partial\s+view|"
    r"restricted\s+view|behind\s+(?:pole|pillar|stage|screen)|"
    r"\bpole\b|\bpillar\b|side\s+view|rear\s+view",
    re.IGNORECASE,
)


@dataclass
class Criteria:
    dates: List[date]
    section_min: int
    section_max: int
    min_quantity: int
    max_price_per_ticket: float
    require_contiguous: bool = True
    exclude_obstructed: bool = True
    # Accept event-level "tickets from $X" info when seat-level data is
    # unavailable (matched on date+price only, labeled clearly in the text).
    allow_price_fallback: bool = True

    def section_in_range(self, section_number: Optional[int]) -> bool:
        return section_number is not None and self.section_min <= section_number <= self.section_max


def looks_obstructed(listing: Listing) -> bool:
    """True if the source flagged it, or the notes/row text imply an obstruction."""
    if listing.is_obstructed:
        return True
    haystack = " ".join(filter(None, [listing.notes, listing.section, listing.row or ""]))
    return bool(OBSTRUCTED_RE.search(haystack))


def _has_consecutive_run(seat_numbers: List[int], n: int) -> bool:
    """Whether `seat_numbers` contains a run of `n` consecutive integers."""
    if n <= 0:
        return True
    unique = sorted(set(seat_numbers))
    if len(unique) < n:
        return False
    run = 1  # a single seat is a run of length 1
    for prev, cur in zip(unique, unique[1:]):
        run = run + 1 if cur == prev + 1 else 1
        if run >= n:
            return True
    return n == 1


def can_buy_group(listing: Listing, n: int, require_contiguous: bool) -> bool:
    """Can we actually purchase `n` seats together from this listing?"""
    if listing.quantity < n:
        return False
    # Respect the seller's split rules: if they publish allowed group sizes,
    # `n` must be one of them (or the block is exactly `n`).
    if listing.split_options and n not in listing.split_options and listing.quantity != n:
        return False
    # If seat numbers are known, require an actual consecutive run; otherwise a
    # single listing block is treated as an adjacent group of seats.
    if require_contiguous and listing.seat_numbers:
        return _has_consecutive_run(listing.seat_numbers, n)
    return True


def match(listing: Listing, criteria: Criteria) -> Tuple[bool, str]:
    """Return (matches, reason). `reason` explains the first failed check."""
    if criteria.dates and listing.event_date not in criteria.dates:
        return False, f"date {listing.event_date} not in target dates"
    # Event-level price info has no section/seat detail: match on price alone
    # (date already checked) so the user still hears about a price under target.
    if listing.is_price_level:
        if not criteria.allow_price_fallback:
            return False, "price-level fallback disabled"
        if listing.price_per_ticket > criteria.max_price_per_ticket:
            return False, f"price ${listing.price_per_ticket:.0f} > ${criteria.max_price_per_ticket:.0f}"
        return True, "price-level match (no seat detail)"
    if not criteria.section_in_range(listing.section_number):
        return False, f"section {listing.section!r} outside {criteria.section_min}-{criteria.section_max}"
    if listing.price_per_ticket > criteria.max_price_per_ticket:
        return False, f"price ${listing.price_per_ticket:.0f} > ${criteria.max_price_per_ticket:.0f}"
    if not can_buy_group(listing, criteria.min_quantity, criteria.require_contiguous):
        return False, f"cannot get {criteria.min_quantity} contiguous seats"
    if criteria.exclude_obstructed and looks_obstructed(listing):
        return False, "obstructed / limited view"
    return True, "match"


def find_matches(listings: List[Listing], criteria: Criteria) -> List[Listing]:
    """Filter and sort matching listings cheapest-first."""
    matches = [lst for lst in listings if match(lst, criteria)[0]]
    matches.sort(key=lambda l: l.price_per_ticket)
    return matches
