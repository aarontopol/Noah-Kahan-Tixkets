"""Provider registry: build the enabled providers from config + secrets."""
from __future__ import annotations

from typing import List

from .base import TicketProvider
from .browser import BrowserProvider
from .mock import MockProvider
from .seatgeek import SeatGeekProvider
from .stubhub import StubHubProvider
from .ticketmaster import TicketmasterProvider


def build_providers(config) -> List[TicketProvider]:
    """Instantiate every enabled + configured provider."""
    enabled = set(config.enabled_providers)
    secrets = config.secrets
    candidates: List[TicketProvider] = []

    if "mock" in enabled:
        candidates.append(MockProvider(venue=config.venue, artist=config.artist))
    if "seatgeek" in enabled:
        candidates.append(SeatGeekProvider(secrets.seatgeek_client_id, secrets.seatgeek_client_secret))
    if "ticketmaster" in enabled:
        candidates.append(TicketmasterProvider(secrets.ticketmaster_api_key))
    if "stubhub" in enabled:
        candidates.append(StubHubProvider(secrets.stubhub_token, {}))
    if "browser" in enabled:
        candidates.append(BrowserProvider())

    ready, skipped = [], []
    for provider in candidates:
        (ready if provider.is_configured() else skipped).append(provider)
    for provider in skipped:
        if provider.name == "browser":
            print("[providers] skipping browser: Playwright not installed "
                  "(pip install playwright && playwright install chromium)")
        else:
            print(f"[providers] skipping {provider.name}: not configured (missing API key/token)")
    return ready
