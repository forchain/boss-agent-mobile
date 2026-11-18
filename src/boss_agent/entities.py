"""
boss_agent.entities
===================
Canonical domain entities grouped by domain concern (Issue #311, Spec #303).
"""

from __future__ import annotations

from boss_agent.core.candidate.domain.candidate_profile import (
    CandidateProfile,
    StructuredCandidateProfile,
)
from boss_agent.core.search.domain.models import (
    FilterConfig,
    SavedSearch,
    SearchConfig,
    _saved_search_max_jobs_default,
)
from boss_agent.job_entities import (
    JobCardBrief,
    JobLocationLine,
    JobPosting,
    JobRecord,
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
