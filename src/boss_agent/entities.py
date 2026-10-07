"""
boss_agent.entities
===================
Canonical domain entities grouped by domain concern (Issue #311, Spec #303).
"""

from __future__ import annotations

from boss_agent.candidate_entities import CandidateProfile, StructuredCandidateProfile
from boss_agent.job_entities import (
    JobCardBrief,
    JobLocationLine,
    JobPosting,
    JobRecord,
)
from boss_agent.search_entities import (
    FilterConfig,
    SavedSearch,
    SearchConfig,
    _saved_search_max_jobs_default,
)

__all__ = [
    "CandidateProfile",
    "FilterConfig",
    "JobCardBrief",
    "JobLocationLine",
    "JobPosting",
    "JobRecord",
    "SavedSearch",
    "SearchConfig",
    "StructuredCandidateProfile",
    "_saved_search_max_jobs_default",
]
