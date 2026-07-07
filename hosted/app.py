"""Hosted (Vercel) UI: manage the watchlist from anywhere, backed by GitHub.

Differences from the local `webui` app:
- Storage is the repo's watches.json via the GitHub API (no local disk).
- Every API call requires the access code (UI_PASSWORD env) — this app is on
  the public internet. The page prompts once and the browser remembers it.
- "Check now" dispatches the GitHub Actions monitor workflow instead of
  fetching ticket data in-process, and links to the run logs.

Required environment (set in Vercel project settings):
  UI_PASSWORD   access code you'll type on your phone (required — fails closed)
  GITHUB_TOKEN  fine-grained PAT for the repo: Contents R/W + Actions R/W
  GITHUB_REPO   e.g. "aarontopol/Noah-Kahan-Tixkets"
Optional: TEXTBELT_KEY, ALERT_PHONE (test-sms button), TICKETMASTER_API_KEY (event search).
"""
from __future__ import annotations

import hmac
import os
from functools import wraps

from flask import Flask, jsonify, render_template, request

from monitor.config import Secrets
from monitor.notifier import TEST_MESSAGE, TextBeltNotifier, check_quota
from monitor.providers.ticketmaster import search_events
from monitor.watch import DEFAULT_PROVIDERS, DEFAULT_RUNTIME, EDITABLE_FIELDS, Watch

from .github_store import ConflictError, GitHubStore


