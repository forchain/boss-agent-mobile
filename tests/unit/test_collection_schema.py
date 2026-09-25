"""
tests/unit/test_collection_schema.py
====================================
The Collection Schema is the one owner of the State Stream Broker's five collections.

These tests assert the *externally visible* consequences of that ownership: what
columns a provisioned collection has, what payload a store write produces, and what
default a reader gets — never the module's private helpers. The class of bug they
exist to make structurally impossible is the one that shipped: the SQLite dialect
declared fields the REST dialect silently omitted, so a remotely provisioned
PocketBase never stored them.
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from boss_agent.broker.collection_schema import (
    AUTOMATION_TASKS,
    COLLECTIONS,
    JOB_RECORDS,
    LEASE_FIELD,
    SAVED_SEARCH_MAX_JOBS,
    SAVED_SEARCHES,
    backfills,
    column_migrations,
    dialect_field_names,
    normalize_record,
    pocketbase_collection_payload,
    pocketbase_fields,
    sqlite_ddl,
    sqlite_metadata_fields,
    wire_payload,
)
from boss_agent.broker.provisioner import provision_remote_pocketbase, provision_sqlite_database
from boss_agent.models import SavedSearch
from boss_agent.searches import record_to_saved_search

_COLLECTIONS_DDL = """
    CREATE TABLE _collections (
        id TEXT PRIMARY KEY,
        system BOOLEAN DEFAULT FALSE,
        type TEXT DEFAULT "base",
        name TEXT UNIQUE NOT NULL,
        fields JSON DEFAULT "[]" NOT NULL,
        indexes JSON DEFAULT "[]" NOT NULL,
        listRule TEXT DEFAULT NULL,
        viewRule TEXT DEFAULT NULL,
        createRule TEXT DEFAULT NULL,
        updateRule TEXT DEFAULT NULL,
        deleteRule TEXT DEFAULT NULL,
        options JSON DEFAULT "{}" NOT NULL,
        created TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ')),
        updated TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ'))
    )
"""


def _blank_pocketbase_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "data.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(_COLLECTIONS_DDL)
    conn.commit()
    conn.close()
    return db_file


def _table_columns(db_file: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table})")  # noqa: S608 - table is a schema constant
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()
    return columns


# --------------------------------------------------------------------------- #
# The two dialects
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("collection", COLLECTIONS, ids=lambda c: c.name)
def test_both_dialects_declare_the_same_fields(collection) -> None:
    """A field can live in the SQLite dialect or the REST dialect, never only one.

    The sole permitted difference is the columns PocketBase manages itself.
    """
    sqlite_names, remote_names = dialect_field_names(collection)
    assert set(remote_names) <= set(sqlite_names)
    assert set(sqlite_names) - set(remote_names) == set(collection.server_managed_names())


def test_rest_dialect_declares_the_saved_searches_fields_it_used_to_omit() -> None:
    """`target_action` and `max_jobs` must reach a remotely provisioned collection."""
    remote = {f["name"] for f in pocketbase_fields(SAVED_SEARCHES)}
    assert {"target_action", "max_jobs"} <= remote


def test_rest_dialect_declares_the_job_records_fields_it_used_to_omit() -> None:
    """Worker writes must not silently drop these on a remote deployment."""
    remote = {f["name"] for f in pocketbase_fields(JOB_RECORDS)}
    assert {
        "company_scale",
        "industry",
        "tags",
        "recruiter_title",
        "is_headhunter",
        "relaxed_by_whitelist",
        "screening_audit",
        "applied_at",
        "applied_source",
    } <= remote


def test_job_description_keeps_its_explicit_length_cap_in_both_dialects() -> None:
    """PocketBase caps text at 5000 chars without an explicit max; an expanded JD needs more."""
    rest_field = next(
        f for f in pocketbase_fields(JOB_RECORDS) if f["name"] == "job_description"
    )
    assert rest_field["options"]["max"] > 5000
    assert "job_description TEXT" in sqlite_ddl(JOB_RECORDS)


# --------------------------------------------------------------------------- #
# The lease column
# --------------------------------------------------------------------------- #


def test_the_lease_column_is_the_one_the_worker_writes() -> None:
    """`worker_id` is the single lease owner; the vestigial column is not provisioned."""
    assert AUTOMATION_TASKS.lease_field == LEASE_FIELD
    assert LEASE_FIELD in AUTOMATION_TASKS.field_names
    assert not AUTOMATION_TASKS.has_field("assigned_worker")
    assert "assigned_worker" not in sqlite_ddl(AUTOMATION_TASKS)
    assert "assigned_worker" not in {f["name"] for f in pocketbase_fields(AUTOMATION_TASKS)}


def test_backfill_folds_the_legacy_column_into_the_lease_column(tmp_path: Path) -> None:
    """A task whose holder was recorded in `assigned_worker` keeps its holder on upgrade."""
    db_file = _blank_pocketbase_db(tmp_path)
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE automation_tasks (
            id TEXT PRIMARY KEY,
            task_type TEXT,
            status TEXT,
            worker_id TEXT,
            assigned_worker TEXT
        )
    """)
    conn.execute(
        "INSERT INTO _collections (id, name, fields) "
        "VALUES ('pbc_auto_tasks', 'automation_tasks', '[]')"
    )
    conn.execute(
        "INSERT INTO automation_tasks (id, task_type, status, worker_id, assigned_worker) "
        "VALUES ('t1', 'SCRAPE_JOBS', 'running', NULL, 'worker-legacy')"
    )
    conn.execute(
        "INSERT INTO automation_tasks (id, task_type, status, worker_id, assigned_worker) "
        "VALUES ('t2', 'SCRAPE_JOBS', 'running', 'worker-current', 'worker-stale')"
    )
    conn.commit()
    conn.close()

    assert provision_sqlite_database(db_file) is True

    conn = sqlite3.connect(str(db_file))
    holders = dict(conn.execute("SELECT id, worker_id FROM automation_tasks"))
    conn.close()
    assert holders["t1"] == "worker-legacy", "the legacy column must be adopted"
    assert holders["t2"] == "worker-current", "the lease column must win when it is set"


