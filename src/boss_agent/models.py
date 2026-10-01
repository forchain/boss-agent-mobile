"""
boss_agent.models
=================
TEMPORARY COMPATIBILITY SURFACE (Issue #311, Spec #303).
Expiry: Issues #312 (migrate consumers) and #313 (contract/removal).

All symbols are now defined in independent, focused domain modules:
- boss_agent.enums
- boss_agent.keyword_constants
- boss_agent.identifier_helpers
- boss_agent.job_entities
- boss_agent.candidate_entities
- boss_agent.search_entities
- boss_agent.screening_policy
- boss_agent.entities
"""

from __future__ import annotations

from boss_agent.candidate_entities import CandidateProfile
from boss_agent.enums import (
    CHECK_CHAT_ACTION,
    STATE_RANK,
    TARGET_ACTION_RANK,
    AuthStatus,
    ChannelPreference,
    ChatButtonState,
    JobRecordStatus,
    TargetAction,
    TargetTaskType,
)
from boss_agent.identifier_helpers import (
    classify_chat_button,
    clean_job_title,
    commute_columns,
    compute_job_fingerprint,
    extract_digest_from_jd,
    extract_tags_from_text,
    format_recruiter_greeting_prefix,
    is_communication_expired,
    is_direct_hire_company,
    is_headhunter_agency_name,
    is_invalid_company_name,
    is_likely_location,
    is_masked_company_name,
    is_substantive_jd,
    normalize_recruiter_name,
    parse_recruiter_title,
    parse_utc_timestamp,
    resolve_headhunter_channel,
    sanitize_tags,
    split_recruiter_name,
)
from boss_agent.job_entities import JobCardBrief, JobPosting, JobRecord
from boss_agent.keyword_constants import (
    _CN_JD_HEADER_WORDS,
    _EN_JD_HEADER_WORDS,
    _JD_HEADER_ANNOTATION,
    _JD_HEADER_PREFIX_RE,
    _JD_HEADER_STANDALONE_RE,
    _RECRUITER_SEPARATOR_RE,
    APPLIED_SOURCE_AGENT,
    APPLIED_SOURCE_PLATFORM_HISTORICAL,
    CLOSED_BUTTON_TEXTS,
    COMMON_COMPOUND_SURNAMES,
    COMMON_TECH_TAGS,
    COMMUNICATED_BUTTON_TEXTS,
    COMPANY_INDICATOR_KEYWORDS,
    DEFAULT_COMMUNICATION_COOLDOWN_DAYS,
    EDUCATION_KEYWORDS,
    EXPERIENCE_KEYWORDS,
    EXPIRED_POSTING_REASON,
    GENERIC_ROLE_TOKENS,
    HEADHUNTER_AGENCY_KEYWORDS,
    HEADHUNTER_COMMUTE_PROBE_SKIP_REASON,
    INVALID_COMPANY_NAMES,
    KNOWN_CITIES,
    MIN_JD_CHARS,
    PLATFORM_BADGE_MARKERS,
    RECRUITER_SEPARATORS,
    RECRUITER_TITLE_KEYWORDS,
    UNCONTACTED_BUTTON_TEXTS,
    UNUSABLE_JD_MARKERS,
)
from boss_agent.screening_policy import ScreeningPolicy
from boss_agent.search_entities import (
    FilterConfig,
    SavedSearch,
    SearchConfig,
    _saved_search_max_jobs_default,
)

__all__ = [
    # Enums & rank maps
    "AuthStatus",
    "JobRecordStatus",
    "TargetAction",
    "TargetTaskType",
    "ChatButtonState",
    "ChannelPreference",
    "STATE_RANK",
    "TARGET_ACTION_RANK",
    "CHECK_CHAT_ACTION",
    # Keyword constants
    "RECRUITER_SEPARATORS",
    "_RECRUITER_SEPARATOR_RE",
    "PLATFORM_BADGE_MARKERS",
    "COMMON_COMPOUND_SURNAMES",
    "GENERIC_ROLE_TOKENS",
    "KNOWN_CITIES",
    "RECRUITER_TITLE_KEYWORDS",
    "EDUCATION_KEYWORDS",
    "EXPERIENCE_KEYWORDS",
    "COMPANY_INDICATOR_KEYWORDS",
    "INVALID_COMPANY_NAMES",
    "_CN_JD_HEADER_WORDS",
    "_EN_JD_HEADER_WORDS",
    "_JD_HEADER_ANNOTATION",
    "_JD_HEADER_STANDALONE_RE",
    "_JD_HEADER_PREFIX_RE",
    "COMMON_TECH_TAGS",
    "COMMUNICATED_BUTTON_TEXTS",
    "CLOSED_BUTTON_TEXTS",
    "UNCONTACTED_BUTTON_TEXTS",
    "APPLIED_SOURCE_AGENT",
    "APPLIED_SOURCE_PLATFORM_HISTORICAL",
    "EXPIRED_POSTING_REASON",
    "HEADHUNTER_COMMUTE_PROBE_SKIP_REASON",
    "DEFAULT_COMMUNICATION_COOLDOWN_DAYS",
    "MIN_JD_CHARS",
    "UNUSABLE_JD_MARKERS",
    "HEADHUNTER_AGENCY_KEYWORDS",
    # Identifier & classification helpers
    "clean_job_title",
    "split_recruiter_name",
    "normalize_recruiter_name",
    "parse_recruiter_title",
    "format_recruiter_greeting_prefix",
    "compute_job_fingerprint",
    "is_likely_location",
    "is_invalid_company_name",
    "sanitize_tags",
    "extract_digest_from_jd",
    "extract_tags_from_text",
    "classify_chat_button",
    "resolve_headhunter_channel",
    "is_direct_hire_company",
    "parse_utc_timestamp",
    "is_communication_expired",
    "commute_columns",
    "is_substantive_jd",
    "is_headhunter_agency_name",
    "is_masked_company_name",
    # Entities
    "JobCardBrief",
    "JobRecord",
    "JobPosting",
    "CandidateProfile",
    "ScreeningPolicy",
    "SearchConfig",
    "FilterConfig",
    "_saved_search_max_jobs_default",
    "SavedSearch",
]
