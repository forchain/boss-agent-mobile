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


@pytest.mark.asyncio
async def test_pocketbase_broker_job_records_fallback_on_404(tmp_path, monkeypatch):
    """When PocketBase returns 404 (missing collection), broker saves to and reads from local fallback."""
    from unittest.mock import MagicMock

    from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker

    # Patch working directory / fallback file to tmp_path
    monkeypatch.chdir(tmp_path)

    mock_session = MagicMock()
    broker = PocketBaseTaskBroker(base_url="https://remote-pb:4433", session=mock_session)

    # Mock 404 on GET (collection not found)
    get_404 = MagicMock(status_code=404, text='{"message":"Missing or invalid collection context."}')
    mock_session.get.return_value = get_404

    # Mock 404 on POST
    post_404 = MagicMock(status_code=404, text='{"message":"Missing or invalid collection context."}')
    mock_session.post.return_value = post_404

    # 1. Upsert a new job record
    rec = await broker.upsert_job_record(
        {
            "title": "Agent研发架构师",
            "company_name": "互联网大厂",
            "recruiter_name": "李猎头",
            "salary_range": "7-10万",
            "location": "上海",
            "fingerprint": "fp_fallback_test_123",
            "status": "unmatched",
        }
    )
    assert rec["title"] == "Agent研发架构师"
    assert rec["id"] is not None

    # 2. has_job_fingerprint returns True from fallback
    has_fp = await broker.has_job_fingerprint("fp_fallback_test_123")
    assert has_fp is True

    # 3. list_job_records returns the record from fallback
    items = await broker.list_job_records(status="unmatched")
    assert len(items) == 1
    assert items[0]["title"] == "Agent研发架构师"
    assert items[0]["company_name"] == "互联网大厂"

    # 4. get_job_record returns the record from fallback
    fetched = await broker.get_job_record(rec["id"])
    assert fetched is not None
    assert fetched["title"] == "Agent研发架构师"


@pytest.mark.asyncio
async def test_job_records_digest_and_job_description_decoupling():
    """Verify that digest and job_description are decoupled across models and broker."""
    from boss_agent.models import JobPosting, JobRecord
    from boss_agent.pages import JobCardBrief

    # 1. JobCardBrief supports digest with backward-compatible snippet alias
    card = JobCardBrief(
        title="AI Engineer",
        company_name="Google",
        recruiter_name="Recruiter",
        digest="负责大模型开发",
    )
    assert card.digest == "负责大模型开发"
    assert card.snippet == "负责大模型开发"

    # Card initialized with snippet should populate digest
    card2 = JobCardBrief(
        title="AI Engineer 2",
        company_name="Google",
        recruiter_name="Recruiter",
        snippet="负责智能体开发",
    )
    assert card2.digest == "负责智能体开发"
    assert card2.snippet == "负责智能体开发"

    # 2. JobPosting and JobRecord support digest and job_description independently
    posting = JobPosting(
        title="AI Engineer",
        company_name="Google",
        salary_range="50-70K",
        job_description="1. 负责核心系统架构...\n2. 具备5年以上实战经验",
        digest="负责大模型开发",
    )
    assert posting.digest == "负责大模型开发"
    assert posting.job_description.startswith("1. 负责核心系统架构")

    rec = JobRecord(
        title="AI Engineer",
        company_name="Google",
        recruiter_name="Recruiter",
        digest="负责大模型开发",
        job_description="",
    )
    assert rec.digest == "负责大模型开发"
    assert rec.job_description == ""

    # 3. In-memory broker persistence preserves digest and updates job_description on enrichment
    broker = InMemoryTaskBroker()
    saved = await broker.upsert_job_record(
        {
            "title": "AI Engineer",
            "company_name": "Google",
            "recruiter_name": "Recruiter",
            "digest": "列表卡片提取的摘要信息",
            "job_description": "",
            "status": "unmatched",
        }
    )
    assert saved["digest"] == "列表卡片提取的摘要信息"
    assert saved["job_description"] == ""

    # Simulate detail page enrichment
    enriched = await broker.upsert_job_record(
        {
            "title": "AI Engineer",
            "company_name": "Google",
            "recruiter_name": "Recruiter",
            "job_description": "详情页提取的完整岗位职责与要求长文本...",
        }
    )
    assert enriched["id"] == saved["id"]
    assert enriched["digest"] == "列表卡片提取的摘要信息"
    assert enriched["job_description"] == "详情页提取的完整岗位职责与要求长文本..."

