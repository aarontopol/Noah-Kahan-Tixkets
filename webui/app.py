"""Flask web UI + JSON API for managing the ticket-monitor watchlist.

Run it with:  python -m webui   (defaults to http://127.0.0.1:5000)

It reads/writes the same watches.json the agent uses, so anything you change
here is picked up by the next scheduled (or --loop) run.
"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from monitor.agent import check_watch
from monitor.config import Secrets
from monitor.providers.ticketmaster import search_events
from monitor.watch import EDITABLE_FIELDS, Watch, WatchStore


def create_app(store_path: str = "watches.json", state_dir: str = "state") -> Flask:
    app = Flask(__name__)
    app.config["STORE_PATH"] = store_path
    app.config["STATE_DIR"] = state_dir

    def store() -> WatchStore:
        # Reload per request so external edits (agent/git) are always reflected.
        return WatchStore(app.config["STORE_PATH"])

    # -- page ----------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    # -- watches CRUD --------------------------------------------------------
    @app.get("/api/watches")
    def list_watches():
        st = store()
        return jsonify({
            "watches": [w.to_dict() for w in st.list()],
            "providers": st.providers,
            "runtime": st.runtime,
            "secrets_status": _secrets_status(),
        })

    @app.post("/api/watches")
    def create_watch():
        st = store()
        payload = request.get_json(force=True) or {}
        watch = Watch.from_dict(_coerce(payload))
        errors = watch.validation_errors()
        if errors:
            return jsonify({"error": "; ".join(errors)}), 400
        st.add(watch)
        return jsonify(watch.to_dict()), 201

    @app.put("/api/watches/<watch_id>")
    @app.patch("/api/watches/<watch_id>")
    def update_watch(watch_id):
        st = store()
        payload = _coerce(request.get_json(force=True) or {})
        fields = {k: v for k, v in payload.items() if k in EDITABLE_FIELDS}
        updated = st.update(watch_id, fields)
        if updated is None:
            return jsonify({"error": "not found"}), 404
        errors = updated.validation_errors()
        if errors:
            return jsonify({"error": "; ".join(errors)}), 400
        st.save()
        return jsonify(updated.to_dict())

    @app.delete("/api/watches/<watch_id>")
    def delete_watch(watch_id):
        st = store()
        return (jsonify({"ok": True}) if st.delete(watch_id)
                else (jsonify({"error": "not found"}), 404))

    # -- run a single watch now (dry-run preview) ----------------------------
    @app.post("/api/watches/<watch_id>/check")
    def check(watch_id):
        st = store()
        watch = st.get(watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        dry = bool((request.get_json(silent=True) or {}).get("dry_run", True))
        try:
            result = check_watch(watch, st, app.config["STATE_DIR"], dry_run=dry)
        except Exception as exc:  # noqa: BLE001
            st.save()
            return jsonify({"error": str(exc), "watch": watch.to_dict()}), 502
        st.save()
        return jsonify({
            "watch": watch.to_dict(),
            "matched": result.matched,
            "fetched": result.fetched,
            "notified": result.notified,
            "matches": [_listing_view(m) for m in result.matches],
        })

    # -- global provider / runtime settings ----------------------------------
    @app.put("/api/settings")
    def update_settings():
        st = store()
        payload = request.get_json(force=True) or {}
        if "providers" in payload and isinstance(payload["providers"], dict):
            st.providers.update({k: bool(v) for k, v in payload["providers"].items()})
        if "runtime" in payload and isinstance(payload["runtime"], dict):
            for key in ("poll_interval_minutes", "max_matches_in_text"):
                if key in payload["runtime"]:
                    st.runtime[key] = int(payload["runtime"][key])
        st.save()
        return jsonify({"providers": st.providers, "runtime": st.runtime})

    # -- event search (to pick new concerts) ---------------------------------
    @app.get("/api/search")
    def search():
        keyword = request.args.get("q", "").strip()
        city = request.args.get("city", "").strip()
        if not keyword:
            return jsonify({"results": [], "error": "enter an artist or event name"}), 400
        api_key = os.getenv("TICKETMASTER_API_KEY", "")
        if not api_key:
            return jsonify({"results": [], "error": "no TICKETMASTER_API_KEY set — add events manually"})
        try:
            results = search_events(api_key, keyword, city)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"results": [], "error": f"search failed: {exc}"}), 502
        return jsonify({"results": results})

    return app


def _secrets_status() -> dict:
    s = Secrets.from_env()
    return {
        "textbelt": bool(s.textbelt_key),
        "alert_phone": s.alert_phone or "",
        "ticketmaster": bool(s.ticketmaster_api_key),
        "seatgeek": bool(s.seatgeek_client_id),
        "stubhub": bool(s.stubhub_token),
    }


def _coerce(payload: dict) -> dict:
    """Normalize incoming JSON: numbers as numbers, dates as a clean list."""
    out = dict(payload)
    for key in ("section_min", "section_max", "min_quantity"):
        if key in out and out[key] not in (None, ""):
            out[key] = int(out[key])
    if "max_price_per_ticket" in out and out["max_price_per_ticket"] not in (None, ""):
        out["max_price_per_ticket"] = float(out["max_price_per_ticket"])
    for key in ("require_contiguous", "exclude_obstructed", "enabled"):
        if key in out:
            out[key] = bool(out[key])
    if "dates" in out and isinstance(out["dates"], str):
        out["dates"] = [d.strip() for d in out["dates"].replace(",", "\n").splitlines() if d.strip()]
    return out


def _listing_view(listing) -> dict:
    return {
        "source": listing.source,
        "section": listing.section,
        "row": listing.row,
        "quantity": listing.quantity,
        "price": listing.price_per_ticket,
        "url": listing.url,
        "summary": listing.summary(),
    }