def create_app(store: GitHubStore | None = None, password: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["STORE"] = store or GitHubStore(
        token=os.getenv("GITHUB_TOKEN", ""),
        repo=os.getenv("GITHUB_REPO", ""),
        branch=os.getenv("GITHUB_BRANCH", "main"),
    )
    app.config["UI_PASSWORD"] = password if password is not None else os.getenv("UI_PASSWORD", "")

    def require_auth(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            expected = app.config["UI_PASSWORD"]
            if not expected:
                return jsonify({"error": "UI_PASSWORD is not configured on the server"}), 503
            supplied = request.headers.get("X-UI-Key", "") or request.args.get("key", "")
            if not hmac.compare_digest(supplied, expected):
                return jsonify({"error": "wrong or missing access code"}), 401
            return fn(*args, **kwargs)
        return wrapper

    def guarded_store() -> GitHubStore:
        st = app.config["STORE"]
        if not st.configured:
            raise RuntimeError("GITHUB_TOKEN / GITHUB_REPO are not configured on the server")
        return st

    # -- page ------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    # -- watchlist ---------------------------------------------------------------
    @app.get("/api/watches")
    @require_auth
    def list_watches():
        st = guarded_store()
        data, _ = st.load()
        return jsonify({
            "watches": data.get("watches", []),
            "providers": {**DEFAULT_PROVIDERS, **(data.get("providers") or {})},
            "runtime": {**DEFAULT_RUNTIME, **(data.get("runtime") or {})},
            "secrets_status": _secrets_status(),
            "repo": st.repo,
        })

    def _mutate(message: str, fn):
        """Load → apply fn(data) → commit unless fn reported an error status."""
        st = guarded_store()
        data, sha = st.load()
        result = fn(data)
        resp, status = result if isinstance(result, tuple) else (result, 200)
        if status < 400:
            st.save(data, sha, message)
        return resp, status

    @app.post("/api/watches")
    @require_auth
    def create_watch():
        payload = _coerce(request.get_json(force=True) or {})

        def apply(data):
            watch = Watch.from_dict(payload)
            errors = watch.validation_errors()
            if errors:
                return jsonify({"error": "; ".join(errors)}), 400
            data.setdefault("watches", []).append(watch.to_dict())
            return jsonify(watch.to_dict()), 201

        try:
            return _mutate(f"Add watch: {payload.get('artist', '?')} [skip ci]", apply)
        except ConflictError as exc:
            return jsonify({"error": str(exc)}), 409

    @app.put("/api/watches/<watch_id>")
    @app.patch("/api/watches/<watch_id>")
    @require_auth
    def update_watch(watch_id):
        payload = _coerce(request.get_json(force=True) or {})
        fields = {k: v for k, v in payload.items() if k in EDITABLE_FIELDS}

        def apply(data):
            for raw in data.get("watches", []):
                if raw.get("id") == watch_id:
                    raw.update(fields)
                    watch = Watch.from_dict(raw)
                    errors = watch.validation_errors()
                    if errors:
                        return jsonify({"error": "; ".join(errors)}), 400
                    return jsonify(watch.to_dict())
            return jsonify({"error": "not found"}), 404

        try:
            return _mutate(f"Update watch {watch_id} [skip ci]", apply)
        except ConflictError as exc:
            return jsonify({"error": str(exc)}), 409

    @app.delete("/api/watches/<watch_id>")
    @require_auth
    def delete_watch(watch_id):
        def apply(data):
            watches = data.get("watches", [])
            kept = [w for w in watches if w.get("id") != watch_id]
            if len(kept) == len(watches):
                return jsonify({"error": "not found"}), 404
            data["watches"] = kept
            return jsonify({"ok": True})

        try:
            return _mutate(f"Remove watch {watch_id} [skip ci]", apply)
        except ConflictError as exc:
            return jsonify({"error": str(exc)}), 409

    # -- settings ------------------------------------------------------------------
    @app.put("/api/settings")
    @require_auth
    def update_settings():
        payload = request.get_json(force=True) or {}

        def apply(data):
            if isinstance(payload.get("providers"), dict):
                data.setdefault("providers", {}).update(
                    {k: bool(v) for k, v in payload["providers"].items()})
            if isinstance(payload.get("runtime"), dict):
                for key in ("poll_interval_minutes", "max_matches_in_text"):
                    if key in payload["runtime"]:
                        data.setdefault("runtime", {})[key] = int(payload["runtime"][key])
            return jsonify({"providers": data.get("providers"), "runtime": data.get("runtime")})

        try:
            return _mutate("Update monitor settings [skip ci]", apply)
        except ConflictError as exc:
            return jsonify({"error": str(exc)}), 409

    # -- check now: run the cloud monitor -----------------------------------------
    @app.post("/api/check-now")
    @require_auth
    def check_now():
        st = guarded_store()
        dry = bool((request.get_json(silent=True) or {}).get("dry_run", False))
        st.dispatch_workflow(inputs={"dry_run": dry})
        return jsonify({"ok": True,
                        "actions_url": f"https://github.com/{st.repo}/actions"})

    # -- SMS wiring ----------------------------------------------------------------
    @app.post("/api/test-sms")
    @require_auth
    def test_sms():
        s = Secrets.from_env()
        if not s.textbelt_key or not s.alert_phone:
            return jsonify({"error": "TEXTBELT_KEY / ALERT_PHONE not set on the server "
                                     "(use the GitHub Action's test-SMS input instead)"}), 400
        if not TextBeltNotifier(s.textbelt_key, s.alert_phone).send(TEST_MESSAGE):
            return jsonify({"error": "TextBelt rejected the send"}), 502
        return jsonify({"ok": True, "phone": s.alert_phone, "quota": check_quota(s.textbelt_key)})

    @app.get("/api/quota")
    @require_auth
    def quota():
        return jsonify({"quota": check_quota(Secrets.from_env().textbelt_key)})

    # -- event search ----------------------------------------------------------------
    @app.get("/api/search")
    @require_auth
    def search():
        keyword = request.args.get("q", "").strip()
        if not keyword:
            return jsonify({"results": [], "error": "enter an artist or event name"}), 400
        api_key = os.getenv("TICKETMASTER_API_KEY", "")
        if not api_key:
            return jsonify({"results": [], "error": "no TICKETMASTER_API_KEY set — add events manually"})
        try:
            return jsonify({"results": search_events(api_key, keyword,
                                                     request.args.get("city", "").strip())})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"results": [], "error": f"search failed: {exc}"}), 502

    @app.errorhandler(RuntimeError)
    def runtime_error(exc):
        return jsonify({"error": str(exc)}), 503

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
    """Same input normalization as the local UI."""
    out = dict(payload)
    for key in ("section_min", "section_max", "min_quantity"):
        if key in out and out[key] not in (None, ""):
            out[key] = int(out[key])
    if "max_price_per_ticket" in out and out["max_price_per_ticket"] not in (None, ""):
        out["max_price_per_ticket"] = float(out["max_price_per_ticket"])
    for key in ("require_contiguous", "exclude_obstructed", "price_range_fallback", "enabled"):
        if key in out:
            out[key] = bool(out[key])
    if "dates" in out and isinstance(out["dates"], str):
        out["dates"] = [d.strip() for d in out["dates"].replace(",", "\n").splitlines() if d.strip()]
    return out
