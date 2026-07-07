"""Provider interface. Every source normalizes its data into `Listing` objects."""
from __future__ import annotations

import abc
from typing import List

from ..models import Listing


class TicketProvider(abc.ABC):
    """A source of ticket listings (SeatGeek, Ticketmaster, StubHub, mock…)."""

    name: str = "base"

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Whether this provider has the credentials/config it needs to run."""

    @abc.abstractmethod
    def fetch(self, config) -> List[Listing]:
        """Return all currently-available listings for the target event(s).

        Implementations should be defensive: network/parse errors must raise
        so the orchestrator can skip this source without crashing the run.
        """
