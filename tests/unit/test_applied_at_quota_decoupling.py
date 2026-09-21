"""
tests/unit/test_applied_at_quota_decoupling.py
==============================================
Unit tests for the `applied_at` schema field and daily greeting quota decoupling (Issue #199).

`applied_at` is populated only when the agent actually dispatches a greeting message.
Platform historical contacts imported as `applied` (via the "继续沟通" detail button) carry no
`applied_at` and therefore must never consume the daily greeting quota.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.broker.provisioner import JOB_RECORDS_FIELDS, provision_sqlite_database
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from droid_agent_core.locators import get_global_locator_registry


def test_job_records_schema_declares_applied_at():
    """The PocketBase job_records collection definition must declare the applied_at field."""
    field_names = {f["name"] for f in JOB_RECORDS_FIELDS}
    assert "applied_at" in field_names, (
        "JOB_RECORDS_FIELDS must declare 'applied_at' so remote PocketBase collections "
        "accepts the quota-decoupled dispatch timestamp."
    )


def _provision_fresh_db(db_file: Path) -> None:
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("""
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
    """)
    conn.commit()
    conn.close()


def _job_record_columns(db_file: Path) -> set[str]:
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(job_records)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()
    return columns


def test_provisioner_creates_applied_at_column(tmp_path: Path):
    """Provisioning a fresh SQLite database must create job_records.applied_at."""
    db_file = tmp_path / "data.db"
    _provision_fresh_db(db_file)

    assert provision_sqlite_database(db_file) is True

    assert "applied_at" in _job_record_columns(db_file)


def test_provisioner_migrates_existing_table_without_applied_at(tmp_path: Path):
    """Provisioning an already-populated legacy database must add the applied_at column."""
    db_file = tmp_path / "legacy.db"
    _provision_fresh_db(db_file)

    # Simulate a legacy job_records table lacking applied_at
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE job_records (
            id TEXT PRIMARY KEY,
            fingerprint TEXT UNIQUE,
            title TEXT,
            company_name TEXT,
            status TEXT DEFAULT 'unmatched',
            created TEXT,
            updated TEXT
        )
        """
    )
    cursor.execute(
        "INSERT INTO _collections (id, system, type, name, fields) "
        "VALUES ('pbc_job_records', 0, 'base', 'job_records', '[]')"
    )
    conn.commit()
    conn.close()

    assert provision_sqlite_database(db_file) is True

    assert "applied_at" in _job_record_columns(db_file)


@pytest.mark.asyncio
async def test_historical_applied_record_without_applied_at_does_not_count_toward_quota():
    """Importing a platform historical contact must not consume today's greeting quota."""
    broker = InMemoryTaskBroker()

    await broker.upsert_job_record(
        {
            "fingerprint": "fp-historical-contact",
            "title": "大模型算法工程师",
            "company_name": "深至科技",
            "recruiter_name": "王女士",
            "status": "applied",
        }
    )

    assert await broker.count_today_applied_jobs() == 0


@pytest.mark.asyncio
async def test_only_applied_at_today_counts_toward_quota():
    """Quota counts strictly on applied_at, ignoring record creation/update timestamps."""
    broker = InMemoryTaskBroker()
    today_iso = datetime.now(UTC).isoformat()
    stale_iso = (datetime.now(UTC) - timedelta(days=3)).isoformat()

    await broker.upsert_job_record(
        {
            "fingerprint": "fp-dispatched-today",
            "title": "Agent 平台开发",
            "company_name": "游族网络",
            "recruiter_name": "李先生",
            "status": "applied",
            "applied_at": today_iso,
        }
    )
    await broker.upsert_job_record(
        {
            "fingerprint": "fp-dispatched-earlier",
            "title": "Agent 平台开发",
            "company_name": "商汤科技",
            "recruiter_name": "张先生",
            "status": "applied",
            "applied_at": stale_iso,
        }
    )

    assert await broker.count_today_applied_jobs() == 1


@pytest.mark.asyncio
async def test_upsert_preserves_applied_at_on_later_rescrape():
    """A later re-scrape of the same job must neither clear nor rewrite applied_at."""
    broker = InMemoryTaskBroker()
    applied_at = datetime.now(UTC).isoformat()

    created = await broker.upsert_job_record(
        {
            "fingerprint": "fp-preserve-applied-at",
            "title": "移动端 Agent 工程师",
            "company_name": "小红书",
            "recruiter_name": "陈先生",
            "status": "applied",
            "applied_at": applied_at,
        }
    )
    assert created["applied_at"] == applied_at

    rescraped = await broker.upsert_job_record(
        {
            "fingerprint": "fp-preserve-applied-at",
            "title": "移动端 Agent 工程师",
            "company_name": "小红书",
            "recruiter_name": "陈先生",
            "digest": "负责移动端 Agent 平台建设",
        }
    )

    assert rescraped["applied_at"] == applied_at, (
        "Re-scraping a card must preserve the original dispatch timestamp used for quota counting."
    )


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def mock_driver():
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    return driver


