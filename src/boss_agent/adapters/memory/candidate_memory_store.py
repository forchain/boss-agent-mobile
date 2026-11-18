"""
boss_agent.adapters.memory.candidate_memory_store
=================================================
Volatile Candidate Profile / Resume Revision store for tests and local development.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from boss_agent.core.candidate.interfaces.candidate_memory_store import CandidateMemoryStore


class InMemoryCandidateMemoryStore(CandidateMemoryStore):
    """Volatile candidate memory for tests and local development."""

    def __init__(
        self,
        load_local_profile: bool = False,
        initial_profiles: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._profiles: dict[str, dict[str, Any]] = dict(initial_profiles or {})
        self._revisions: dict[str, list[dict[str, Any]]] = {}

    async def get_candidate_profile(self, user_id: str = "default") -> dict[str, Any] | None:
        profile = self._profiles.get(user_id)
        return dict(profile) if profile else None

    async def save_candidate_profile(
        self, profile_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        # Merge, like the PocketBase adapter: a save that omits a field must not wipe it.
        # The two adapters are meant to be substitutable, and a shared contract suite is
        # what caught that this one replaced wholesale where the other patched.
        merged = dict(self._profiles.get(user_id) or {})
        for key, value in profile_data.items():
            if (
                value is not None
                and value != ""
                and value != []
                and value != {}
                or key not in merged
            ):
                merged[key] = value
        self._profiles[user_id] = merged
        return dict(merged)

    async def list_resume_revisions(self, user_id: str = "default") -> list[dict[str, Any]]:
        revisions = self._revisions.get(user_id, [])
        return [
            dict(r) for r in sorted(revisions, key=lambda x: x.get("created", ""), reverse=True)
        ]

    async def create_resume_revision(
        self, revision_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        revision = dict(revision_data)
        revision.setdefault("id", str(uuid.uuid4())[:15])
        revision.setdefault("user_id", user_id)
        revision.setdefault("created", datetime.now(UTC).isoformat())
        self._revisions.setdefault(user_id, []).append(revision)
        return revision
