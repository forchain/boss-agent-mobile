"""
boss_agent.job_store
====================
Dedicated Job Record Store repository seam (ADR 0013, Spec #231).

Job records carry domain rules the task stream knows nothing about: canonical
deduplication fingerprints, the direct-hire company exclusion pool, the re-application
cool-down window and the daily greeting quota. Those rules live here, shared by an
in-memory adapter for tests and a PocketBase adapter for production, instead of being
shredded across whichever broker happened to be in use.

The cool-down and quota math is deliberately identical in both adapters: expiry is
computed from ``applied_at`` (falling back to the ingestion date for platform
historical contacts) and quota counts only greetings the agent actually dispatched.
"""

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from boss_agent.models import (
    JobRecordStatus,
    compute_job_fingerprint,
    is_communication_expired,
    is_direct_hire_company,
    is_invalid_company_name,
    sanitize_tags,
)

logger = logging.getLogger("boss_agent.job_store")

INVALID_JOB_TITLES: frozenset[str] = frozenset(
    {"", "未注明职位", "未注明岗位", "未知职位", "未知岗位"}
)
INVALID_COMPANY_NAMES: frozenset[str] = frozenset({"", "未注明公司", "未知公司"})

# Same trade-off as the web dashboard's MAX_PAGES walk: enough pages for any realistic
# contact history, bounded so a runaway collection cannot stall the worker.
APPLIED_POOL_PAGE_SIZE = 200
APPLIED_POOL_MAX_PAGES = 25


def _advanced_status(current: str | None, incoming: Any) -> str | None:
    """The status to write when a new observation meets a stored one, or None to keep it.

    Status only ever moves forward, so a later save-only pass cannot silently demote an
    already-applied job. The two documented exceptions both *lower* the rank on purpose:
    an explicit rejection always wins, and a freshly extracted JD lifts a record out of
    its pre-JD limbo.
    """
    from boss_agent.models import STATE_RANK

    status_val = incoming.value if hasattr(incoming, "value") else incoming
    if not status_val:
        return None
    cur_rank = STATE_RANK.get(current or "", 0)
    new_rank = STATE_RANK.get(status_val, 0)
    if (
        new_rank > cur_rank
        or status_val == JobRecordStatus.IGNORED
        or (
            status_val == JobRecordStatus.JD_SAVED
            and current in (JobRecordStatus.UNMATCHED, JobRecordStatus.DIGEST_ONLY)
        )
    ):
        return status_val
    return None


def _quote_filter_value(value: Any) -> str:
    """Render a scraped value as a quoted PocketBase filter literal.

    Company names and titles reach the filter verbatim, so a raw quote is a syntax error
    that makes the whole query fail — which silently turns a dedup lookup into a duplicate
    insert. Escape the escape character first, then the quote.
    """
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _placeholder_fill(existing: dict[str, Any], record_data: dict[str, Any]) -> dict[str, Any]:
    """Fields that fill a gap in the stored record but never overwrite what is known."""
    filled: dict[str, Any] = {}
    for key in ("company_scale", "industry", "recruiter_title", "salary_range", "location"):
        if record_data.get(key) and not existing.get(key):
            filled[key] = record_data[key]
    return filled


def _sticky_field_updates(existing: dict[str, Any], record_data: dict[str, Any]) -> dict[str, Any]:
    """Merge rules for the fields an observation may enrich but never erase.

    One authority for both adapters, so a re-scrape cannot blank what the record already
    holds. The two list fields differ on purpose: ``tags`` are card facets, so the first
    non-empty read wins and a later empty one is ignored, while ``jd_key_requirements`` is
    re-extracted from the JD text and may be refined by a later non-empty read.
    ``is_headhunter`` may be discovered by a later observation but never downgraded — a
    headhunter channel recorded as direct-hire would wrongly join the same-company pool.
    """
    updates: dict[str, Any] = {}
    if record_data.get("tags") and not existing.get("tags"):
        updates["tags"] = record_data["tags"]
    if record_data.get("jd_key_requirements"):
        updates["jd_key_requirements"] = record_data["jd_key_requirements"]
    if "is_headhunter" in record_data and (
        record_data["is_headhunter"] or existing.get("is_headhunter") is None
    ):
        updates["is_headhunter"] = record_data["is_headhunter"]
    # Commute distance (spec #209): only overwrite with a known value so
    # a later probe that fails open never erases a measured distance.
    if record_data.get("commute_distance_km") is not None:
        updates["commute_distance_km"] = record_data["commute_distance_km"]
    if record_data.get("commute_distance_text"):
        updates["commute_distance_text"] = record_data["commute_distance_text"]
    return updates


