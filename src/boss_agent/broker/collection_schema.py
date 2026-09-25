"""
src/boss_agent/broker/collection_schema.py
==========================================
The one owner of the State Stream Broker's PocketBase collection schemas.

The five collections — ``automation_tasks``, ``candidate_profiles``,
``resume_revisions``, ``job_records`` and ``saved_searches`` — used to be declared by
hand in six places: the provisioner's SQLite dialect, the provisioner's REST dialect,
the Job Record Store's write payload, the broker adapter's SQL fallback, the Web
Dashboard's TypeScript mirrors, and a dead copy inside :mod:`boss_agent.broker.models`.
The copies drifted, and three drifts corrupted live behavior:

* the REST dialect omitted ``saved_searches.target_action`` / ``max_jobs`` and nine
  ``job_records`` columns, so a remotely provisioned PocketBase never stored them;
* ``SavedSearch.max_jobs`` defaulted to 20 in the domain model but 30 in the
  provisioner and the Web UI;
* the worker's lease writes landed in ``worker_id`` while the dashboard read the
  vestigial ``assigned_worker`` column, rendering the Worker badge blank.

Everything that needs a collection's shape reads it from here:

* the provisioner's SQLite dialect renders ``CREATE TABLE`` from :func:`sqlite_ddl`;
* its REST dialect renders ``_collections.fields`` from :func:`pocketbase_fields`;
* upgrades render ``ALTER TABLE`` from :func:`column_migrations` and the ordered
  :func:`backfills`;
* the Job Record Store builds its write payload from :func:`wire_payload`;
* the SavedSearch registry maps raw HTTP records through :func:`wire_payload` too.

Adding a field is therefore one edit, and a dialect cannot ship a field the other
lacks — :func:`dialect_field_names` is what the equality test asserts against.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# PocketBase field kinds
# --------------------------------------------------------------------------- #

TEXT = "text"
NUMBER = "number"
BOOL = "bool"
JSON = "json"
DATE = "date"
AUTODATE = "autodate"

#: PocketBase caps a text field with no explicit max at 5000 chars
#: (``core/field_text.go``), and an explicit ``0`` falls back to the same cap, so
#: long JD bodies need a large explicit max or writes fail validation.
LONG_TEXT_MAX_CHARS = 100_000

#: SQLite column type per PocketBase kind.
_SQLITE_TYPES: Mapping[str, str] = {
    TEXT: "TEXT",
    NUMBER: "INTEGER",
    BOOL: "BOOLEAN",
    JSON: "JSON",
    DATE: "TEXT",
    AUTODATE: "TEXT",
}

#: The value PocketBase itself stores in the two server-managed autodate columns.
_SQLITE_NOW = "strftime('%Y-%m-%d %H:%M:%fZ')"


@dataclass(frozen=True)
class Field:
    """One declared column, rendered by both provisioning dialects.

    ``default`` is the value the field takes when a writer omits it — the Job Record
    Store's payload and the SavedSearch registry's record→domain mapper both read it,
    so a column added to an older deployment degrades to a declared default rather
    than surfacing as ``None`` mid-task. ``None`` means "genuinely absent" for
    nullable pointer columns (``applied_at``, ``source_task_id``).
    """

    name: str
    kind: str
    required: bool = False
    primary_key: bool = False
    unique: bool = False
    max_chars: int | None = None  # explicit text cap for long-text columns
    default: Any = None  # Python-side writer default
    sql_default: str | None = None  # ALTER-safe constant SQL default
    on_create: bool = False  # autodate: set once by the server on insert
    on_update: bool = False  # autodate: refreshed by the server on update
    #: Whether the *remote* provisioning dialect declares this column.
    #:
    #: PocketBase rejects a client-declared ``id`` and adds ``created``/``updated``
    #: itself, so those three are server-managed and appear only in the local
    #: dialect. Note that the local dialect has two renderings: the ``CREATE TABLE``
    #: DDL (which declares ``id`` as the SQLite primary key) and the
    #: ``_collections.fields`` JSON (which must repeat ``id`` with ``primaryKey``,
    #: or PocketBase stops exposing the record id entirely).
    remote: bool = True

    def sqlite_type(self) -> str:
        return _SQLITE_TYPES[self.kind]

    def sqlite_column(self) -> str:
        """The ``name TYPE ...`` fragment used inside ``CREATE TABLE``."""
        parts = [self.name, self.sqlite_type()]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if self.unique:
            parts.append("UNIQUE")
        parts.extend(self._sqlite_default_clause())
        return " ".join(parts)

    def sqlite_alter_column(self) -> str:
        """The ``name TYPE ...`` fragment used by ``ALTER TABLE ... ADD COLUMN``.

        SQLite refuses ``PRIMARY KEY``/``UNIQUE`` in an added column, and a
        non-constant default (the ``strftime`` expression on autodate columns) is
        likewise rejected, so both are dropped here and the column lands nullable.
        """
        parts = [self.name, self.sqlite_type()]
        if self.sql_default is not None:
            parts.append(f"DEFAULT {self.sql_default}")
        return " ".join(parts)

    def _sqlite_default_clause(self) -> list[str]:
        if self.sql_default is not None:
            return [f"DEFAULT {self.sql_default}"]
        if self.kind == AUTODATE and self.on_create:
            return [f"DEFAULT ({_SQLITE_NOW})"]
        return []

    def pocketbase_field(self) -> dict[str, Any]:
        """The field object PocketBase's collection API expects."""
        pb: dict[str, Any] = {"name": self.name, "type": self.kind, "required": self.required}
        if self.primary_key:
            pb["primaryKey"] = True
        if self.max_chars is not None:
            pb["min"] = 0
            pb["max"] = self.max_chars
            pb["pattern"] = ""
            pb["options"] = {"min": 0, "max": self.max_chars, "pattern": ""}
        if self.kind == AUTODATE:
            pb["onCreate"] = self.on_create
            pb["onUpdate"] = self.on_update
        return pb


