"""Exercise the providers' price-level fallbacks with stubbed HTTP responses."""
from datetime import date
from types import SimpleNamespace

import pytest
import requests

from monitor.providers.seatgeek import SeatGeekProvider
from monitor.providers.ticketmaster import TicketmasterProvider


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status} Client Error")

    def json(self):
        return self.payload


class FakeHttp:
    """Routes GETs to canned responses by URL substring."""

    def __init__(self, routes):
        self.routes = routes  # list of (substring, FakeResponse)

    def get(self, url, params=None, timeout=None, headers=None):
        for fragment, resp in self.routes:
            if fragment in url:
                return resp
        raise AssertionError(f"unexpected url {url}")


def config():
    return SimpleNamespace(
        artist="Noah Kahan", venue="Coors Field", city="Denver",
        dates=[date(2026, 8, 8), date(2026, 8, 9)],
        ticketmaster_event_ids={"2026-08-08": "TM8", "2026-08-09": "TM9"},
    )


def test_ticketmaster_falls_back_to_details_price_when_facets_blocked():
    provider = TicketmasterProvider("key")
    provider.http = FakeHttp([
        # keyword search: matches the events but publishes no priceRanges
        ("events.json", FakeResponse({"_embedded": {"events": [
            {"id": "TM8", "url": "https://tm/8",
             "dates": {"start": {"localDate": "2026-08-08"}}},
        ]}})),
        # per-event details: publishes the price range
        ("events/TM8", FakeResponse({"url": "https://tm/8", "priceRanges": [{"min": 137.0}]})),
        ("events/TM9", FakeResponse({"url": "https://tm/9", "priceRanges": [{"min": 155.5}]})),
        # seat-map facets: blocked, as observed in production
        ("offeradapter", FakeResponse({}, status=403)),
    ])

    listings = provider.fetch(config())

    assert {l.listing_id for l in listings} == {"TM8:price-range", "TM9:price-range"}
    by_id = {l.listing_id: l for l in listings}
    assert by_id["TM8:price-range"].price_per_ticket == 137.0
    assert all(l.is_price_level for l in listings)
    assert by_id["TM9:price-range"].url == "https://tm/9"


def test_ticketmaster_no_fallback_when_seat_data_flows():
    provider = TicketmasterProvider("key")
    provider.http = FakeHttp([
        ("events.json", FakeResponse({"_embedded": {"events": [
            {"id": "TM8", "url": "https://tm/8", "priceRanges": [{"min": 137.0}],
             "dates": {"start": {"localDate": "2026-08-08"}}},
        ]}})),
        ("events/TM9", FakeResponse({"priceRanges": [{"min": 155.5}]})),
        ("offeradapter", FakeResponse({
            "facets": [{"section": "128", "available": 6, "offers": ["o1"]}],
            "_embedded": {"offer": [{"offerId": "o1", "totalPrice": 301.0}]},
        })),
    ])

    listings = provider.fetch(config())

    seat = [l for l in listings if not l.is_price_level]
    assert len(seat) == 2  # one real section listing per event
    assert all(l.section == "128" and l.price_per_ticket == 301.0 for l in seat)
    # fallback not emitted for events with seat data
    assert not [l for l in listings if l.is_price_level]


def test_ticketmaster_details_uses_discovery_id_when_pinned_id_404s():
    """Pinned website IDs 404 on the Discovery details endpoint (observed in
    production); the discovery-namespace ID from the search must be used."""
    provider = TicketmasterProvider("key")
    provider.http = FakeHttp([
        ("events.json", FakeResponse({"_embedded": {"events": [
            {"id": "DISC8", "url": "https://tm/8",
             "dates": {"start": {"localDate": "2026-08-08"}}},
            {"id": "DISC9", "url": "https://tm/9",
             "dates": {"start": {"localDate": "2026-08-09"}}},
        ]}})),
        # discovery-namespace IDs resolve on the details endpoint...
        ("events/DISC8", FakeResponse({"priceRanges": [{"min": 137.0}]})),
        ("events/DISC9", FakeResponse({"priceRanges": [{"min": 155.5}]})),
        # ...website IDs would 404 (must not be attempted first)
        ("events/TM8", FakeResponse({}, status=404)),
        ("events/TM9", FakeResponse({}, status=404)),
        ("offeradapter", FakeResponse({}, status=403)),
    ])

    listings = provider.fetch(config())

    assert {(l.listing_id, l.price_per_ticket) for l in listings} == {
        ("TM8:price-range", 137.0), ("TM9:price-range", 155.5)}
    assert all(l.is_price_level for l in listings)


