"""
boss_agent.adapters.outbound.pocketbase.job_store
=================================================
PocketBase Job Record Store adapter (ADR 0013, Spec #231).

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
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from boss_agent.core.job.interfaces.store import JobRecordStore
from boss_agent.core.shared.enums import STATE_RANK, JobRecordStatus
from boss_agent.core.shared.errors import (
    BrokerError,
    ConflictError,
    RecordNotFoundError,
    TransportError,
    ValidationError,
)
from boss_agent.core.shared.identifier_helpers import (
    compute_job_fingerprint,
    is_communication_expired,
    is_direct_hire_company,
    is_invalid_company_name,
    sanitize_tags,
)
from boss_agent.core.shared.keyword_constants import INVALID_JOB_TITLES

from .async_bridge import execute_broker_request

logger = logging.getLogger("boss_agent.adapters.outbound.pocketbase.job_store")

INVALID_COMPANY_NAMES: frozenset[str] = frozenset({"", "未注明公司", "未知公司"})

#: Page size when walking applied direct-hire records from PocketBase.
#: The query is bounded by the cooldown time window rather than an arbitrary
#: record count, and the walk continues until the relevant records are exhausted.
APPLIED_POOL_PAGE_SIZE = 200

#: Availability backstop for the exclusion-pool walk — not a semantic cap.
#: With a permanent cool-down (``cooldown_days <= 0``) the query carries no time bound, so a
#: pathological ``status='applied'`` collection could otherwise page forever on the hot path.
#: 200 pages × 200 rows = 40,000 records is far past any realistic history; tripping it is
#: logged loudly rather than silently truncating the pool, so a real 40k-record history is a
#: signal to raise this (or stop suppressing permanently), not a quiet under-count.
APPLIED_POOL_MAX_PAGES = 200


def _advanced_status(current: str | None, incoming: Any) -> str | None:
    """The status to write when a new observation meets a stored one, or None to keep it.

    Status only ever moves forward, so a later save-only pass cannot silently demote an
    already-applied job. The two documented exceptions both *lower* the rank on purpose:
    an explicit rejection always wins, and a freshly extracted JD lifts a record out of
    its pre-JD limbo.
    """
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
    for key in (
        "company_scale",
        "industry",
        "recruiter_title",
        "salary_range",
        "location",
        # The station is read from the detail page and a posting's office does not move,
        # so a later observation that finds none must never erase one already recorded.
        "location_line",
        "metro_lines",
        "metro_station",
    ):
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
    ``greeting_message`` and ``greeting_source`` move together for the same reason: a
    card-level pass that carries neither must leave a human's approved copy and its
    provenance intact (issue #300).
    """
    updates: dict[str, Any] = {}
    # The greeting and who wrote it travel together (issue #300): a pass that brings no
    # greeting of its own must not erase a human's approved copy, and a pass that brings a
    # new one must not leave the old provenance claim standing.
    if record_data.get("greeting_message"):
        updates["greeting_message"] = record_data["greeting_message"]
    if record_data.get("greeting_source"):
        updates["greeting_source"] = record_data["greeting_source"]
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
    """The canonical field set of a brand-new job record, shared by both adapters.

    The field list is the Collection Schema's, so a column added to ``job_records``
    is written by the store the moment it is declared, and a key the schema does not
    know about is dropped rather than smuggled onto the wire.

    The schema is imported inside the function for the same reason the saved-search
    entities import theirs lazily: ``boss_agent.broker``'s package ``__init__`` imports
    the broker adapter, which imports this module. A module-scope import here made
    ``import boss_agent.job_store`` fail on its own, and the failure was survivable only
    because some *other* module happened to re-enter the broker package afterwards.
    """
    from .collection_schema import JOB_RECORDS, wire_payload

    fields = wire_payload(JOB_RECORDS, record_data)
    fields["fingerprint"] = fingerprint

    status_val = fields.get("status") or JobRecordStatus.UNMATCHED
    if hasattr(status_val, "value"):
        status_val = status_val.value
    fields["status"] = status_val or JobRecordStatus.UNMATCHED.value

    # Boolean and timestamp columns are coerced here because a caller may hand over a
    # value that survived a JSON round-trip (``"false"``) or an ORM-ish truthy object.
    fields["is_headhunter"] = bool(fields.get("is_headhunter", False))
    fields["relaxed_by_whitelist"] = bool(fields.get("relaxed_by_whitelist", False))
    fields["first_seen_at"] = now
    fields["last_seen_at"] = now
    fields["created"] = now
    fields["updated"] = now
    return fields


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
        if record_data.get("match_score") is not None:
            body["match_score"] = record_data["match_score"]
        if "screened_reason" in record_data:
            body["screened_reason"] = record_data["screened_reason"]
        if "screening_stage" in record_data:
            body["screening_stage"] = record_data["screening_stage"]
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
        url = self._jobs_collection_url()
        resp = await execute_broker_request(
            lambda: self.session.get(
                url,
                params={
                    "filter": f"fingerprint={_quote_filter_value(fingerprint)}",
                    "perPage": "1",
                },
                headers=self._headers(),
            ),
            expected_statuses=(200,),
            allow_404=True,
            error_prefix="PocketBase check fingerprint failed",
        )
        if resp.status_code == 404:
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
            fb_resp = await execute_broker_request(
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
                expected_statuses=(200,),
                allow_404=True,
                error_prefix="PocketBase check company+title fallback failed",
            )
            if fb_resp.status_code == 200:
                items = fb_resp.json().get("items", [])
        return items[0] if items else None

    async def _write_job_record(
        self, send: Callable[..., Any], url: str, body: dict[str, Any]
    ) -> requests.Response:
        """Write a job record, truncating an over-long ``job_description`` and retrying once.

        ``send`` is the bound session method to write with (``session.patch`` or
        ``session.post``).
        """
        import asyncio

        from .pocketbase_adapter import (
            LENGTH_RECOVERY_FIELD,
            is_length_rejection_for_job_description,
            resolve_text_constraint_limit,
        )

        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: send(url, json=body, headers=self._headers())
            )
        except (requests.RequestException, ConnectionError, TimeoutError, OSError) as e:
            raise TransportError(f"Job record write failed: {e}") from e

        rejection_text = getattr(resp, "text", None)
        if is_length_rejection_for_job_description(rejection_text):
            description = body.get(LENGTH_RECOVERY_FIELD)
            limit = resolve_text_constraint_limit(rejection_text)
            if isinstance(description, str) and len(description) > limit:
                logger.warning(
                    "PocketBase rejected %s: job_description is %d chars, over the server's %d-char "
                    "text cap. Retrying with a truncated description; the tail is dropped.",
                    url,
                    len(description),
                    limit,
                )
                retry_body = {**body, LENGTH_RECOVERY_FIELD: description[:limit]}
                try:
                    resp = await loop.run_in_executor(
                        None, lambda: send(url, json=retry_body, headers=self._headers())
                    )
                except (requests.RequestException, ConnectionError, TimeoutError, OSError) as e:
                    raise TransportError(f"Job record write retry failed: {e}") from e

        if resp.status_code in (200, 201):
            return resp
        if resp.status_code == 400:
            raise ValidationError(f"PocketBase write rejected (400 Bad Request): {resp.text}")
        if resp.status_code == 404:
            raise RecordNotFoundError(f"PocketBase record not found (404): {resp.text}")
        if resp.status_code == 409:
            raise ConflictError(f"PocketBase write conflict (409 Conflict): {resp.text}")
        if resp.status_code >= 500:
            raise TransportError(f"PocketBase server error ({resp.status_code}): {resp.text}")
        raise BrokerError(f"PocketBase write failed (HTTP {resp.status_code}): {resp.text}")

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
            return None

        self._normalize(record_data, title, comp_name)

        url = self._jobs_collection_url()
        now = datetime.now(UTC).isoformat()

        existing = await self._get_existing(fingerprint, record_data)
        if existing:
            patch_body = self._patch_body(existing, record_data, now)
            # A rejected write raises its typed broker error out of ``_write_job_record``
            # rather than being absorbed here: swallowing a 400 would let a record that was
            # never written pass for a saved one, under-counting the daily greeting quota and
            # the direct-hire exclusion pool (Spec #303, story #8).
            patch_resp = await self._write_job_record(
                self.session.patch,
                f"{url}/{existing['id']}",
                patch_body,
            )
            return patch_resp.json()

        body: dict[str, Any] = {
            **_record_fields(record_data, fingerprint, now),
            "company_name": comp_name or record_data.get("company_name", ""),
            "id": record_data.get("id") or uuid.uuid4().hex[:15],
        }

        resp = await self._write_job_record(self.session.post, url, body)
        return resp.json()

    async def get_job_record_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        url = self._jobs_collection_url()
        filter_expr = f"fingerprint={_quote_filter_value(fingerprint)}"
        resp = await execute_broker_request(
            lambda: self.session.get(
                url,
                params={"filter": filter_expr, "perPage": "1"},
                headers=self._headers(),
            ),
            expected_statuses=(200,),
            allow_404=True,
            error_prefix="PocketBase get_job_record_by_fingerprint failed",
        )
        if resp.status_code == 404:
            return None
        items = resp.json().get("items", [])
        return items[0] if items else None

    async def has_job_fingerprint(self, fingerprint: str) -> bool:
        return await self.get_job_record_by_fingerprint(fingerprint) is not None

    async def get_job_record(self, record_id: str) -> dict[str, Any] | None:
        resp = await execute_broker_request(
            lambda: self.session.get(
                f"{self._jobs_collection_url()}/{record_id}", headers=self._headers()
            ),
            expected_statuses=(200,),
            allow_404=True,
            error_prefix=f"PocketBase get_job_record {record_id} failed",
        )
        if resp.status_code == 404:
            return None
        return resp.json()

    async def list_job_records(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"sort": "-created", "perPage": str(limit)}
        if status:
            if status == "unmatched":
                params["filter"] = (
                    '(status="unmatched" || status="digest_only" || status="jd_saved")'
                )
            else:
                params["filter"] = f"status='{status}'"
        resp = await execute_broker_request(
            lambda: self.session.get(
                self._jobs_collection_url(), params=params, headers=self._headers()
            ),
            expected_statuses=(200,),
            allow_404=True,
            error_prefix="PocketBase list_job_records failed",
        )
        if resp.status_code == 404:
            return []
        return resp.json().get("items", [])

    async def update_job_record_status(
        self,
        record_id: str,
        status: str,
        match_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"status": status}
        if match_data:
            body.update(match_data)
        resp = await execute_broker_request(
            lambda: self.session.patch(
                f"{self._jobs_collection_url()}/{record_id}",
                json=body,
                headers=self._headers(),
            ),
            expected_statuses=(200,),
            allow_404=True,
            error_prefix=f"PocketBase update_job_record_status {record_id} failed",
        )
        if resp.status_code == 404:
            raise KeyError(f"Job record {record_id} not found")
        return resp.json()

    async def delete_job_record(self, record_id: str) -> bool:
        resp = await execute_broker_request(
            lambda: self.session.delete(
                f"{self._jobs_collection_url()}/{record_id}", headers=self._headers()
            ),
            expected_statuses=(200, 204),
            allow_404=True,
            error_prefix=f"PocketBase delete_job_record {record_id} failed",
        )
        return resp.status_code in (200, 204)

    async def get_applied_direct_companies(self, cooldown_days: int = 0) -> set[str]:
        """Collect every direct-hire company with an unexpired communication.

        The cooldown bound is expressed directly in the broker query so the walk is
        bounded by the relevant communication window rather than by an arbitrary record
        count cap. The collection is walked page by page until exhausted (removing the
        previous 5,000-record ceiling), ensuring long-lived candidates never lose their
        oldest relevant contacts.

        Removing that ceiling does not mean "unbounded": a permanent cool-down
        (``cooldown_days <= 0``) leaves the filter with no time bound at all, so the walk is
        still protected by ``APPLIED_POOL_MAX_PAGES`` — an availability backstop, not a
        semantic cap, that logs loudly when it trips.
        """
        url = self._jobs_collection_url()
        base_filter = "status='applied' && is_headhunter!=true"
        if cooldown_days > 0:
            cutoff = datetime.now(UTC) - timedelta(days=cooldown_days)
            cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S.000Z")
            filter_expr = (
                f"{base_filter} && (applied_at >= '{cutoff_str}' || "
                f"((applied_at = '' || applied_at = null) && created >= '{cutoff_str}'))"
            )
        else:
            filter_expr = base_filter

        items: list[dict[str, Any]] = []
        page = 1
        while True:
            if page > APPLIED_POOL_MAX_PAGES:
                logger.error(
                    "Exclusion-pool walk hit the %d-page availability backstop after %d rows "
                    "for %s; the pool may be truncated. This is not a semantic cap — raise "
                    "APPLIED_POOL_MAX_PAGES or the cooldown if this history is genuine.",
                    APPLIED_POOL_MAX_PAGES,
                    len(items),
                    url,
                )
                break
            params = {
                "filter": filter_expr,
                "page": str(page),
                "perPage": str(APPLIED_POOL_PAGE_SIZE),
                # is_headhunter has to be projected: the guard below reads it per record.
                "fields": "company_name,is_headhunter,applied_at,created",
            }
            resp = await execute_broker_request(
                lambda p=params: self.session.get(url, params=p, headers=self._headers()),
                expected_statuses=(200,),
                error_prefix=f"PocketBase get_applied_direct_companies failed at page {page}",
            )
            data = resp.json()
            batch = data.get("items", [])
            items.extend(batch)
            total_pages = data.get("totalPages", 1)
            if page >= total_pages or len(batch) < APPLIED_POOL_PAGE_SIZE:
                break
            page += 1

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
        body = {
            "status": JobRecordStatus.JD_SAVED.value,
            # Empty string is PocketBase's canonical way to clear an optional date field.
            "applied_at": "",
            "applied_source": "",
        }
        resp = await execute_broker_request(
            lambda: self.session.patch(
                f"{self._jobs_collection_url()}/{record_id}",
                json=body,
                headers=self._headers(),
            ),
            expected_statuses=(200,),
            allow_404=True,
            error_prefix=f"PocketBase clear_job_communication {record_id} failed",
        )
        if resp.status_code == 404:
            raise KeyError(f"Job record {record_id} not found")
        return resp.json()

    async def count_today_applied_jobs(self) -> int:
        now = datetime.now(UTC)
        today_midnight = now.strftime("%Y-%m-%d 00:00:00.000Z")
        tomorrow_midnight = (now + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00.000Z")
        resp = await execute_broker_request(
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
            expected_statuses=(200,),
            error_prefix="PocketBase count_today_applied_jobs failed",
        )
        return int(resp.json().get("totalItems", 0))
