"""
boss_agent.adapters.memory.job_store
====================================
Volatile Job Record Store for tests and local development.

The record-mapping helpers are shared with the PocketBase adapter so both stores write
the same field set, honour the same monotonic-status rule and keep the same sticky
fields.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from boss_agent.adapters.outbound.pocketbase.job_store import (
    INVALID_COMPANY_NAMES,
    _advanced_status,
    _placeholder_fill,
    _record_fields,
    _sticky_field_updates,
)
from boss_agent.core.job.interfaces.store import JobRecordStore
from boss_agent.core.shared.enums import JobRecordStatus
from boss_agent.core.shared.identifier_helpers import (
    compute_job_fingerprint,
    is_communication_expired,
    is_direct_hire_company,
    is_invalid_company_name,
    sanitize_tags,
)
from boss_agent.core.shared.keyword_constants import INVALID_JOB_TITLES

logger = logging.getLogger("boss_agent.adapters.memory.job_store")


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
        if record_data.get("match_score") is not None:
            rec["match_score"] = record_data["match_score"]
        if "screened_reason" in record_data:
            rec["screened_reason"] = record_data["screened_reason"]
        if "screening_stage" in record_data:
            rec["screening_stage"] = record_data["screening_stage"]
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

    async def upsert_job_record(self, record_data: dict[str, Any]) -> dict[str, Any] | None:
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
            return None

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
