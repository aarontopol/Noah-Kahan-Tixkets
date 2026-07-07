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

from .agent import interval_elapsed, record_run_time, run_loop, run_once, run_watchlist, run_watchlist_loop
from .config import Config, Secrets
from .notifier import TEST_MESSAGE, TextBeltNotifier, check_quota
from .watch import WatchStore


def send_test_sms() -> int:
    """Send a one-off test text so you can verify TextBelt wiring end-to-end."""
    secrets = Secrets.from_env()
    if not secrets.textbelt_key or not secrets.alert_phone:
        print("[test-sms] TEXTBELT_KEY and ALERT_PHONE must both be set")
        return 1
    notifier = TextBeltNotifier(secrets.textbelt_key, secrets.alert_phone)
    ok = notifier.send(TEST_MESSAGE)
    quota = check_quota(secrets.textbelt_key)
    if quota is not None:
        print(f"[test-sms] TextBelt quota remaining: {quota}")
    print(f"[test-sms] {'sent' if ok else 'FAILED'}")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="monitor", description="Noah Kahan ticket monitor")
    parser.add_argument("--watches", default="watches.json", help="path to the watchlist store")
    parser.add_argument("--state-dir", default="state", help="directory for per-watch dedupe state")
    parser.add_argument("--config", default=None, help="use a single-event config.yaml instead of the watchlist")
    parser.add_argument("--state", default="state/seen.json", help="state file for --config mode")
    parser.add_argument("--loop", action="store_true", help="poll continuously")
    parser.add_argument("--dry-run", action="store_true", help="print texts instead of sending")
    parser.add_argument("--test-sms", action="store_true", help="send a test text and exit")
    parser.add_argument("--respect-interval", action="store_true",
                        help="skip the run if poll_interval_minutes hasn't elapsed since the last one "
                             "(used by the frequent GitHub Actions cron)")
    args = parser.parse_args(argv)

    if args.test_sms:
        return send_test_sms()

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

    # The Actions cron fires every 5 min; poll_interval_minutes (set in the web
    # UI) decides how often we actually check, so cadence is UI-controlled.
    if args.respect_interval:
        interval = int(WatchStore(args.watches).runtime.get("poll_interval_minutes", 15))
        if not interval_elapsed(args.state_dir, interval):
            print(f"[agent] skipping: last check was < {interval} min ago (poll_interval_minutes)")
            return 0
        record_run_time(args.state_dir)

    results = run_watchlist(args.watches, args.state_dir, dry_run=args.dry_run)
    total_matched = sum(r.matched for r in results)
    total_notified = sum(r.notified for r in results)
    print(f"\n[agent] done: {len(results)} event(s) checked, "
          f"{total_matched} match(es), {total_notified} alert(s) sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
