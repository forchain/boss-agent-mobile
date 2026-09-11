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
async def test_pocketbase_broker_job_records_no_fallback_on_empty(tmp_path, monkeypatch):
    """When PocketBase returns empty items, list_job_records returns empty list without creating fallback."""
    from pathlib import Path
    from unittest.mock import MagicMock

    from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker

    monkeypatch.chdir(tmp_path)

    mock_session = MagicMock()
    broker = PocketBaseTaskBroker(base_url="https://remote-pb:4433", session=mock_session)

    # Mock 200 with empty items
    get_empty = MagicMock(status_code=200, json=lambda: {"items": [], "totalItems": 0})
    mock_session.get.return_value = get_empty

    items = await broker.list_job_records(status="unmatched")
    assert items == []
    assert not Path(".boss_agent/job_records_fallback.json").exists()


@pytest.mark.asyncio
async def test_pocketbase_broker_job_records_no_fallback_on_404(tmp_path, monkeypatch):
    """When PocketBase returns 404, broker returns empty/False and does not create or read fallback file."""
    from pathlib import Path
    from unittest.mock import MagicMock

    from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker

    monkeypatch.chdir(tmp_path)

    mock_session = MagicMock()
    broker = PocketBaseTaskBroker(base_url="https://remote-pb:4433", session=mock_session)

    get_404 = MagicMock(status_code=404, text='{"message":"Missing or invalid collection context."}')
    mock_session.get.return_value = get_404

    # 1. has_job_fingerprint returns False and does not create fallback
    has_fp = await broker.has_job_fingerprint("fp_test_123")
    assert has_fp is False
    assert not Path(".boss_agent/job_records_fallback.json").exists()

    # 2. list_job_records returns [] on 404
    items = await broker.list_job_records(status="unmatched")
    assert items == []
    assert not Path(".boss_agent/job_records_fallback.json").exists()

    # 3. get_job_record returns None on 404
    fetched = await broker.get_job_record("nonexistent_id")
    assert fetched is None
    assert not Path(".boss_agent/job_records_fallback.json").exists()


@pytest.mark.asyncio
async def test_pocketbase_broker_upsert_persists_without_fallback_file(tmp_path, monkeypatch):
    """Upserting job records persists to PocketBase directly without creating fallback JSON file."""
    from pathlib import Path
    from unittest.mock import MagicMock

    from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker

    monkeypatch.chdir(tmp_path)

    mock_session = MagicMock()
    broker = PocketBaseTaskBroker(base_url="https://remote-pb:4433", session=mock_session)

    # 1. Mock check_resp: no existing record
    check_resp = MagicMock(status_code=200, json=lambda: {"items": []})
    # 2. Mock create_resp
    post_resp = MagicMock(
        status_code=201,
        json=lambda: {
            "id": "rec_pb_123",
            "title": "Agent研发架构师",
            "company_name": "互联网大厂",
            "recruiter_name": "李猎头",
            "fingerprint": "fp_test_123",
            "status": "unmatched",
        },
    )
    mock_session.get.return_value = check_resp
    mock_session.post.return_value = post_resp

    rec = await broker.upsert_job_record(
        {
            "title": "Agent研发架构师",
            "company_name": "互联网大厂",
            "recruiter_name": "李猎头",
            "salary_range": "7-10万",
            "location": "上海",
            "fingerprint": "fp_test_123",
            "status": "unmatched",
        }
    )
    assert rec["id"] == "rec_pb_123"
    assert rec["title"] == "Agent研发架构师"
    assert mock_session.post.called
    assert not Path(".boss_agent/job_records_fallback.json").exists()


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


@pytest.mark.asyncio
async def test_in_memory_broker_delete_job_record_and_release_fingerprint():
    """Deleting a job record must remove it from listings and release its fingerprint."""
    broker = InMemoryTaskBroker()
    fp = compute_job_fingerprint("OpenAI", "Prompt Engineer", "Sam")

    saved = await broker.upsert_job_record(
        {
            "title": "Prompt Engineer",
            "company_name": "OpenAI",
            "recruiter_name": "Sam",
            "salary_range": "50-80K",
            "status": "unmatched",
        }
    )
    rec_id = saved["id"]

    # Verify presence
    assert await broker.has_job_fingerprint(fp) is True
    assert (await broker.get_job_record(rec_id)) is not None
    assert len(await broker.list_job_records()) == 1

    # Delete the record
    deleted = await broker.delete_job_record(rec_id)
    assert deleted is True

    # Fingerprint must be released, allowing re-ingestion
    assert await broker.has_job_fingerprint(fp) is False
    assert (await broker.get_job_record(rec_id)) is None
    assert len(await broker.list_job_records()) == 0

    # Deleting non-existent record returns False
    assert await broker.delete_job_record("non_existent_id") is False


@pytest.mark.asyncio
async def test_pocketbase_broker_delete_job_record():
    """PocketBaseTaskBroker delete_job_record handles 204, 404, and exceptions."""
    from unittest.mock import MagicMock

    import requests

    from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker

    mock_session = MagicMock()
    broker = PocketBaseTaskBroker(base_url="https://remote-pb:4433", session=mock_session)

    # 1. Successful deletion (204 No Content)
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_session.delete.return_value = mock_resp

    success = await broker.delete_job_record("rec_123")
    assert success is True
    mock_session.delete.assert_called_with(
        "https://remote-pb:4433/api/collections/job_records/records/rec_123",
        headers={"Content-Type": "application/json"},
    )

    # 2. Record not found (404)
    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    mock_session.delete.return_value = mock_resp_404

    not_found = await broker.delete_job_record("rec_404")
    assert not_found is False

    # 3. Network / RequestException
    mock_session.delete.side_effect = requests.RequestException("Connection error")
    err_res = await broker.delete_job_record("rec_err")
    assert err_res is False


def test_extract_digest_and_tags_from_jd():
    """extract_digest_from_jd extracts concise summary and extract_tags_from_text matches tech tags."""
    from boss_agent.models import JobRecord, extract_digest_from_jd, extract_tags_from_text

    raw_jd = """岗位职责
负责公司 后端与前端系统的设计、开发与迭代
使用 Java 构建和维护核心后端服务，保障系统稳定性与扩展性
负责混合 App（RN）相关功能开发

任职要求（必须）
扎实的 Java 后端开发经验，具备完整项目经验
擅长Spec coding / vibe coding
具备 前后端协同开发能力

加分项（非必须）
有 React Native、Flutter 或其他混合 App 的 production 项目经验"""

    digest = extract_digest_from_jd(raw_jd)
    assert "负责公司 后端与前端系统的设计、开发与迭代" in digest
    assert len(digest) <= 100
    assert "加分项" not in digest

    tags = extract_tags_from_text("外企-全栈开发工程师 " + raw_jd)
    assert "Java" in tags
    assert "React Native" in tags or "Flutter" in tags or "全栈" in tags

    # Verify auto-population on JobRecord when digest/tags missing
    rec = JobRecord(
        title="外企-全栈开发工程师-不加班1075 @%",
        company_name="某公司",
        recruiter_name="招聘者",
        job_description=raw_jd,
    )
    assert rec.title == "外企-全栈开发工程师-不加班1075"
    assert rec.digest != ""
    assert "负责公司 后端与前端系统" in rec.digest
    assert len(rec.tags) > 0
    assert "Java" in rec.tags

