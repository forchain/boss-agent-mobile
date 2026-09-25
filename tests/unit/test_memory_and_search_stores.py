"""
tests/unit/test_memory_and_search_stores.py
===========================================
The Candidate Memory Store and SavedSearch Store seams (ADR 0013, step 2).

The same behavior suite runs against each store's in-memory and PocketBase adapters,
mirroring the existing Job Record Store test pattern — that pattern is the prior art
and the target shape for these two seams.

All Fast Unit: a fake session stands in for PocketBase, so no process and no port is
involved.
"""

from pathlib import Path
from typing import Any

import pytest

from boss_agent.broker.pocketbase_adapter import BaseTaskBroker, InMemoryTaskBroker
from boss_agent.candidate_memory_store import (
    CandidateMemoryStore,
    InMemoryCandidateMemoryStore,
    PocketBaseCandidateMemoryStore,
)
from boss_agent.models import FilterConfig, SavedSearch, SearchConfig
from boss_agent.saved_search_store import (
    InMemorySavedSearchStore,
    PocketBaseSavedSearchStore,
    SavedSearchStore,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.ok = 200 <= status_code < 300
        self.text = str(self._payload)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakePocketBaseSession:
    """A dict-backed stand-in for `requests.Session`, keyed by collection."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}
        self.calls: list[tuple[str, str]] = []

    def _collection(self, url: str) -> tuple[str, str | None]:
        parts = url.split("/api/collections/", 1)[1].split("/")
        name = parts[0]
        record_id = parts[2] if len(parts) > 2 else None
        return name, record_id

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url))
        name, record_id = self._collection(url)
        records = self.collections.get(name, {})
        if record_id:
            if record_id not in records:
                return FakeResponse(404, {})
            return FakeResponse(200, records[record_id])
        params = kwargs.get("params") or {}
        items = list(records.values())
        if params.get("filter"):
            needle = str(params["filter"]).split("'")[1]
            items = [i for i in items if i.get("user_id") == needle]
        return FakeResponse(200, {"items": items, "totalItems": len(items)})

    def post(self, url: str, json: Any = None, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url))
        name, _ = self._collection(url)
        body = dict(json or {})
        record_id = body.get("id") or f"rec{len(self.collections.get(name, {})) + 1:05d}"
        body["id"] = record_id
        self.collections.setdefault(name, {})[record_id] = body
        return FakeResponse(200, body)

    def patch(self, url: str, json: Any = None, **kwargs: Any) -> FakeResponse:
        self.calls.append(("PATCH", url))
        name, record_id = self._collection(url)
        body = dict(json or {})
        assert record_id
        body["id"] = record_id
        self.collections.setdefault(name, {})[record_id] = body
        return FakeResponse(200, body)

    def delete(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("DELETE", url))
        name, record_id = self._collection(url)
        if record_id not in self.collections.get(name, {}):
            return FakeResponse(404, {})
        self.collections[name].pop(record_id)
        return FakeResponse(204, {})


def _headers() -> dict[str, str]:
    return {"Accept": "application/json"}


@pytest.fixture
def pb_session() -> FakePocketBaseSession:
    return FakePocketBaseSession()


@pytest.fixture
def scratch_sqlite(tmp_path: Path) -> Path:
    """An empty local database, so the fallback is hermetic.

    Without this the store degrades to whatever `config/candidate_memory` state the
    machine running the suite happens to have — and a "no profile yet" assertion
    measures the developer, not the code.
    """
    import sqlite3

    db_file = tmp_path / "scratch.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE candidate_profiles (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, "
        "education TEXT, core_skills TEXT, project_highlights TEXT, work_experiences TEXT, "
        "projects TEXT, target_positions TEXT, raw_summary TEXT, raw_resume_text TEXT, updated TEXT)"
    )
    conn.execute(
        "CREATE TABLE resume_revisions (id TEXT PRIMARY KEY, user_id TEXT, file_name TEXT, "
        "file_type TEXT, file_size INTEGER, extracted_text TEXT, diff_summary TEXT, "
        "created TEXT, updated TEXT)"
    )
    conn.commit()
    conn.close()
    return db_file


# --------------------------------------------------------------------------- #
# The broker is confined
# --------------------------------------------------------------------------- #


def test_the_abstract_broker_declares_exactly_the_task_verbs() -> None:
    """ADR 0013's confinement, complete: tasks, leases, logs and subscriptions."""
    assert BaseTaskBroker.__abstractmethods__ == {
        "create_task",
        "get_task",
        "list_pending_tasks",
        "claim_task",
        "update_heartbeat",
        "update_task_status",
        "list_stale_running_tasks",
        "requeue_task",
        "append_log",
        "subscribe_tasks",
    }


@pytest.mark.parametrize(
    "verb",
    [
        "get_candidate_profile",
        "save_candidate_profile",
        "list_resume_revisions",
        "create_resume_revision",
        "list_saved_searches",
        "get_saved_search",
        "save_saved_search",
        "delete_saved_search",
    ],
)
def test_the_confined_verbs_are_gone_from_the_broker(verb: str) -> None:
    assert not hasattr(BaseTaskBroker, verb)
    assert not callable(getattr(InMemoryTaskBroker(), verb, None))


def test_the_broker_composes_the_seams_it_no_longer_inherits() -> None:
    broker = InMemoryTaskBroker()
    assert isinstance(broker.candidate_memory, CandidateMemoryStore)
    assert isinstance(broker.saved_searches, SavedSearchStore)
    assert not isinstance(broker, CandidateMemoryStore)
    assert not isinstance(broker, SavedSearchStore)


# --------------------------------------------------------------------------- #
# Candidate Memory Store: one suite, both adapters
# --------------------------------------------------------------------------- #


def _memory_stores(
    pb_session: FakePocketBaseSession, scratch_sqlite: Path
) -> list[CandidateMemoryStore]:
    return [
        InMemoryCandidateMemoryStore(load_local_profile=False),
        PocketBaseCandidateMemoryStore(
            base_url="http://pb.test",
            session=pb_session,
            headers=_headers,
            sqlite_db_path=scratch_sqlite,
        ),
    ]


@pytest.mark.parametrize("index", [0, 1], ids=["in-memory", "pocketbase"])
@pytest.mark.asyncio
async def test_profile_round_trips(
    pb_session: FakePocketBaseSession, scratch_sqlite: Path, index: int
) -> None:
    store = _memory_stores(pb_session, scratch_sqlite)[index]
    assert await store.get_candidate_profile() is None

    await store.save_candidate_profile({"name": "周先生", "core_skills": ["Python"]})
    profile = await store.get_candidate_profile()
    assert profile is not None
    assert profile["name"] == "周先生"
    assert profile["core_skills"] == ["Python"]


@pytest.mark.parametrize("index", [0, 1], ids=["in-memory", "pocketbase"])
@pytest.mark.asyncio
async def test_a_partial_save_does_not_wipe_the_stored_profile(
    pb_session: FakePocketBaseSession, scratch_sqlite: Path, index: int
) -> None:
    store = _memory_stores(pb_session, scratch_sqlite)[index]
    await store.save_candidate_profile({"name": "周先生", "core_skills": ["Python"]})
    await store.save_candidate_profile({"name": "周先生"})
    profile = await store.get_candidate_profile()
    assert profile is not None
    assert profile["core_skills"] == ["Python"]


@pytest.mark.parametrize("index", [0, 1], ids=["in-memory", "pocketbase"])
@pytest.mark.asyncio
async def test_revisions_are_listed_newest_first(
    pb_session: FakePocketBaseSession, scratch_sqlite: Path, index: int
) -> None:
    store = _memory_stores(pb_session, scratch_sqlite)[index]
    assert await store.list_resume_revisions() == []

    await store.create_resume_revision({"file_name": "old.txt", "created": "2026-01-01T00:00:00Z"})
    await store.create_resume_revision({"file_name": "new.txt", "created": "2026-06-01T00:00:00Z"})

    revisions = await store.list_resume_revisions()
    assert {r["file_name"] for r in revisions} == {"old.txt", "new.txt"}
    if index == 0:
        assert [r["file_name"] for r in revisions] == ["new.txt", "old.txt"]


@pytest.mark.parametrize("index", [0, 1], ids=["in-memory", "pocketbase"])
@pytest.mark.asyncio
async def test_a_revision_records_its_owner(
    pb_session: FakePocketBaseSession, scratch_sqlite: Path, index: int
) -> None:
    store = _memory_stores(pb_session, scratch_sqlite)[index]
    revision = await store.create_resume_revision({"file_name": "resume.pdf"}, user_id="alice")
    assert revision["user_id"] == "alice"
    assert revision.get("id")


# --------------------------------------------------------------------------- #
# SavedSearch Store: one suite, both adapters
# --------------------------------------------------------------------------- #


def _search(search_id: str = "s1", name: str = "策略") -> SavedSearch:
    return SavedSearch(
        id=search_id,
        name=name,
        search=SearchConfig(keyword="agent"),
        filter=FilterConfig(),
        cron_expression="0 9 * * *",
        is_enabled=True,
    )


def _search_stores(
    pb_session: FakePocketBaseSession, on_delete: Any = None
) -> list[SavedSearchStore]:
    return [
        InMemorySavedSearchStore(on_delete=on_delete),
        PocketBaseSavedSearchStore(
            base_url="http://pb.test",
            session=pb_session,
            headers=_headers,
            on_delete=on_delete,
        ),
    ]


@pytest.mark.parametrize("index", [0, 1], ids=["in-memory", "pocketbase"])
@pytest.mark.asyncio
async def test_a_search_round_trips(pb_session: FakePocketBaseSession, index: int) -> None:
    store = _search_stores(pb_session)[index]
    assert await store.list_saved_searches() == []

    await store.save_saved_search(_search())
    listed = await store.list_saved_searches()
    assert [s.id for s in listed] == ["s1"]
    fetched = await store.get_saved_search("s1")
    assert fetched is not None
    assert fetched.name == "策略"
    assert fetched.cron_expression == "0 9 * * *"


@pytest.mark.parametrize("index", [0, 1], ids=["in-memory", "pocketbase"])
@pytest.mark.asyncio
async def test_deleting_a_missing_search_reports_false(
    pb_session: FakePocketBaseSession, index: int
) -> None:
    store = _search_stores(pb_session)[index]
    assert await store.delete_saved_search("nope") is False


@pytest.mark.asyncio
async def test_deleting_a_search_revokes_its_schedule(pb_session: FakePocketBaseSession) -> None:
    """A deleted search must not keep firing: the store owns the revocation hook."""
    revoked: list[str] = []

    async def on_delete(search_id: str) -> None:
        revoked.append(search_id)

    for store in _search_stores(pb_session, on_delete=on_delete):
        revoked.clear()
        await store.save_saved_search(_search())
        assert await store.delete_saved_search("s1") is True
        assert revoked == ["s1"]
        assert await store.get_saved_search("s1") is None


@pytest.mark.asyncio
async def test_the_pocketbase_store_saves_the_collections_field_spellings() -> None:
    """The store is the one place that knows how a SavedSearch maps onto the record."""
    session = FakePocketBaseSession()
    store = PocketBaseSavedSearchStore(
        base_url="http://pb.test", session=session, headers=_headers
    )
    await store.save_saved_search(_search())

    stored = session.collections["saved_searches"]["s1"]
    assert stored["keyword"] == "agent"
    # The strategy derives its action from its task type at construction.
    assert str(stored["target_action"]) == "auto_apply"
    assert stored["max_jobs"] == 30, "the schema's declared default"
    assert set(stored["filter"]) == {
        "education",
        "salary",
        "experience",
        "activity",
        "company_scales",
        "industries",
        "enable_filter",
    }


def test_the_sqlite_fallback_is_exposed_by_the_store_not_the_broker() -> None:
    """Resilience lives inside the seam it protects."""
    assert hasattr(PocketBaseCandidateMemoryStore, "_query_sqlite_profile")
    assert not hasattr(InMemoryTaskBroker(), "_query_sqlite_profile")


def test_the_fallback_reads_a_local_database(tmp_path: Path) -> None:
    """Given only a SQLite file, the store still resolves a profile."""
    import sqlite3

    db_file = tmp_path / "data.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE candidate_profiles (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, "
        "education TEXT, core_skills TEXT, updated TEXT)"
    )
    conn.execute(
        "INSERT INTO candidate_profiles VALUES "
        "('p1', 'default', '离线候选人', '[]', '[\"Rust\"]', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker  # noqa: F401

    store = PocketBaseCandidateMemoryStore(
        base_url="http://unreachable.invalid",
        session=FakePocketBaseSession(),
        headers=_headers,
        sqlite_db_path=db_file,
    )
    assert store._query_sqlite_profile("default")["core_skills"] == ["Rust"]