@dataclass(frozen=True)
class Collection:
    """One PocketBase collection: its fields, indexes and identity facts.

    ``lease_field`` names the single column that records which Automation Worker
    holds a task's lease; ``fingerprint_field`` names the deduplication column that
    Job Fingerprint normalization writes.
    """

    name: str
    collection_id: str  # the PocketBase collection id both dialects provision under
    fields: tuple[Field, ...]
    indexes: tuple[str, ...] = ()
    lease_field: str | None = None
    fingerprint_field: str | None = None

    def field(self, name: str) -> Field:
        """The declared field called ``name``. Raises ``KeyError`` when absent."""
        for spec in self.fields:
            if spec.name == name:
                return spec
        raise KeyError(f"{self.name} has no field {name!r}")

    def has_field(self, name: str) -> bool:
        return any(spec.name == name for spec in self.fields)

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields)

    def server_managed_names(self) -> tuple[str, ...]:
        """Columns PocketBase creates itself and the REST dialect must not declare."""
        return tuple(spec.name for spec in self.fields if not spec.remote)


# --------------------------------------------------------------------------- #
# Dialect renderers — the only places either provisioning path turns a
# Collection into vendor syntax.
# --------------------------------------------------------------------------- #


def sqlite_ddl(collection: Collection) -> str:
    """The ``CREATE TABLE IF NOT EXISTS`` statement for ``collection``."""
    columns = ",\n    ".join(spec.sqlite_column() for spec in collection.fields)
    return f"CREATE TABLE IF NOT EXISTS {collection.name} (\n    {columns}\n)"


def sqlite_index_ddl(collection: Collection) -> tuple[str, ...]:
    """Idempotent ``CREATE INDEX IF NOT EXISTS`` statements for ``collection``."""
    return collection.indexes


def column_migrations(collection: Collection, existing: Iterable[str]) -> list[tuple[str, str]]:
    """``(column, fragment)`` pairs to add to a table provisioned by an older schema.

    Declaration order is preserved so the additions read in the same sequence as the
    schema itself, and the primary key is skipped because SQLite cannot add one.
    """
    present = set(existing)
    return [
        (spec.name, spec.sqlite_alter_column())
        for spec in collection.fields
        if not spec.primary_key and spec.name not in present
    ]


