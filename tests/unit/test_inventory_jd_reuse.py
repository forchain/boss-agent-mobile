"""
tests/unit/test_inventory_jd_reuse.py
=====================================
Reusing the JD a Job Record already holds instead of collecting it again (issue #301).

A card that comes back for a second look — because #299 put its draft back in reach, or
because the feed simply shows it again — has usually already been read once. Opening the
detail page is cheap; scrolling it, tapping the 查看更多 hotspot, re-reading the text and
probing for the distance widget are not, and they buy nothing when the JD on file is
usable. So the pipeline asks one question first: is the stored JD usable?

"Usable" is not a new rule. It is the two the codebase already applies to a freshly
extracted JD — `is_substantive_jd` for enough signal, and the truncation markers
`expand_description_if_collapsed` looks for — applied to the stored text instead. When it
fails, collection happens exactly as before, and every defense that reads a JD (the
commute ceiling, whitelist relaxation, "an unevaluable JD never costs a greeting slot")
still runs on the same data.
"""

from unittest.mock import MagicMock

import pytest
from _feed_harness import (
    GOOD_JD,
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
    GREETING_SOURCE_AGENT,
    ChatButtonState,
    JobRecordStatus,
    ScreeningPolicy,
    TargetAction,
)
from boss_agent.screening import CandidateScreener

SHORT_JD = "岗位职责：负责 Agent 平台相关工作。"  # under MIN_JD_CHARS
TRUNCATED_JD = GOOD_JD + "\n查看更多"
# A policy that screens nothing and enforces no ceiling, so a test can say plainly
# "the fast path, with nothing else on the detail page to fetch". `ScreeningPolicy()`
# carries this workspace's 40km ceiling, which would buy a probe.
NO_FILTERS = ScreeningPolicy(enable_screening=False, max_commute_distance_km=None)


def _screener(greeting: str = "王女士您好,幸会!生成的招呼语") -> CandidateScreener:
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": 91,
        "match_reasons": ["契合"],
        "greeting_message": "生成的招呼语",
    }
    return CandidateScreener(llm_client=llm)


def _on_file(card, **overrides) -> dict:
    """The record a previous run left behind: a usable JD, no greeting sent."""
    record = {
        "fingerprint": card.card.fingerprint,
        "title": card.card.title,
        "company_name": card.card.company_name,
        "recruiter_name": card.card.recruiter_name,
        "status": JobRecordStatus.JD_SAVED.value,
        "job_description": GOOD_JD,
        "salary_range": "40-60K",
        "match_score": 88,
    }
    record.update(overrides)
    return record


def _sent_chat() -> MagicMock:
    chat = MagicMock()
    chat.click_send.return_value = True
    return chat


@pytest.mark.asyncio
async def test_an_inventory_jd_skips_the_expansion_and_the_re_read():
    """The quantified win: zero device reads of the posting, one greeting sent."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_on_file(card))

    detail = _detail_page()
    chat = _sent_chat()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=_screener(),
        log=log,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=NO_FILTERS))

    detail.extract_job_posting.assert_not_called()
    detail.expand_description_if_collapsed.assert_not_called()
    detail.extract_commute_distance.assert_not_called()
    assert result.applied_count == 1
    assert any("复用库存 JD" in line for line in logs), logs


@pytest.mark.asyncio
async def test_the_fast_path_still_probes_the_contact_control_first():
    """A platform contact is still found before anything is read or spent."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_on_file(card))

    detail = _detail_page(state=ChatButtonState.COMMUNICATED)
    pipeline = _pipeline(
        store, feed=ScriptedFeed([[card]]), detail=detail, chat=_sent_chat(), screener=_screener()
    )

    await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=NO_FILTERS))

    detail.get_chat_button_state.assert_called_once()
    detail.extract_job_posting.assert_not_called()
    applied = await store.list_job_records(status=JobRecordStatus.APPLIED.value)
    assert applied[0]["applied_source"] == "platform_historical"


@pytest.mark.asyncio
async def test_a_truncated_inventory_jd_is_collected_again():
    """A stored JD that still carries 查看更多 was never fully read: go and read it."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_on_file(card, job_description=TRUNCATED_JD))

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=_sent_chat(),
        screener=_screener(),
    )

    await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=NO_FILTERS))

    detail.extract_job_posting.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_jd", [SHORT_JD, "", "   ", "无详细岗位描述", TRUNCATED_JD])
async def test_a_thin_or_truncated_inventory_jd_is_collected_again(stored_jd):
    """Anything that fails the usability test is read again — one shape per run.

    A distinct employer per case, because the same-company guard would legitimately silence
    a second card in the same run, and that is not what this test measures.
    """
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", f"甲公司-{len(stored_jd)}")
    await store.upsert_job_record(_on_file(card, job_description=stored_jd))

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=_sent_chat(),
        screener=_screener(),
    )

    await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=NO_FILTERS))

    detail.extract_job_posting.assert_called_once()


@pytest.mark.asyncio
async def test_an_unevaluable_jd_still_costs_no_greeting_slot():
    """The standing rule survives the fast path: nothing evaluable, nothing sent."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_on_file(card, job_description=SHORT_JD))

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(
        card.card.title, card.card.company_name
    )
    detail.extract_job_posting.return_value.job_description = "无详细岗位描述"
    chat = _sent_chat()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=_screener(),
        log=log,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=NO_FILTERS))

    chat.click_send.assert_not_called()
    assert result.applied is False
    assert await store.count_today_applied_jobs() == 0
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] == JobRecordStatus.JD_SAVED.value


