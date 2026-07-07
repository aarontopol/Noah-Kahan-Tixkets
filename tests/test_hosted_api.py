"""Tests for the Vercel-hosted UI (GitHub-backed, access-code protected)."""
import copy

import pytest

from hosted.app import create_app


class FakeStore:
    """In-memory stand-in for GitHubStore."""

    def __init__(self, data):
        self.data = data
        self.sha = "sha-1"
        self.commits = []          # (message, snapshot)
        self.dispatches = []       # inputs dicts
        self.configured = True
        self.repo = "aarontopol/Noah-Kahan-Tixkets"

    def load(self):
        return copy.deepcopy(self.data), self.sha

    def save(self, data, sha, message):
        assert sha == self.sha
        self.data = copy.deepcopy(data)
        self.commits.append((message, copy.deepcopy(data)))
        self.sha = f"sha-{len(self.commits) + 1}"
        return self.sha

    def dispatch_workflow(self, workflow="monitor.yml", inputs=None):
        self.dispatches.append(inputs or {})


SEED = {
    "watches": [{
        "artist": "Noah Kahan", "venue": "Coors Field", "city": "Denver",
        "dates": ["2026-08-08", "2026-08-09"], "ticketmaster_event_ids": {},
        "section_min": 120, "section_max": 141, "min_quantity": 4,
        "max_price_per_ticket": 350.0, "require_contiguous": True,
        "exclude_obstructed": True, "price_range_fallback": True,
        "enabled": True, "id": "nk", "created_at": "2026-07-07T00:00:00+00:00",
        "last_checked": None, "last_match_count": 0, "last_error": "", "last_sources": {},
    }],
    "providers": {"mock": False, "seatgeek": True, "ticketmaster": True, "stubhub": False},
    "runtime": {"poll_interval_minutes": 15, "max_matches_in_text": 6},
}

PW = "secret-code"


@pytest.fixture
def store():
    return FakeStore(copy.deepcopy(SEED))


@pytest.fixture
def client(store):
    app = create_app(store=store, password=PW)
    app.config.update(TESTING=True)
    return app.test_client()


def auth(extra=None):
    headers = {"X-UI-Key": PW}
    headers.update(extra or {})
    return headers


def test_requires_access_code(client):
    assert client.get("/api/watches").status_code == 401
    assert client.get("/api/watches", headers={"X-UI-Key": "wrong"}).status_code == 401


def test_fails_closed_without_password(store):
    app = create_app(store=store, password="")
    resp = app.test_client().get("/api/watches", headers={"X-UI-Key": "anything"})
    assert resp.status_code == 503


def test_page_serves_without_auth(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Access code" in resp.data


def test_list_watches(client):
    data = client.get("/api/watches", headers=auth()).get_json()
    assert len(data["watches"]) == 1
    assert data["repo"] == "aarontopol/Noah-Kahan-Tixkets"


def test_update_price_commits(client, store):
    resp = client.put("/api/watches/nk", headers=auth(),
                      json={"max_price_per_ticket": "299"})
    assert resp.status_code == 200
    assert resp.get_json()["max_price_per_ticket"] == 299.0
    assert len(store.commits) == 1
    message, snapshot = store.commits[0]
    assert "[skip ci]" in message
    assert snapshot["watches"][0]["max_price_per_ticket"] == 299.0


def test_update_validation_rejected_and_not_committed(client, store):
    resp = client.put("/api/watches/nk", headers=auth(),
                      json={"section_min": "300", "section_max": "100"})
    assert resp.status_code == 400
    assert store.commits == []


def test_add_and_delete_watch(client, store):
    resp = client.post("/api/watches", headers=auth(), json={
        "artist": "Hozier", "dates": "2026-09-12", "max_price_per_ticket": "180"})
    assert resp.status_code == 201
    new_id = resp.get_json()["id"]
    assert len(store.data["watches"]) == 2

    assert client.delete(f"/api/watches/{new_id}", headers=auth()).status_code == 200
    assert len(store.data["watches"]) == 1


def test_update_missing_watch_404(client):
    assert client.put("/api/watches/ghost", headers=auth(),
                      json={"max_price_per_ticket": "1"}).status_code == 404


def test_settings_commit(client, store):
    resp = client.put("/api/settings", headers=auth(),
                      json={"runtime": {"poll_interval_minutes": "5"}})
    assert resp.status_code == 200
    assert store.data["runtime"]["poll_interval_minutes"] == 5


def test_check_now_dispatches_workflow(client, store):
    resp = client.post("/api/check-now", headers=auth(), json={"dry_run": True})
    assert resp.status_code == 200
    assert store.dispatches == [{"dry_run": True}]
    assert "actions" in resp.get_json()["actions_url"]


def test_test_sms_without_server_keys(client, monkeypatch):
    monkeypatch.delenv("TEXTBELT_KEY", raising=False)
    monkeypatch.delenv("ALERT_PHONE", raising=False)
    resp = client.post("/api/test-sms", headers=auth())
    assert resp.status_code == 400