def test_backfill_needs_the_legacy_column_to_exist() -> None:
    """The repair is gated on the table actually carrying the column it mentions."""
    assert backfills(AUTOMATION_TASKS, existing={"id", "worker_id"}) == ()
    assert len(backfills(AUTOMATION_TASKS, existing={"id", "worker_id", "assigned_worker"})) == 1


# --------------------------------------------------------------------------- #
# Provisioning renders the schema
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("collection", COLLECTIONS, ids=lambda c: c.name)
def test_a_provisioned_database_carries_exactly_the_declared_columns(
    tmp_path: Path, collection
) -> None:
    """What the provisioner creates is the schema, column for column."""
    db_file = _blank_pocketbase_db(tmp_path)
    assert provision_sqlite_database(db_file) is True
    assert _table_columns(db_file, collection.name) == set(collection.field_names)


@pytest.mark.parametrize("collection", COLLECTIONS, ids=lambda c: c.name)
def test_the_collections_metadata_is_rendered_from_the_schema(
    tmp_path: Path, collection
) -> None:
    """`_collections.fields` is a view of the schema, not a third spelling."""
    db_file = _blank_pocketbase_db(tmp_path)
    assert provision_sqlite_database(db_file) is True

    conn = sqlite3.connect(str(db_file))
    (fields_json,) = conn.execute(
        "SELECT fields FROM _collections WHERE name = ?", (collection.name,)
    ).fetchone()
    conn.close()

    assert json.loads(fields_json) == sqlite_metadata_fields(collection)


@pytest.mark.parametrize("collection", COLLECTIONS, ids=lambda c: c.name)
def test_local_metadata_declares_the_primary_key(tmp_path: Path, collection) -> None:
    """PocketBase reads its primary key from `_collections.fields`.

    Dropping the `id` entry there is not a cosmetic change: PocketBase then renders
    every record without an `id` at all, breaking every lookup by id.
    """
    metadata = sqlite_metadata_fields(collection)
    id_field = next(f for f in metadata if f["name"] == "id")
    assert id_field.get("primaryKey") is True
    assert id_field.get("autogeneratePattern") == "[a-z0-9]{15}"
    assert id_field.get("pattern") == "^[a-z0-9]+$"
    # It is the *local* dialect that needs it; the REST API rejects a declared id.
    assert "id" not in {f["name"] for f in pocketbase_fields(collection)}


def test_migrations_add_every_declared_column_an_older_table_lacks() -> None:
    """Adding a field to the schema is the only edit a column addition needs."""
    missing = column_migrations(JOB_RECORDS, existing=["id", "title", "company_name"])
    added = {name for name, _fragment in missing}
    assert {"digest", "applied_at", "fingerprint", "relaxed_by_whitelist"} <= added
    assert "id" not in added, "SQLite cannot add a primary key"


# --------------------------------------------------------------------------- #
# Remote provisioning can migrate, not only create
# --------------------------------------------------------------------------- #


def _remote_session(existing_collections: list[dict]) -> MagicMock:
    session = MagicMock()
    auth_resp = MagicMock(ok=True)
    auth_resp.json.return_value = {"token": "test_token"}
    list_resp = MagicMock(ok=True)
    list_resp.json.return_value = {"items": existing_collections}
    ok_resp = MagicMock(ok=True)
    records_resp = MagicMock(ok=True)
    records_resp.json.return_value = {"totalItems": 1}  # skip seeding

    def mock_post(url, **kwargs):
        return auth_resp if "auth-with-password" in url else ok_resp

    def mock_get(url, **kwargs):
        return list_resp if url.endswith("/api/collections") else records_resp

    session.post.side_effect = mock_post
    session.get.side_effect = mock_get
    session.patch.return_value = ok_resp
    return session


