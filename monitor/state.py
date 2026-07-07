"""Persistent dedupe store so you aren't texted repeatedly about the same seats.

We remember the lowest price we've already alerted for each listing. A listing
is worth a new alert when we've never seen it, or when its price has dropped
since the last alert.
"""
from __future__ import annotations

import json
import os
from typing import Dict

from .models import Listing


class SeenStore:
    def __init__(self, path: str):
        self.path = path
        self._prices: Dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._prices = {k: float(v) for k, v in json.load(fh).items()}
            except (json.JSONDecodeError, ValueError, OSError):
                self._prices = {}

    def should_notify(self, listing: Listing) -> bool:
        """True if this listing is new or cheaper than when we last alerted."""
        previous = self._prices.get(listing.dedup_key)
        if previous is None:
            return True
        # small epsilon so float noise doesn't trigger a re-alert
        return listing.price_per_ticket < previous - 0.01

    def record(self, listing: Listing) -> None:
        """Remember the (lowest) price we've alerted for this listing."""
        previous = self._prices.get(listing.dedup_key)
        if previous is None or listing.price_per_ticket < previous:
            self._prices[listing.dedup_key] = listing.price_per_ticket

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._prices, fh, indent=2, sort_keys=True)
