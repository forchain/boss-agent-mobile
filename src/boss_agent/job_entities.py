"""
boss_agent.job_entities
=======================
Job-related domain entities: JobCardBrief, JobRecord, and JobPosting (Issue #311, Spec #303).
"""

from __future__ import annotations

import re
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

#: The Job Detail Page location line, e.g. ``上海·浦东新区·张江(近13/16号线华夏中路地铁站)``
#: (issue #332). The platform is the only source of a metro station: the card's location
#: facet (``tv_distance``) names a district and nothing finer.
LOCATION_LINE_METRO_PATTERN = re.compile(r"[（(]([^)）]*)[)）]")
#: ``近13/16号线华夏中路地铁站`` → lines ``13/16号线``, station ``华夏中路地铁站``. The line
#: spec is optional, because a station quoted without one (``近张江高科``) is still a place
#: an operator can refuse.
LOCATION_LINE_TRANSIT_PATTERN = re.compile(
    r"^近?\s*(?P<lines>[0-9０-９][0-9０-９/、,，\-—\s]*号线)?\s*(?P<station>.+)$"
)


@dataclass(frozen=True)
class JobLocationLine:
    """The Job Detail Page location line, split into the parts a filter can use (#332).

    The card stage sees a district and the detail stage sees a station, so this is the
    richest location the platform ever renders and the only place a station is known.
    Every part is optional: the line degrades to a bare district, or to nothing at all
    when the platform renders none, and neither is an error.
    """

    raw: str = ""
    city: str = ""
    district: str = ""
    business_district: str = ""
    metro_lines: str = ""
    metro_station: str = ""

    @property
    def match_text(self) -> str:
        """The text the screening policy's location lists are matched against.

        The platform's own rendering, verbatim. Quoting it in an audit reason gives the
        operator the exact string on their own screen, and because the district tokens and
        the ``近13/16号线华夏中路地铁站`` suffix both live in it, one string serves a
        district entry and a station entry of the same list.
        """
        return self.raw

    @classmethod
    def parse(cls, raw: str) -> JobLocationLine:
        """Split a rendered location line, tolerating every shape it degrades into."""
        text = (raw or "").strip()
        if not text:
            return cls()

        prefix, metro = text, ""
        found = LOCATION_LINE_METRO_PATTERN.search(text)
        if found:
            prefix = text[: found.start()].strip()
            metro = found.group(1).strip()

        parts = [p.strip() for p in re.split(r"[·•]", prefix) if p.strip()]
        city = parts[0] if parts else ""
        district = parts[1] if len(parts) > 1 else ""
        business_district = parts[2] if len(parts) > 2 else ""

        metro_lines = ""
        metro_station = ""
        if metro:
            transit = LOCATION_LINE_TRANSIT_PATTERN.match(metro)
            if transit:
                metro_lines = (transit.group("lines") or "").strip()
                metro_station = (transit.group("station") or "").strip()
            else:
                # No line spec to split off: the whole parenthetical names the place.
                metro_station = metro

        return cls(
            raw=text,
            city=city,
            district=district,
            business_district=business_district,
            metro_lines=metro_lines,
            metro_station=metro_station,
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
    #: The detail page's location line and the metro station it names (issue #332).
    #: Filled from the detail page, never from the card, which carries no station.
    location_line: str = ""
    metro_lines: str = ""
    metro_station: str = ""
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
    #: Which stage made the rejection (see :class:`ScreeningStage`); empty when the
    #: record was never rejected or predates the field, so the dashboard falls back to a
    #: neutral 「已忽略」 rather than guessing a stage.
    screening_stage: str = ""
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
    #: The detail page's own location line and the metro station it names (issue #332).
    #: Kept apart from ``location``, which stays the card's district facet: a station is
    #: only ever known here, and overwriting the facet with the line would change what the
    #: card-stage filters have always been matching against.
    location_line: str = ""
    metro_lines: str = ""
    metro_station: str = ""
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
