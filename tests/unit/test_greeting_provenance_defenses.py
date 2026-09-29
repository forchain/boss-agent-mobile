"""
tests/unit/test_greeting_provenance_defenses.py
===============================================
What a human greeting does NOT change (#300, criteria 6 and 8).

Reusing an approved copy replaces the words and the drafting call. Everything that protects
the daily quota, the employer pool and the record's own history stays where it was: an
employer already contacted inside the cool-down still costs nothing, an unsent greeting
still anchors nobody, and a score that was never computed never overwrites the one the
operator used to approve the copy.
"""

from unittest.mock import MagicMock

import pytest
from _feed_harness import (
    GOOD_JD,
    TODAY,
    ScriptedFeed,
    _apply_config,
    _card,
    _detail_page,
    _pipeline,
    _posting,
)

from boss_agent.job_store import InMemoryJobRecordStore
from boss_agent.models import (
    APPLIED_SOURCE_AGENT,
    GREETING_SOURCE_HUMAN,
    JobRecordStatus,
    ScreeningPolicy,
)
from boss_agent.screening import CandidateScreener

HUMAN_COPY = "李工您好，看到贵司在招 Agent 平台方向，我做过端侧推理编排，想具体聊聊。"
# A policy that spends no model call before the greeting decision is reached.
NO_SCREEN = ScreeningPolicy(enable_screening=False, max_commute_distance_km=None)


async def _seed(store, card, **overrides):
    """A record whose greeting a person wrote and approved, JD and score included."""
    record = {
        "fingerprint": card.card.fingerprint,
        "title": card.card.title,
        "company_name": card.card.company_name,
        "recruiter_name": card.card.recruiter_name,
        "status": JobRecordStatus.JD_SAVED.value,
        "job_description": GOOD_JD,
        "greeting_message": HUMAN_COPY,
        "greeting_source": GREETING_SOURCE_HUMAN,
        "match_score": 88,
    }
    record.update(overrides)
    await store.upsert_job_record(record)


def _screener():
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": 91,
        "match_reasons": ["契合"],
        "greeting_message": "生成的招呼语",
    }
    return CandidateScreener(llm_client=llm), llm


def _outreach(**overrides):
    return _apply_config(**overrides)


@pytest.mark.asyncio
async def test_a_human_copy_still_yields_to_the_same_company_guard():
    """Provenance changes the words, never who may be contacted (#300 c8).

    A direct-hire employer already contacted inside the cool-down owns a shared in-house
    candidate pool, so a second greeting there costs a slot and reaches nobody new.
    """
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await _seed(store, card)
    await store.upsert_job_record({
        "fingerprint": "fp-earlier-contact",
        "title": "另一个岗位",
        "company_name": "智元创新",
        "recruiter_name": "刘女士",
        "status": JobRecordStatus.APPLIED.value,
        "applied_at": TODAY,
        "applied_source": APPLIED_SOURCE_AGENT,
        "is_headhunter": False,
    })

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, llm = _screener()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat, screener=screener, log=log
    )
    result = await pipeline.stream_jobs(_outreach(cooldown_days=30, screening_policy=NO_SCREEN))

    chat.click_send.assert_not_called()
    assert result.applied is False
    assert any("同企已沟通避嫌" in line for line in logs), logs
    llm.chat_completion_json.assert_not_called()


@pytest.mark.asyncio
async def test_a_human_copy_anchors_the_company_only_once_it_is_sent():
    """A delivered human greeting joins the exclusion pool; an unsent one never does."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await _seed(store, card)
    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, _ = _screener()

    pipeline = _pipeline(
        store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat, screener=screener
    )
    await pipeline.stream_jobs(_outreach(screening_policy=NO_SCREEN))

    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] == JobRecordStatus.APPLIED.value
    assert card.card.company_name in await store.get_applied_direct_companies(cooldown_days=30)
    assert await store.count_today_applied_jobs() == 1


@pytest.mark.asyncio
async def test_a_human_copy_skips_drafting_even_with_semantic_screening_configured():
    """The zero-call promise is about drafting, not about the model being idle overall.

    With a JD blacklist configured the semantic screen still runs (a different decision,
    taken before any greeting text is chosen), so this pins the exact call count instead of
    leaning on an empty blacklist to look like zero tokens.
    """
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await _seed(store, card)
    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    llm = MagicMock()
    verdict = {"pass": True, "reason": "未触犯黑名单"}
    llm.chat_completion_json.return_value = verdict

    pipeline = _pipeline(
        store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat,
        screener=CandidateScreener(llm_client=llm),
    )
    await pipeline.stream_jobs(
        _outreach(screening_policy=ScreeningPolicy(jd_blacklist=["Java"], enable_screening=True))
    )

    assert llm.chat_completion_json.call_count == 1, "one semantic screen, zero drafts"
    system_prompt = llm.chat_completion_json.call_args[0][0][0]["content"]
    assert "精筛" in system_prompt, system_prompt[:120]
    assert chat.type_greeting_message.call_args[0][0] == HUMAN_COPY


@pytest.mark.asyncio
async def test_the_score_that_approved_a_human_copy_survives_the_send():
    """A reused copy is not re-scored, and its old score is not erased to 0 (#300).

    Skipping generation means the evaluation carries no score, and 0 is not "unknown" — it
    is "the worst score possible". The number the operator used to approve that copy is the
    record to keep, and a threshold cannot veto a score that was never computed.
    """
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await _seed(store, card)
    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, llm = _screener()

    pipeline = _pipeline(
        store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat, screener=screener
    )
    result = await pipeline.stream_jobs(_outreach(min_score=95, screening_policy=NO_SCREEN))

    llm.chat_completion_json.assert_not_called()
    assert result.applied_count == 1
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["match_score"] == 88
    assert stored["status"] == JobRecordStatus.APPLIED.value
    assert result.score == 88


@pytest.mark.asyncio
async def test_a_payload_with_no_direct_target_ignores_a_stray_greeting():
    """`greeting_message` is a 定向投递 field, not a broadcast knob (#300 c4).

    A search payload that happened to carry the key must not send one text to every card in
    the sweep, so only a payload naming its target may override the record.
    """
    from boss_agent.feed_pipeline import FeedStreamConfig

    search_like = FeedStreamConfig.from_payload({
        "target_action": "auto_apply",
        "keyword": "Agent",
        "greeting_message": "误放的文案",
    })
    assert search_like.direct_greeting == ""

    direct = FeedStreamConfig.from_payload({
        "target_action": "auto_apply",
        "direct_job_id": "rec-7",
        "greeting_message": "我在面板里改过的文案",
    })
    assert direct.direct_greeting == "我在面板里改过的文案"