@pytest.mark.asyncio
async def test_a_measured_commute_ceiling_still_rejects_on_an_inventory_jd():
    """Reusing the JD does not skip the App-Enforced Filter — the distance is on file too."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_on_file(card, commute_distance_km=19.5, commute_distance_text="距离家庭住址19.5千米"))

    policy = ScreeningPolicy(max_commute_distance_km=5.0)
    assert policy.is_commute_filter_active
    detail = _detail_page()
    chat = _sent_chat()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=_screener(),
        log=log,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=policy))

    detail.extract_job_posting.assert_not_called()
    chat.click_send.assert_not_called()
    assert result.skipped == 1
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] == JobRecordStatus.IGNORED.value
    assert "通勤" in stored["screened_reason"] or "19.5" in stored["screened_reason"]


@pytest.mark.asyncio
async def test_an_unknown_distance_is_probed_rather_than_guessed():
    """No stored distance + an active ceiling = the probe still runs.

    The fast path skips re-reading the *text*. Dropping a distance the record never held
    would turn a real rejection into a fail-open pass, so the one widget the ceiling needs
    is still fetched — without the expansion and extraction around it.
    """
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新", location="上海  浦东新区  张江")
    await store.upsert_job_record(_on_file(card))  # no commute_distance_km

    # The 考察名单 (spec #328) is what makes this posting worth measuring at all.
    policy = ScreeningPolicy(max_commute_distance_km=5.0, business_district_inspect_list=["张江"])
    detail = _detail_page()
    detail.extract_commute_distance.return_value = (22.0, "距离家庭住址22.0千米")
    chat = _sent_chat()

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=_screener(),
    )

    await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=policy))

    detail.extract_commute_distance.assert_called_once()
    detail.extract_job_posting.assert_not_called()
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] == JobRecordStatus.IGNORED.value


@pytest.mark.asyncio
async def test_a_headhunter_posting_is_never_probed_and_fails_open():
    """A masked employer renders no distance widget: skip the probe, keep the job evaluable."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "某知名电子商务公司")
    await store.upsert_job_record(_on_file(card, is_headhunter=True))

    policy = ScreeningPolicy(max_commute_distance_km=5.0)
    detail = _detail_page()
    chat = _sent_chat()
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=_screener(),
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=policy))

    detail.extract_commute_distance.assert_not_called()
    detail.extract_job_posting.assert_not_called()
    assert result.applied_count == 1, "an unknown distance fails open, it never vetoes"


@pytest.mark.asyncio
async def test_the_pass_that_collects_and_the_pass_that_delivers_read_the_jd_once():
    """#299 and #301 together: a draft the quota held back is delivered without a re-read.

    First pass: nothing on file, so the detail page is read; the quota is already spent, so
    the greeting is saved and not sent. Second pass: the record is re-visited because a draft
    is not a delivery — and the JD it now holds means no device reading happens at all.
    """
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_on_file(card, job_description=""))

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = _sent_chat()

    first = await _pipeline(
        store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat, screener=_screener()
    ).stream_jobs(_apply_config(max_jobs=1, screening_policy=NO_FILTERS, daily_greeting_limit=0))

    assert first.applied is False, "the quota was already spent"
    assert detail.extract_job_posting.call_count == 1, "nothing on file yet, so this pass collects"
    draft = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert draft["status"] == JobRecordStatus.MATCHED.value
    assert draft["job_description"] == GOOD_JD, "the collected JD is what the next run reuses"

    second_detail = _detail_page()
    second_detail.extract_job_posting.return_value = _posting(
        card.card.title, card.card.company_name
    )
    result = await _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=second_detail,
        chat=chat,
        screener=_screener(),
    ).stream_jobs(_apply_config(max_jobs=1, screening_policy=NO_FILTERS))

    assert result.applied_count == 1, "and it is really sent this time"
    second_detail.extract_job_posting.assert_not_called()


