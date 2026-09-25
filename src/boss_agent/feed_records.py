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

from .job_store import INVALID_JOB_TITLES
from .models import INVALID_COMPANY_NAMES, JobRecordStatus, is_invalid_company_name
from .pages import JobCardBrief
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
    }
