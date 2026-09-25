"""
tests/unit/test_feed_records.py
===============================
Unit tests for the feed record payload builders (ADR 0013).

The pipeline persists these payloads after every card and every detail-page visit, so the
rules that decide what lands in a job record — which source wins a field, what an invalid
value falls back to — are exercised here without a driver or a device.
"""

from boss_agent.feed_records import (
    card_facets_record,
    card_record,
    effective_company,
    effective_title,
    enriched_record,
)
from boss_agent.models import JobPosting, JobRecordStatus
from boss_agent.pages import JobCardBrief

JD = "岗位职责：主导企业级大模型应用与Agent工作流平台建设，负责推理链编排与落地。"


def _card(**overrides) -> JobCardBrief:
    data = {
        "title": "AI Agent 平台工程师",
        "company_name": "智元创新",
        "recruiter_name": "王女士",
        "fingerprint": "fp-1",
        "salary_range": "40-60K",
        "location": "杭州",
        "tags": ["硕士", "5-10年"],
        "digest": "卡片摘要",
        "company_scale": "500-999人",
        "industry": "人工智能",
    }
    data.update(overrides)
    return JobCardBrief(**data)


def _posting(**overrides) -> JobPosting:
    data = {
        "title": "AI Agent 平台工程师（急招）",
        "company_name": "智元创新科技有限公司",
        "salary_range": "50-70K",
        "location": "上海",
        "job_description": JD,
        "recruiter_name": "李先生",
    }
    data.update(overrides)
    return JobPosting(**data)


# ---------------------------------------------------------------------------
# Source precedence
# ---------------------------------------------------------------------------


def test_detail_page_wins_when_it_has_a_real_title_and_company():
    card = _card()
    posting = _posting()

    assert effective_title(posting, card) == "AI Agent 平台工程师（急招）"
    assert effective_company(posting, card) == "智元创新科技有限公司"


def test_card_supplies_the_value_when_the_detail_page_has_a_placeholder():
    card = _card()

    assert effective_title(_posting(title="未注明职位"), card) == "AI Agent 平台工程师"
    assert effective_title(_posting(title=""), card) == "AI Agent 平台工程师"
    # The detail page's company is rejected when extraction picked up junk instead of an
    # employer: a placeholder, an empty string, or a stray duty line.
    assert effective_company(_posting(company_name="未注明公司"), card) == "智元创新"
    assert effective_company(_posting(company_name=""), card) == "智元创新"
    assert effective_company(_posting(company_name="负责移动端研发"), card) == "智元创新"
    assert effective_company(_posting(company_name="3-5年经验"), card) == "智元创新"


# ---------------------------------------------------------------------------
# Card facets
# ---------------------------------------------------------------------------


def test_card_facets_record_carries_the_card_and_the_run_context():
    payload = card_facets_record(_card(), keyword="算法", source_task_id="task-9")

    assert payload["fingerprint"] == "fp-1"
    assert payload["title"] == "AI Agent 平台工程师"
    assert payload["tags"] == ["硕士", "5-10年"]
    assert payload["digest"] == "卡片摘要"
    assert payload["search_keywords"] == ["算法"]
    assert payload["source_task_id"] == "task-9"
    # A card has no JD yet; the detail page fills it in later.
    assert payload["job_description"] == ""


def test_card_facets_record_omits_an_absent_keyword():
    assert card_facets_record(_card(), keyword=None, source_task_id=None)["search_keywords"] == []


# ---------------------------------------------------------------------------
# Optimistic card record
# ---------------------------------------------------------------------------


def test_card_record_preserves_a_previously_extracted_jd():
    """A re-scrape of a job whose JD is already stored must not drop it back to unmatched."""
    payload = card_record(
        _card(),
        keyword="算法",
        source_task_id=None,
        verdict=None,
        existing_record={"job_description": JD},
    )

    assert payload["job_description"] == JD
    assert payload["status"] == JobRecordStatus.JD_SAVED.value


def test_card_record_is_unmatched_without_a_stored_jd():
    payload = card_record(
        _card(), keyword=None, source_task_id=None, verdict=None, existing_record=None
    )

    assert payload["job_description"] == ""
    assert payload["status"] == JobRecordStatus.UNMATCHED.value
    assert payload["relaxed_by_whitelist"] is False


# ---------------------------------------------------------------------------
# Enriched record
# ---------------------------------------------------------------------------


def test_enriched_record_takes_the_posting_but_keeps_the_card_facets():
    card = _card()
    facets = card_facets_record(card, keyword="算法", source_task_id="task-9")

    enriched = enriched_record(
        card,
        _posting(),
        keyword="算法",
        source_task_id="task-9",
        card_record=facets,
        verdict=None,
    )

    assert enriched["title"] == "AI Agent 平台工程师（急招）"
    assert enriched["company_name"] == "智元创新科技有限公司"
    assert enriched["job_description"] == JD
    assert enriched["salary_range"] == "50-70K"
    assert enriched["location"] == "上海"
    # Card facets the detail page does not carry survive untouched.
    assert enriched["tags"] == ["硕士", "5-10年"]
    assert enriched["digest"] == "卡片摘要"
    assert enriched["fingerprint"] == "fp-1"


def test_enriched_record_falls_back_to_the_card_record_fields():
    card = _card()
    facets = card_facets_record(card, keyword=None, source_task_id=None)

    enriched = enriched_record(
        card,
        _posting(salary_range="", location="", job_description=""),
        keyword=None,
        source_task_id=None,
        card_record=facets,
        verdict=None,
    )

    assert enriched["salary_range"] == "40-60K"
    assert enriched["location"] == "杭州"
    assert enriched["job_description"] == ""
    assert enriched["recruiter_name"] == "王女士"


def test_enriched_record_flags_a_headhunter_channel_from_either_source():
    facets = card_facets_record(_card(), keyword=None, source_task_id=None)

    def build(card: JobCardBrief, posting: JobPosting) -> dict:
        return enriched_record(
            card,
            posting,
            keyword=None,
            source_task_id=None,
            card_record=facets,
            verdict=None,
        )

    assert build(_card(is_headhunter=True), _posting())["is_headhunter"] is True
    assert build(_card(), _posting(is_headhunter=True))["is_headhunter"] is True
    assert build(_card(), _posting())["is_headhunter"] is False
