"""Tests for the cron throttle, per-source data health, and status-changed script."""
import json
import subprocess
import sys
from datetime import date

from monitor.agent import check_watch, interval_elapsed, record_run_time
from monitor.config import Secrets
from monitor.watch import Watch, WatchStore


# --- throttle -----------------------------------------------------------------
def test_interval_elapsed_no_record_runs(tmp_path):
    assert interval_elapsed(str(tmp_path), 15) is True


def test_interval_elapsed_respects_recent_run(tmp_path):
    state = str(tmp_path)
    record_run_time(state, now=1_000_000.0)
    # 5 minutes later with a 15-min interval -> skip
    assert interval_elapsed(state, 15, now=1_000_000.0 + 5 * 60) is False
    # 15 minutes later -> run
    assert interval_elapsed(state, 15, now=1_000_000.0 + 15 * 60) is True


def test_interval_elapsed_tolerates_cron_jitter(tmp_path):
    state = str(tmp_path)
    record_run_time(state, now=1_000_000.0)
    # 14m30s later with a 15-min interval: within the 90s jitter allowance -> run
    assert interval_elapsed(state, 15, now=1_000_000.0 + 14.5 * 60) is True


def test_interval_elapsed_corrupt_record_runs(tmp_path):
    (tmp_path / "last_run.txt").write_text("not-a-number")
    assert interval_elapsed(str(tmp_path), 15) is True


# --- per-source data health -----------------------------------------------------
def test_check_watch_stamps_source_counts(tmp_path):
    store = WatchStore(str(tmp_path / "w.json"))
    store.providers = {"mock": True, "seatgeek": False, "ticketmaster": False, "stubhub": False}
    watch = store.add(Watch(artist="Noah Kahan", venue="Coors Field", city="Denver",
                            dates=["2026-08-08", "2026-08-09"],
                            section_min=120, section_max=141,
                            min_quantity=4, max_price_per_ticket=350))
    check_watch(watch, store, str(tmp_path / "state"), dry_run=True, secrets=Secrets())
    assert watch.last_sources == {"mock": 7}
    assert watch.last_match_count == 2
    assert watch.last_checked is not None


# --- status_changed.py ----------------------------------------------------------
def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_status_changed_detection(tmp_path):
    import os
    import shutil
    repo = tmp_path / "repo"
    repo.mkdir()
    script_src = os.path.join(os.path.dirname(__file__), "..", "scripts", "status_changed.py")
    (repo / "scripts").mkdir()
    shutil.copy(script_src, repo / "scripts" / "status_changed.py")

    payload = {"watches": [{"id": "nk", "last_checked": None, "last_match_count": 0}]}
    (repo / "watches.json").write_text(json.dumps(payload))
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")

    run = lambda: subprocess.run(  # noqa: E731
        [sys.executable, "scripts/status_changed.py"], cwd=repo, capture_output=True).returncode

    # identical -> no commit needed (exit 1)
    assert run() == 1

    # only last_checked changed -> still no commit (exit 1)
    payload["watches"][0]["last_checked"] = "2026-07-07T09:00:00+00:00"
    (repo / "watches.json").write_text(json.dumps(payload))
    assert run() == 1

    # match count changed -> commit (exit 0)
    payload["watches"][0]["last_match_count"] = 3
    (repo / "watches.json").write_text(json.dumps(payload))
    assert run() == 0
