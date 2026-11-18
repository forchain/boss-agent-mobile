"""
boss_agent.core.job.interfaces.store
====================================
The Job Record Store repository seam (ADR 0013, Spec #231).

Job records carry domain rules the task stream knows nothing about: canonical
deduplication fingerprints, the direct-hire company exclusion pool, the re-application
cool-down window and the daily greeting quota. This port owns the verbs; the
InMemory and PocketBase implementations live in the adapter module until #457 moves
them.

The cool-down and quota math is deliberately identical in both adapters: expiry is
computed from ``applied_at`` (falling back to the ingestion date for platform
historical contacts) and quota counts only greetings the agent actually dispatched.
"""

from abc import ABC, abstractmethod
from typing import Any


class JobRecordStore(ABC):
    """Repository interface for Job Records, exclusion pools and quota accounting."""

    @abstractmethod
    async def upsert_job_record(self, record_data: dict[str, Any]) -> dict[str, Any] | None:
        """Insert a new job record or merge new information into the existing one.

        Returns the stored record, or ``None`` when the input is incomplete or placeholder
        junk that is deliberately skipped ("宁可不录入"). A *rejected write* is never absorbed
        into an empty value: the persistence seam raises its typed broker error
        (``ValidationError`` for a 400, ``TransportError`` for a transport fault) so an
        under-counted quota or exclusion pool surfaces instead of looking like a saved record
        (Spec #303, story #8).
        """

    @abstractmethod
    async def get_job_record_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        """Get a job record by its canonical deduplication fingerprint."""

    @abstractmethod
    async def has_job_fingerprint(self, fingerprint: str) -> bool:
        """Whether a job record with the given fingerprint already exists."""

    @abstractmethod
    async def get_job_record(self, record_id: str) -> dict[str, Any] | None:
        """Get a job record by ID."""

    @abstractmethod
    async def list_job_records(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List job records, optionally filtered by status."""

    @abstractmethod
    async def update_job_record_status(
        self,
        record_id: str,
        status: str,
        match_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update the status and optional match results of a job record.

        Raises KeyError when the record does not exist and RuntimeError when the write
        could not be performed, so a caller can never mistake a failed write for a saved one.
        """

    @abstractmethod
    async def delete_job_record(self, record_id: str) -> bool:
        """Delete a job record by ID. Returns False when absent or on failure."""

    @abstractmethod
    async def get_applied_direct_companies(self, cooldown_days: int = 0) -> set[str]:
        """Distinct direct-hire companies with active communication inside the cool-down window.

        Headhunter channels and masked/confidential company names are never included, since
        they do not represent a shared in-house HR candidate pool.
        """

    @abstractmethod
    async def clear_job_communication(self, record_id: str) -> dict[str, Any]:
        """Transition a communicated job back to `jd_saved`, clearing its dispatch timestamp.

        The extracted JD is preserved so the job can be re-evaluated and re-applied without
        re-scraping the mobile detail page (cool-down expiry or manual clearance).
        """

    @abstractmethod
    async def count_today_applied_jobs(self) -> int:
        """Count how many greetings were dispatched today, in UTC."""