@pytest.mark.asyncio
async def test_an_agent_draft_still_regenerates_from_an_inventory_jd():
    """#301 skips *collection*, #300 skips *generation*. Only a human copy skips drafting."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(
        _on_file(card, greeting_message="旧的机器草稿", greeting_source=GREETING_SOURCE_AGENT)
    )

    detail = _detail_page()
    chat = _sent_chat()
    pipeline = _pipeline(
        store, feed=ScriptedFeed([[card]]), detail=detail, chat=chat, screener=_screener()
    )

    await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=NO_FILTERS))

    detail.extract_job_posting.assert_not_called()
    # The drafting path keeps its salutation normalisation; only a human copy is verbatim.
    assert chat.type_greeting_message.call_args[0][0] == "王女士您好,幸会!生成的招呼语"


@pytest.mark.asyncio
async def test_a_save_only_sweep_never_reopens_a_record_it_can_already_read():
    """The two mechanisms stay distinct, and this test tells them apart.

    A 深度存JD run does not reach the inventory fast path at all: the state ladder already
    treats a record with a JD as finished, so no detail page is opened. The fast path serves
    the visits the ladder *does* allow — an outreach run coming back for a draft (#299).
    """
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_on_file(card))

    detail = _detail_page()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store, feed=ScriptedFeed([[card]]), detail=detail, chat=_sent_chat(),
        screener=_screener(), log=log,
    )

    result = await pipeline.stream_jobs(
        FeedStreamConfig(
            target_action=TargetAction.SAVE_JD,
            keyword="Agent",
            max_jobs=1,
            screening_policy=NO_FILTERS,
        )
    )

    assert result.skipped == 1
    detail.extract_job_posting.assert_not_called()
    assert not any("复用库存 JD" in line for line in logs), logs
    assert any("State Machine" in line for line in logs), logs


def test_usability_is_the_existing_rule_applied_to_the_stored_text():
    """No new threshold: the same two judgements that govern a fresh read govern a stored one.

    The predicate is `models.jd_is_usable_on_file`, which composes `is_substantive_jd` with
    `jd_is_truncated` — the very check the detail page runs before it spends scrolls and taps
    on 查看更多. One rule, one owner (#301).
    """
    from boss_agent.models import jd_is_truncated, jd_is_usable_on_file

    assert jd_is_usable_on_file(GOOD_JD) is True
    assert jd_is_usable_on_file(None) is False
    assert jd_is_usable_on_file("") is False
    assert jd_is_usable_on_file("   ") is False
    assert jd_is_usable_on_file(SHORT_JD) is False
    assert jd_is_usable_on_file("无详细岗位描述") is False
    assert jd_is_usable_on_file(TRUNCATED_JD) is False
    assert jd_is_usable_on_file(GOOD_JD + "...") is False
    # Surrounding whitespace is not part of the body.
    assert jd_is_usable_on_file(f"  \n{GOOD_JD}  ") is True

    # …and the truncation half is literally the detail page's own rule.
    from boss_agent.pages import JobDetailPage  # noqa: F401  (import seam smoke)

    assert jd_is_truncated("岗位职责短…查看更多") is True
    assert jd_is_truncated(GOOD_JD) is False


@pytest.mark.asyncio
async def test_the_fast_path_still_relaxes_the_ceiling_for_a_passion_token():
    """A whitelisted title still buys the distant posting a visit *and* a greeting.

    The relaxation runs on the posting assembled from the stored JD, so the fast path has
    to reach it with the same fields a live read would have produced — otherwise #301 would
    quietly turn an exemption into a rejection.
    """
    store = InMemoryJobRecordStore()
    card = _card("大模型平台工程师", "智元创新")
    await store.upsert_job_record(
        _on_file(card, commute_distance_km=22.0, commute_distance_text="距离家庭住址22.0千米")
    )

    policy = ScreeningPolicy(max_commute_distance_km=5.0, title_whitelist=["大模型"])
    detail = _detail_page()
    chat = _sent_chat()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=_screener(),
        log=log,
    )
    result = await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=policy))

    detail.extract_job_posting.assert_not_called()
    detail.extract_commute_distance.assert_not_called()
    assert any("白名单放宽" in line for line in logs), logs
    assert result.applied_count == 1
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["relaxed_by_whitelist"] is True
    assert stored["status"] == JobRecordStatus.APPLIED.value


@pytest.mark.asyncio
async def test_the_fast_path_is_stopped_by_cancellation_before_anything_is_sent():
    """The cancellation probe is not a detail-page step, and the fast path keeps it that way."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_on_file(card))

    detail = _detail_page()
    chat = _sent_chat()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    cancelled = {"value": False}

    async def is_cancelled() -> bool:
        return cancelled["value"]

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=_screener(),
        is_cancelled=is_cancelled,
        log=log,
    )

    async def cancel_after_first_read() -> int:
        cancelled["value"] = True
        return 0

    store.count_today_applied_jobs = cancel_after_first_read  # type: ignore[method-assign]

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1, screening_policy=NO_FILTERS))

    chat.click_send.assert_not_called()
    assert result.cancelled is True
    assert any("Task Cancelled" in line for line in logs), logs
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] != JobRecordStatus.APPLIED.value
