"""Ticketmaster provider (official Discovery API + public seat-map facets).

Two stages:

1. Discovery API (https://developer.ticketmaster.com) — needs a free
   TICKETMASTER_API_KEY. Confirms the events and gives per-event price ranges.
   Also honors the event IDs pinned in config.yaml.

2. Seat-map "facets" — Ticketmaster's own interactive seat map is backed by a
   public JSON endpoint that returns per-section availability and the cheapest
   offer in each section (primary + resale). We parse that into section-level
   listings so we can apply the section / price / quantity filters. This is an
   unofficial endpoint: it can change, and it exposes section-level availability
   rather than guaranteed-adjacent seat numbers, so contiguity is approximated
   by "at least N seats available in the section". It degrades gracefully —
   any error just means this stage contributes nothing that run.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from ..models import Listing
from .base import TicketProvider
from .http import session

DISCOVERY_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
FACETS_URL = "https://offeradapter.ticketmaster.com/api/ismds/event/{event_id}/facets"
# Ticketmaster's public web consumer key (the same one their site uses).
WEB_CONSUMER_KEY = "b462oi7fic6pehcdkzony5bxhe"


def search_events(api_key: str, keyword: str, city: str = "", size: int = 20) -> List[dict]:
    """Search Ticketmaster's Discovery API for events to add to the watchlist.

    Returns lightweight candidates: {name, date, venue, city, event_id, url}.
    Used by the web UI's "search for an event" box. Requires a (free) API key;
    with none, the UI falls back to manual entry.
    """
    if not api_key:
        return []
    params = {"apikey": api_key, "keyword": keyword, "size": size, "classificationName": "music"}
    if city:
        params["city"] = city
    resp = session().get(DISCOVERY_URL, params=params, timeout=30)
    resp.raise_for_status()
    results = []
    for ev in (resp.json().get("_embedded", {}) or {}).get("events", []):
        venues = (ev.get("_embedded", {}) or {}).get("venues", []) or [{}]
        venue = venues[0] if venues else {}
        results.append({
            "name": ev.get("name", ""),
            "date": (ev.get("dates", {}).get("start", {}) or {}).get("localDate", ""),
            "venue": venue.get("name", ""),
            "city": (venue.get("city", {}) or {}).get("name", ""),
            "event_id": str(ev.get("id", "")),
            "url": ev.get("url", ""),
        })
    return results


class TicketmasterProvider(TicketProvider):
    name = "ticketmaster"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.http = session()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def fetch(self, config) -> List[Listing]:
        events = self._discover_events(config)
        listings: List[Listing] = []
        for ev_date, ev_id, ev_url in events:
            try:
                listings.extend(self._fetch_facets(ev_id, config, ev_date, ev_url))
            except Exception as exc:  # noqa: BLE001 - seat-map is best-effort
                print(f"[ticketmaster] facets unavailable for {ev_id} ({ev_date}): {exc}")
        return listings

    # -- stage 1: discovery ---------------------------------------------------
    def _discover_events(self, config) -> List[tuple]:
        """Return (date, event_id, url) tuples for the target shows."""
        found: Dict[str, tuple] = {}
        target_dates = {d.isoformat(): d for d in config.dates}

        # Pinned IDs from config take priority.
        for iso, ev_id in config.ticketmaster_event_ids.items():
            if ev_id and iso in target_dates:
                found[iso] = (target_dates[iso], ev_id, "")

        params = {
            "apikey": self.api_key,
            "keyword": config.artist,
            "city": config.city,
            "size": 50,
        }
        try:
            resp = self.http.get(DISCOVERY_URL, params=params, timeout=30)
            resp.raise_for_status()
            for ev in (resp.json().get("_embedded", {}) or {}).get("events", []):
                iso = (ev.get("dates", {}).get("start", {}) or {}).get("localDate", "")
                if iso in target_dates and iso not in found:
                    low = _min_price(ev)
                    if low is not None:
                        print(f"[ticketmaster] {iso} price range from ${low}")
                    found[iso] = (target_dates[iso], str(ev.get("id", "")), ev.get("url", ""))
        except Exception as exc:  # noqa: BLE001
            print(f"[ticketmaster] discovery lookup failed: {exc}")

        return [v for v in found.values() if v[1]]

    # -- stage 2: seat-map facets --------------------------------------------
    def _fetch_facets(self, event_id: str, config, ev_date, ev_url) -> List[Listing]:
        params = {
            "apikey": WEB_CONSUMER_KEY,
            "by": "section",
            "show": "places",
            "mode": "primary:default",
            "q": "available",
            "compress": "places",
            "embed": ["offer", "description"],
            "resaleChannelId": "internal.ecommerce.consumer.desktop.web.browser.ticketmaster.us",
        }
        url = FACETS_URL.format(event_id=event_id)
        resp = self.http.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        offers = _index_offers(data)
        listings: List[Listing] = []
        for facet in data.get("facets", []) or []:
            section = _facet_section(facet)
            if not section:
                continue
            available = int(facet.get("available") or facet.get("count") or 0)
            price, note = _cheapest_offer(facet.get("offers", []), offers)
            if price is None:
                continue
            listings.append(
                Listing(
                    source="ticketmaster",
                    event_id=event_id,
                    event_name=config.artist,
                    event_date=ev_date,
                    venue=config.venue,
                    section=str(section),
                    quantity=available,
                    price_per_ticket=price,
                    # Facets give section availability, not seat numbers: treat
                    # the section block as adjacent (see module docstring).
                    seat_numbers=[],
                    split_options=[],
                    is_obstructed=False,
                    notes=note,
                    url=ev_url,
                    listing_id=f"{event_id}:{section}",
                )
            )
        return listings


# --- parsing helpers ---------------------------------------------------------
def _min_price(event) -> Optional[float]:
    prices = event.get("priceRanges") or []
    mins = [p.get("min") for p in prices if p.get("min") is not None]
    return min(mins) if mins else None


def _index_offers(data) -> Dict[str, dict]:
    embedded = (data.get("_embedded") or {}).get("offer") or []
    index = {}
    for off in embedded:
        oid = off.get("offerId") or off.get("id")
        if oid:
            index[oid] = off
    return index


def _facet_section(facet) -> Optional[str]:
    # Different facet shapes expose the section under different keys.
    for key in ("section", "sectionName", "name"):
        if facet.get(key):
            return str(facet[key])
    places = facet.get("places")
    if isinstance(places, list) and places:
        return str(places[0])
    return None


def _cheapest_offer(offer_ids, offers) -> tuple:
    """Return (min_total_price, note) across the referenced offers."""
    best: Optional[float] = None
    note = ""
    for oid in offer_ids or []:
        off = offers.get(oid)
        if not off:
            continue
        price = off.get("totalPrice", off.get("faceValue"))
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if best is None or price < best:
            best = price
            note = off.get("description", "") or off.get("name", "")
    return best, note


def _to_date(value: str):
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
