"""Load search criteria from config.yaml and secrets from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List

import yaml

from .filters import Criteria


def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


@dataclass
class Secrets:
    """Pulled from the environment (never committed)."""
    textbelt_key: str = ""
    alert_phone: str = ""
    seatgeek_client_id: str = ""
    ticketmaster_api_key: str = ""
    stubhub_token: str = ""

    @classmethod
    def from_env(cls) -> "Secrets":
        return cls(
            textbelt_key=os.getenv("TEXTBELT_KEY", ""),
            alert_phone=os.getenv("ALERT_PHONE", ""),
            seatgeek_client_id=os.getenv("SEATGEEK_CLIENT_ID", ""),
            ticketmaster_api_key=os.getenv("TICKETMASTER_API_KEY", ""),
            stubhub_token=os.getenv("STUBHUB_TOKEN", ""),
        )


@dataclass
class Config:
    artist: str
    venue: str
    city: str
    dates: List[date]
    ticketmaster_event_ids: Dict[str, str]
    criteria: Criteria
    enabled_providers: List[str]
    poll_interval_minutes: int
    max_matches_in_text: int
    secrets: Secrets = field(default_factory=Secrets.from_env)

    @classmethod
    def load(cls, path: str = "config.yaml") -> "Config":
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)

        ev = raw.get("event", {})
        cr = raw.get("criteria", {})
        pr = raw.get("providers", {})
        rt = raw.get("runtime", {})

        dates = [_parse_date(d) for d in ev.get("dates", [])]
        criteria = Criteria(
            dates=dates,
            section_min=int(cr.get("section_min", 120)),
            section_max=int(cr.get("section_max", 141)),
            min_quantity=int(cr.get("min_quantity", 4)),
            max_price_per_ticket=float(cr.get("max_price_per_ticket", 350.0)),
            require_contiguous=bool(cr.get("require_contiguous", True)),
            exclude_obstructed=bool(cr.get("exclude_obstructed", True)),
            allow_price_fallback=bool(cr.get("price_range_fallback", True)),
        )
        enabled = [name for name, on in pr.items() if on]

        cfg = cls(
            artist=ev.get("artist", "Noah Kahan"),
            venue=ev.get("venue", ""),
            city=ev.get("city", ""),
            dates=dates,
            ticketmaster_event_ids={k: (v or "") for k, v in (ev.get("ticketmaster_event_ids") or {}).items()},
            criteria=criteria,
            enabled_providers=enabled,
            poll_interval_minutes=int(rt.get("poll_interval_minutes", 15)),
            max_matches_in_text=int(rt.get("max_matches_in_text", 6)),
        )
        # Allow environment overrides for the most-tweaked knob.
        env_price = os.getenv("MAX_PRICE_PER_TICKET")
        if env_price:
            cfg.criteria.max_price_per_ticket = float(env_price)
        return cfg
