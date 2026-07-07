"""Orchestration: fetch from every provider, filter, dedupe, and text on match."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

from .config import Config
from .filters import find_matches
from .models import Listing
from .notifier import TextBeltNotifier, build_message
from .providers import build_providers
from .state import SeenStore


@dataclass
class RunResult:
    fetched: int
    matched: int
    notified: int
    matches: List[Listing]


def run_once(config: Config, state_path: str, dry_run: bool = False) -> RunResult:
    providers = build_providers(config)
    if not providers:
        print("[agent] no providers configured — set API keys or enable `mock` in config.yaml")

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
