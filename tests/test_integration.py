"""End-to-end pipeline test using the mock provider + sample data."""
import os
from datetime import date

from monitor.agent import run_once
from monitor.config import Config


def _mock_config(tmp_path, max_price=350.0):
    cfg = Config(
        artist="Noah Kahan",
        venue="Coors Field",
        city="Denver",
        dates=[date(2026, 8, 8), date(2026, 8, 9)],
        ticketmaster_event_ids={},
        criteria=_criteria(max_price),
        enabled_providers=["mock"],
        poll_interval_minutes=15,
        max_matches_in_text=6,
    )
    return cfg


def _criteria(max_price):
    from monitor.filters import Criteria
    return Criteria(
        dates=[date(2026, 8, 8), date(2026, 8, 9)],
        section_min=120, section_max=141, min_quantity=4,
        max_price_per_ticket=max_price, require_contiguous=True, exclude_obstructed=True,
    )


def test_pipeline_finds_expected_mock_matches(tmp_path):
    cfg = _mock_config(tmp_path)
    state = str(tmp_path / "seen.json")
    result = run_once(cfg, state, dry_run=True)

    # From data/sample_listings.json only mk-1 (312) and mk-2 (289) qualify:
    # mk-3 too expensive, mk-4 obstructed, mk-5 wrong section, mk-6 only 2,
    # mk-7 non-contiguous.
    ids = {m.listing_id for m in result.matches}
    assert ids == {"mk-1", "mk-2"}
    assert result.notified == 2


def test_pipeline_dedupes_on_second_run(tmp_path):
    cfg = _mock_config(tmp_path)
    state = str(tmp_path / "seen.json")
    first = run_once(cfg, state, dry_run=True)
    second = run_once(cfg, state, dry_run=True)
    assert first.notified == 2
    assert second.notified == 0  # already alerted, nothing new


def test_lower_threshold_filters_more(tmp_path):
    cfg = _mock_config(tmp_path, max_price=300.0)
    state = str(tmp_path / "seen.json")
    result = run_once(cfg, state, dry_run=True)
    # Only mk-2 (289) is <= 300 now.
    assert {m.listing_id for m in result.matches} == {"mk-2"}
