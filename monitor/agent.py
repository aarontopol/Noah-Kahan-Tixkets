"""Orchestration: fetch from every provider, filter, dedupe, and text on match."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .config import Config, Secrets
from .filters import find_matches
from .models import Listing
from .notifier import TextBeltNotifier, build_message
from .providers import TicketProvider, build_providers
from .state import SeenStore
from .watch import Watch, WatchStore


@dataclass
class RunResult:
    fetched: int
    matched: int
    notified: int
    matches: List[Listing]
    # listings fetched per source this run; -1 means the source errored.
    source_counts: dict = None


def run_once(
    config: Config,
    state_path: str,
    dry_run: bool = False,
    providers: Optional[List[TicketProvider]] = None,
) -> RunResult:
    if providers is None:
        providers = build_providers(config)
    if not providers:
        print("[agent] no providers configured — set API keys or enable `mock`")

    # 1. Gather listings from all sources; one bad source never kills the run.
    all_listings: List[Listing] = []
    source_counts: dict = {}
    for provider in providers:
        try:
            found = provider.fetch(config)
            print(f"[agent] {provider.name}: {len(found)} listing(s)")
            all_listings.extend(found)
            source_counts[provider.name] = len(found)
        except Exception as exc:  # noqa: BLE001 - isolate provider failures
            print(f"[agent] {provider.name} failed: {exc}")
            source_counts[provider.name] = -1  # -1 = errored this run

    # 2. Apply the search criteria.
    matches = find_matches(all_listings, config.criteria)
    print(f"[agent] {len(matches)} listing(s) match your criteria")

    # 3. Dedupe against what we've already texted about.
    store = SeenStore(state_path)
    fresh = [m for m in matches if store.should_notify(m)]

    notified = 0
    if fresh:
        message = build_message(fresh, config.max_matches_in_text)
        notifier = TextBeltNotifier(
            api_key=config.secrets.textbelt_key,
            phone=config.secrets.alert_phone,
            dry_run=dry_run,
        )
        if notifier.send(message):
            notified = len(fresh)
            for m in fresh:
                store.record(m)
            store.save()
    else:
        print("[agent] nothing new to alert (all matches already texted at this price or lower)")

    return RunResult(fetched=len(all_listings), matched=len(matches), notified=notified,
                     matches=matches, source_counts=source_counts)


def run_loop(config: Config, state_path: str, dry_run: bool = False) -> None:
    interval = max(1, config.poll_interval_minutes) * 60
    print(f"[agent] loop mode: polling every {config.poll_interval_minutes} min. Ctrl-C to stop.")
    while True:
        try:
            run_once(config, state_path, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            print(f"[agent] run error: {exc}")
        time.sleep(interval)


# --- scheduling throttle ------------------------------------------------------
# GitHub cron fires more often than you may want to check; these helpers let the
# run respect poll_interval_minutes (editable in the web UI) instead.
_LAST_RUN_FILE = "last_run.txt"
_CRON_JITTER_SECONDS = 90  # GitHub cron is best-effort; allow runs a bit early


def interval_elapsed(state_dir: str, interval_minutes: int, now: Optional[float] = None) -> bool:
    """True if at least interval_minutes (minus jitter) passed since the last run."""
    now = time.time() if now is None else now
    try:
        with open(os.path.join(state_dir, _LAST_RUN_FILE), "r", encoding="utf-8") as fh:
            last = float(fh.read().strip())
    except (OSError, ValueError):
        return True  # no record -> run
    return (now - last) >= max(0, interval_minutes * 60 - _CRON_JITTER_SECONDS)


def record_run_time(state_dir: str, now: Optional[float] = None) -> None:
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, _LAST_RUN_FILE), "w", encoding="utf-8") as fh:
        fh.write(str(time.time() if now is None else now))


# --- watchlist mode (multiple events managed via the web UI) -----------------
def _config_for_watch(watch: Watch, store: WatchStore, secrets: Secrets) -> Config:
    """Build a per-event Config from a Watch plus global provider/secret settings."""
    return Config(
        artist=watch.artist,
        venue=watch.venue,
        city=watch.city,
        dates=watch.date_objects(),
        ticketmaster_event_ids=watch.ticketmaster_event_ids,
        criteria=watch.criteria(),
        enabled_providers=store.enabled_providers(),
        poll_interval_minutes=int(store.runtime.get("poll_interval_minutes", 15)),
        max_matches_in_text=int(store.runtime.get("max_matches_in_text", 6)),
        secrets=secrets,
    )


def check_watch(
    watch: Watch,
    store: WatchStore,
    state_dir: str,
    dry_run: bool = False,
    providers: Optional[List[TicketProvider]] = None,
    secrets: Optional[Secrets] = None,
) -> RunResult:
    """Run a single watch and stamp its status back onto the watch object."""
    secrets = secrets or Secrets.from_env()
    config = _config_for_watch(watch, store, secrets)
    if providers is None:
        providers = build_providers(config)
    state_path = os.path.join(state_dir, f"{watch.id}.json")
    try:
        result = run_once(config, state_path, dry_run=dry_run, providers=providers)
        watch.last_error = ""
    except Exception as exc:  # noqa: BLE001
        watch.last_error = str(exc)
        watch.last_checked = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        raise
    watch.last_checked = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    watch.last_match_count = result.matched
    watch.last_sources = result.source_counts or {}
    return result


def run_watchlist(
    store_path: str = "watches.json",
    state_dir: str = "state",
    dry_run: bool = False,
) -> List[RunResult]:
    """Check every enabled watch in the store, reusing one set of providers."""
    store = WatchStore(store_path)
    secrets = Secrets.from_env()
    enabled = [w for w in store.list() if w.enabled]
    if not enabled:
        print("[agent] watchlist is empty (nothing enabled) — add events in the web UI")
        return []

    # Providers are keyed by global settings, so build them once for all watches.
    base = Config(
        artist="", venue="", city="", dates=[], ticketmaster_event_ids={},
        criteria=enabled[0].criteria(), enabled_providers=store.enabled_providers(),
        poll_interval_minutes=15, max_matches_in_text=6, secrets=secrets,
    )
    providers = build_providers(base)

    results = []
    for watch in enabled:
        print(f"\n[agent] === {watch.label()} ===")
        try:
            results.append(check_watch(watch, store, state_dir, dry_run, providers, secrets))
        except Exception as exc:  # noqa: BLE001 - one bad watch shouldn't stop the rest
            print(f"[agent] watch {watch.id} failed: {exc}")
    store.save()  # persist last_checked / match counts
    return results


def run_watchlist_loop(store_path: str = "watches.json", state_dir: str = "state", dry_run: bool = False) -> None:
    store = WatchStore(store_path)
    interval = max(1, int(store.runtime.get("poll_interval_minutes", 15))) * 60
    print(f"[agent] watchlist loop: polling every {interval // 60} min. Ctrl-C to stop.")
    while True:
        try:
            run_watchlist(store_path, state_dir, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] run error: {exc}")
        time.sleep(interval)