def _wire_job_detail_mocks(mock_driver: MagicMock) -> None:
    mock_title = MagicMock(text="Senior Python Agent Engineer")
    mock_company = MagicMock(text="Future Robotics")
    mock_salary = MagicMock(text="45-70K")
    mock_desc = MagicMock(text="Expertise in Python, LLM agents, and Android automation.")
    mock_elem = MagicMock()

    def mock_find(by, value):
        if "tv_job_name" in value or "job_name" in value:
            return [mock_title]
        if "tv_company_name" in value or "company_name" in value:
            return [mock_company]
        if "tv_job_salary" in value or "salary" in value:
            return [mock_salary]
        if "tv_job_desc" in value or "desc" in value:
            return [mock_desc]
        return [mock_elem]

    mock_driver.find_elements.side_effect = mock_find


def _mock_llm_client(match_score: int = 90) -> MagicMock:
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": match_score,
        "jd_key_requirements": ["精通 Python"],
        "match_reasons": ["具备 Agent 落地经验"],
        "greeting_message": "针对贵司 Agent 落地需求，我具备完整实战经验！",
    }
    return llm


@pytest.mark.asyncio
async def test_auto_apply_records_applied_at_only_after_message_dispatch(broker, mock_driver):
    """A successful greeting dispatch must stamp applied_at with today's UTC timestamp."""
    _wire_job_detail_mocks(mock_driver)

    config = WorkerConfig(worker_id="test-worker-applied-at", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[AutoApplyHandler(llm_client=_mock_llm_client())],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "Python",
            "min_score": 75,
            "preview_only": False,
            "auto_send": True,
            "candidate_profile": {
                "name": "Candidate",
                "years_of_experience": 6,
                "core_skills": ["Python", "Agents"],
            },
        },
    )

    assert await worker.run_once() is True
    finished = await broker.get_task(task.id)
    assert finished is not None
    assert finished.status == TaskStatus.SUCCESS

    records = await broker.list_job_records(status="applied")
    assert len(records) == 1
    applied_at = str(records[0].get("applied_at") or "")
    assert applied_at.startswith(datetime.now(UTC).strftime("%Y-%m-%d"))
    assert records[0].get("applied_source") == "agent_auto_send"
    assert await broker.count_today_applied_jobs() == 1


@pytest.mark.asyncio
async def test_auto_apply_draft_only_mode_leaves_applied_at_empty(broker, mock_driver):
    """Draft-only (safe mode) runs must never stamp applied_at nor consume quota."""
    _wire_job_detail_mocks(mock_driver)

    config = WorkerConfig(worker_id="test-worker-draft-only", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[AutoApplyHandler(llm_client=_mock_llm_client())],
    )

    await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "Python",
            "min_score": 75,
            "preview_only": True,
            "auto_send": False,
            "candidate_profile": {"name": "Candidate", "core_skills": ["Python"]},
        },
    )

    assert await worker.run_once() is True

    records = await broker.list_job_records(status="matched")
    assert len(records) == 1
    assert not records[0].get("applied_at")
    assert await broker.count_today_applied_jobs() == 0


def _hide_locator_keys(mock_driver: MagicMock, *keys: str) -> None:
    """Make the given locator keys unresolvable, leaving every other lookup intact."""
    registry = get_global_locator_registry()
    hidden = {
        (selector.by.value, selector.value)
        for key in keys
        for selector in (registry.get_selectors(key) or [])
    }
    inner = mock_driver.find_elements.side_effect

    def mock_find(by, value):
        if (by, value) in hidden:
            return []
        return inner(by, value)

    mock_driver.find_elements.side_effect = mock_find


@pytest.mark.asyncio
async def test_auto_apply_does_not_record_applied_when_send_button_is_missing(broker, mock_driver):
    """A greeting that never leaves the app must not be recorded as an application.

    Recording it as `applied` would both consume the daily quota and plant a same-company
    exclusion anchor against an employer we never actually contacted.
    """
    _wire_job_detail_mocks(mock_driver)
    _hide_locator_keys(mock_driver, "chat.send_btn")

    config = WorkerConfig(worker_id="test-worker-send-failure", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[AutoApplyHandler(llm_client=_mock_llm_client())],
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "Python",
            "min_score": 75,
            "preview_only": False,
            "auto_send": True,
            "candidate_profile": {
                "name": "Candidate",
                "years_of_experience": 6,
                "core_skills": ["Python", "Agents"],
            },
        },
    )

    assert await worker.run_once() is True

    assert await broker.list_job_records(status="applied") == [], (
        "An unsent greeting must not leave an `applied` record behind; it would seed a false "
        "same-company exclusion anchor with no applied_at to age out of."
    )

    records = await broker.list_job_records(status="matched")
    assert len(records) == 1, "The tailored greeting should survive as a draft for manual sending."
    assert records[0].get("greeting_message")
    assert not records[0].get("applied_at")
    assert not records[0].get("applied_source")
    assert await broker.count_today_applied_jobs() == 0

    finished = await broker.get_task(task.id)
    assert finished is not None
    assert not any("Dispatched greeting message" in line for line in finished.logs), (
        "A failed dispatch must not be reported as a successful send."
    )
