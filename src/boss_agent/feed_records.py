"""
boss_agent.feed_records
=======================
Job-record payloads assembled from what a feed run has seen.

Navigating the app and deciding what a job is worth are the pipeline's job; the *shape* of
the record it persists is a separate concern. Keeping those builders here as pure functions
over a card and a detail-page posting means the widest payload in the feed path — the card
facets and the enriched record — can be read and tested without a driver or a device.
"""

from typing import Any

from .enums import JobRecordStatus
from .identifier_helpers import is_invalid_company_name
from .job_entities import JobCardBrief, JobPosting
from .job_store import INVALID_JOB_TITLES
from .keyword_constants import INVALID_COMPANY_NAMES
from .screening import CardScreeningVerdict


def effective_title(posting: Any, card: JobCardBrief) -> str:
    """Prefer the detail page's title, falling back to the card's."""
    title = (posting.title or "").strip()
    if title and title not in INVALID_JOB_TITLES:
        return title
    return (card.title or "").strip()


def effective_company(posting: Any, card: JobCardBrief) -> str:
    """Prefer the detail page's company unless it is a placeholder."""
    company = (posting.company_name or "").strip()
    if company and company not in INVALID_COMPANY_NAMES and not is_invalid_company_name(company):
        return company
    return (card.company_name or "").strip()


def card_facets_record(
    card: JobCardBrief, *, keyword: str | None, source_task_id: str | None
) -> dict[str, Any]:
    """Card facets as a persistable record payload."""
    digest = getattr(card, "digest", "") or getattr(card, "snippet", "") or ""
    return {
        "fingerprint": card.fingerprint,
        "title": card.title,
        "company_name": card.company_name,
        "recruiter_name": card.recruiter_name,
        "recruiter_title": getattr(card, "recruiter_title", "") or "",
        "is_headhunter": getattr(card, "is_headhunter", False),
        "company_scale": getattr(card, "company_scale", "") or "",
        "industry": getattr(card, "industry", "") or "",
        "tags": list(getattr(card, "tags", None) or []),
        "salary_range": getattr(card, "salary_range", "") or "",
        "location": getattr(card, "location", "") or "",
        "digest": digest,
        "job_description": "",
        "jd_key_requirements": list(getattr(card, "tags", None) or []),
        "search_keywords": [keyword] if keyword else [],
        "source_task_id": source_task_id,
        "commute_distance_km": getattr(card, "commute_distance_km", None),
        "commute_distance_text": getattr(card, "commute_distance_text", "") or "",
    }


def card_record(
    card: JobCardBrief,
    *,
    keyword: str | None,
    source_task_id: str | None,
    verdict: CardScreeningVerdict | None,
    existing_record: dict[str, Any] | None,
) -> dict[str, Any]:
    """The optimistic record written before the detail page is even opened."""
    preserved_jd = (existing_record or {}).get("job_description", "")
    return {
        **card_facets_record(card, keyword=keyword, source_task_id=source_task_id),
        "job_description": preserved_jd,
        "status": (
            JobRecordStatus.JD_SAVED.value
            if preserved_jd.strip()
            else JobRecordStatus.UNMATCHED.value
        ),
        "relaxed_by_whitelist": bool(verdict and verdict.relaxed_by_whitelist),
        "screening_audit": verdict.screening_audit if verdict else "",
    }


def posting_from_record(
    record: dict[str, Any],
    card: JobCardBrief,
    job_description: str,
    commute_distance_km: float | None = None,
    commute_distance_text: str = "",
) -> JobPosting:
    """The posting a stored Job Record describes, without reading the detail page again.

    The inverse of :func:`enriched_record`, and it lives beside it on purpose: a card
    visited through #301's inventory path must be judged from exactly the same facets a
    freshly extracted posting is judged from. Card facets win where the record has nothing
    to add, and a placeholder in either is not an answer — the recruiter name drives the
    greeting's salutation, so a mapping that disagreed with the live one would greet the
    same posting differently depending on which path read it.
    """
    stored_title = str(record.get("title") or "").strip()
    posting = JobPosting(
        title=stored_title or card.title,
        company_name=str(record.get("company_name") or "").strip() or card.company_name,
        salary_range=str(record.get("salary_range") or "").strip() or card.salary_range,
        job_description=job_description,
        location=str(record.get("location") or "").strip() or card.location or None,
        tags=list(record.get("tags") or []) or list(card.tags or []),
        recruiter_name=str(record.get("recruiter_name") or "").strip() or card.recruiter_name,
        recruiter_title=str(record.get("recruiter_title") or "").strip() or card.recruiter_title,
        company_scale=str(record.get("company_scale") or "").strip() or card.company_scale,
        industry=str(record.get("industry") or "").strip() or card.industry,
        is_headhunter=bool(record.get("is_headhunter") or card.is_headhunter),
        commute_distance_km=commute_distance_km,
        commute_distance_text=commute_distance_text,
    )
    # The same guards a live read applies: "未注明职位"/"未注明公司" is not an identity, and
    # the card is the fallback the detail page would have supplied.
    posting.title = effective_title(posting, card)
    posting.company_name = effective_company(posting, card)
    return posting


def enriched_record(
    card: JobCardBrief,
    posting: Any,
    *,
    keyword: str | None,
    source_task_id: str | None,
    card_record: dict[str, Any],
    verdict: CardScreeningVerdict | None,
) -> dict[str, Any]:
    """The full record for a card whose detail page has been read.

    Card facets stay authoritative where the detail page has nothing to add: the extracted
    posting is a richer source, but a placeholder there must not overwrite a real value.
    """
    jd_text = posting.job_description or ""
    return {
        "fingerprint": card.fingerprint,
        "title": effective_title(posting, card),
        "company_name": effective_company(posting, card),
        "recruiter_name": (card.recruiter_name or posting.recruiter_name or "招聘者"),
        "recruiter_title": (card.recruiter_title or getattr(posting, "recruiter_title", "") or ""),
        "is_headhunter": bool(card.is_headhunter or getattr(posting, "is_headhunter", False)),
        "company_scale": (card.company_scale or getattr(posting, "company_scale", "") or ""),
        "industry": card.industry or getattr(posting, "industry", "") or "",
        "tags": list(card.tags) or list(getattr(posting, "tags", None) or []),
        "salary_range": posting.salary_range or card_record.get("salary_range", ""),
        "location": posting.location or card_record.get("location", ""),
        "digest": card_record.get("digest", "") or getattr(posting, "digest", "") or "",
        "job_description": jd_text or card_record.get("job_description", ""),
        "relaxed_by_whitelist": bool(verdict and verdict.relaxed_by_whitelist),
        "screening_audit": verdict.screening_audit if verdict else "",
        "search_keywords": [keyword] if keyword else [],
        "source_task_id": source_task_id,
        "commute_distance_km": getattr(posting, "commute_distance_km", None)
        or getattr(card, "commute_distance_km", None),
        "commute_distance_text": (
            getattr(posting, "commute_distance_text", "")
            or getattr(card, "commute_distance_text", "")
            or ""
        ),
    }
