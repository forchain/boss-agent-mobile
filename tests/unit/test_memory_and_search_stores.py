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

from boss_agent.broker.collection_schema import SAVED_SEARCH_MAX_JOBS
from boss_agent.broker.pocketbase_adapter import (
    BaseTaskBroker,
    InMemoryTaskBroker,
    PocketBaseTaskBroker,
)
from boss_agent.broker.provisioner import DEFAULT_INITIAL_SEARCHES
from boss_agent.candidate_memory_store import (
    CandidateMemoryStore,
    InMemoryCandidateMemoryStore,
    PocketBaseCandidateMemoryStore,
)
from boss_agent.enums import TargetTaskType
from boss_agent.errors import TransportError
from boss_agent.saved_search_store import (
    InMemorySavedSearchStore,
    PocketBaseSavedSearchStore,
    SavedSearchStore,
    resolve_saved_search_store,
)
from boss_agent.search_entities import FilterConfig, SavedSearch, SearchConfig


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
        #: Headers of each request, so auth wiring is assertable through a public read.
        self.request_headers: list[dict[str, str]] = []

    def _collection(self, url: str) -> tuple[str, str | None]:
        parts = url.split("/api/collections/", 1)[1].split("/")
        name = parts[0]
        record_id = parts[2] if len(parts) > 2 else None
        return name, record_id

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url))
        self.request_headers.append(kwargs.get("headers") or {})
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


