from datetime import date

from monitor.watch import Watch, WatchStore


def test_watch_to_criteria_roundtrip():
    w = Watch(artist="Noah Kahan", dates=["2026-08-08", "bad", "2026-08-09"],
              section_min=120, section_max=141, min_quantity=4, max_price_per_ticket=350)
    crit = w.criteria()
    assert crit.dates == [date(2026, 8, 8), date(2026, 8, 9)]  # invalid dropped
    assert crit.section_min == 120 and crit.section_max == 141
    assert crit.max_price_per_ticket == 350.0


def test_validation_catches_bad_input():
    assert "artist is required" in Watch(artist="").validation_errors()
    assert any("date" in e for e in Watch(artist="X", dates=[]).validation_errors())
    assert any("section_min" in e for e in
               Watch(artist="X", dates=["2026-08-08"], section_min=200, section_max=100).validation_errors())


def test_store_crud_roundtrip(tmp_path):
    path = str(tmp_path / "watches.json")
    store = WatchStore(path)
    assert store.list() == []

    w = store.add(Watch(artist="Hozier", dates=["2026-09-12"], max_price_per_ticket=180))
    assert len(store.list()) == 1

    # reload from disk
    reloaded = WatchStore(path)
    assert reloaded.get(w.id).artist == "Hozier"

    # update only editable fields; ignore junk keys
    reloaded.update(w.id, {"max_price_per_ticket": 150.0, "id": "hacked"})
    got = WatchStore(path).get(w.id)
    assert got.max_price_per_ticket == 150.0
    assert got.id == w.id  # id is not editable

    # delete
    assert reloaded.delete(w.id) is True
    assert WatchStore(path).list() == []
    assert reloaded.delete("nope") is False


def test_enabled_providers_helper(tmp_path):
    store = WatchStore(str(tmp_path / "w.json"))
    store.providers = {"mock": True, "seatgeek": False, "ticketmaster": True, "stubhub": False}
    assert set(store.enabled_providers()) == {"mock", "ticketmaster"}


def test_store_defaults_when_missing_file(tmp_path):
    store = WatchStore(str(tmp_path / "absent.json"))
    assert store.runtime["poll_interval_minutes"] == 15
    assert "seatgeek" in store.providers
