"""
tests/unit/test_greeting_provenance.py
======================================
Where a Job Record's greeting came from, and what the worker owes it (issue #300).

Previewing a greeting stopped being a run mode in #298: an operator who wants to read one
generates or edits it in the Web Dashboard, and that copy is then the human's. So the
record has to know which text is whose, and a run that finds a human's copy sends it
verbatim and spends no token re-drafting it — otherwise "generate it, then let me edit
it" is a loop that rewrites the edit on the next sweep.

Two authorities, in order: the greeting in a 定向投递 payload (the human edited it in the
modal for this send, and until this ticket nothing read it at all), then the record's own
human-marked copy. An agent draft — and a legacy record whose provenance is unknown —
keeps today's behaviour exactly: draft, then gate on the score.
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

from boss_agent.feed_pipeline import FeedStreamConfig
from boss_agent.job_store import InMemoryJobRecordStore
from boss_agent.models import (
    APPLIED_SOURCE_AGENT,
    GREETING_SOURCE_AGENT,
    GREETING_SOURCE_HUMAN,
    JobCardBrief,
    JobRecordStatus,
    TargetAction,
)
from boss_agent.screening import CandidateScreener

HUMAN_COPY = "李工您好！看到贵司在招 Agent 平台方向，我做过端侧推理编排，想具体聊聊。"
RECRUITER = "王女士"
# What the drafting path sends: `ensure_greeting_prefix` puts the recruiter's canonical
# salutation on the front of every *draft*. A human copy must never be touched by it.
DRAFTED_SENT = "王女士您好,幸会!生成的招呼语"


def _record(card: JobCardBrief, **overrides) -> dict:
    record = {
        "fingerprint": card.fingerprint,
        "title": card.title,
        "company_name": card.company_name,
        "recruiter_name": card.recruiter_name,
        "status": JobRecordStatus.MATCHED.value,
        "job_description": GOOD_JD,
        "greeting_message": "我是一名有 8 年经验的后端工程师，对贵司岗位很感兴趣。",
        "greeting_source": GREETING_SOURCE_AGENT,
        "match_score": 88,
    }
    record.update(overrides)
    return record


def _screener() -> tuple[CandidateScreener, MagicMock]:
    """A screener over a spy client: any token spent on it is visible to the test."""
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": 91,
        "match_reasons": ["契合"],
        "greeting_message": "生成的招呼语",
    }
    return CandidateScreener(llm_client=llm), llm


def _outreach(**overrides):
    data = {"max_jobs": 1}
    data.update(overrides)
    return _apply_config(**data)


@pytest.mark.asyncio
async def test_a_human_greeting_is_sent_verbatim_with_zero_model_calls():
    card = _card("AI Agent 平台工程师", "智元创新")
    store = InMemoryJobRecordStore()
    await store.upsert_job_record(
        _record(card.card, greeting_message=HUMAN_COPY, greeting_source=GREETING_SOURCE_HUMAN)
    )

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, llm = _screener()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat,
                         screener=screener, log=log)

    result = await pipeline.stream_jobs(_outreach())

    llm.chat_completion_json.assert_not_called()
    # Byte-identical: the salutation is not re-prefixed, trimmed or rewritten.
    chat.type_greeting_message.assert_called_once()
    assert chat.type_greeting_message.call_args[0][0] == HUMAN_COPY
    assert result.applied_count == 1
    assert any("复用人工招呼语" in line for line in logs), logs

    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] == JobRecordStatus.APPLIED.value
    assert stored["applied_source"] == APPLIED_SOURCE_AGENT
    assert stored["greeting_message"] == HUMAN_COPY
    assert stored["greeting_source"] == GREETING_SOURCE_HUMAN


@pytest.mark.asyncio
async def test_the_salutation_of_a_human_copy_is_left_alone():
    """`ensure_greeting_prefix` normalises drafts; it must not touch a human's text.

    The human wrote "李工您好" deliberately. Rewriting it into the recruiter's canonical
    salutation would send something the human never approved.
    """
    card = _card("移动端架构师", "智能终端科技")
    store = InMemoryJobRecordStore()
    await store.upsert_job_record(
        _record(card.card, greeting_message=HUMAN_COPY, greeting_source=GREETING_SOURCE_HUMAN)
    )
    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, _ = _screener()

    pipeline = _pipeline(store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat,
                         screener=screener)
    await pipeline.stream_jobs(_outreach())

    sent = chat.type_greeting_message.call_args[0][0]
    assert sent == HUMAN_COPY
    assert not sent.startswith(f"{RECRUITER[:2]}") or sent == HUMAN_COPY


@pytest.mark.asyncio
async def test_an_agent_draft_is_still_generated_and_score_gated():
    """No human copy means no behaviour change: draft, then the threshold decides."""
    card = _card("AI Agent 平台工程师", "智元创新")
    store = InMemoryJobRecordStore()
    await store.upsert_job_record(_record(card.card))  # greeting_source: agent

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, llm = _screener()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat,
                         screener=screener, log=log)
    result = await pipeline.stream_jobs(_outreach())

    assert llm.chat_completion_json.call_count == 1
    assert chat.type_greeting_message.call_args[0][0] == DRAFTED_SENT
    assert result.applied_count == 1
    assert not any("复用人工招呼语" in line for line in logs), logs
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["greeting_message"] == DRAFTED_SENT
    assert stored["greeting_source"] == GREETING_SOURCE_AGENT


@pytest.mark.asyncio
async def test_a_legacy_record_with_no_known_source_is_not_treated_as_human():
    """Records written before provenance existed must not block the rollout."""
    card = _card("AI Agent 平台工程师", "智元创新")
    store = InMemoryJobRecordStore()
    await store.upsert_job_record(
        _record(card.card, greeting_source=None, greeting_message="历史草稿")
    )
    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, llm = _screener()

    pipeline = _pipeline(store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat,
                         screener=screener)
    await pipeline.stream_jobs(_outreach())

    assert llm.chat_completion_json.call_count == 1
    assert chat.type_greeting_message.call_args[0][0] == DRAFTED_SENT


@pytest.mark.asyncio
async def test_an_empty_human_greeting_falls_back_to_drafting():
    """A source marker with no text is no copy: it must not send a blank message."""
    card = _card("AI Agent 平台工程师", "智元创新")
    store = InMemoryJobRecordStore()
    await store.upsert_job_record(
        _record(card.card, greeting_message="", greeting_source=GREETING_SOURCE_HUMAN)
    )
    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, llm = _screener()

    pipeline = _pipeline(store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat,
                         screener=screener)
    await pipeline.stream_jobs(_outreach())

    assert llm.chat_completion_json.call_count == 1
    assert chat.type_greeting_message.call_args[0][0] == DRAFTED_SENT


@pytest.mark.asyncio
async def test_repeated_runs_over_a_human_copy_never_re_generate():
    """The quota-degraded case from #298 and the resend from #299, without a token bill.

    First run: the daily limit is spent, so the human copy is saved and nothing goes out.
    Second run: the same card is greeted — still with no generation call at all.
    """
    card = _card("AI Agent 平台工程师", "智元创新")
    store = InMemoryJobRecordStore()
    await store.upsert_job_record(
        _record(card.card, greeting_message=HUMAN_COPY, greeting_source=GREETING_SOURCE_HUMAN)
    )
    for i in range(20):
        await store.upsert_job_record(
            {
                "fingerprint": f"fp-used-{i}",
                "title": f"占位 {i}",
                "company_name": f"公司 {i}",
                "recruiter_name": "王女士",
                "status": JobRecordStatus.APPLIED.value,
                "applied_at": TODAY,
            }
        )

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, llm = _screener()
    pipeline = _pipeline(store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat,
                         screener=screener)

    await pipeline.stream_jobs(_outreach())
    assert chat.click_send.call_count == 0
    llm.chat_completion_json.assert_not_called()

    await pipeline.stream_jobs(_outreach(daily_greeting_limit=50))
    assert chat.click_send.call_count == 1
    assert chat.type_greeting_message.call_args[0][0] == HUMAN_COPY
    llm.chat_completion_json.assert_not_called()
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] == JobRecordStatus.APPLIED.value


@pytest.mark.asyncio
async def test_a_targeted_application_greeting_outranks_the_record():
    """The 定向投递 modal's edited copy is the highest-priority source.

    It used to travel in the payload with no reader at all, so the button that says 模拟器将
    自动执行沟通 sent something else.
    """
    card = _card("AI Agent 平台工程师", "智元创新")
    store = InMemoryJobRecordStore()
    await store.upsert_job_record(
        _record(
            card.card,
            greeting_message=HUMAN_COPY,
            greeting_source=GREETING_SOURCE_HUMAN,
            status=JobRecordStatus.JD_SAVED.value,
        )
    )
    modal_copy = "李工您好，这版是我刚在面板里改过的，请发这一版。"

    config = FeedStreamConfig.from_payload(
        {
            "target_action": TargetAction.AUTO_APPLY.value,
            "direct_job_id": "rec-1",
            "job_title": card.card.title,
            "company_name": card.card.company_name,
            "greeting_message": modal_copy,
            # Screening is not what this test is about, and the workspace's configured
            # blacklist would spend a semantic-screen call on the way to the send. The
            # quota and cool-down are pinned too, so the assertion measures the payload and
            # not whatever this machine happens to have in config/settings.local.yaml.
            "daily_greeting_limit": 20,
            "communication_cooldown_days": 0,
            "screening_policy": {"enable_screening": False},
            "min_score": 0,
            "auto_send": True,
            "preview_only": False,
        }
    )
    assert config.direct_greeting == modal_copy

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, llm = _screener()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(store, feed=ScriptedFeed([]), detail=detail, chat=chat,
                         screener=screener, log=log)

    result = await pipeline.stream_jobs(config)

    llm.chat_completion_json.assert_not_called()
    assert chat.type_greeting_message.call_args[0][0] == modal_copy
    assert result.applied_count == 1
    assert any("定向投递" in line and "人工招呼语" in line for line in logs), logs
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["greeting_message"] == modal_copy
    assert stored["greeting_source"] == GREETING_SOURCE_HUMAN


@pytest.mark.asyncio
async def test_a_human_copy_does_not_bypass_the_quota_or_the_company_guard():
    """Provenance changes the text, never the defenses around the send."""
    card = _card("AI Agent 平台工程师", "智元创新")
    store = InMemoryJobRecordStore()
    await store.upsert_job_record(
        _record(card.card, greeting_message=HUMAN_COPY, greeting_source=GREETING_SOURCE_HUMAN)
    )
    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    screener, _ = _screener()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat,
                         screener=screener, log=log)

    await pipeline.stream_jobs(_outreach(daily_greeting_limit=0))
    chat.click_send.assert_not_called()
    assert any("LIMIT REACHED" in line for line in logs), logs
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] == JobRecordStatus.MATCHED.value
    assert stored["greeting_message"] == HUMAN_COPY, "the human copy survives the degraded run"