def pocketbase_fields(collection: Collection) -> list[dict[str, Any]]:
    """The ``fields`` array for PocketBase's collection create/update API.

    Server-managed columns are omitted: PocketBase rejects a client-declared ``id``
    and provides ``created``/``updated`` itself.
    """
    return [spec.pocketbase_field() for spec in collection.fields if spec.remote]


def sqlite_metadata_fields(collection: Collection) -> list[dict[str, Any]]:
    """The ``_collections.fields`` JSON the local dialect stores.

    This is *not* the REST payload: PocketBase reads its collection schema from this
    JSON, so it has to repeat every column — including ``id`` with ``primaryKey``.
    Without that marker PocketBase stops reporting record ids at all, which silently
    breaks every lookup by id.
    """
    return [spec.pocketbase_field() for spec in collection.fields]


def pocketbase_collection_payload(collection: Collection) -> dict[str, Any]:
    """The full REST body that provisions ``collection`` on a remote PocketBase."""
    return {
        "id": collection.collection_id,
        "name": collection.name,
        "type": "base",
        "listRule": "",
        "viewRule": "",
        "createRule": "",
        "updateRule": "",
        "deleteRule": "",
        "fields": pocketbase_fields(collection),
    }


def dialect_field_names(collection: Collection) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(sqlite_names, remote_names)`` — the two dialects' declared field lists.

    The REST list legitimately lacks exactly the server-managed columns (PocketBase
    owns ``id`` and the autodate pair), so the test that keeps the dialects honest
    asserts ``set(sqlite) - set(remote) == set(collection.server_managed_names())``.
    """
    return collection.field_names, tuple(spec.name for spec in collection.fields if spec.remote)


# --------------------------------------------------------------------------- #
# Writer-facing views
# --------------------------------------------------------------------------- #


def wire_payload(collection: Collection, record: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The write payload for ``collection``: declared fields only, defaults applied.

    Undeclared keys in ``record`` are dropped rather than forwarded, which is what
    keeps a hand-built dict from smuggling a column the collection does not have.
    """
    source = record or {}
    return {
        spec.name: source.get(spec.name, spec.default)
        for spec in collection.fields
        if not spec.primary_key
    }


def wire_defaults(collection: Collection) -> dict[str, Any]:
    """The declared writer defaults, for callers that need the baseline alone."""
    return {spec.name: spec.default for spec in collection.fields if not spec.primary_key}


def normalize_record(
    collection: Collection, record: Mapping[str, Any], *, exclude: Iterable[str] = ()
) -> dict[str, Any]:
    """Fill a record read back from a possibly-older collection with declared defaults.

    A collection provisioned before a column existed returns records without it; every
    reader that goes through here sees the same fallback the schema declares instead of
    ``KeyError`` or an incidental ``None``.

    ``exclude`` names columns whose absence the *domain* means to interpret itself —
    the derived fields that fall back to a sibling key rather than to a storage
    default. Substituting the storage default there would silently change meaning.
    """
    skipped = set(exclude)
    normalized = dict(record)
    for spec in collection.fields:
        if spec.name in skipped:
            continue
        if (spec.name not in normalized or normalized[spec.name] is None) and spec.default is not None:
            normalized[spec.name] = spec.default
    return normalized


# --------------------------------------------------------------------------- #
# The five collections
# --------------------------------------------------------------------------- #

AUTOMATION_TASKS_NAME = "automation_tasks"
CANDIDATE_PROFILES_NAME = "candidate_profiles"
RESUME_REVISIONS_NAME = "resume_revisions"
JOB_RECORDS_NAME = "job_records"
SAVED_SEARCHES_NAME = "saved_searches"

#: The lease column. The Automation Worker's claim writes land here and the Task
#: Management Dashboard's Worker badge reads here; the vestigial ``assigned_worker``
#: column is no longer provisioned and is backfilled into this one on upgrade.
LEASE_FIELD = "worker_id"

#: The deduplication column Job Fingerprint normalization writes.
FINGERPRINT_FIELD = "fingerprint"

