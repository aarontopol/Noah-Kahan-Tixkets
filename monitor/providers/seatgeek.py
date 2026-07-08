"""SeatGeek provider.

Three data tiers, best available wins:

1. Partner listings from the official Platform API (approved accounts only).
2. The seat-map endpoint seatgeek.com's own website uses — unofficial but
   returns true seat-level listings (section, row, quantity, price) that the
   public API withholds. Parsed defensively since its schema can drift.
3. The official event stats' lowest listed price, surfaced as a clearly
   labeled price-level result.

Requires SEATGEEK_CLIENT_ID (+ SEATGEEK_CLIENT_SECRET for newer apps).
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from ..models import Listing
from .base import TicketProvider
from .http import session

EVENTS_URL = "https://api.seatgeek.com/2/events"
# The endpoint seatgeek.com's own interactive seat map is served by. Unofficial
# (schema may drift), but it exposes true seat-level listings — section, row,
# quantity, price — that the Platform API withholds from non-partner keys.
CONSUMER_LISTINGS_URL = "https://seatgeek.com/api/event_listings_v2"

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://seatgeek.com/",
}


class SeatGeekProvider(TicketProvider):
    name = "seatgeek"

    def __init__(self, client_id: str, client_secret: str = ""):
        self.client_id = client_id
        self.client_secret = client_secret
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
        # Newer SeatGeek apps are rejected (403) unless the client_secret is
        # sent too; include it whenever it's configured.
        if self.client_secret:
            params["client_secret"] = self.client_secret
        # Their WAF also dislikes obviously-scripted user agents.
        headers = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")}
        try:
            resp = self.http.get(EVENTS_URL, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            hint = ""
            if "403" in str(exc):
                hint = (" — SeatGeek returns 403 for unapproved apps or when the "
                        "client_secret is missing; add SEATGEEK_CLIENT_SECRET "
                        "(from seatgeek.com/account/develop) and check the app's status")
            raise RuntimeError(f"{exc}{hint}") from exc
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
                continue

            stats = ev.get("stats") or {}
            print(f"[seatgeek] {ev_date} stats: {stats}")

            # Best shot first: the seat map's own endpoint (real seat-level data).
            seat_listings: List[Listing] = []
            try:
                seat_listings = self._fetch_consumer_listings(
                    ev_id, config.artist, ev_date, venue, ev_url)
                print(f"[seatgeek] {ev_date}: {len(seat_listings)} seat-level "
                      f"listing(s) via seat-map endpoint")
            except Exception as exc:  # noqa: BLE001 - unofficial endpoint, best-effort
                print(f"[seatgeek] seat-map endpoint unavailable for {ev_id}: {exc}")
            if seat_listings:
                listings.extend(seat_listings)
                continue

            # Fallback: the official stats' lowest listed price, surfaced as a
            # clearly-labeled price-level result (see monitor/filters.py).
            low = _lowest_from_stats(stats)
            if low is not None:
                listings.append(Listing(
                    source="seatgeek",
                    event_id=ev_id,
                    event_name=config.artist,
                    event_date=ev_date,
                    venue=venue,
                    section="",
                    quantity=0,
                    price_per_ticket=low,
                    notes="lowest listed price on SeatGeek (any section)",
                    url=ev_url,
                    listing_id=f"{ev_id}:price-range",
                    is_price_level=True,
                ))
        return listings

    def _fetch_consumer_listings(self, ev_id, artist, ev_date, venue, ev_url) -> List[Listing]:
        resp = self.http.get(CONSUMER_LISTINGS_URL,
                             params={"id": ev_id, "client_id": self.client_id},
                             headers=BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        raw = resp.json().get("listings") or []
        out = []
        for row in raw:
            lst = _listing_from_consumer_row(row, ev_id, artist, ev_date, venue, ev_url)
            if lst is not None:
                out.append(lst)
        if raw and not out:
            # Schema drifted — log the shape so the next fix is a one-liner.
            print(f"[seatgeek] could not parse seat-map listings; "
                  f"sample keys: {sorted(raw[0])[:20]}")
        return out


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


def _lowest_from_stats(stats: dict):
    """Lowest price across the several stat fields SeatGeek may populate."""
    values = []
    for key in ("lowest_price", "lowest_sg_base_price", "lowest_price_good_deals"):
        try:
            v = stats.get(key)
            if v is not None:
                values.append(float(v))
        except (TypeError, ValueError):
            continue
    return min(values) if values else None


def _first(row: dict, *keys):
    """First non-empty value among candidate key spellings."""
    for key in keys:
        value = row.get(key)
        if value not in (None, "", []):
            return value
    return None


def _listing_from_consumer_row(row, ev_id, artist, ev_date, venue, ev_url):
    """Parse one seat-map listing. The endpoint uses compact keys ("s", "r",
    "q", "p"…) that occasionally change; try known spellings and skip rows we
    can't understand rather than guessing."""
    section = _first(row, "s", "section", "sec")
    price = _first(row, "pf", "p", "price", "display_price", "dp")
    if section is None or price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None

    row_label = _first(row, "r", "row")
    qty_raw = _first(row, "q", "quantity", "qty")
    splits_raw = _first(row, "sq", "splits") or []
    if isinstance(qty_raw, list):  # some variants put the split list in "q"
        splits_raw = splits_raw or qty_raw
        qty_raw = max(qty_raw) if qty_raw else 0
    try:
        quantity = int(qty_raw or 0)
    except (TypeError, ValueError):
        quantity = 0
    splits = [int(x) for x in splits_raw if str(x).isdigit()] if isinstance(splits_raw, list) else []

    flags = row.get("f") if isinstance(row.get("f"), list) else []
    flag_text = " ".join(str(f) for f in flags)

    return Listing(
        source="seatgeek",
        event_id=ev_id,
        event_name=artist,
        event_date=ev_date,
        venue=venue,
        section=str(section),
        row=str(row_label) if row_label is not None else None,
        quantity=quantity,
        price_per_ticket=price,
        split_options=splits,
        is_obstructed="obstructed" in flag_text.lower(),
        notes=flag_text,
        url=str(_first(row, "url") or ev_url),
        listing_id=str(_first(row, "id", "listing_id") or f"{ev_id}:{section}|{row_label}"),
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