def test_remote_provisioning_patches_an_existing_collection_with_the_declared_fields() -> None:
    """The gap that shipped: a remote collection could never be migrated."""
    live = {
        "id": "pbc_saved_searches",
        "name": "saved_searches",
        "type": "base",
        "fields": [{"name": "name", "type": "text", "required": True}],
    }
    session = _remote_session([live])

    with patch("requests.Session", return_value=session):
        assert (
            provision_remote_pocketbase("http://127.0.0.1:8090", "a@b.c", "pw") is True
        )

    assert session.patch.called, "an existing collection missing fields must be patched"
    body = session.patch.call_args.kwargs["json"]
    patched = {f["name"] for f in body["fields"]}
    assert {"target_action", "max_jobs", "cron_expression"} <= patched


def test_remote_migration_never_drops_a_column_the_schema_does_not_know() -> None:
    """Upgrading must add, not destroy: an unknown live column keeps its data."""
    live = {
        "id": "pbc_saved_searches",
        "name": "saved_searches",
        "type": "base",
        "fields": [
            {"name": "name", "type": "text", "required": True},
            {"name": "legacy_note", "type": "text", "required": False},
        ],
    }
    session = _remote_session([live])

    with patch("requests.Session", return_value=session):
        assert provision_remote_pocketbase("http://127.0.0.1:8090", "a@b.c", "pw") is True

    patched = {f["name"] for f in session.patch.call_args.kwargs["json"]["fields"]}
    assert "legacy_note" in patched
    assert "max_jobs" in patched


def test_remote_provisioning_leaves_an_up_to_date_collection_alone() -> None:
    """An already-migrated collection is not rewritten on every start."""
    live = pocketbase_collection_payload(SAVED_SEARCHES)
    session = _remote_session([live])

    with patch("requests.Session", return_value=session):
        assert provision_remote_pocketbase("http://127.0.0.1:8090", "a@b.c", "pw") is True

    assert not session.patch.called


# --------------------------------------------------------------------------- #
# Writer-facing views
# --------------------------------------------------------------------------- #


def test_the_job_record_write_payload_is_the_schema() -> None:
    """The store writes the declared column set, so a new column is written at once."""
    payload = wire_payload(JOB_RECORDS, {"title": "工程师"})
    assert set(payload) == set(JOB_RECORDS.field_names) - {"id"}
    assert payload["title"] == "工程师"
    assert payload["status"] == "unmatched"
    assert payload["tags"] == []


def test_the_store_payload_rejects_keys_the_schema_does_not_declare() -> None:
    """An undeclared key must not be smuggled onto the wire."""
    from boss_agent.job_store import _record_fields

    fields = _record_fields({"title": "工程师", "smuggled": "nope"}, "fp", "now")
    assert "smuggled" not in fields
    assert fields["fingerprint"] == "fp"
    assert set(fields) == set(JOB_RECORDS.field_names) - {"id"}


def test_reading_an_older_record_degrades_to_the_declared_default() -> None:
    """A collection provisioned before a column existed reads predictably."""
    normalized = normalize_record(JOB_RECORDS, {"fingerprint": "fp", "title": "工程师"})
    assert normalized["status"] == "unmatched"
    assert normalized["relaxed_by_whitelist"] is False
    assert normalized["tags"] == []


def test_normalized_reads_leave_derived_columns_for_the_domain() -> None:
    """A column the domain derives from a sibling key is not pre-filled."""
    normalized = normalize_record(
        SAVED_SEARCHES, {"name": "s"}, exclude=("name", "keyword", "enable_search", "enable_filter", "target_action")
    )
    assert "target_action" not in normalized
    assert normalized["max_jobs"] == SAVED_SEARCH_MAX_JOBS


# --------------------------------------------------------------------------- #
# The 20-vs-30 default, owned once
# --------------------------------------------------------------------------- #


def test_max_jobs_default_lives_in_exactly_one_place() -> None:
    """The domain model, the provisioner and the registry all read the schema's value."""
    assert SAVED_SEARCHES.field("max_jobs").default == SAVED_SEARCH_MAX_JOBS
    assert SavedSearch(id="s").max_jobs == SAVED_SEARCH_MAX_JOBS
    assert record_to_saved_search("s", {"name": "s"}).max_jobs == SAVED_SEARCH_MAX_JOBS


def test_a_stored_max_jobs_still_wins_over_the_default() -> None:
    """The default is a fallback, not an override."""
    assert SavedSearch.from_dict("s", {"max_jobs": 7}).max_jobs == 7
    assert record_to_saved_search("s", {"name": "s", "max_jobs": 7}).max_jobs == 7


def test_the_registry_mapper_derives_target_action_from_the_task_type() -> None:
    """Normalizing the record must not pre-empt the domain's derivation."""
    search = record_to_saved_search(
        "s", {"name": "s", "target_task_type": "AUTO_APPLY", "target_action": None}
    )
    assert search.target_action == "auto_apply"
    assert search.target_task_type == "AUTO_APPLY"