#: Baseline job-count ceiling for a SavedSearch. Declared once so the domain model,
#: the provisioner and the Web UI cannot disagree the way 20-vs-30 once did.
SAVED_SEARCH_MAX_JOBS = 30


def _autodate() -> tuple[Field, Field]:
    return (
        Field("created", AUTODATE, on_create=True, remote=False),
        Field("updated", AUTODATE, on_create=True, on_update=True, remote=False),
    )


AUTOMATION_TASKS = Collection(
    name=AUTOMATION_TASKS_NAME,
    collection_id="pbc_auto_tasks",
    lease_field=LEASE_FIELD,
    fields=(
        Field("id", TEXT, primary_key=True, default=None, remote=False),
        Field("task_type", TEXT, required=True),
        Field("status", TEXT, required=True, default="pending"),
        Field("payload", JSON, default={}),
        Field(LEASE_FIELD, TEXT),
        Field("locked_at", DATE),
        Field("last_heartbeat_at", DATE),
        Field("retry_count", NUMBER, default=0, sql_default="0"),
        Field("logs", JSON, default=[]),
        Field("error_message", TEXT),
        # Task Provenance (CONTEXT.md): manual | test | scheduler. A real column rather
        # than a payload marker, so startup reclamation can cancel test-sourced tasks
        # without reading five different marker keys, and the dashboard can show the
        # rest. Legacy rows default to manual — the sweep must not reclaim a task whose
        # origin it cannot prove.
        Field("source", TEXT, default="manual", sql_default="'manual'"),
        *_autodate(),
    ),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_status_created "
        "ON automation_tasks (status, created)",
        "CREATE INDEX IF NOT EXISTS idx_worker_id ON automation_tasks (worker_id)",
    ),
)

CANDIDATE_PROFILES = Collection(
    name=CANDIDATE_PROFILES_NAME,
    collection_id="pbc_cand_prof",
    fields=(
        Field("id", TEXT, primary_key=True, default=None, remote=False),
        Field("user_id", TEXT, required=True, default="default"),
        Field("name", TEXT, default=""),
        Field("years_of_experience", NUMBER, default=0),
        Field("education", JSON, default=[]),
        Field("core_skills", JSON, default=[]),
        Field("project_highlights", JSON, default=[]),
        Field("work_experiences", JSON, default=[]),
        Field("projects", JSON, default=[]),
        Field("target_positions", JSON, default=[]),
        Field("raw_summary", TEXT, default=""),
        Field("raw_resume_text", TEXT, default=""),
        *_autodate(),
    ),
    indexes=("CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_user_id ON candidate_profiles (user_id)",),
)

RESUME_REVISIONS = Collection(
    name=RESUME_REVISIONS_NAME,
    collection_id="pbc_res_rev",
    fields=(
        Field("id", TEXT, primary_key=True, default=None, remote=False),
        Field("user_id", TEXT, required=True, default="default"),
        Field("file_name", TEXT, required=True, default=""),
        Field("file_type", TEXT, default=""),
        Field("file_size", NUMBER, default=0),
        Field("extracted_text", TEXT, default=""),
        Field("diff_summary", TEXT, default=""),
        *_autodate(),
    ),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_revisions_user_created ON resume_revisions (user_id, created)",
    ),
)

JOB_RECORDS = Collection(
    name=JOB_RECORDS_NAME,
    collection_id="pbc_job_records",
    fingerprint_field=FINGERPRINT_FIELD,
    fields=(
        Field("id", TEXT, primary_key=True, default=None, remote=False),
        Field(FINGERPRINT_FIELD, TEXT, required=True, unique=True),
        Field("title", TEXT, required=True, default=""),
        Field("company_name", TEXT, required=True, default=""),
        Field("recruiter_name", TEXT, required=True, default=""),
        Field("salary_range", TEXT, default=""),
        Field("location", TEXT, default=""),
        Field("digest", TEXT, default=""),
        Field("job_description", TEXT, max_chars=LONG_TEXT_MAX_CHARS, default=""),
        Field("company_scale", TEXT, default=""),
        Field("industry", TEXT, default=""),
        Field("tags", JSON, default=[]),
        Field("recruiter_title", TEXT, default=""),
        Field("is_headhunter", BOOL, default=False, sql_default="FALSE"),
        Field("status", TEXT, required=True, default="unmatched", sql_default="'unmatched'"),
        Field("match_score", NUMBER),
        Field("jd_key_requirements", JSON, default=[]),
        Field("greeting_message", TEXT, default=""),
        Field("search_keywords", JSON, default=[]),
        Field("screened_reason", TEXT, default=""),
        Field("relaxed_by_whitelist", BOOL, default=False, sql_default="FALSE"),
        Field("screening_audit", TEXT, default=""),
        Field("applied_at", DATE),
        Field("applied_source", TEXT, default=""),
        Field("commute_distance_km", NUMBER),
        Field("commute_distance_text", TEXT, default=""),
        Field("first_seen_at", DATE),
        Field("last_seen_at", DATE),
        Field("source_task_id", TEXT),
        *_autodate(),
    ),
    indexes=(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_job_fingerprint ON job_records (fingerprint)",
    ),
)

