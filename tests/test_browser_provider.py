"""Tests for the browser-capture provider's non-browser logic (config plumbing,
URL parsing, payload parsing). The actual Playwright session can't run against
ticket sites from CI, so it's exercised only on home machines."""
from datetime import date
from types import SimpleNamespace

from monitor.agent import _config_for_watch
from monitor.config import Secrets
from monitor.providers.browser import BrowserProvider, _event_id_from_url
from monitor.watch import Watch, WatchStore


def test_event_id_from_url():
    assert _event_id_from_url("https://seatgeek.com/x-tickets/denver/18046704") == "18046704"
    assert _event_id_from_url("https://seatgeek.com/x/18066156?aff=1") == "18066156"
    assert _event_id_from_url("https://seatgeek.com/no-id-here") == "https://seatgeek.com/no-id-here"


def test_page_urls_flow_from_watch_to_config(tmp_path):
    store = WatchStore(str(tmp_path / "w.json"))
    watch = Watch(artist="Noah Kahan", dates=["2026-08-08"],
                  seatgeek_page_urls={"2026-08-08": "https://seatgeek.com/e/18046704"})
    config = _config_for_watch(watch, store, Secrets())
    assert config.seatgeek_page_urls == {"2026-08-08": "https://seatgeek.com/e/18046704"}


def test_fetch_without_urls_returns_empty():
    provider = BrowserProvider()
    config = SimpleNamespace(dates=[date(2026, 8, 8)], artist="NK", venue="Coors Field",
                             seatgeek_page_urls={})
    assert provider.fetch(config) == []


def test_fetch_ignores_urls_for_other_dates():
    provider = BrowserProvider()
    # URL keyed to a date outside the watch -> no targets -> no Playwright launch
    config = SimpleNamespace(dates=[date(2026, 8, 8)], artist="NK", venue="Coors Field",
                             seatgeek_page_urls={"2026-09-01": "https://seatgeek.com/e/1"})
    assert provider.fetch(config) == []


def test_watch_roundtrips_page_urls(tmp_path):
    path = str(tmp_path / "w.json")
    store = WatchStore(path)
    store.add(Watch(artist="X", dates=["2026-08-08"],
                    seatgeek_page_urls={"2026-08-08": "https://seatgeek.com/e/42"}))
    reloaded = WatchStore(path)
    assert reloaded.list()[0].seatgeek_page_urls == {"2026-08-08": "https://seatgeek.com/e/42"}
