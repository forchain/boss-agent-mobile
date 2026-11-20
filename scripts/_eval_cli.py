"""
scripts/_eval_cli.py
====================
Shared input parsing and profile loading helpers for evaluation and refinement CLI scripts
(``evaluate_match.py``, ``refine_greeting.py``, ``refine_screening.py``).
"""

from __future__ import annotations

import json
import sys

from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.candidate.domain.profile_normalizer import ProfileNormalizer
from boss_agent.core.job.domain.job_entities import JobPosting
from boss_agent.main import load_cached_candidate_profile


def parse_job_posting_json(raw_job: str) -> JobPosting:
    """Parse CLI ``--job`` JSON into a 9-facet ``JobPosting`` entity."""
    job_dict = json.loads(raw_job)
    return JobPosting(
        title=job_dict.get("job_title") or job_dict.get("title") or "目标岗位",
        company_name=job_dict.get("company_name") or job_dict.get("company") or "招聘公司",
        salary_range=job_dict.get("salary_range") or job_dict.get("salary") or "面议",
        job_description=job_dict.get("job_description") or job_dict.get("description") or "",
        recruiter_name=job_dict.get("recruiter_name") or job_dict.get("recruiter") or None,
        recruiter_title=job_dict.get("recruiter_title") or None,
        tags=job_dict.get("tags") or [],
        digest=job_dict.get("digest") or "",
        search_filter=job_dict.get("search_filter") or None,
    )


def parse_candidate_profile_arg(raw_profile: str | None) -> CandidateProfile | None:
    """Parse ``--profile`` JSON and fall back to stored Candidate Profile on stub/missing input."""
    candidate_profile: CandidateProfile | None = None
    if raw_profile:
        try:
            profile_dict = json.loads(raw_profile)
            norm_dict = ProfileNormalizer.normalize(profile_dict)
            candidate_profile = CandidateProfile.from_dict(norm_dict)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse candidate profile JSON ({e})\n")

    if (
        candidate_profile is None
        or (not candidate_profile.profile_document and not candidate_profile.work_experiences)
        or candidate_profile.name in ("测试候选人", "求职者", "")
    ):
        try:
            cached = load_cached_candidate_profile()
            if (
                cached
                and (cached.profile_document or cached.work_experiences or cached.core_skills)
                and (
                    not candidate_profile
                    or candidate_profile.name in ("测试候选人", "求职者", "")
                    or not candidate_profile.profile_document
                )
            ):
                candidate_profile = cached
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load cached candidate memory ({e})\n")

    return candidate_profile