def _record_fields(record_data: dict[str, Any], fingerprint: str, now: str) -> dict[str, Any]:
    """The canonical field set of a brand-new job record, shared by both adapters."""
    status_val = record_data.get("status", JobRecordStatus.UNMATCHED)
    if hasattr(status_val, "value"):
        status_val = status_val.value
    return {
        "fingerprint": fingerprint,
        "title": record_data.get("title", ""),
        "company_name": record_data.get("company_name", ""),
        "recruiter_name": record_data.get("recruiter_name", ""),
        "recruiter_title": record_data.get("recruiter_title", ""),
        "is_headhunter": record_data.get("is_headhunter", False),
        "company_scale": record_data.get("company_scale", ""),
        "industry": record_data.get("industry", ""),
        "tags": record_data.get("tags", []),
        "salary_range": record_data.get("salary_range", ""),
        "location": record_data.get("location", ""),
        "digest": record_data.get("digest", ""),
        "job_description": record_data.get("job_description", ""),
        "status": status_val or JobRecordStatus.UNMATCHED.value,
        "screened_reason": record_data.get("screened_reason", ""),
        "relaxed_by_whitelist": bool(record_data.get("relaxed_by_whitelist", False)),
        "screening_audit": record_data.get("screening_audit", ""),
        "applied_at": record_data.get("applied_at"),
        "applied_source": record_data.get("applied_source", ""),
        "commute_distance_km": record_data.get("commute_distance_km"),
        "commute_distance_text": record_data.get("commute_distance_text", ""),
        "match_score": record_data.get("match_score"),
        "jd_key_requirements": record_data.get("jd_key_requirements", []),
        "greeting_message": record_data.get("greeting_message", ""),
        "search_keywords": record_data.get("search_keywords", []),
        "source_task_id": record_data.get("source_task_id"),
        "first_seen_at": now,
        "last_seen_at": now,
        "created": now,
        "updated": now,
    }


class JobRecordStore(ABC):
    """Repository interface for Job Records, exclusion pools and quota accounting."""

    @abstractmethod
    async def upsert_job_record(self, record_data: dict[str, Any]) -> dict[str, Any]:
        """Insert a new job record or merge new information into the existing one."""

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


