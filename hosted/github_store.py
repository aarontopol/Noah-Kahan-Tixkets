"""GitHub-backed watchlist storage for the hosted (Vercel) UI.

Vercel functions have no persistent disk, so instead of a local file the
hosted UI reads and commits `watches.json` in the GitHub repo — the same file
the scheduled monitor consumes. Every save is a commit, so changes are
auditable and picked up by the next monitor run automatically.
"""
from __future__ import annotations

import base64
import json
from typing import Optional, Tuple

import requests

API_ROOT = "https://api.github.com"


class ConflictError(Exception):
    """The file changed on GitHub between our read and our write."""


class GitHubStore:
    def __init__(self, token: str, repo: str, path: str = "watches.json",
                 branch: str = "main", timeout: int = 20):
        self.token = token
        self.repo = repo  # "owner/name"
        self.path = path
        self.branch = branch
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tixkets-hosted-ui",
        }

    @property
    def configured(self) -> bool:
        return bool(self.token and self.repo)

    def load(self) -> Tuple[dict, str]:
        """Return (watches_data, blob_sha)."""
        url = f"{API_ROOT}/repos/{self.repo}/contents/{self.path}"
        resp = requests.get(url, params={"ref": self.branch},
                            headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        content = base64.b64decode(payload["content"]).decode("utf-8")
        return json.loads(content), payload["sha"]

    def save(self, data: dict, sha: str, message: str) -> str:
        """Commit new contents; returns the new blob sha."""
        url = f"{API_ROOT}/repos/{self.repo}/contents/{self.path}"
        body = {
            "message": message,
            "content": base64.b64encode(
                (json.dumps(data, indent=2) + "\n").encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": self.branch,
        }
        resp = requests.put(url, json=body, headers=self._headers(), timeout=self.timeout)
        if resp.status_code == 409:
            raise ConflictError("watches.json changed on GitHub — reload and try again")
        resp.raise_for_status()
        return resp.json().get("content", {}).get("sha", "")

    def dispatch_workflow(self, workflow: str = "monitor.yml",
                          inputs: Optional[dict] = None) -> None:
        """Trigger the monitor workflow (e.g. a manual check-now)."""
        url = f"{API_ROOT}/repos/{self.repo}/actions/workflows/{workflow}/dispatches"
        resp = requests.post(url, json={"ref": self.branch, "inputs": inputs or {}},
                             headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
