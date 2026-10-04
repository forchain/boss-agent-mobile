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

from boss_agent.async_bridge import execute_broker_request
from boss_agent.errors import (
    TransportError,
)

logger = logging.getLogger("boss_agent.candidate_memory_store")

#: Cap for the legacy ``raw_summary`` mirror. PocketBase's default text field holds 5000
#: characters, so the mirror is trimmed to fit; ``profile_document`` is the field that keeps
#: the whole document (see ``LONG_TEXT_MAX_CHARS`` in ``broker.collection_schema``).
RAW_SUMMARY_MAX_CHARS = 4990

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
            return self._sqlite_db_path if Path(self._sqlite_db_path).is_file() else None
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
        except (sqlite3.Error, OSError) as e:  # persistence-guard: allow
            # The SQLite file is a local read-through cache, not the source of truth: a miss
            # or a corrupt cache means "no cached profile", and the PocketBase read follows.
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

                has_profile_doc = any(
                    col[1] == "profile_document"
                    for col in cursor.execute("PRAGMA table_info(candidate_profiles)").fetchall()
                )
                doc = (
                    profile_data.get("profile_document")
                    or profile_data.get("raw_summary")
                    or existing_dict.get("profile_document")
                    or existing_dict.get("raw_summary", "")
                )

                if has_profile_doc:
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
                        doc,
                        doc,
                        profile_data.get("raw_resume_text")
                        or existing_dict.get("raw_resume_text", ""),
                        now,
                    )
                    cursor.execute(
                        """
                        INSERT INTO candidate_profiles (
                            id, user_id, name, years_of_experience, education, core_skills,
                            project_highlights, work_experiences, projects, target_positions,
                            raw_summary, profile_document, raw_resume_text, updated
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            profile_document=excluded.profile_document,
                            raw_resume_text=excluded.raw_resume_text,
                            updated=excluded.updated
                        """,
                        values,
                    )
                else:
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
                        doc,
                        profile_data.get("raw_resume_text")
                        or existing_dict.get("raw_resume_text", ""),
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
            raise TransportError(f"Failed to save candidate profile to SQLite fallback: {e}") from e
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
        except (sqlite3.Error, OSError) as e:  # persistence-guard: allow
            # Local cache read: an empty revision list is the honest answer for an
            # unreadable cache, not a fabricated failure to be propagated.
            logger.warning("Failed to query resume revisions from SQLite: %s", e)
            return []

    def _save_sqlite_revision(self, revision_data: dict[str, Any], user_id: str) -> dict[str, Any]:
        db_path = self._resolve_sqlite_db_path()
        if not db_path:
            raise TransportError("Cannot save resume revision: no SQLite database found")
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
            raise TransportError(f"Failed to save resume revision to SQLite: {e}") from e

    # ------------------------------------------------------------------ #
    # REST paths
    # ------------------------------------------------------------------ #

    def _collection_url(self, collection: str) -> str:
        return f"{self.base_url}/api/collections/{collection}/records"

    async def get_candidate_profile(self, user_id: str = "default") -> dict[str, Any] | None:
        url = self._collection_url("candidate_profiles")
        try:
            resp = await execute_broker_request(
                lambda: self.session.get(
                    url,
                    params={"filter": f"user_id='{user_id}'", "perPage": "1", "sort": "-updated"},
                    headers=self._headers(),
                ),
                expected_statuses=(200,),
                allow_404=True,
                error_prefix=f"PocketBase get_candidate_profile failed for user '{user_id}'",
            )
            if resp.status_code == 404:
                return self._query_sqlite_profile(user_id)
            items = resp.json().get("items", [])
            return items[0] if items else self._query_sqlite_profile(user_id)
        except TransportError as err:
            logger.warning(
                "PocketBase get_candidate_profile transport error, trying SQLite: %s", err
            )
            fallback = self._query_sqlite_profile(user_id)
            if fallback is not None:
                return fallback
            raise

    async def save_candidate_profile(
        self, profile_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        url = self._collection_url("candidate_profiles")
        try:
            try:
                existing = await self.get_candidate_profile(user_id=user_id)
            except TransportError:
                existing = self._query_sqlite_profile(user_id)

            if existing and existing.get("id"):
                merged_body = dict(existing)
                for key, value in profile_data.items():
                    if value is not None and value != "" and value != [] and value != {}:
                        merged_body[key] = value
                incoming_doc = profile_data.get("profile_document") or profile_data.get(
                    "raw_summary"
                )
                if incoming_doc:
                    # profile_document is the single source of truth and carries the whole
                    # document (LONG_TEXT_MAX_CHARS). raw_summary stays its legacy mirror and
                    # must respect PocketBase's 5000-char default text limit, so it is the
                    # only one of the two that gets truncated.
                    merged_body["profile_document"] = incoming_doc
                    merged_body["raw_summary"] = incoming_doc[:RAW_SUMMARY_MAX_CHARS]
                elif existing.get("profile_document") or existing.get("raw_summary"):
                    doc = existing.get("profile_document") or existing.get("raw_summary", "")
                    merged_body["profile_document"] = doc
                    merged_body["raw_summary"] = str(doc)[:RAW_SUMMARY_MAX_CHARS]
                merged_body["user_id"] = user_id
                resp = await execute_broker_request(
                    lambda: self.session.patch(
                        f"{url}/{existing['id']}", json=merged_body, headers=self._headers()
                    ),
                    expected_statuses=(200,),
                    error_prefix=f"PocketBase patch candidate profile failed for user '{user_id}'",
                )
            else:
                body = {**profile_data, "user_id": user_id}
                incoming_doc = profile_data.get("profile_document") or profile_data.get(
                    "raw_summary"
                )
                if incoming_doc:
                    body["profile_document"] = incoming_doc
                    body["raw_summary"] = incoming_doc[:RAW_SUMMARY_MAX_CHARS]
                resp = await execute_broker_request(
                    lambda: self.session.post(url, json=body, headers=self._headers()),
                    expected_statuses=(200, 201),
                    error_prefix=f"PocketBase post candidate profile failed for user '{user_id}'",
                )
            return resp.json()
        except TransportError as err:
            logger.warning(
                "PocketBase save_candidate_profile transport error, trying SQLite: %s", err
            )
            if self._resolve_sqlite_db_path() is not None:
                return self._save_sqlite_profile(profile_data, user_id)
            raise

    async def list_resume_revisions(self, user_id: str = "default") -> list[dict[str, Any]]:
        url = self._collection_url("resume_revisions")
        try:
            resp = await execute_broker_request(
                lambda: self.session.get(
                    url,
                    params={
                        "filter": f"user_id='{user_id}'",
                        "sort": "-created",
                        "perPage": "100",
                    },
                    headers=self._headers(),
                ),
                expected_statuses=(200,),
                allow_404=True,
                error_prefix=f"PocketBase list_resume_revisions failed for user '{user_id}'",
            )
            if resp.status_code == 404:
                return self._query_sqlite_revisions(user_id)
            return resp.json().get("items", [])
        except TransportError as err:
            logger.warning(
                "PocketBase list_resume_revisions transport error, trying SQLite: %s", err
            )
            if self._resolve_sqlite_db_path() is not None:
                return self._query_sqlite_revisions(user_id)
            raise

    async def create_resume_revision(
        self, revision_data: dict[str, Any], user_id: str = "default"
    ) -> dict[str, Any]:
        url = self._collection_url("resume_revisions")
        body = {**revision_data, "user_id": user_id}
        try:
            resp = await execute_broker_request(
                lambda: self.session.post(url, json=body, headers=self._headers()),
                expected_statuses=(200, 201),
                error_prefix=f"PocketBase create_resume_revision failed for user '{user_id}'",
            )
            return resp.json()
        except TransportError as err:
            logger.warning(
                "PocketBase create_resume_revision transport error, trying SQLite: %s", err
            )
            if self._resolve_sqlite_db_path() is not None:
                return self._save_sqlite_revision(body, user_id)
            raise


async def async_migrate_legacy_candidate_profile(
    local_path: Path | str | None = None,
    broker: Any = None,
    user_id: str = "default",
) -> dict[str, Any] | None:
    """Lift an existing local candidate profile JSON file into the broker's Candidate Profile collection.

    Converts legacy profile formats (such as dictionary-shaped core_skills, raw_summary-only
    profile documents, or legacy project structures) into the canonical StructuredCandidateProfile
    schema and persists it to the Candidate Profile database collection.
    """
    path = Path(local_path or "config/candidate_memory.json")
    if not path.is_file():
        return None

    try:
        raw_text = path.read_text(encoding="utf-8")
        if not raw_text.strip():
            return None
        raw_data = json.loads(raw_text)
    except (
        json.JSONDecodeError,
        OSError,
        UnicodeDecodeError,
        ValueError,
    ) as e:  # persistence-guard: allow
        # Best-effort migration of a legacy on-disk profile: an unparseable file is treated
        # as "no legacy profile to migrate", not as a failure worth aborting startup for.
        logger.warning("Failed to parse legacy profile at %s: %s", path, e)
        return None

    if not isinstance(raw_data, dict):
        return None

    from boss_agent.memory import StructuredCandidateProfile

    profile = StructuredCandidateProfile.from_dict(raw_data)
    profile_dict = profile.to_dict()

    if broker is None:
        from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker

        broker = PocketBaseTaskBroker()

    return await broker.candidate_memory.save_candidate_profile(profile_dict, user_id=user_id)


def migrate_legacy_candidate_profile(
    local_path: Path | str | None = None,
    broker: Any = None,
    user_id: str = "default",
) -> dict[str, Any] | None:
    """Synchronous entry point for async_migrate_legacy_candidate_profile."""
    from boss_agent.async_bridge import run_sync

    return run_sync(
        async_migrate_legacy_candidate_profile(
            local_path=local_path, broker=broker, user_id=user_id
        ),
        timeout=10.0,
    )
