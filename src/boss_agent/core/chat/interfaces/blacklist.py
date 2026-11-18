"""The blacklist-persistence port triage writes through."""

from pathlib import Path
from typing import Protocol

from boss_agent.core.job.domain.screening_policy import ScreeningPolicy


class BlacklistSink(Protocol):
    """Persist one company blacklist entry.

    The in-memory Screening Policy is updated by the run itself; this port owns only
    the durable write, so a triage run never performs file IO.
    """

    def persist_company_blacklist(
        self, policy: ScreeningPolicy, company_name: str
    ) -> Path | None: ...
