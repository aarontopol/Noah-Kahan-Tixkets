#!/usr/bin/env python3
"""One-command launcher for running the ticket monitor on a home computer.

    python start_monitor.py              # keep checking on the configured interval
    python start_monitor.py --dry-run    # one pass, print the text instead of sending
    python start_monitor.py --test-sms   # send a test text and exit

Reads keys from a `.env` file next to this script (copy `.env.example` to
`.env` and fill it in) so no shell-specific environment setup is needed.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path: str) -> bool:
    if not os.path.exists(path):
        print("No .env file found.")
        print("  1. Copy .env.example to .env (same folder as this script)")
        print("  2. Open .env in any text editor and fill in your keys")
        print("  3. Run this again")
        return False
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key, value)
    return True


def main() -> int:
    os.chdir(HERE)  # so watches.json / state/ resolve regardless of launch dir
    if not load_env(os.path.join(HERE, ".env")):
        return 1

    missing = [k for k in ("TEXTBELT_KEY", "ALERT_PHONE") if not os.environ.get(k)]
    if missing:
        print(f"Warning: {', '.join(missing)} not set in .env — alerts can't be texted.")

    from monitor.__main__ import main as monitor_main

    args = sys.argv[1:]
    if not args:
        args = ["--loop"]
        print("Starting the monitor loop (Ctrl-C to stop)…")
    return monitor_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
