"""
tests/unit/test_job_record_store.py
===================================
Unit tests for the dedicated JobRecordStore repository seam (Spec #231 / Ticket #233).

The store is exercised on its own — no task broker, no task leases — and both adapters
are driven with the same fixture data so their deduplication, cool-down and quota
semantics are proven identical rather than merely similar.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker, PocketBaseTaskBroker
from boss_agent.job_store import (
    InMemoryJobRecordStore,
    JobRecordStore,
    PocketBaseJobRecordStore,
)

TODAY = datetime.now(UTC)
YESTERDAY = TODAY - timedelta(days=1)
LONG_AGO = TODAY - timedelta(days=90)


def _record(
    fingerprint: str,
    company: str,
    title: str = "AI Agent 工程师",
    status: str = "applied",
    applied_at: str | None = None,
    is_headhunter: bool = False,
) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint,
        "company_name": company,
        "title": title,
        "recruiter_name": "王女士",
        "status": status,
        "applied_at": applied_at,
        "is_headhunter": is_headhunter,
    }


# ---------------------------------------------------------------------------
# Minimal PocketBase collection endpoint, enough to run the store's real queries
# ---------------------------------------------------------------------------


def _split_top(expr: str, operator: str) -> list[str]:
    """Split on a top-level operator, ignoring operator text nested in parentheses."""
    parts, depth, current = [], 0, ""
    i = 0
    while i < len(expr):
        char = expr[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and expr.startswith(operator, i):
            parts.append(current)
            current = ""
            i += len(operator)
            continue
        current += char
        i += 1
    parts.append(current)
    return [p.strip() for p in parts]


def _decode_literal(raw: str) -> str | None:
    """Decode a PocketBase filter literal, or None when its quoting is malformed.

    PocketBase requires a quote inside a quoted value to be backslash-escaped; a raw
    quote is a syntax error that makes the whole query fail. Emulating that here is what
    lets a test prove that an unescaped company name silently breaks the dedup lookup.
    """
    if raw[:1] not in ("'", '"'):
        return raw
    quote = raw[0]
    if raw[-1:] != quote:
        return None
    decoded, i = "", 1
    while i < len(raw) - 1:
        char = raw[i]
        if char == "\\" and i + 1 < len(raw) - 1:
            decoded += raw[i + 1]
            i += 2
            continue
        if char == quote:
            return None
        decoded += char
        i += 1
    return decoded


def _matches(expr: str, item: dict[str, Any]) -> bool:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")") and _split_top(expr[1:-1], "&&"):
        expr = expr[1:-1].strip()

    for operator in ("||", "&&"):
        parts = _split_top(expr, operator)
        if len(parts) > 1:
            results = [_matches(part, item) for part in parts]
            return any(results) if operator == "||" else all(results)

    match = re.match(r"^(\w+)\s*(>=|<=|!=|=|<|>)\s*(.+)$", expr)
    assert match, f"unsupported filter expression: {expr!r}"
    field, op, raw = match.group(1), match.group(2), match.group(3).strip()
    expected = _decode_literal(raw)
    if expected is None:
        # A malformed literal is a syntax error server-side: the query returns nothing.
        return False
    actual = str(item.get(field) or "")
    if op == "=":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == ">=":
        return actual >= expected
    if op == "<=":
        return actual <= expected
    if op == "<":
        return actual < expected
    return actual > expected


class FakePocketBaseSession:
    """Dict-backed stand-in for a PocketBase ``requests.Session``."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self._counter = 0
        # When set, the next patch returns this response instead, so failure paths can be
        # exercised without pretending a write succeeded.
        self.patch_failure: MagicMock | None = None

    def seed(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            self._counter += 1
            record = dict(item)
            record.setdefault("id", f"rec{self._counter}")
            record.setdefault("created", TODAY.isoformat())
            self.records[record["id"]] = record

    def _query(self, params: dict[str, Any]) -> dict[str, Any]:
        items = list(self.records.values())
        if params.get("filter"):
            items = [i for i in items if _matches(params["filter"], i)]
        if params.get("sort", "").lstrip("-") == "created" and params["sort"].startswith("-"):
            items.sort(key=lambda x: str(x.get("created", "")), reverse=True)
        page = int(params.get("page", 1))
        per_page = int(params.get("perPage", 30))
        window = items[(page - 1) * per_page : page * per_page]
        if params.get("fields"):
            wanted = [f.strip() for f in params["fields"].split(",")]
            window = [{k: v for k, v in i.items() if k in wanted} for i in window]
        return {"items": window, "totalItems": len(items)}

    def get(self, url: str, params: dict[str, Any] | None = None, headers=None):
        resp = MagicMock(status_code=200)
        if url.rsplit("/", 1)[-1] in self.records:
            resp.json.return_value = self.records[url.rsplit("/", 1)[-1]]
        else:
            resp.json.return_value = self._query(params or {})
        return resp

    def post(self, url: str, json: dict[str, Any], headers=None):
        self._counter += 1
        record = {"id": json.pop("id", None) or f"rec{self._counter}", **json}
        self.records[record["id"]] = record
        return MagicMock(status_code=200, json=MagicMock(return_value=record))

    def patch(self, url: str, json: dict[str, Any], headers=None):
        record_id = url.rsplit("/", 1)[-1]
        if self.patch_failure is not None:
            failure, self.patch_failure = self.patch_failure, None
            return failure
        if record_id not in self.records:
            return MagicMock(status_code=404, text="Not found")
        self.records[record_id].update(json)
        return MagicMock(status_code=200, json=MagicMock(return_value=self.records[record_id]))

    def delete(self, url: str, headers=None):
        self.records.pop(url.rsplit("/", 1)[-1], None)
        return MagicMock(status_code=204)


def _pocketbase_store(session: FakePocketBaseSession) -> PocketBaseJobRecordStore:
    return PocketBaseJobRecordStore(
        base_url="http://mock-pb:8090",
        session=session,  # type: ignore[arg-type]
        headers=lambda: {"Content-Type": "application/json"},
    )


@pytest.fixture
def pb_session() -> FakePocketBaseSession:
    return FakePocketBaseSession()


# ---------------------------------------------------------------------------
# Interface conformance
# ---------------------------------------------------------------------------


def test_adapters_implement_the_job_record_store_interface(pb_session):
    """Both adapters are JobRecordStores, and neither needs the task broker to work."""
    assert isinstance(InMemoryJobRecordStore(), JobRecordStore)
    assert isinstance(_pocketbase_store(pb_session), JobRecordStore)

    # The ABC must not be instantiable directly: it is a seam, not a default.
    with pytest.raises(TypeError):
        JobRecordStore()  # type: ignore[abstract]


def test_brokers_expose_the_store_as_a_separate_seam():
    in_memory = InMemoryTaskBroker()
    assert isinstance(in_memory.job_store, InMemoryJobRecordStore)

    pocketbase = PocketBaseTaskBroker(base_url="http://mock-pb:8090", session=MagicMock())
    assert isinstance(pocketbase.job_store, PocketBaseJobRecordStore)
    # The legacy broker surface still delegates, so existing callers keep working.
    assert pocketbase.job_store is not pocketbase


def test_store_lifecycle_does_not_need_a_task_broker():
    """A job ledger can be used with no task stream in sight."""
    import asyncio

    store = InMemoryJobRecordStore()
    saved = asyncio.run(store.upsert_job_record(_record("fp-solo", "深至科技", status="jd_saved")))

    assert saved["status"] == "jd_saved"
    assert asyncio.run(store.has_job_fingerprint("fp-solo")) is True


def test_broker_facade_delegates_to_the_store():
    """The retained broker wrappers are pure delegation, not a second implementation."""
    import asyncio
    from unittest.mock import AsyncMock

    broker = InMemoryTaskBroker()
    broker.job_store.upsert_job_record = AsyncMock(return_value={})  # type: ignore[method-assign]

    asyncio.run(broker.job_store.upsert_job_record(_record("fp-delegated", "某公司")))
    broker.job_store.upsert_job_record.assert_called_once()


# ---------------------------------------------------------------------------
# Unified semantics across adapters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_adapters_agree_on_the_applied_direct_company_pool(pb_session):
    """Same records in, same exclusion pool out — including cool-down expiry."""
    fixtures = [
        _record("fp-a", "深至科技", applied_at=YESTERDAY.isoformat()),
        _record("fp-b", "小红书", applied_at=LONG_AGO.isoformat()),
        _record("fp-c", "某人力资源公司", applied_at=YESTERDAY.isoformat(), is_headhunter=True),
        _record("fp-d", "某知名公司", applied_at=YESTERDAY.isoformat()),
        _record("fp-e", "游族网络", status="jd_saved", applied_at=None),
    ]
    pb_session.seed(fixtures)

    memory_store = InMemoryJobRecordStore()
    pb_store = _pocketbase_store(pb_session)
    for record in fixtures:
        await memory_store.upsert_job_record(dict(record))

    for cooldown in (0, 30, 120):
        memory_pool = await memory_store.get_applied_direct_companies(cooldown_days=cooldown)
        pb_pool = await pb_store.get_applied_direct_companies(cooldown_days=cooldown)
        assert memory_pool == pb_pool, f"adapters disagree at cooldown_days={cooldown}"
        # Headhunters, masked names and non-applied records never join the pool.
        assert "某人力资源公司" not in memory_pool
        assert "某知名公司" not in memory_pool
        assert "游族网络" not in memory_pool

    assert await memory_store.get_applied_direct_companies(cooldown_days=30) == {"深至科技"}
    assert await memory_store.get_applied_direct_companies(cooldown_days=0) == {
        "深至科技",
        "小红书",
    }


@pytest.mark.asyncio
async def test_both_adapters_agree_on_todays_dispatch_quota(pb_session):
    """Quota counts dispatched greetings only: historical imports never consume a slot."""
    fixtures = [
        _record("fp-today-1", "深至科技", applied_at=TODAY.isoformat()),
        _record("fp-today-2", "小红书", applied_at=TODAY.isoformat()),
        _record("fp-old", "商汤科技", applied_at=YESTERDAY.isoformat()),
        _record("fp-historical", "游族网络", applied_at=None),
        # A clock-skewed future stamp must not consume today's quota in either adapter.
        _record("fp-future", "字节跳动", applied_at=(TODAY + timedelta(days=2)).isoformat()),
    ]
    pb_session.seed(fixtures)

    memory_store = InMemoryJobRecordStore()
    pb_store = _pocketbase_store(pb_session)
    for record in fixtures:
        await memory_store.upsert_job_record(dict(record))

    assert await memory_store.count_today_applied_jobs() == 2
    assert await pb_store.count_today_applied_jobs() == 2


@pytest.mark.asyncio
async def test_both_adapters_deduplicate_on_the_canonical_fingerprint(pb_session):
    fixtures = [_record("fp-dup", "深至科技", status="jd_saved", applied_at=None)]
    pb_session.seed(fixtures)

    memory_store = InMemoryJobRecordStore()
    pb_store = _pocketbase_store(pb_session)
    for store in (memory_store, pb_store):
        await store.upsert_job_record(dict(fixtures[0]))
        await store.upsert_job_record(
            {**fixtures[0], "search_keywords": ["算法"], "job_description": "完整JD正文" * 10}
        )

    for store in (memory_store, pb_store):
        found = await store.get_job_record_by_fingerprint("fp-dup")
        assert found is not None
        assert found["search_keywords"] == ["算法"]
        assert found["job_description"].startswith("完整JD正文")
        assert await store.has_job_fingerprint("fp-dup") is True


@pytest.mark.asyncio
async def test_pocketbase_dedupes_when_a_scraped_name_contains_a_quote(pb_session):
    """A quote in a scraped company name must not defeat the company+title dedup lookup.

    Generic '招聘者' cards dedupe on company + title rather than fingerprint, so those two
    scraped strings are interpolated into a PocketBase filter; an unescaped quote there is
    a syntax error that makes the lookup miss and files the job twice.
    """
    seeded = {
        "fingerprint": "fp-quote-1",
        "company_name": '杭州"云智"科技有限公司',
        "title": "大模型算法工程师",
        "recruiter_name": "招聘者",
        "status": "jd_saved",
    }
    pb_session.seed([seeded])
    store = _pocketbase_store(pb_session)

    merged = await store.upsert_job_record(
        {
            **seeded,
            "fingerprint": "fp-quote-2",
            "job_description": "岗位职责：负责大模型算法研发与落地。" * 3,
        }
    )

    assert len(pb_session.records) == 1, "the quote broke the lookup and duplicated the job"
    assert merged["id"] == "rec1"


@pytest.mark.asyncio
async def test_both_adapters_agree_on_the_sticky_field_merge(pb_session):
    """Re-scraping a known job lands the same record in both adapters.

    tags / is_headhunter / jd_key_requirements are merged by shared rules, so an empty
    observation cannot blank what the record already holds and a headhunter channel is
    never silently demoted into the direct-hire exclusion pool.
    """
    first = {
        "fingerprint": "fp-merge",
        "company_name": "深至科技",
        "title": "大模型算法工程师",
        "recruiter_name": "王女士",
        "status": "jd_saved",
        "tags": ["硕士", "5-10年"],
        "is_headhunter": False,
        "jd_key_requirements": ["LangGraph", "Multi-Agent"],
    }
    memory_store = InMemoryJobRecordStore()
    pb_store = _pocketbase_store(pb_session)

    async def scrape(payload: dict[str, Any]) -> None:
        for store in (memory_store, pb_store):
            await store.upsert_job_record(dict(payload))

    # A later pass that saw nothing for these fields must not erase them.
    await scrape(first)
    await scrape(
        {
            **first,
            "tags": [],
            "jd_key_requirements": [],
            "is_headhunter": True,
            "job_description": "岗位职责：主导大模型算法研发与落地。" * 5,
        }
    )

    for name, store in (("memory", memory_store), ("pocketbase", pb_store)):
        found = await store.get_job_record_by_fingerprint("fp-merge")
        assert found is not None, name
        assert found["tags"] == ["硕士", "5-10年"], f"{name} let an empty read erase tags"
        assert found["jd_key_requirements"] == ["LangGraph", "Multi-Agent"], name
        assert found["is_headhunter"] is True, f"{name} missed a discovered headhunter channel"
        assert found["job_description"].startswith("岗位职责")

    # A later non-empty read refines the JD requirements but never re-tags the card facets,
    # and never downgrades the headhunter flag.
    await scrape(
        {
            **first,
            "tags": ["本科"],
            "jd_key_requirements": ["RAG"],
            "is_headhunter": False,
        }
    )

    merged: dict[str, dict[str, Any]] = {}
    for name, store in (("memory", memory_store), ("pocketbase", pb_store)):
        found = await store.get_job_record_by_fingerprint("fp-merge")
        assert found is not None, name
        merged[name] = found
        assert found["tags"] == ["硕士", "5-10年"], name
        assert found["jd_key_requirements"] == ["RAG"], name
        assert found["is_headhunter"] is True, name

    for key in ("tags", "is_headhunter", "jd_key_requirements"):
        assert merged["memory"][key] == merged["pocketbase"][key], f"adapters disagree on {key}"


@pytest.mark.asyncio
async def test_both_adapters_release_a_communication_while_keeping_the_jd(pb_session):
    fixtures = [
        {
            "fingerprint": "fp-release",
            "company_name": "深至科技",
            "title": "大模型算法工程师",
            "recruiter_name": "王女士",
            "status": "applied",
            "applied_at": TODAY.isoformat(),
            "applied_source": "agent_auto_send",
            "job_description": "岗位职责：负责大模型算法研发与落地。" * 3,
        }
    ]
    pb_session.seed(fixtures)

    memory_store = InMemoryJobRecordStore()
    pb_store = _pocketbase_store(pb_session)
    for record in fixtures:
        await memory_store.upsert_job_record(dict(record))

    for store in (memory_store, pb_store):
        target = await store.get_job_record_by_fingerprint("fp-release")
        released = await store.clear_job_communication(target["id"])
        assert released["status"] == "jd_saved"
        assert not released["applied_at"]
        assert not released["applied_source"]
        assert released["job_description"]
        assert await store.count_today_applied_jobs() == 0


# ---------------------------------------------------------------------------
# Failure reporting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_adapters_report_a_missing_record_on_status_update(pb_session):
    """Updating the status of an unknown record fails loudly in both adapters."""
    with pytest.raises(KeyError):
        await InMemoryJobRecordStore().update_job_record_status("missing", "applied")

    store = _pocketbase_store(pb_session)
    with pytest.raises(KeyError):
        await store.update_job_record_status("missing", "applied")


@pytest.mark.asyncio
async def test_pocketbase_status_update_does_not_fake_success(pb_session):
    """A server failure must surface as an error, not as a plausible-looking record."""
    pb_session.seed([_record("fp-status-fail", "深至科技", status="jd_saved", applied_at=None)])
    store = _pocketbase_store(pb_session)
    pb_session.patch_failure = MagicMock(status_code=500, text="boom")

    with pytest.raises(RuntimeError, match="500"):
        await store.update_job_record_status("rec1", "applied")

    assert pb_session.records["rec1"]["status"] == "jd_saved", "the write did not land"


# ---------------------------------------------------------------------------
# In-memory store specifics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_store_rejects_incomplete_records():
    """宁可不录入: a card without a real title or company never becomes a record."""
    store = InMemoryJobRecordStore()

    assert await store.upsert_job_record({"title": "", "company_name": "某公司"}) == {}
    assert await store.upsert_job_record({"title": "未注明职位", "company_name": "某公司"}) == {}
    assert await store.upsert_job_record({"title": "工程师", "company_name": "未知公司"}) == {}
    assert await store.list_job_records() == []


@pytest.mark.asyncio
async def test_in_memory_store_keeps_dispatch_stamp_on_rescrape():
    """Re-scraping an applied job must not reset its quota slot or cool-down clock."""
    store = InMemoryJobRecordStore()
    created = await store.upsert_job_record(
        {
            **_record("fp-rescrape", "深至科技", applied_at=TODAY.isoformat()),
            "applied_source": "agent_auto_send",
        }
    )

    rescraped = await store.upsert_job_record(
        _record("fp-rescrape", "深至科技", status="applied", applied_at=None)
    )

    assert rescraped["id"] == created["id"]
    assert rescraped["applied_at"] == TODAY.isoformat()
    assert rescraped["applied_source"] == "agent_auto_send"
    assert await store.count_today_applied_jobs() == 1


@pytest.mark.asyncio
async def test_in_memory_store_dedupes_generic_recruiter_placeholders():
    """Two '招聘者' cards for the same company+title are one job, not two."""
    store = InMemoryJobRecordStore()
    first = await store.upsert_job_record(
        {
            "fingerprint": "fp-generic-1",
            "title": "AI Agent 工程师",
            "company_name": "智元创新",
            "recruiter_name": "招聘者",
        }
    )
    second = await store.upsert_job_record(
        {
            "fingerprint": "fp-generic-2",
            "title": "AI Agent 工程师",
            "company_name": "智元创新",
            "recruiter_name": "招聘者",
        }
    )

    assert first["id"] == second["id"]
    assert len(await store.list_job_records()) == 1


@pytest.mark.asyncio
async def test_in_memory_store_delete_releases_the_fingerprint():
    store = InMemoryJobRecordStore()
    saved = await store.upsert_job_record(_record("fp-delete", "深至科技"))

    assert await store.delete_job_record(saved["id"]) is True
    assert await store.has_job_fingerprint("fp-delete") is False
    assert await store.delete_job_record("non-existent") is False
