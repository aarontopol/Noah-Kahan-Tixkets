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
    for provider in providers:
        try:
            found = provider.fetch(config)
            print(f"[agent] {provider.name}: {len(found)} listing(s)")
            all_listings.extend(found)
        except Exception as exc:  # noqa: BLE001 - isolate provider failures
            print(f"[agent] {provider.name} failed: {exc}")

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

    return RunResult(fetched=len(all_listings), matched=len(matches), notified=notified, matches=matches)


def run_loop(config: Config, state_path: str, dry_run: bool = False) -> None:
    interval = max(1, config.poll_interval_minutes) * 60
    print(f"[agent] loop mode: polling every {config.poll_interval_minutes} min. Ctrl-C to stop.")
    while True:
        try:
            run_once(config, state_path, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            print(f"[agent] run error: {exc}")
        time.sleep(interval)


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
