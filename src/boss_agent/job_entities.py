"""
boss_agent.job_entities
=======================
Job-related domain entities: JobCardBrief, JobRecord, and JobPosting (Issue #311, Spec #303).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from boss_agent.identifier_helpers import (
    clean_job_title,
    compute_job_fingerprint,
    extract_digest_from_jd,
    extract_tags_from_text,
    resolve_headhunter_channel,
    sanitize_tags,
    split_recruiter_name,
)


@dataclass
class JobCardBrief:
    """Lightweight extraction from a job card in search/list view for deduplication."""

    title: str
    company_name: str
    recruiter_name: str
    fingerprint: str = ""
    salary_range: str = ""
    location: str = ""
    tags: list[str] = field(default_factory=list)
    digest: str = ""
    snippet: str = ""
    company_scale: str = ""
    industry: str = ""
    recruiter_title: str = ""
    is_headhunter: bool = False
    # App-Enforced commute distance carried over from the detail page probe
    # (spec #209). None = unknown; screening fails open on it.
    commute_distance_km: float | None = None
    commute_distance_text: str = ""

    def __post_init__(self) -> None:
        if self.title:
            self.title = clean_job_title(self.title)
        if self.recruiter_name:
            self.recruiter_name, derived_recruiter_title = split_recruiter_name(self.recruiter_name)
            if not self.recruiter_title and derived_recruiter_title:
                self.recruiter_title = derived_recruiter_title

        if not self.digest and self.snippet:
            self.digest = self.snippet
        elif self.digest and not self.snippet:
            self.snippet = self.digest

        self.is_headhunter = resolve_headhunter_channel(
            self.is_headhunter, self.recruiter_name, self.recruiter_title
        )
        self.tags = sanitize_tags(
            self.tags,
            recruiter_name=self.recruiter_name,
            recruiter_title=self.recruiter_title,
            location=self.location,
            company_name=self.company_name,
            title=self.title,
        )
        if not self.fingerprint:
            self.fingerprint = compute_job_fingerprint(
                company_name=self.company_name,
                title=self.title,
                recruiter_name=self.recruiter_name,
            )


@dataclass
class JobRecord:
    title: str
    company_name: str
    recruiter_name: str
    fingerprint: str = ""
    id: str | None = None
    salary_range: str = ""
    location: str | None = None
    digest: str = ""
    job_description: str = ""
    company_scale: str = ""
    industry: str = ""
    tags: list[str] = field(default_factory=list)
    recruiter_title: str = ""
    is_headhunter: bool = False
    status: str = "unmatched"
    match_score: int | None = None
    jd_key_requirements: list[str] = field(default_factory=list)
    greeting_message: str = ""
    search_keywords: list[str] = field(default_factory=list)
    screened_reason: str = ""
    relaxed_by_whitelist: bool = False
    screening_audit: str = ""
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    source_task_id: str | None = None
    created: str | None = None
    updated: str | None = None

    def __post_init__(self) -> None:
        if self.title:
            self.title = clean_job_title(self.title)
        if self.recruiter_name:
            self.recruiter_name, derived_recruiter_title = split_recruiter_name(self.recruiter_name)
            if not self.recruiter_title and derived_recruiter_title:
                self.recruiter_title = derived_recruiter_title

        if not self.is_headhunter and (
            "猎头" in (self.recruiter_title or "") or "猎头" in (self.recruiter_name or "")
        ):
            self.is_headhunter = True
        if not self.fingerprint:
            self.fingerprint = compute_job_fingerprint(
                company_name=self.company_name,
                title=self.title,
                recruiter_name=self.recruiter_name,
            )
        if not self.digest and self.job_description:
            self.digest = extract_digest_from_jd(self.job_description)
        if not self.tags:
            self.tags = extract_tags_from_text(f"{self.title} {self.job_description}")
        self.tags = sanitize_tags(
            self.tags,
            recruiter_name=self.recruiter_name,
            recruiter_title=self.recruiter_title,
            location=self.location or "",
            company_name=self.company_name,
            title=self.title,
        )
        if self.jd_key_requirements:
            self.jd_key_requirements = sanitize_tags(
                self.jd_key_requirements,
                recruiter_name=self.recruiter_name,
                recruiter_title=self.recruiter_title,
                location=self.location or "",
                company_name=self.company_name,
                title=self.title,
            )


@dataclass
class JobPosting:
    title: str
    company_name: str
    salary_range: str
    job_description: str
    digest: str = ""
    location: str | None = None
    tags: list[str] = field(default_factory=list)
    recruiter_name: str | None = None
    recruiter_title: str | None = None
    company_scale: str = ""
    industry: str = ""
    is_headhunter: bool = False
    # App-Enforced commute distance, probed from the detail page bottom widget
    # (spec #209). None means unknown/absent — screening must fail open on it.
    commute_distance_km: float | None = None
    commute_distance_text: str = ""

    def __post_init__(self) -> None:
        if self.title:
            self.title = clean_job_title(self.title)
        if self.recruiter_name:
            self.recruiter_name, derived_recruiter_title = split_recruiter_name(self.recruiter_name)
            if not self.recruiter_title and derived_recruiter_title:
                self.recruiter_title = derived_recruiter_title

        self.is_headhunter = resolve_headhunter_channel(
            self.is_headhunter, self.recruiter_name, self.recruiter_title
        )
        if not self.digest and self.job_description:
            self.digest = extract_digest_from_jd(self.job_description)
        self.tags = sanitize_tags(
            self.tags,
            recruiter_name=self.recruiter_name or "",
            recruiter_title=self.recruiter_title or "",
            location=self.location or "",
            company_name=self.company_name,
            title=self.title,
        )