class JobRecordStoreFacade:
    """Legacy delegating surface keeping broker callers working during the migration.

    ADR 0013 retains these wrappers for one transition cycle so existing worker daemons
    and the web dashboard's broker handle keep operating while callers move to
    ``broker.job_store``. New code should talk to the store directly.
    """

    job_store: JobRecordStore

    async def upsert_job_record(self, record_data: dict[str, Any]) -> dict[str, Any]:
        return await self.job_store.upsert_job_record(record_data)

    async def get_job_record_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        return await self.job_store.get_job_record_by_fingerprint(fingerprint)

    async def has_job_fingerprint(self, fingerprint: str) -> bool:
        return await self.job_store.has_job_fingerprint(fingerprint)

    async def get_job_record(self, record_id: str) -> dict[str, Any] | None:
        return await self.job_store.get_job_record(record_id)

    async def list_job_records(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return await self.job_store.list_job_records(status=status, limit=limit)

    async def update_job_record_status(
        self,
        record_id: str,
        status: str,
        match_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.job_store.update_job_record_status(record_id, status, match_data)

    async def delete_job_record(self, record_id: str) -> bool:
        return await self.job_store.delete_job_record(record_id)

    async def get_applied_direct_companies(self, cooldown_days: int = 0) -> set[str]:
        return await self.job_store.get_applied_direct_companies(cooldown_days=cooldown_days)

    async def clear_job_communication(self, record_id: str) -> dict[str, Any]:
        return await self.job_store.clear_job_communication(record_id)

    async def count_today_applied_jobs(self) -> int:
        return await self.job_store.count_today_applied_jobs()


class InMemoryJobRecordStore(JobRecordStore):
    """Volatile job record store for tests and local development."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._fingerprint_index: dict[str, str] = {}

    def _merge_existing(self, rec: dict[str, Any], record_data: dict[str, Any], now: str) -> None:
        """Fold a newer observation of the same job into its stored record.

        Status only ever moves forward (with the two documented exceptions), so a later
        save-only pass cannot silently demote an already-applied job.
        """
        rec["last_seen_at"] = now
        title = (record_data.get("title") or "").strip()
        if title and title not in INVALID_JOB_TITLES and title != rec.get("title"):
            rec["title"] = title
        new_kw = record_data.get("search_keywords", [])
        rec["search_keywords"] = list(dict.fromkeys((rec.get("search_keywords") or []) + new_kw))
        new_recruiter = record_data.get("recruiter_name")
        if new_recruiter and new_recruiter not in ("", "招聘者"):
            rec["recruiter_name"] = new_recruiter
        if record_data.get("digest") and not rec.get("digest"):
            rec["digest"] = record_data["digest"]
        if record_data.get("job_description"):
            rec["job_description"] = record_data["job_description"]
        advanced = _advanced_status(rec.get("status"), record_data.get("status"))
        if advanced:
            rec["status"] = advanced
        rec.update(_placeholder_fill(rec, record_data))
        rec.update(_sticky_field_updates(rec, record_data))
        if record_data.get("greeting_message"):
            rec["greeting_message"] = record_data["greeting_message"]
        if record_data.get("match_score") is not None:
            rec["match_score"] = record_data["match_score"]
        if "screened_reason" in record_data:
            rec["screened_reason"] = record_data["screened_reason"]
        if "relaxed_by_whitelist" in record_data:
            rec["relaxed_by_whitelist"] = bool(record_data["relaxed_by_whitelist"])
        if record_data.get("screening_audit"):
            rec["screening_audit"] = record_data["screening_audit"]
        # A dispatch stamp is never overwritten by a later observation without one, so
        # re-scraping a job cannot reset the daily quota or the cool-down clock.
        if record_data.get("applied_at"):
            rec["applied_at"] = record_data["applied_at"]
        if record_data.get("applied_source"):
            rec["applied_source"] = record_data["applied_source"]
        rec["updated"] = now

    def _find_duplicate(self, fingerprint: str, record_data: dict[str, Any]) -> str | None:
        """Resolve the record a fresh observation belongs to.

        Exact fingerprint first; then, for generic recruiter placeholders, the same
        company + title, so the roster of "招聘者" cards does not duplicate one job.
        """
        existing_id = self._fingerprint_index.get(fingerprint) if fingerprint else None
        if existing_id:
            return existing_id
        title = (record_data.get("title") or "").strip()
        comp_name = (record_data.get("company_name") or "").strip()
        if comp_name and title and record_data.get("recruiter_name") in ("", "招聘者"):
            for cand_id, cand_rec in self._records.items():
                if (
                    cand_rec.get("company_name", "").strip() == comp_name
                    and cand_rec.get("title", "").strip() == title
                ):
                    return cand_id
        return None

    async def upsert_job_record(self, record_data: dict[str, Any]) -> dict[str, Any]:
        title = (record_data.get("title") or "").strip()
        comp_name = (record_data.get("company_name") or "").strip()
        fingerprint = record_data.get("fingerprint") or (
            compute_job_fingerprint(
                company_name=comp_name,
                title=title,
                recruiter_name=record_data.get("recruiter_name", ""),
            )
            if comp_name
            else ""
        )

        existing_id = self._find_duplicate(fingerprint, record_data)
        if not existing_id and (
            not title
            or title in INVALID_JOB_TITLES
            or not comp_name
            or comp_name in INVALID_COMPANY_NAMES
            or is_invalid_company_name(comp_name)
        ):
            logger.warning(
                "Rejected upsert of incomplete or invalid job record: title='%s', company='%s'",
                title,
                comp_name,
            )
            return {}

        r_name = record_data.get("recruiter_name", "")
        r_title = record_data.get("recruiter_title", "")
        loc = record_data.get("location", "")
        for tag_key in ("tags", "jd_key_requirements"):
            if isinstance(record_data.get(tag_key), list):
                record_data[tag_key] = sanitize_tags(
                    record_data[tag_key],
                    recruiter_name=r_name,
                    recruiter_title=r_title,
                    location=loc,
                    company_name=comp_name,
                    title=title,
                )

        now = datetime.now(UTC).isoformat()
        if existing_id:
            rec = self._records[existing_id]
            self._merge_existing(rec, record_data, now)
            return dict(rec)

        rec_id = str(record_data.get("id") or uuid.uuid4().hex[:15])
        new_rec: dict[str, Any] = {
            "id": rec_id,
            **_record_fields(record_data, fingerprint, now),
        }
        self._records[rec_id] = new_rec
        if fingerprint:
            self._fingerprint_index[fingerprint] = rec_id
        return dict(new_rec)

    async def get_job_record_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        rec_id = self._fingerprint_index.get(fingerprint)
        if rec_id and rec_id in self._records:
            return dict(self._records[rec_id])
        return None

    async def has_job_fingerprint(self, fingerprint: str) -> bool:
        return fingerprint in self._fingerprint_index

    async def get_job_record(self, record_id: str) -> dict[str, Any] | None:
        rec = self._records.get(record_id)
        return dict(rec) if rec else None

    async def list_job_records(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        records = list(self._records.values())
        if status:
            if status == "unmatched":
                records = [
                    r
                    for r in records
                    if r.get("status") in ("unmatched", "digest_only", "jd_saved")
                ]
            else:
                records = [r for r in records if r.get("status") == status]
        records.sort(key=lambda x: str(x.get("created", "")), reverse=True)
        return [dict(r) for r in records[:limit]]

    async def update_job_record_status(
        self,
        record_id: str,
        status: str,
        match_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rec = self._records.get(record_id)
        if not rec:
            raise KeyError(f"Job record {record_id} not found")
        rec["status"] = status
        if match_data:
            rec.update(match_data)
        rec["updated"] = datetime.now(UTC).isoformat()
        return dict(rec)

    async def delete_job_record(self, record_id: str) -> bool:
        rec = self._records.pop(record_id, None)
        if rec is None:
            return False
        fp = rec.get("fingerprint")
        if fp and self._fingerprint_index.get(fp) == record_id:
            del self._fingerprint_index[fp]
        return True

    async def get_applied_direct_companies(self, cooldown_days: int = 0) -> set[str]:
        companies: set[str] = set()
        for rec in self._records.values():
            if rec.get("status") != JobRecordStatus.APPLIED:
                continue
            name = (rec.get("company_name") or "").strip()
            if not is_direct_hire_company(name, rec.get("is_headhunter")):
                continue
            if is_communication_expired(rec, cooldown_days):
                continue
            companies.add(name)
        return companies

    async def clear_job_communication(self, record_id: str) -> dict[str, Any]:
        rec = self._records.get(record_id)
        if not rec:
            raise KeyError(f"Job record {record_id} not found")
        rec["status"] = JobRecordStatus.JD_SAVED.value
        rec["applied_at"] = ""
        rec["applied_source"] = ""
        rec["updated"] = datetime.now(UTC).isoformat()
        return dict(rec)

    async def count_today_applied_jobs(self) -> int:
        """Count greetings actually dispatched today (UTC), keyed strictly on `applied_at`.

        Platform historical contacts imported as `applied` carry no `applied_at` and are
        therefore excluded from the daily quota.
        """
        today_prefix = datetime.now(UTC).strftime("%Y-%m-%d")
        return sum(
            1
            for rec in self._records.values()
            if str(rec.get("applied_at") or "").startswith(today_prefix)
        )


class PocketBaseJobRecordStore(JobRecordStore):
    """PocketBase-backed job record store (REST adapter)."""

    def __init__(
        self,
        base_url: str,
        session: requests.Session,
        headers: Callable[[], dict[str, str]],
    ) -> None:
        self.base_url = base_url
        self.session = session
        self._headers = headers

    def _jobs_collection_url(self) -> str:
        return f"{self.base_url}/api/collections/job_records/records"

    @staticmethod
    def _normalize(record_data: dict[str, Any], title: str, comp_name: str) -> dict[str, Any]:
        """Sanitize the tag lists carried by the record in place."""
        r_name = record_data.get("recruiter_name", "")
        r_title = record_data.get("recruiter_title", "")
        loc = record_data.get("location", "")
        for tag_key in ("tags", "jd_key_requirements"):
            if isinstance(record_data.get(tag_key), list):
                record_data[tag_key] = sanitize_tags(
                    record_data[tag_key],
                    recruiter_name=r_name,
                    recruiter_title=r_title,
                    location=loc,
                    company_name=comp_name,
                    title=title,
                )
        return record_data

    @staticmethod
    def _patch_body(
        existing: dict[str, Any], record_data: dict[str, Any], now: str
    ) -> dict[str, Any]:
        """Build the merge patch for an existing record, honouring monotonic status."""
        title = (record_data.get("title") or "").strip()
        new_kw = record_data.get("search_keywords", [])
        body: dict[str, Any] = {
            "last_seen_at": now,
            "search_keywords": list(
                dict.fromkeys((existing.get("search_keywords") or []) + new_kw)
            ),
        }
        if title and title not in INVALID_JOB_TITLES and title != existing.get("title"):
            body["title"] = title
        new_recruiter = record_data.get("recruiter_name")
        if (
            new_recruiter
            and new_recruiter not in ("", "招聘者")
            and new_recruiter != existing.get("recruiter_name")
        ):
            body["recruiter_name"] = new_recruiter
        if record_data.get("digest") and not existing.get("digest"):
            body["digest"] = record_data["digest"]
        if record_data.get("job_description"):
            body["job_description"] = record_data["job_description"]
        advanced = _advanced_status(existing.get("status"), record_data.get("status"))
        if advanced:
            body["status"] = advanced
        body.update(_placeholder_fill(existing, record_data))
        body.update(_sticky_field_updates(existing, record_data))
        if record_data.get("greeting_message"):
            body["greeting_message"] = record_data["greeting_message"]
        if record_data.get("match_score") is not None:
            body["match_score"] = record_data["match_score"]
        if "screened_reason" in record_data:
            body["screened_reason"] = record_data["screened_reason"]
        if "relaxed_by_whitelist" in record_data:
            body["relaxed_by_whitelist"] = bool(record_data["relaxed_by_whitelist"])
        if record_data.get("screening_audit"):
            body["screening_audit"] = record_data["screening_audit"]
        if record_data.get("applied_at"):
            body["applied_at"] = record_data["applied_at"]
        if record_data.get("applied_source"):
            body["applied_source"] = record_data["applied_source"]
        return body

    async def _get_existing(
        self, fingerprint: str, record_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Look the record up by fingerprint, then by company + title for generic recruiters."""
        import asyncio

        url = self._jobs_collection_url()
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: self.session.get(
                url,
                params={
                    "filter": f"fingerprint={_quote_filter_value(fingerprint)}",
                    "perPage": "1",
                },
                headers=self._headers(),
            ),
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("items", [])
        comp_name = (record_data.get("company_name") or "").strip()
        title = (record_data.get("title") or "").strip()
        if (
            not items
            and comp_name
            and title
            and record_data.get("recruiter_name") in ("", "招聘者")
        ):
            fb_resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    params={
                        "filter": (
                            f"company_name={_quote_filter_value(comp_name)}"
                            f" && title={_quote_filter_value(title)}"
                        ),
                        "perPage": "1",
                    },
                    headers=self._headers(),
                ),
            )
            if fb_resp.status_code == 200:
                items = fb_resp.json().get("items", [])
        return items[0] if items else None

    async def _write_job_record(
        self, send: Callable[..., Any], url: str, body: dict[str, Any]
    ) -> Any:
        """Write a job record, truncating an over-long ``job_description`` and retrying once.

        ``send`` is the bound session method to write with (``session.patch`` or
        ``session.post``). A collection still carrying PocketBase's implicit text cap rejects
        the whole write for a full expanded JD, and the caller then reports an empty upsert —
        the enriched record is lost for exactly the comprehensive postings the JD matters most
        for. One retry at the server's own reported boundary keeps the record; the truncation
        is logged as a warning because the tail is genuinely lost.
        """
        import asyncio

        from boss_agent.broker.pocketbase_adapter import (
            LENGTH_RECOVERY_FIELD,
            is_length_rejection_for_job_description,
            resolve_text_constraint_limit,
        )

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: send(url, json=body, headers=self._headers())
        )

        rejection_text = getattr(response, "text", None)
        if not is_length_rejection_for_job_description(rejection_text):
            return response

        description = body.get(LENGTH_RECOVERY_FIELD)
        limit = resolve_text_constraint_limit(rejection_text)
        if not isinstance(description, str) or len(description) <= limit:
            # The rejected field is not the one we can shrink: report the original failure.
            return response

        logger.warning(
            "PocketBase rejected %s: job_description is %d chars, over the server's %d-char "
            "text cap. Retrying with a truncated description; the tail is dropped.",
            url,
            len(description),
            limit,
        )
        retry_body = {**body, LENGTH_RECOVERY_FIELD: description[:limit]}
        return await loop.run_in_executor(
            None, lambda: send(url, json=retry_body, headers=self._headers())
        )

    async def upsert_job_record(self, record_data: dict[str, Any]) -> dict[str, Any]:
        import asyncio

        title = (record_data.get("title") or "").strip()
        comp_name = (record_data.get("company_name") or "").strip()
        fingerprint = record_data.get("fingerprint") or (
            compute_job_fingerprint(
                company_name=comp_name,
                title=title,
                recruiter_name=record_data.get("recruiter_name", ""),
            )
            if comp_name
            else ""
        )
        if (
            not title
            or title in INVALID_JOB_TITLES
            or not comp_name
            or comp_name in INVALID_COMPANY_NAMES
            or is_invalid_company_name(comp_name)
        ):
            logger.warning(
                "Rejected upsert of incomplete or invalid job record: title='%s', company='%s'",
                title,
                comp_name,
            )
            return {}

        self._normalize(record_data, title, comp_name)

        url = self._jobs_collection_url()
        now = datetime.now(UTC).isoformat()
        loop = asyncio.get_running_loop()

        try:
            existing = await self._get_existing(fingerprint, record_data)
            if existing:
                patch_body = self._patch_body(existing, record_data, now)
                patch_resp = await self._write_job_record(
                    self.session.patch,
                    f"{url}/{existing['id']}",
                    patch_body,
                )
                if patch_resp.status_code == 200:
                    return patch_resp.json()
                logger.error(
                    "Failed to patch job record %s in PocketBase (%d): %s",
                    existing["id"],
                    patch_resp.status_code,
                    patch_resp.text,
                )
        except Exception as e:
            logger.warning("PocketBase check fingerprint exception: %s", e)

        body: dict[str, Any] = {
            **_record_fields(record_data, fingerprint, now),
            "company_name": comp_name or record_data.get("company_name", ""),
            "id": record_data.get("id") or uuid.uuid4().hex[:15],
        }

        try:
            resp = await self._write_job_record(self.session.post, url, body)
            if resp.status_code in (200, 201):
                return resp.json()
            logger.error(
                "Failed to insert job record to PocketBase (%d): %s",
                resp.status_code,
                resp.text,
            )
        except Exception as e:
            logger.warning("PocketBase insert job record exception: %s", e)

        return {}

    async def get_job_record_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        import asyncio

        url = self._jobs_collection_url()
        filter_expr = f"fingerprint={_quote_filter_value(fingerprint)}"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    params={"filter": filter_expr, "perPage": "1"},
                    headers=self._headers(),
                ),
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    return items[0]
        except Exception as e:
            logger.warning("PocketBase get_job_record_by_fingerprint failed: %s", e)
        return None

    async def has_job_fingerprint(self, fingerprint: str) -> bool:
        return await self.get_job_record_by_fingerprint(fingerprint) is not None

    async def get_job_record(self, record_id: str) -> dict[str, Any] | None:
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    f"{self._jobs_collection_url()}/{record_id}", headers=self._headers()
                ),
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning("PocketBase get_job_record failed: %s", e)
        return None

    async def list_job_records(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        import asyncio

        params: dict[str, Any] = {"sort": "-created", "perPage": str(limit)}
        if status:
            if status == "unmatched":
                params["filter"] = (
                    '(status="unmatched" || status="digest_only" || status="jd_saved")'
                )
            else:
                params["filter"] = f"status='{status}'"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    self._jobs_collection_url(), params=params, headers=self._headers()
                ),
            )
            if resp.status_code == 200:
                return resp.json().get("items", [])
        except Exception as e:
            logger.warning("PocketBase list_job_records failed: %s", e)
        return []

    async def update_job_record_status(
        self,
        record_id: str,
        status: str,
        match_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import asyncio

        body: dict[str, Any] = {"status": status}
        if match_data:
            body.update(match_data)
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.patch(
                    f"{self._jobs_collection_url()}/{record_id}",
                    json=body,
                    headers=self._headers(),
                ),
            )
        except Exception as e:
            raise RuntimeError(f"Failed to update job record {record_id}: {e}") from e
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            raise KeyError(f"Job record {record_id} not found")
        # Never hand back a plausible-looking record for a write that did not land: the
        # caller cannot tell the difference, and the next quota or cool-down read cannot either.
        raise RuntimeError(
            f"Failed to update job record {record_id} in PocketBase "
            f"({resp.status_code}): {resp.text}"
        )

    async def delete_job_record(self, record_id: str) -> bool:
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.delete(
                    f"{self._jobs_collection_url()}/{record_id}", headers=self._headers()
                ),
            )
            if resp.status_code in (200, 204):
                return True
            if resp.status_code != 404:
                logger.warning(
                    "PocketBase delete_job_record returned %s: %s", resp.status_code, resp.text
                )
            return False
        except Exception as e:
            logger.warning("PocketBase delete_job_record failed: %s", e)
            return False

    async def get_applied_direct_companies(self, cooldown_days: int = 0) -> set[str]:
        """Collect every direct-hire company with an unexpired communication.

        The pool is walked page by page: a candidate with more than one page of lifetime
        contacts would otherwise lose the older anchors and be re-contacted at a company
        they have already approached.
        """
        import asyncio

        url = self._jobs_collection_url()
        loop = asyncio.get_running_loop()

        items: list[dict[str, Any]] = []
        try:
            for page in range(1, APPLIED_POOL_MAX_PAGES + 1):
                params = {
                    "filter": "status='applied' && is_headhunter!=true",
                    "page": str(page),
                    "perPage": str(APPLIED_POOL_PAGE_SIZE),
                    # is_headhunter has to be projected: the guard below reads it per record.
                    "fields": "company_name,is_headhunter,applied_at,created",
                }
                resp = await loop.run_in_executor(
                    None,
                    lambda p=params: self.session.get(url, params=p, headers=self._headers()),
                )
                if resp.status_code != 200:
                    logger.warning(
                        "PocketBase get_applied_direct_companies stopped at page %d (%d)",
                        page,
                        resp.status_code,
                    )
                    break
                batch = resp.json().get("items", [])
                items.extend(batch)
                if len(batch) < APPLIED_POOL_PAGE_SIZE:
                    break
        except Exception as e:
            logger.warning("PocketBase get_applied_direct_companies failed: %s", e)
            return set()

        companies: set[str] = set()
        for item in items:
            name = (item.get("company_name") or "").strip()
            if not is_direct_hire_company(name, item.get("is_headhunter")):
                continue
            if is_communication_expired(item, cooldown_days):
                continue
            companies.add(name)
        return companies

    async def clear_job_communication(self, record_id: str) -> dict[str, Any]:
        import asyncio

        body = {
            "status": JobRecordStatus.JD_SAVED.value,
            # Empty string is PocketBase's canonical way to clear an optional date field.
            "applied_at": "",
            "applied_source": "",
        }
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.patch(
                    f"{self._jobs_collection_url()}/{record_id}",
                    json=body,
                    headers=self._headers(),
                ),
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error(
                "Failed to release job communication %s in PocketBase (%d): %s",
                record_id,
                resp.status_code,
                resp.text,
            )
        except Exception as e:
            logger.warning("PocketBase clear_job_communication failed: %s", e)
        return {}

    async def count_today_applied_jobs(self) -> int:
        import asyncio

        now = datetime.now(UTC)
        today_midnight = now.strftime("%Y-%m-%d 00:00:00.000Z")
        tomorrow_midnight = (now + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00.000Z")
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    self._jobs_collection_url(),
                    params={
                        # Bounded on both ends so a future-dated stamp cannot consume a
                        # slot, matching the in-memory adapter's exact-day count.
                        "filter": (
                            f"applied_at>='{today_midnight}' && applied_at<'{tomorrow_midnight}'"
                        ),
                        "perPage": "1",
                    },
                    headers=self._headers(),
                ),
            )
            if resp.status_code == 200:
                return int(resp.json().get("totalItems", 0))
        except Exception as e:
            logger.warning("PocketBase count_today_applied_jobs failed: %s", e)
        return 0
