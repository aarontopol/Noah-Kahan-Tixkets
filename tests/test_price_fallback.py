"""Tests for the official price-range fallback (used when seat data is blocked)."""
from datetime import date

from monitor.filters import Criteria, find_matches, match
from monitor.models import Listing
from monitor.watch import Watch


def price_level_listing(price=250.0, day=date(2026, 8, 8)):
    return Listing(
        source="ticketmaster", event_id="e1", event_name="Noah Kahan",
        event_date=day, venue="Coors Field", section="", quantity=0,
        price_per_ticket=price, listing_id="e1:price-range", is_price_level=True,
        url="https://www.ticketmaster.com/x",
    )


def criteria(allow=True, max_price=350.0):
    return Criteria(
        dates=[date(2026, 8, 8), date(2026, 8, 9)],
        section_min=120, section_max=141, min_quantity=4,
        max_price_per_ticket=max_price, allow_price_fallback=allow,
    )


def test_price_level_matches_on_price_alone():
    ok, reason = match(price_level_listing(250.0), criteria())
    assert ok and "price-level" in reason


def test_price_level_over_threshold_rejected():
    ok, _ = match(price_level_listing(400.0), criteria())
    assert not ok


def test_price_level_wrong_date_rejected():
    ok, _ = match(price_level_listing(day=date(2026, 8, 10)), criteria())
    assert not ok


def test_price_level_disabled_by_flag():
    ok, reason = match(price_level_listing(250.0), criteria(allow=False))
    assert not ok and "disabled" in reason


def test_price_level_summary_is_honest():
    text = price_level_listing(137.0).summary()
    assert "seat details unavailable" in text
    assert "$137" in text


def test_price_level_sorts_with_seat_listings():
    seat = Listing(
        source="test", event_id="e1", event_name="NK", event_date=date(2026, 8, 8),
        venue="Coors Field", section="128", quantity=4, price_per_ticket=300.0,
        seat_numbers=[1, 2, 3, 4], listing_id="seat-1",
    )
    matches = find_matches([seat, price_level_listing(250.0)], criteria())
    assert [m.listing_id for m in matches] == ["e1:price-range", "seat-1"]


def test_watch_flag_flows_into_criteria():
    w = Watch(artist="X", dates=["2026-08-08"], price_range_fallback=False)
    assert w.criteria().allow_price_fallback is False
    assert Watch(artist="X", dates=["2026-08-08"]).criteria().allow_price_fallback is True
