"""Multi-event watchlist: the store the web UI edits and the agent reads.

A "watch" is one event you're monitoring plus the criteria for it (price,
section range, seat count, view/contiguity rules). Everything lives in a single
JSON file so it's trivial to edit, diff, and commit.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from .filters import Criteria

DEFAULT_PROVIDERS = {"mock": False, "seatgeek": True, "ticketmaster": True, "stubhub": False}
DEFAULT_RUNTIME = {"poll_interval_minutes": 15, "max_matches_in_text": 6}

# Fields the UI is allowed to update on an existing watch.
EDITABLE_FIELDS = {
    "artist", "venue", "city", "dates", "ticketmaster_event_ids",
    "section_min", "section_max", "min_quantity", "max_price_per_ticket",
    "require_contiguous", "exclude_obstructed", "enabled",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


@dataclass
class Watch:
    artist: str
    venue: str = ""
    city: str = ""
    dates: List[str] = field(default_factory=list)            # ISO "YYYY-MM-DD"
    ticketmaster_event_ids: Dict[str, str] = field(default_factory=dict)
    section_min: int = 120
    section_max: int = 141
    min_quantity: int = 4
    max_price_per_ticket: float = 350.0
    require_contiguous: bool = True
    exclude_obstructed: bool = True
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: str = field(default_factory=_now_iso)
    last_checked: Optional[str] = None
    last_match_count: int = 0
    last_error: str = ""
    # per-source listing counts from the last check (-1 = source errored);
    # lets the UI show whether each source is actually returning data.
    last_sources: Dict[str, int] = field(default_factory=dict)

    # -- conversions used by the agent ---------------------------------------
    def date_objects(self) -> List[date]:
        return [d for d in (_to_date(x) for x in self.dates) if d is not None]

    def criteria(self) -> Criteria:
        return Criteria(
            dates=self.date_objects(),
            section_min=int(self.section_min),
            section_max=int(self.section_max),
            min_quantity=int(self.min_quantity),
            max_price_per_ticket=float(self.max_price_per_ticket),
            require_contiguous=bool(self.require_contiguous),
            exclude_obstructed=bool(self.exclude_obstructed),
        )

    def label(self) -> str:
        when = ", ".join(self.dates) if self.dates else "date TBD"
        place = " @ ".join(p for p in [self.venue, self.city] if p)
        return f"{self.artist} — {place} ({when})" if place else f"{self.artist} ({when})"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Watch":
        known = {k: v for k, v in raw.items() if k in {f.name for f in cls.__dataclass_fields__.values()}}
        return cls(**known)

    def validation_errors(self) -> List[str]:
        errs = []
        if not str(self.artist).strip():
            errs.append("artist is required")
        if not self.date_objects():
            errs.append("at least one valid date (YYYY-MM-DD) is required")
        if int(self.section_min) > int(self.section_max):
            errs.append("section_min must be <= section_max")
        if int(self.min_quantity) < 1:
            errs.append("min_quantity must be >= 1")
        if float(self.max_price_per_ticket) <= 0:
            errs.append("max_price_per_ticket must be > 0")
        return errs


class WatchStore:
    """CRUD over the watchlist JSON file (watches + provider/runtime settings)."""

    def __init__(self, path: str = "watches.json"):
        self.path = path
        self.watches: List[Watch] = []
        self.providers: Dict[str, bool] = dict(DEFAULT_PROVIDERS)
        self.runtime: Dict[str, int] = dict(DEFAULT_RUNTIME)
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.watches = [Watch.from_dict(w) for w in raw.get("watches", [])]
        self.providers = {**DEFAULT_PROVIDERS, **(raw.get("providers") or {})}
        self.runtime = {**DEFAULT_RUNTIME, **(raw.get("runtime") or {})}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {
            "watches": [w.to_dict() for w in self.watches],
            "providers": self.providers,
            "runtime": self.runtime,
        }
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, self.path)

    # -- CRUD -----------------------------------------------------------------
    def list(self) -> List[Watch]:
        return list(self.watches)

    def get(self, watch_id: str) -> Optional[Watch]:
        return next((w for w in self.watches if w.id == watch_id), None)

    def add(self, watch: Watch) -> Watch:
        self.watches.append(watch)
        self.save()
        return watch

    def update(self, watch_id: str, fields: dict) -> Optional[Watch]:
        watch = self.get(watch_id)
        if watch is None:
            return None
        for key, value in fields.items():
            if key in EDITABLE_FIELDS:
                setattr(watch, key, value)
        self.save()
        return watch

    def delete(self, watch_id: str) -> bool:
        before = len(self.watches)
        self.watches = [w for w in self.watches if w.id != watch_id]
        changed = len(self.watches) != before
        if changed:
            self.save()
        return changed

    def enabled_providers(self) -> List[str]:
        return [name for name, on in self.providers.items() if on]
