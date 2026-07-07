import json

import pytest

from monitor.watch import Watch, WatchStore
from webui.app import create_app


@pytest.fixture
def client(tmp_path):
    store_path = str(tmp_path / "watches.json")
    store = WatchStore(store_path)
    store.providers = {"mock": True, "seatgeek": False, "ticketmaster": False, "stubhub": False}
    store.add(Watch(artist="Noah Kahan", venue="Coors Field", city="Denver",
                    dates=["2026-08-08", "2026-08-09"], id="nk",
                    section_min=120, section_max=141, min_quantity=4, max_price_per_ticket=350))
    app = create_app(store_path, str(tmp_path / "state"))
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Ticket" in resp.data


def test_list_watches(client):
    data = client.get("/api/watches").get_json()
    assert len(data["watches"]) == 1
    assert data["providers"]["mock"] is True
    assert "textbelt" in data["secrets_status"]


def test_create_and_delete(client):
    resp = client.post("/api/watches", json={
        "artist": "Hozier", "dates": "2026-09-12", "max_price_per_ticket": "180",
        "section_min": "1", "section_max": "10", "min_quantity": "2"})
    assert resp.status_code == 201
    new_id = resp.get_json()["id"]
    assert len(client.get("/api/watches").get_json()["watches"]) == 2

    assert client.delete(f"/api/watches/{new_id}").status_code == 200
    assert len(client.get("/api/watches").get_json()["watches"]) == 1


def test_create_validation_error(client):
    resp = client.post("/api/watches", json={"artist": "", "dates": ""})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_update_price(client):
    resp = client.put("/api/watches/nk", json={"max_price_per_ticket": "299"})
    assert resp.status_code == 200
    assert resp.get_json()["max_price_per_ticket"] == 299.0


def test_update_missing_watch(client):
    assert client.put("/api/watches/ghost", json={"max_price_per_ticket": "1"}).status_code == 404


def test_check_now_returns_matches(client):
    resp = client.post("/api/watches/nk/check", json={"dry_run": True})
    assert resp.status_code == 200
    data = resp.get_json()
    # mock sample data yields mk-1 (312) and mk-2 (289) for this criteria
    assert data["matched"] == 2
    assert {m["section"] for m in data["matches"]} == {"120", "128"}


def test_check_now_respects_edited_price(client):
    client.put("/api/watches/nk", json={"max_price_per_ticket": "300"})
    data = client.post("/api/watches/nk/check", json={"dry_run": True}).get_json()
    assert data["matched"] == 1  # only the $289 listing survives


def test_settings_update(client):
    resp = client.put("/api/settings", json={
        "providers": {"seatgeek": True}, "runtime": {"poll_interval_minutes": "30"}})
    data = resp.get_json()
    assert data["providers"]["seatgeek"] is True
    assert data["runtime"]["poll_interval_minutes"] == 30


def test_search_without_key(client):
    data = client.get("/api/search?q=Noah").get_json()
    assert data["results"] == []
    assert "TICKETMASTER_API_KEY" in data["error"]


def test_test_sms_requires_secrets(client, monkeypatch):
    monkeypatch.delenv("TEXTBELT_KEY", raising=False)
    monkeypatch.delenv("ALERT_PHONE", raising=False)
    resp = client.post("/api/test-sms")
    assert resp.status_code == 400
    assert "TEXTBELT_KEY" in resp.get_json()["error"]


def test_quota_without_key_is_null(client, monkeypatch):
    monkeypatch.delenv("TEXTBELT_KEY", raising=False)
    assert client.get("/api/quota").get_json() == {"quota": None}


def test_check_now_exposes_source_health(client):
    client.post("/api/watches/nk/check", json={"dry_run": True})
    watch = client.get("/api/watches").get_json()["watches"][0]
    assert watch["last_sources"] == {"mock": 7}
