"""
boss_agent.candidate_memory_store
=================================
Candidate Profile and Resume Revision persistence, behind its own repository seam.

ADR 0013 confined ``BaseTaskBroker`` to task lifecycle, leases and realtime logs, but
the confinement stopped halfway: four Candidate Profile / Resume Revision verbs stayed
on the broker alongside the ten task verbs, so resume-memory work had to acquire a
task-lifecycle handle to touch a profile.

The shape is deliberately the ``JobRecordStore``'s — an abstract protocol, an
in-memory adapter for the Fast Unit tier, and a PocketBase adapter that carries the
offline SQLite fallback *inside the seam it protects* rather than loose in the broker.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("boss_agent.candidate_memory_store")

#: Columns the profile stores as JSON text and reads back as Python values.
PROFILE_JSON_COLUMNS: tuple[str, ...] = (
    "education",
    "core_skills",
    "project_highlights",
    "work_experiences",
    "projects",
    "target_positions",
)


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


class InMemoryCandidateMemoryStore(CandidateMemoryStore):
    """Volatile candidate memory for tests and local development."""

    def __init__(self, load_local_profile: bool = True) -> None:
        self._profiles: dict[str, dict[str, Any]] = {}
        self._revisions: dict[str, list[dict[str, Any]]] = {}
        if load_local_profile:
            self._load_local_profile()

    def _load_local_profile(self) -> None:
        config_path = Path("config/candidate_memory.json")
        if config_path.exists():
            with contextlib.suppress(Exception):
                self._profiles["default"] = json.loads(config_path.read_text(encoding="utf-8"))

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
            if value is not None and value != "" and value != [] and value != {} or key not in merged:
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


class PocketBaseCandidateMemoryStore(CandidateMemoryStore):
    """PocketBase-backed candidate memory, with an offline SQLite fallback.

    Each read and write attempts the REST API first and degrades to the local SQLite
    database the broker is serving from. That fallback lives here, in the seam it
    protects, instead of in the broker: it is a resilience property *of this store*, and
    nesting it in a task-lifecycle class made it look like a broker concern.
    """

    def __init__(
        self,
        *,
        base_url: str,
        session: Any,
        headers: Callable[[], dict[str, str]],
        sqlite_db_path: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self._headers = headers
        self._sqlite_db_path = sqlite_db_path

    # ------------------------------------------------------------------ #
    # SQLite fallback
    # ------------------------------------------------------------------ #

    def _resolve_sqlite_db_path(self) -> Path | None:
        if self._sqlite_db_path is not None:
            return self._sqlite_db_path
        for candidate in (
            os.environ.get("PB_DB_PATH"),
            Path(".boss_agent/pb_data/data.db"),
            Path("pb_data/data.db"),
        ):
            if candidate and Path(candidate).is_file():
                return Path(candidate)
        return None

    def _query_sqlite_profile(self, user_id: str) -> dict[str, Any] | None:
        db_path = self._resolve_sqlite_db_path()
        if not db_path:
            return None
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM candidate_profiles WHERE user_id = ? "
                    "ORDER BY updated DESC LIMIT 1",
                    (user_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                for json_col in PROFILE_JSON_COLUMNS:
                    if data.get(json_col) and isinstance(data[json_col], str):
                        with contextlib.suppress(Exception):
                            data[json_col] = json.loads(data[json_col])
                return data
        except Exception as e:
            logger.warning("Failed to read candidate profile from SQLite fallback: %s", e)
            return None

    def _save_sqlite_profile(self, profile_data: dict[str, Any], user_id: str) -> dict[str, Any]:
        db_path = self._resolve_sqlite_db_path()
        if not db_path:
            return profile_data
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.cursor()
                now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"
                cursor.execute(
                    "SELECT * FROM candidate_profiles WHERE user_id = ? "
                    "ORDER BY updated DESC LIMIT 1",
                    (user_id,),
                )
                existing_row = cursor.fetchone()
                existing_dict: dict[str, Any] = {}
                if existing_row:
                    col_names = [d[0] for d in cursor.description]
                    existing_dict = dict(zip(col_names, existing_row, strict=False))
                profile_id = (
                    existing_dict.get("id") or profile_data.get("id") or str(uuid.uuid4())[:15]
                )

                # Merge fields so a partial save cannot wipe a richer stored profile.
                def resolve_field(key: str, default_val: Any) -> Any:
                    val = profile_data.get(key)
                    if val is not None and val != "" and val != [] and val != {}:
                        return val
                    existing_val = existing_dict.get(key)
                    if existing_val:
                        if isinstance(default_val, (list, dict)) and isinstance(existing_val, str):
                            with contextlib.suppress(Exception):
                                return json.loads(existing_val)
                        return existing_val
                    return default_val

                values = (
                    profile_id,
                    user_id,
                    resolve_field("name", ""),
                    resolve_field("years_of_experience", 0),
                    json.dumps(resolve_field("education", []), ensure_ascii=False),
                    json.dumps(resolve_field("core_skills", []), ensure_ascii=False),
                    json.dumps(resolve_field("project_highlights", []), ensure_ascii=False),
                    json.dumps(resolve_field("work_experiences", []), ensure_ascii=False),
                    json.dumps(resolve_field("projects", []), ensure_ascii=False),
                    json.dumps(resolve_field("target_positions", []), ensure_ascii=False),
                    profile_data.get("profile_document")
                    or profile_data.get("raw_summary")
                    or existing_dict.get("raw_summary", ""),
                    profile_data.get("raw_resume_text") or existing_dict.get("raw_resume_text", ""),
                    now,
                )
                cursor.execute(
                    """
                    INSERT INTO candidate_profiles (
                        id, user_id, name, years_of_experience, education, core_skills,
                        project_highlights, work_experiences, projects, target_positions,
                        raw_summary, raw_resume_text, updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        user_id=excluded.user_id,
                        name=excluded.name,
                        years_of_experience=excluded.years_of_experience,
                        education=excluded.education,
                        core_skills=excluded.core_skills,
                        project_highlights=excluded.project_highlights,
                        work_experiences=excluded.work_experiences,
                        projects=excluded.projects,
                        target_positions=excluded.target_positions,
                        raw_summary=excluded.raw_summary,
                        raw_resume_text=excluded.raw_resume_text,
                        updated=excluded.updated
                    """,
                    values,
                )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to save candidate profile to SQLite fallback: %s", e)
        return profile_data

    def _query_sqlite_revisions(self, user_id: str) -> list[dict[str, Any]]:
        db_path = self._resolve_sqlite_db_path()
        if not db_path:
            return []
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM resume_revisions WHERE user_id = ? ORDER BY created DESC",
                    (user_id,),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.warning("Failed to query resume revisions from SQLite: %s", e)
            return []

    def _save_sqlite_revision(
        self, revision_data: dict[str, Any], user_id: str
    ) -> dict[str, Any]:
        db_path = self._resolve_sqlite_db_path()
        if not db_path:
            return revision_data
        try:
            import sqlite3

            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.cursor()
                now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"
                revision_id = revision_data.get("id") or str(uuid.uuid4())[:15]
                cursor.execute(
                    """
                    INSERT INTO resume_revisions (
                        id, user_id, file_name, file_type, file_size,
                        extracted_text, diff_summary, created, updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        user_id,
                        revision_data.get("file_name", "resume.txt"),
                        revision_data.get("file_type", "txt"),
                        revision_data.get("file_size", 0),
                        revision_data.get("extracted_text", ""),
                        revision_data.get("diff_summary", ""),
                        now,
                        now,
                    ),
                )
                conn.commit()
                return {
                    **revision_data,
                    "id": revision_id,
                    "user_id": user_id,
                    "created": now,
                    "updated": now,
                }
        except Exception as e:
            logger.warning("Failed to save resume revision to SQLite: %s", e)
            return revision_data

    # ------------------------------------------------------------------ #
    # REST paths
    # ------------------------------------------------------------------ #

    def _collection_url(self, collection: str) -> str:
        return f"{self.base_url}/api/collections/{collection}/records"

    async def get_candidate_profile(self, user_id: str = "default") -> dict[str, Any] | None:
        url = self._collection_url("candidate_profiles")
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    params={"filter": f"user_id='{user_id}'", "perPage": "1", "sort": "-updated"},
                    headers=self._headers(),
                ),
            )
            if resp.status_code == 404:
                return self._query_sqlite_profile(user_id)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return items[0] if items else self._query_sqlite_profile(user_id)
        except Exception as e:
            logger.warning("PocketBase get_candidate_profile failed, fallback to SQLite: %s", e)
            return self._query_sqlite_profile(user_id)

    async def save_candidate_profile(
        self, profile_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        url = self._collection_url("candidate_profiles")
        loop = asyncio.get_running_loop()
        try:
            existing = await self.get_candidate_profile(user_id=user_id)
            if existing and existing.get("id"):
                merged_body = dict(existing)
                for key, value in profile_data.items():
                    if value is not None and value != "" and value != [] and value != {}:
                        merged_body[key] = value
                incoming_doc = profile_data.get("profile_document") or profile_data.get(
                    "raw_summary"
                )
                if incoming_doc:
                    merged_body["raw_summary"] = incoming_doc
                elif existing.get("raw_summary"):
                    merged_body["raw_summary"] = existing["raw_summary"]
                merged_body["user_id"] = user_id
                resp = await loop.run_in_executor(
                    None,
                    lambda: self.session.patch(
                        f"{url}/{existing['id']}", json=merged_body, headers=self._headers()
                    ),
                )
            else:
                body = {**profile_data, "user_id": user_id}
                resp = await loop.run_in_executor(
                    None,
                    lambda: self.session.post(url, json=body, headers=self._headers()),
                )
            if resp.ok:
                return resp.json()
        except Exception as e:
            logger.warning("PocketBase save_candidate_profile failed, fallback to SQLite: %s", e)
        return self._save_sqlite_profile(profile_data, user_id)

    async def list_resume_revisions(self, user_id: str = "default") -> list[dict[str, Any]]:
        url = self._collection_url("resume_revisions")
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get(
                    url,
                    params={
                        "filter": f"user_id='{user_id}'",
                        "sort": "-created",
                        "perPage": "100",
                    },
                    headers=self._headers(),
                ),
            )
            if resp.status_code == 404:
                return self._query_sqlite_revisions(user_id)
            resp.raise_for_status()
            return resp.json().get("items", [])
        except Exception as e:
            logger.warning("PocketBase list_resume_revisions failed, fallback to SQLite: %s", e)
            return self._query_sqlite_revisions(user_id)

    async def create_resume_revision(
        self, revision_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        url = self._collection_url("resume_revisions")
        loop = asyncio.get_running_loop()
        body = {**revision_data, "user_id": user_id}
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.post(url, json=body, headers=self._headers()),
            )
            if resp.ok:
                return resp.json()
        except Exception as e:
            logger.warning("PocketBase create_resume_revision failed, fallback to SQLite: %s", e)
        return self._save_sqlite_revision(body, user_id)