def test_seatgeek_emits_price_level_from_stats():
    provider = SeatGeekProvider("client-id")
    provider.http = FakeHttp([
        ("api.seatgeek.com", FakeResponse({"events": [
            {"id": 42, "url": "https://sg/42",
             "datetime_local": "2026-08-08T18:30:00",
             "venue": {"name": "Coors Field"},
             "stats": {"lowest_price": 255}},
        ]})),
    ])

    listings = provider.fetch(config())

    assert len(listings) == 1
    lst = listings[0]
    assert lst.is_price_level and lst.price_per_ticket == 255.0
    assert lst.source == "seatgeek" and lst.listing_id == "42:price-range"


def test_seatgeek_seat_map_listings_preferred_over_price_level():
    provider = SeatGeekProvider("client-id")
    provider.http = FakeHttp([
        ("api.seatgeek.com", FakeResponse({"events": [
            {"id": 42, "url": "https://sg/42",
             "datetime_local": "2026-08-09T18:30:00",
             "venue": {"name": "Coors Field"},
             "stats": {"lowest_price": 302}},
        ]})),
        # the seat-map endpoint answers with compact-key listings
        ("seatgeek.com/api/event_listings_v2", FakeResponse({"listings": [
            {"id": "L1", "s": "121", "r": "12", "q": 6, "sq": [2, 4, 6], "pf": 640.0},
            {"id": "L2", "s": "343", "r": "3", "q": 2, "sq": [2], "p": 302.0},
            {"junk": True},  # unparseable rows are skipped, not fatal
        ]})),
    ])

    listings = provider.fetch(config())

    assert len(listings) == 2  # seat-level rows win; no price-level fallback emitted
    by_id = {l.listing_id: l for l in listings}
    l1 = by_id["L1"]
    assert (l1.section, l1.row, l1.quantity, l1.price_per_ticket) == ("121", "12", 6, 640.0)
    assert l1.split_options == [2, 4, 6]
    assert not any(l.is_price_level for l in listings)


def test_seatgeek_consumer_row_verbose_keys():
    from datetime import date as d
    from monitor.providers.seatgeek import _listing_from_consumer_row
    row = {"section": "128", "row": "9", "quantity": 4, "splits": [2, 4],
           "price": "355.5", "f": ["obstructed view"]}
    lst = _listing_from_consumer_row(row, "42", "NK", d(2026, 8, 8), "Coors Field", "https://sg")
    assert (lst.section, lst.row, lst.quantity) == ("128", "9", 4)
    assert lst.price_per_ticket == 355.5
    assert lst.is_obstructed is True


def test_lowest_from_stats_uses_alternate_fields():
    from monitor.providers.seatgeek import _lowest_from_stats
    assert _lowest_from_stats({"lowest_price": None, "lowest_sg_base_price": 302}) == 302.0
    assert _lowest_from_stats({"lowest_price": 350, "lowest_sg_base_price": 302}) == 302.0
    assert _lowest_from_stats({"lowest_price": None}) is None


def test_seatgeek_no_price_no_listing():
    provider = SeatGeekProvider("client-id")
    provider.http = FakeHttp([
        ("api.seatgeek.com", FakeResponse({"events": [
            {"id": 42, "datetime_local": "2026-08-08T18:30:00",
             "venue": {"name": "Coors Field"}, "stats": {"lowest_price": None}},
        ]})),
    ])
    assert provider.fetch(config()) == []
