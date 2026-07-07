from datetime import date

from monitor.models import Listing
from monitor.notifier import build_message
from monitor.state import SeenStore


def make_listing(price=300.0, lid="x", section="128"):
    return Listing(
        source="test", event_id="e1", event_name="Noah Kahan", event_date=date(2026, 8, 8),
        venue="Coors Field", section=section, quantity=4, price_per_ticket=price,
        seat_numbers=[1, 2, 3, 4], listing_id=lid, url="https://example.com/x",
    )


def test_new_listing_should_notify(tmp_path):
    store = SeenStore(str(tmp_path / "seen.json"))
    assert store.should_notify(make_listing())


def test_same_price_not_renotified(tmp_path):
    path = str(tmp_path / "seen.json")
    store = SeenStore(path)
    lst = make_listing(price=300.0)
    assert store.should_notify(lst)
    store.record(lst)
    store.save()

    reloaded = SeenStore(path)
    assert not reloaded.should_notify(make_listing(price=300.0))


def test_price_drop_renotifies(tmp_path):
    path = str(tmp_path / "seen.json")
    store = SeenStore(path)
    store.record(make_listing(price=300.0))
    store.save()

    reloaded = SeenStore(path)
    assert reloaded.should_notify(make_listing(price=275.0))
    assert not reloaded.should_notify(make_listing(price=305.0))


def test_state_persists_lowest_price(tmp_path):
    path = str(tmp_path / "seen.json")
    store = SeenStore(path)
    store.record(make_listing(price=300.0))
    store.record(make_listing(price=280.0))  # lower wins
    store.record(make_listing(price=320.0))  # higher ignored
    store.save()
    reloaded = SeenStore(path)
    assert not reloaded.should_notify(make_listing(price=280.0))
    assert reloaded.should_notify(make_listing(price=270.0))


def test_build_message_includes_key_facts():
    msg = build_message([make_listing(price=289.0, section="120")], max_matches=6)
    assert "Noah Kahan" in msg
    assert "Sec 120" in msg
    assert "$289" in msg
    assert "https://example.com/x" in msg


def test_build_message_truncates_and_counts():
    listings = [make_listing(price=200.0 + i, lid=str(i)) for i in range(10)]
    msg = build_message(listings, max_matches=3)
    assert "and 7 more" in msg


def test_build_message_empty():
    assert build_message([], max_matches=6) == ""
