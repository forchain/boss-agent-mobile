"""
boss_agent.core.candidate.interfaces.candidate_memory_store
===========================================================
Candidate Profile / Resume Revision repository port (ADR 0013; extracted from the
implementation module by Spec #443).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CandidateMemoryStore(ABC):
    """Repository interface for candidate memory: the profile and its resume revisions.

    Deliberately broker-free: the LangGraph workflow and the resume-upload endpoint
    receive one of these and never see a task lease.
    """

    @abstractmethod
    async def get_candidate_profile(self, user_id: str = "default") -> dict[str, Any] | None:
        """Fetch the candidate's structured memory profile."""

    @abstractmethod
    async def save_candidate_profile(
        self, profile_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        """Save or update the candidate's structured memory profile."""

    @abstractmethod
    async def list_resume_revisions(self, user_id: str = "default") -> list[dict[str, Any]]:
        """List resume revisions in reverse chronological order."""

    @abstractmethod
    async def create_resume_revision(
        self, revision_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        """Record a new resume upload revision."""
