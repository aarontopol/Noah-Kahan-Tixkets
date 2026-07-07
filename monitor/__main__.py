"""Command-line entry point.

    python -m monitor                 # one check, sends real texts if configured
    python -m monitor --loop          # keep polling on config's interval
    python -m monitor --dry-run       # print the text instead of sending it
    python -m monitor --config x.yaml # use an alternate config file
"""
from __future__ import annotations

import argparse
import sys

from .agent import run_loop, run_once
from .config import Config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="monitor", description="Noah Kahan ticket monitor")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--state", default="state/seen.json", help="path to dedupe state file")
    parser.add_argument("--loop", action="store_true", help="poll continuously")
    parser.add_argument("--dry-run", action="store_true", help="print the text instead of sending")
    args = parser.parse_args(argv)

    config = Config.load(args.config)
    print(
        f"[agent] Watching {config.artist} @ {config.venue} on "
        f"{', '.join(d.isoformat() for d in config.dates)} | "
        f"sec {config.criteria.section_min}-{config.criteria.section_max}, "
        f"{config.criteria.min_quantity} seats, <= ${config.criteria.max_price_per_ticket:.0f}/ea"
    )

    if args.loop:
        run_loop(config, args.state, dry_run=args.dry_run)
        return 0

    result = run_once(config, args.state, dry_run=args.dry_run)
    print(f"[agent] done: fetched={result.fetched} matched={result.matched} notified={result.notified}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
