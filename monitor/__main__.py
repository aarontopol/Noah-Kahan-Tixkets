"""Command-line entry point.

Watchlist mode (default) — monitors every event in watches.json, which the web
UI manages:

    python -m monitor                 # check the whole watchlist once
    python -m monitor --loop          # keep polling on the configured interval
    python -m monitor --dry-run       # print texts instead of sending them

Single-event mode — fall back to the standalone config.yaml:

    python -m monitor --config config.yaml
"""
from __future__ import annotations

import argparse
import os
import sys

from .agent import run_loop, run_once, run_watchlist, run_watchlist_loop
from .config import Config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="monitor", description="Noah Kahan ticket monitor")
    parser.add_argument("--watches", default="watches.json", help="path to the watchlist store")
    parser.add_argument("--state-dir", default="state", help="directory for per-watch dedupe state")
    parser.add_argument("--config", default=None, help="use a single-event config.yaml instead of the watchlist")
    parser.add_argument("--state", default="state/seen.json", help="state file for --config mode")
    parser.add_argument("--loop", action="store_true", help="poll continuously")
    parser.add_argument("--dry-run", action="store_true", help="print texts instead of sending")
    args = parser.parse_args(argv)

    # Single-event mode only when explicitly requested.
    if args.config:
        config = Config.load(args.config)
        print(
            f"[agent] Watching {config.artist} @ {config.venue} on "
            f"{', '.join(d.isoformat() for d in config.dates)}"
        )
        if args.loop:
            run_loop(config, args.state, dry_run=args.dry_run)
            return 0
        result = run_once(config, args.state, dry_run=args.dry_run)
        print(f"[agent] done: fetched={result.fetched} matched={result.matched} notified={result.notified}")
        return 0

    # Watchlist mode (default).
    if not os.path.exists(args.watches):
        print(f"[agent] no watchlist at {args.watches}. Add events via the web UI "
              f"(python -m webui) or pass --config config.yaml.")
        return 1

    if args.loop:
        run_watchlist_loop(args.watches, args.state_dir, dry_run=args.dry_run)
        return 0

    results = run_watchlist(args.watches, args.state_dir, dry_run=args.dry_run)
    total_matched = sum(r.matched for r in results)
    total_notified = sum(r.notified for r in results)
    print(f"\n[agent] done: {len(results)} event(s) checked, "
          f"{total_matched} match(es), {total_notified} alert(s) sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
