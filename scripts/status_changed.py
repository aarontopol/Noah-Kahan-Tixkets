"""Exit 0 if watches.json meaningfully differs from the committed version.

Used by the GitHub Actions workflow to decide whether to commit run status
(match counts, per-source data health, errors) back to the repo so the web UI
reflects cloud runs. `last_checked` alone is ignored — otherwise every run
would create a commit and pollute history.
"""
from __future__ import annotations

import json
import subprocess
import sys

PATH = "watches.json"
IGNORED_FIELDS = ("last_checked",)


def _normalized(data: dict) -> dict:
    data = json.loads(json.dumps(data))  # deep copy
    for watch in data.get("watches", []):
        for field in IGNORED_FIELDS:
            watch.pop(field, None)
    return data


def main() -> int:
    try:
        committed = json.loads(
            subprocess.check_output(["git", "show", f"HEAD:{PATH}"], text=True)
        )
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return 0  # no committed version -> treat as changed
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            current = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return 1  # unreadable working copy -> don't commit it

    return 0 if _normalized(committed) != _normalized(current) else 1


if __name__ == "__main__":
    sys.exit(main())
