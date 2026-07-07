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


def test_seatgeek_no_price_no_listing():
    provider = SeatGeekProvider("client-id")
    provider.http = FakeHttp([
        ("api.seatgeek.com", FakeResponse({"events": [
            {"id": 42, "datetime_local": "2026-08-08T18:30:00",
             "venue": {"name": "Coors Field"}, "stats": {"lowest_price": None}},
        ]})),
    ])
    assert provider.fetch(config()) == []