SAVED_SEARCHES = Collection(
    name=SAVED_SEARCHES_NAME,
    collection_id="pbc_saved_searches",
    fields=(
        Field("id", TEXT, primary_key=True, default=None, remote=False),
        Field("name", TEXT, required=True, default=""),
        Field("description", TEXT, default=""),
        Field("keyword", TEXT, default=""),
        Field("enable_search", BOOL, default=True, sql_default="1"),
        Field("enable_filter", BOOL, default=True, sql_default="1"),
        Field("filter", JSON, default={}),
        # No writer default: the domain derives this from `target_task_type` when the
        # column is empty, so substitution here would invert execution depth. The
        # storage default still exists for rows written directly in SQL.
        Field("target_action", TEXT, sql_default="'save_jd'"),
        Field("max_jobs", NUMBER, default=SAVED_SEARCH_MAX_JOBS, sql_default=str(SAVED_SEARCH_MAX_JOBS)),
        Field("cron_expression", TEXT, default=""),
        Field("is_enabled", BOOL, default=False, sql_default="0"),
        Field("last_run_at", DATE),
        Field("target_task_type", TEXT, default="AUTO_APPLY", sql_default="'AUTO_APPLY'"),
        *_autodate(),
    ),
)

COLLECTIONS: tuple[Collection, ...] = (
    AUTOMATION_TASKS,
    CANDIDATE_PROFILES,
    RESUME_REVISIONS,
    JOB_RECORDS,
    SAVED_SEARCHES,
)

COLLECTIONS_BY_NAME: Mapping[str, Collection] = {c.name: c for c in COLLECTIONS}


# --------------------------------------------------------------------------- #
# Migration backfills
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Backfill:
    """One ordered, idempotent repair run after a collection's columns are added.

    ``requires_columns`` gates the statement on the table actually carrying those
    columns: SQLite resolves column names at prepare time, so a statement mentioning
    a column the table never had fails outright and cannot guard itself in SQL.
    """

    label: str
    sql: str
    requires_columns: tuple[str, ...] = ()


_BACKFILLS: Mapping[str, tuple[Backfill, ...]] = {
    AUTOMATION_TASKS_NAME: (
        Backfill(
            label="adopt-assigned-worker-into-lease-column",
            # The dashboard used to read only `assigned_worker` while the worker
            # wrote only `worker_id`, so a task's holder was recorded in whichever
            # column its writer happened to know about. Fold the legacy column into
            # the lease column the schema actually declares.
            sql=(
                f"UPDATE {AUTOMATION_TASKS_NAME} SET {LEASE_FIELD} = assigned_worker "
                f"WHERE COALESCE({LEASE_FIELD}, '') = '' "
                "AND COALESCE(assigned_worker, '') != ''"
            ),
            requires_columns=("assigned_worker", LEASE_FIELD),
        ),
    ),
}


def backfills(collection: Collection, existing: Iterable[str] = ()) -> tuple[Backfill, ...]:
    """The repairs for ``collection`` whose required columns the table actually has."""
    present = set(existing)
    return tuple(
        entry
        for entry in _BACKFILLS.get(collection.name, ())
        if set(entry.requires_columns) <= present
    )
