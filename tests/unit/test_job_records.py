"""
tests/unit/test_job_records.py
==============================
Unit tests for JobRecord model, fingerprint computation, deduplication, and broker persistence.
"""

import pytest
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import compute_job_fingerprint


def test_compute_job_fingerprint_consistency_and_normalization():
    """Fingerprint should be identical regardless of surrounding whitespace."""
    fp1 = compute_job_fingerprint(
        company_name=" 字节跳动(上海) ",
        title="Agent应用开发工程师-创造力服务平台",
        recruiter_name="买先生 · 产品研发",
    )
    fp2 = compute_job_fingerprint(
        company_name="字节跳动(上海)",
        title=" Agent应用开发工程师-创造力服务平台 ",
        recruiter_name="买先生 · 产品研发",
    )
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex string


def test_compute_job_fingerprint_distinctness():
    """Different title, company, or recruiter must yield different fingerprints."""
    fp1 = compute_job_fingerprint("字节跳动", "AI工程师", "张三")
    fp2 = compute_job_fingerprint("字节跳动", "AI工程师", "李四")
    fp3 = compute_job_fingerprint("阿里巴巴", "AI工程师", "张三")
    assert fp1 != fp2
    assert fp1 != fp3


@pytest.mark.asyncio
async def test_in_memory_broker_job_records_deduplication():
    """Broker should reject duplicate insertions and preserve existing status."""
    broker = InMemoryTaskBroker()

    # 1. Insert new job record
    rec1 = await broker.upsert_job_record(
        {
            "title": "AI Agent 架构师",
            "company_name": "某科技公司",
            "recruiter_name": "王总",
            "salary_range": "40-60K",
            "job_description": "负责移动端 Agent 研发...",
            "search_keywords": ["agent"],
        }
    )
    assert rec1["id"] is not None
    assert rec1["status"] == "unmatched"
    assert rec1["fingerprint"] == compute_job_fingerprint("某科技公司", "AI Agent 架构师", "王总")

    # 2. Update status to 'matched'
    await broker.update_job_record_status(
        record_id=rec1["id"],
        status="matched",
        match_data={"match_score": 88, "greeting_message": "您好王总..."},
    )
    updated = await broker.get_job_record(rec1["id"])
    assert updated is not None
    assert updated["status"] == "matched"
    assert updated["match_score"] == 88

    # 3. Duplicate ingestion of same job from another keyword
    rec2 = await broker.upsert_job_record(
        {
            "title": "AI Agent 架构师",
            "company_name": "某科技公司",
            "recruiter_name": "王总",
            "salary_range": "40-60K",
            "job_description": "负责移动端 Agent 研发...",
            "search_keywords": ["大模型架构"],
        }
    )
    assert rec2["id"] == rec1["id"]
    # Crucial: status must NOT be reset to unmatched!
    assert rec2["status"] == "matched"
    assert "agent" in rec2["search_keywords"]
    assert "大模型架构" in rec2["search_keywords"]


@pytest.mark.asyncio
async def test_in_memory_broker_list_unmatched():
    broker = InMemoryTaskBroker()
    r1 = await broker.upsert_job_record(
        {"title": "T1", "company_name": "C1", "recruiter_name": "R1"}
    )
    r2 = await broker.upsert_job_record(
        {"title": "T2", "company_name": "C2", "recruiter_name": "R2"}
    )

    unmatched = await broker.list_job_records(status="unmatched")
    assert len(unmatched) == 2

    # Mark r1 as matched
    await broker.update_job_record_status(r1["id"], status="matched")

    unmatched_after = await broker.list_job_records(status="unmatched")
    assert len(unmatched_after) == 1
    assert unmatched_after[0]["id"] == r2["id"]


@pytest.mark.asyncio
async def test_pocketbase_broker_job_records_mocked(monkeypatch):
    """Test PocketBaseTaskBroker job records methods with mocked HTTP session."""
    from unittest.mock import MagicMock
    from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker

    mock_session = MagicMock()
    broker = PocketBaseTaskBroker(base_url="http://mock-pb:8090", session=mock_session)

    # 1. has_job_fingerprint returns True if items found
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [{"id": "rec-123"}]}
    mock_session.get.return_value = mock_resp

    has_fp = await broker.has_job_fingerprint("test-fp")
    assert has_fp is True

    # 2. upsert existing record patches last_seen_at
    patch_resp = MagicMock()
    patch_resp.status_code = 200
    patch_resp.json.return_value = {"id": "rec-123", "status": "unmatched"}
    mock_session.patch.return_value = patch_resp

    res = await broker.upsert_job_record(
        {"title": "Job 1", "company_name": "Comp 1", "recruiter_name": "Rec 1", "fingerprint": "test-fp"}
    )
    assert res["id"] == "rec-123"
    assert mock_session.patch.called

