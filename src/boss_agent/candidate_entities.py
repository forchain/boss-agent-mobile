"""
boss_agent.candidate_entities
=============================
Candidate profile domain entity (Issue #311, Spec #303).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CandidateProfile:
    name: str
    target_titles: list[str]
    min_salary: int
    max_salary: int
    city: str
    resume_summary: str
    preferred_industries: list[str] = field(default_factory=list)
