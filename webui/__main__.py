"""Launch the web UI:  python -m webui [--host H] [--port P]"""
from __future__ import annotations

import argparse

from .app import create_app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="webui", description="Ticket monitor web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--watches", default="watches.json")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    app = create_app(args.watches, args.state_dir)
    print(f"Ticket monitor UI → http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
