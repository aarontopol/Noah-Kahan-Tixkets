from datetime import date

import pytest

from monitor.filters import Criteria, can_buy_group, find_matches, looks_obstructed, match
from monitor.models import Listing


def make_listing(**overrides):
    base = dict(
        source="test",
        event_id="e1",
        event_name="Noah Kahan",
        event_date=date(2026, 8, 8),
        venue="Coors Field",
        section="128",
        quantity=4,
        price_per_ticket=300.0,
        seat_numbers=[5, 6, 7, 8],
        split_options=[2, 4],
    )
    base.update(overrides)
    return Listing(**base)


@pytest.fixture
def criteria():
    return Criteria(
        dates=[date(2026, 8, 8), date(2026, 8, 9)],
        section_min=120,
        section_max=141,
        min_quantity=4,
        max_price_per_ticket=350.0,
        require_contiguous=True,
        exclude_obstructed=True,
    )


def test_ideal_listing_matches(criteria):
    ok, reason = match(make_listing(), criteria)
    assert ok, reason


def test_section_below_range_rejected(criteria):
    ok, reason = match(make_listing(section="119"), criteria)
    assert not ok and "section" in reason


def test_section_above_range_rejected(criteria):
    ok, _ = match(make_listing(section="142"), criteria)
    assert not ok


def test_section_boundaries_inclusive(criteria):
    assert match(make_listing(section="120", seat_numbers=[1, 2, 3, 4]), criteria)[0]
    assert match(make_listing(section="141", seat_numbers=[1, 2, 3, 4]), criteria)[0]


def test_upper_deck_section_rejected(criteria):
    ok, _ = match(make_listing(section="308"), criteria)
    assert not ok


def test_price_over_threshold_rejected(criteria):
    ok, reason = match(make_listing(price_per_ticket=350.01), criteria)
    assert not ok and "price" in reason


def test_price_at_threshold_accepted(criteria):
    assert match(make_listing(price_per_ticket=350.0), criteria)[0]


def test_fewer_than_four_seats_rejected(criteria):
    ok, reason = match(make_listing(quantity=2, seat_numbers=[5, 6], split_options=[2]), criteria)
    assert not ok and "contiguous" in reason


def test_non_contiguous_seats_rejected(criteria):
    ok, _ = match(make_listing(seat_numbers=[2, 4, 9, 11], split_options=[4]), criteria)
    assert not ok


def test_six_seat_block_can_yield_four(criteria):
    listing = make_listing(quantity=6, seat_numbers=[1, 2, 3, 4, 5, 6], split_options=[2, 4, 6])
    assert match(listing, criteria)[0]


def test_split_option_forbids_four(criteria):
    # A block of 6 that can only be sold as 2+2+2 (no way to buy exactly 4).
    listing = make_listing(quantity=6, seat_numbers=[1, 2, 3, 4, 5, 6], split_options=[2, 6])
    assert not can_buy_group(listing, 4, True)


def test_obstructed_flag_rejected(criteria):
    ok, reason = match(make_listing(is_obstructed=True), criteria)
    assert not ok and "obstructed" in reason


def test_obstructed_notes_rejected(criteria):
    ok, _ = match(make_listing(notes="Partially obstructed view behind pole"), criteria)
    assert not ok


def test_limited_view_notes_rejected(criteria):
    assert looks_obstructed(make_listing(notes="LIMITED VIEW"))


def test_wrong_date_rejected(criteria):
    ok, reason = match(make_listing(event_date=date(2026, 8, 10)), criteria)
    assert not ok and "date" in reason


def test_find_matches_sorts_cheapest_first(criteria):
    listings = [
        make_listing(section="128", price_per_ticket=330.0, listing_id="a"),
        make_listing(section="120", price_per_ticket=289.0, seat_numbers=[1, 2, 3, 4], listing_id="b"),
        make_listing(section="141", price_per_ticket=310.0, seat_numbers=[1, 2, 3, 4], listing_id="c"),
    ]
    matches = find_matches(listings, criteria)
    assert [m.price_per_ticket for m in matches] == [289.0, 310.0, 330.0]


def test_section_with_prefix_parsed(criteria):
    # "RF 128" style labels still resolve to section 128.
    assert make_listing(section="RF 128").section_number == 128
    assert match(make_listing(section="RF 128"), criteria)[0]