class _RejectingSession(FakePocketBaseSession):
    """A PocketBase that rejects every write, by status code or by transport error."""

    def __init__(self, *, raise_instead: bool = False) -> None:
        super().__init__()
        self._raise_instead = raise_instead

    def _reject(self, method: str, url: str) -> FakeResponse:
        self.calls.append((method, url))
        if self._raise_instead:
            raise RuntimeError("connection reset")
        return FakeResponse(500, {"message": "boom"})

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._reject("GET", url)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._reject("POST", url)

    def patch(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._reject("PATCH", url)


@pytest.mark.parametrize("raise_instead", [False, True], ids=["http-500", "transport-error"])
@pytest.mark.asyncio
async def test_a_failed_search_write_reports_failure_rather_than_a_phantom(
    raise_instead: bool,
) -> None:
    """A write must not come back looking persisted when PocketBase never took it.

    Returning the input unchanged made a phantom indistinguishable from a stored preset,
    and the Automation Scheduler's same-minute guard reads the ``last_run_at`` this write
    is supposed to store — so a swallowed failure meant the same search dispatched again
    on every tick for the rest of the minute. Reads may degrade; writes must speak up.
    """
    store = PocketBaseSavedSearchStore(
        base_url="http://pb.test",
        session=_RejectingSession(raise_instead=raise_instead),
        headers=_headers,
    )
    with pytest.raises(TransportError):
        await store.save_saved_search(_search())
    with pytest.raises(TransportError):
        await store.list_saved_searches()


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
    store = PocketBaseSavedSearchStore(base_url="http://pb.test", session=session, headers=_headers)
    await store.save_saved_search(_search())

    stored = session.collections["saved_searches"]["s1"]
    assert stored["keyword"] == "agent"
    # The strategy derives its action from its task type at construction.
    assert str(stored["target_action"]) == "auto_apply"
    assert stored["max_jobs"] == 30, "the schema's declared default"
    # Every FilterConfig field has to reach the record, not just the ones an older
    # revision happened to list. This dict is the adapter's whole vocabulary, and a
    # field missing from it is silently dropped on every write that passes through
    # here — see the channel regression just below.
    assert set(stored["filter"]) == {
        "education",
        "salary",
        "experience",
        "activity",
        "company_scales",
        "industries",
        "enable_filter",
        "channel_preference",
    }


@pytest.mark.asyncio
async def test_the_pocketbase_store_writes_the_strategy_channel(pb_session: FakePocketBaseSession):
    """A strategy's 仅直招 must survive the adapter's own write.

    The Automation Scheduler re-saves a whole record after every cron dispatch, purely
    to stamp ``last_run_at``. That write goes through this same body, so a channel the
    operator had set stopped being stored the first time a scheduled run happened —
    which is what "I added 仅直招 and it cleared itself" was.
    """
    store = PocketBaseSavedSearchStore(
        base_url="http://pb.test", session=pb_session, headers=_headers
    )
    direct_only = SavedSearch(
        id="s_channel",
        name="次选行业",
        search=SearchConfig(keyword="Agent"),
        filter=FilterConfig(education="不限", channel_preference="direct_only"),
        cron_expression="0 12 * * *",
        is_enabled=True,
    )

    await store.save_saved_search(direct_only)

    stored = pb_session.collections["saved_searches"]["s_channel"]
    assert stored["filter"]["channel_preference"] == "direct_only"

    # And the read-back agrees, so the round trip is closed rather than half-fixed.
    fetched = await store.get_saved_search("s_channel")
    assert fetched is not None
    assert fetched.filter.channel_preference == "direct_only"


@pytest.mark.parametrize("preference", ["", "all", "direct_only", "headhunter_only"])
@pytest.mark.asyncio
async def test_the_pocketbase_store_writes_every_channel_state(
    pb_session: FakePocketBaseSession, preference: str
):
    """``''`` (inherit) is a real value, not an absent key: it has to be written too.

    An omitted key also reads back as inherit, so the difference looks invisible here —
    but it is the difference between "inherit whatever the global setting says" and
    "this record never stated a channel", and only the written form survives an
    unrelated later edit.
    """
    store = PocketBaseSavedSearchStore(
        base_url="http://pb.test", session=pb_session, headers=_headers
    )
    search = SavedSearch(
        id=f"s_{preference or 'inherit'}",
        name="渠道策略",
        search=SearchConfig(keyword="Agent"),
        filter=FilterConfig(channel_preference=preference),
    )

    await store.save_saved_search(search)

    stored = pb_session.collections["saved_searches"][f"s_{preference or 'inherit'}"]
    assert "channel_preference" in stored["filter"]
    assert stored["filter"]["channel_preference"] == preference


@pytest.mark.asyncio
async def test_a_saved_strategy_survives_a_whole_record_rewrite(
    pb_session: FakePocketBaseSession,
):
    """The scheduler's last_run_at write must not drop anything the operator set.

    This is the shape of the real failure: a full-record rewrite that changes one field
    and silently resets another. Asserting on the final state after the rewrite — not
    on the write's payload — is what makes it a regression test for the symptom.
    """
    store = PocketBaseSavedSearchStore(
        base_url="http://pb.test", session=pb_session, headers=_headers
    )
    search = SavedSearch(
        id="s_rewrite",
        name="大公司",
        search=SearchConfig(keyword="Agent"),
        filter=FilterConfig(
            education="不限",
            salary="50K以上",
            industries=["互联网", "计算机软件"],
            channel_preference="direct_only",
        ),
        cron_expression="0 9 * * *",
        is_enabled=True,
    )
    await store.save_saved_search(search)

    # What AutomationScheduler.run_once does after dispatching: stamp the timestamp,
    # then write the record back.
    reloaded = await store.get_saved_search("s_rewrite")
    assert reloaded is not None
    reloaded.last_run_at = "2026-10-08T12:00:04"
    await store.save_saved_search(reloaded)

    final = await store.get_saved_search("s_rewrite")
    assert final is not None
    assert final.last_run_at == "2026-10-08T12:00:04"
    assert final.filter.channel_preference == "direct_only", (
        "仅直招 was erased by the scheduled run's own bookkeeping write"
    )
    assert final.filter.salary == "50K以上"
    assert final.filter.industries == ["互联网", "计算机软件"]


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


# --------------------------------------------------------------------------- #
# Reads hydrate through the Collection Schema
# --------------------------------------------------------------------------- #


def _store_over(records: dict[str, dict[str, Any]]) -> PocketBaseSavedSearchStore:
    """A PocketBase store whose collection already holds these raw records."""
    session = FakePocketBaseSession()
    session.collections["saved_searches"] = records
    return PocketBaseSavedSearchStore(base_url="http://pb.test", session=session, headers=_headers)


@pytest.mark.asyncio
async def test_a_read_lands_on_the_schemas_declared_defaults() -> None:
    """A record read back must hydrate through the Collection Schema, not bare guesses.

    The bare path took a present-but-null column at face value, so a preset whose
    ``target_task_type`` came back null hydrated as ``None`` and — because the domain
    derives its action from the task type — resolved to ``save_jd`` instead of
    ``auto_apply``. The search silently stopped applying, with nothing in the record to
    explain it. Routing reads through the schema is what makes the declared default,
    not an incidental ``None``, decide.
    """
    store = _store_over(
        {
            "s1": {
                "id": "s1",
                "name": "老记录",
                "description": None,
                "target_task_type": None,
                "max_jobs": None,
            }
        }
    )

    read = await store.get_saved_search("s1")

    assert read is not None
    assert read.max_jobs == SAVED_SEARCH_MAX_JOBS, "the schema's declared default"
    assert read.target_task_type == TargetTaskType.AUTO_APPLY
    assert str(read.target_action) == "auto_apply"
    assert read.description == ""


@pytest.mark.asyncio
async def test_derived_columns_keep_their_domain_fallback_on_read() -> None:
    """The five derived columns must not be pre-filled with their storage default.

    ``name``/``keyword``/``enable_*``/``target_action`` are absent on old records
    *meaning* "derive me from a sibling key". Handing them the schema's storage default
    first would turn that absence into a value — an empty ``target_action`` would stop
    inheriting the task type and quietly invert execution depth.
    """
    store = _store_over({"s1": {"id": "s1"}})

    read = await store.get_saved_search("s1")

    assert read is not None
    assert read.name == "s1", "falls back to the id, not the schema's empty string"
    assert read.keyword == "agent", "the domain default, not the schema's empty string"
    assert str(read.target_action) == "auto_apply"


# --------------------------------------------------------------------------- #
# Default search resolution
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("index", [0, 1], ids=["in-memory", "pocketbase"])
@pytest.mark.asyncio
async def test_the_default_search_is_the_default_agent_search_preset(
    pb_session: FakePocketBaseSession, index: int
) -> None:
    store = _search_stores(pb_session)[index]
    await store.save_saved_search(_search("ai_llm_engineer", "AI"))
    await store.save_saved_search(_search("default_agent_search", "默认"))

    assert (await store.get_default_search()).id == "default_agent_search"


@pytest.mark.parametrize("index", [0, 1], ids=["in-memory", "pocketbase"])
@pytest.mark.asyncio
async def test_the_default_search_falls_back_to_the_first_available(
    pb_session: FakePocketBaseSession, index: int
) -> None:
    """Without the named preset, any real search beats a synthesized one."""
    store = _search_stores(pb_session)[index]
    await store.save_saved_search(_search("only_one", "仅有"))

    assert (await store.get_default_search()).id == "only_one"


@pytest.mark.parametrize("index", [0, 1], ids=["in-memory", "pocketbase"])
@pytest.mark.asyncio
async def test_an_empty_store_still_yields_a_usable_default_search(
    pb_session: FakePocketBaseSession, index: int
) -> None:
    """An empty collection is not an error — callers still get a runnable search.

    This keeps the precedence callers already relied on: a store that has never
    been provisioned has to hand back something the Scheduler can start on, rather
    than raising and leaving a fresh install with no default to run.
    """
    store = _search_stores(pb_session)[index]

    fallback = await store.get_default_search()

    assert fallback.id == "default_agent_search"
    assert fallback.name == "Default Agent Search"


# --------------------------------------------------------------------------- #
# In-memory fixture initialization
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_in_memory_store_hydrates_raw_record_fixtures() -> None:
    """``DEFAULT_INITIAL_SEARCHES`` is raw records, so the store has to hydrate them.

    ``resolve_saved_search_store(prefer_database=False)`` hands over exactly that dict;
    taking it verbatim would leave ``list_saved_searches`` handing back dicts to
    consumers that expect the domain type.
    """
    store = InMemorySavedSearchStore(DEFAULT_INITIAL_SEARCHES)

    listed = await store.list_saved_searches()

    assert {s.id for s in listed} == set(DEFAULT_INITIAL_SEARCHES)
    assert all(isinstance(s, SavedSearch) for s in listed)
    default = await store.get_saved_search("default_agent_search")
    assert default is not None
    assert default.keyword == "agent"
    assert default.filter.industries == ["在线教育", "游戏", "人工智能"]


@pytest.mark.asyncio
async def test_the_in_memory_store_still_accepts_domain_objects() -> None:
    """Hydrating raw records must not cost callers who already built the domain type."""
    store = InMemorySavedSearchStore({"s1": _search()})

    assert (await store.get_saved_search("s1")) == _search()


@pytest.mark.asyncio
async def test_the_in_memory_store_still_starts_empty() -> None:
    """Seeding is opt-in — ``InMemoryTaskBroker`` composes this store bare.

    Quietly filling it with defaults would hand every in-memory-broker test a
    ``default_agent_search`` that no test asked for, so the "no presets configured"
    state would stop being reachable.
    """
    assert await InMemorySavedSearchStore().list_saved_searches() == []
    assert await InMemoryTaskBroker().saved_searches.list_saved_searches() == []


# --------------------------------------------------------------------------- #
# Store resolution
# --------------------------------------------------------------------------- #


@pytest.fixture
def hermetic_pocketbase(monkeypatch: pytest.MonkeyPatch) -> FakePocketBaseSession:
    """Point the resolver at a fake PocketBase, so resolution never reaches a network.

    The resolver is the one place that decides *which* store a CLI or harness gets;
    exercising it for real would mean a live port, and a resolver that quietly starts
    depending on one would be a bug nothing else would catch.
    """
    session = FakePocketBaseSession()
    monkeypatch.setenv("POCKETBASE_URL", "http://pb.test:8090")
    monkeypatch.setattr("requests.Session", lambda: session)
    return session


def test_the_broker_and_the_resolver_build_their_auth_header_in_one_place(
    hermetic_pocketbase: FakePocketBaseSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bearer handling is needed by two call sites, so it has to be written down once.

    The two copies produced identical headers, which is why the duplication cost nothing
    until the token scheme changed and only the file nobody was looking at got edited —
    leaving the store authenticated and the broker not, with nothing in either test suite
    to say so. Patching the shared helper makes that split fail here instead: the moment
    one of them grows its own copy again, it stops calling it.
    """
    seen: list[str | None] = []

    def spy(auth_token: str | None) -> dict[str, str]:
        seen.append(auth_token)
        return {"Content-Type": "application/json"}

    monkeypatch.setenv("POCKETBASE_AUTH_TOKEN", "tok-123")
    monkeypatch.setattr("boss_agent.broker.pocketbase_adapter.pocketbase_headers", spy)
    monkeypatch.setattr("boss_agent.saved_search_store.pocketbase_headers", spy)

    broker = PocketBaseTaskBroker(session=hermetic_pocketbase)
    broker._headers()
    resolve_saved_search_store()._headers()

    assert seen == ["tok-123", "tok-123"]


@pytest.mark.asyncio
async def test_the_resolver_builds_an_authenticated_pocketbase_store(
    hermetic_pocketbase: FakePocketBaseSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolved store must carry the caller's token, or every read is a 403.

    A resolver that produced an unauthenticated store would not fail here — it would
    fail later, at the first read, as a bare HTTP error with nothing pointing back at
    the missing credential.
    """
    monkeypatch.setenv("POCKETBASE_AUTH_TOKEN", "tok-123")
    hermetic_pocketbase.collections["saved_searches"] = {"s1": {"id": "s1", "name": "x"}}

    store = resolve_saved_search_store()

    assert isinstance(store, PocketBaseSavedSearchStore)
    assert store.base_url == "http://pb.test:8090"
    await store.get_saved_search("s1")
    assert hermetic_pocketbase.request_headers[-1]["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_the_resolver_sends_no_authorization_without_a_token(
    hermetic_pocketbase: FakePocketBaseSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unauthenticated PocketBase must not be sent a ``Bearer None`` header."""
    monkeypatch.delenv("POCKETBASE_AUTH_TOKEN", raising=False)
    hermetic_pocketbase.collections["saved_searches"] = {"s1": {"id": "s1"}}

    store = resolve_saved_search_store()
    await store.get_saved_search("s1")

    assert "Authorization" not in hermetic_pocketbase.request_headers[-1]


@pytest.mark.asyncio
async def test_the_resolver_can_prefer_the_in_memory_store() -> None:
    """``prefer_database=False`` is the offline path, and it must still have presets.

    The registry's "fall back to defaults in memory when the database is unreachable"
    behavior lived here, so dropping the database has to leave a store that resolves a
    default search rather than an empty one.
    """
    store = resolve_saved_search_store(prefer_database=False)

    assert isinstance(store, InMemorySavedSearchStore)
    assert (await store.get_default_search()).id == "default_agent_search"
    assert await store.get_saved_search("ai_llm_engineer") is not None
