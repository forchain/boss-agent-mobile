"""
tests.unit.test_chat_triage
===========================
Unit tests for the deep Chat Triage module (spec #267, ticket #269).

A run is driven through ``scan()`` with the two fake port adapters — the scripted
仅沟通 screen and chat this suite has always hand-written — so every scenario asserts
on outcomes (which companies were blacklisted, which cards were touched, why the run
stopped) rather than on internal call sequences. Migrated from the handler suite with
its scenario names intact: the behaviour is unchanged, its seam is not.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from _chat_triage_harness import (
    COMPANY,
    DELIVERED_TEXT,
    DESCRIPTOR,
    INVITATION_TEXT,
    REJECTION_TEXT,
    EndlessOutboundHarness,
    FakeClassifier,
    Harness,
    card,
    recording_log,
    throwaway_config,
    triage_run,
)

from boss_agent.chat_triage import (
    MAX_INSPECTED_CARDS,
    ChatActor,
    ChatActorAdapter,
    ChatListReader,
    CommunicationListAdapter,
    StopReason,
)
from boss_agent.models import ScreeningPolicy
from boss_agent.rejection import (
    DEFAULT_REJECTION_REPLY_TEXT,
    DISINTEREST_REASON,
    ChatAcknowledgmentSettings,
    RejectionVerdict,
)

#: The `stop_reason` a First-Screen Scan ends on, pinned as a literal rather than
#: imported from the module: the value is the contract, so a rename inside must fail
#: this suite rather than silently move the contract with it.
FIRST_SCREEN_EXHAUSTED = "first_screen_exhausted"


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """A throwaway config file: a real run must never write the repo's own config."""
    return throwaway_config(tmp_path / "settings.local.yaml")


@pytest.fixture
def policy(config_path: Path) -> ScreeningPolicy:
    return ScreeningPolicy.load_default(config_path=config_path)


# ---------------------------------------------------------------------------
# The seam: two narrow ports, adapted from the page objects
# ---------------------------------------------------------------------------


def test_the_production_adapters_present_exactly_the_ports_triage_depends_on():
    """The page objects reach triage only through these two shapes."""
    page = MagicMock()

    assert isinstance(CommunicationListAdapter(page), ChatListReader)
    assert isinstance(ChatActorAdapter(page), ChatActor)


# ---------------------------------------------------------------------------
# Slice #206: single-card triage and blacklist ingestion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirmed_rejection_blacklists_company_then_acknowledges(policy, config_path):
    """#206: descriptor -> company_blacklist -> persisted config -> polite close."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    ).scan()

    assert report.rejections == 1
    assert report.blacklisted_count == 1
    assert report.blacklisted_companies == (COMPANY,)
    assert policy.company_blacklist == [COMPANY]
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["company_blacklist"] == [COMPANY]
    assert harness.events.index(f"send:{DEFAULT_REJECTION_REPLY_TEXT}") > 0
    assert f"disinterest:{DISINTEREST_REASON}" in harness.events


@pytest.mark.asyncio
async def test_blacklist_is_written_before_the_chat_is_opened(policy, config_path):
    """A failed acknowledgment must not cost the employer its blacklist entry."""
    harness = Harness(
        [card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)], open_message_ok=False
    )

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    ).scan()

    assert report.blacklisted_count == 1
    assert report.failed == 1
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["company_blacklist"] == [COMPANY]


@pytest.mark.asyncio
async def test_employer_is_parsed_from_a_company_position_descriptor(policy):
    harness = Harness([card(REJECTION_TEXT, sender="宋女士", descriptor="磐基技术 | 技术总监")])

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    ).scan()

    assert report.blacklisted_companies == ("磐基技术",)


@pytest.mark.asyncio
async def test_no_rewrite_when_the_company_is_already_blacklisted(config_path, policy):
    """Rejecting the same employer twice must not duplicate or re-write the entry."""
    before = config_path.read_text(encoding="utf-8")
    policy.add_company_to_blacklist(COMPANY)
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    ).scan()

    assert report.blacklisted_count == 0
    assert config_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_masked_company_is_refused_by_the_guardrail(policy, config_path):
    harness = Harness([card(REJECTION_TEXT, sender="HR", descriptor="某中型人工智能公司 | 算法")])

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    ).scan()

    assert report.blacklisted_count == 0
    assert report.guardrail_blocked == 1
    assert policy.company_blacklist == []
    # The rejection is still closed politely.
    assert report.acknowledged == 1


@pytest.mark.asyncio
async def test_headhunter_agency_is_refused_by_the_guardrail(policy):
    harness = Harness(
        [card(REJECTION_TEXT, sender="陈格", descriptor="杭州脉享人力资源 | 大模型算法")]
    )

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    ).scan()

    assert report.blacklisted_count == 0
    assert report.guardrail_blocked == 1
    assert policy.company_blacklist == []


@pytest.mark.asyncio
async def test_unparseable_descriptor_skips_blacklisting_but_still_acknowledges(policy):
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor="")])

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    ).scan()

    assert report.blacklisted_count == 0
    assert report.acknowledged == 1


# ---------------------------------------------------------------------------
# Slice #206: Outbound Message Indicator skipping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_cards_are_skipped_without_any_llm_call(policy):
    """#206/#205-AC2: waiting on a recruiter reply costs zero tokens and zero navigation."""
    harness = Harness(
        [
            card(
                DELIVERED_TEXT, sender="巫女士", status="[送达]", descriptor="相信光网络科技 | 其他"
            ),
            card(
                "收到，谢谢", sender="陈格", status="[已读]", descriptor="杭州脉享人力资源 | 算法"
            ),
            card("收到 谢谢", sender="宋女士", status="[草稿]", descriptor="磐基技术 | 技术总监"),
        ],
        viewport_size=3,
    )
    classifier = FakeClassifier(default=True)

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert classifier.calls == []
    assert report.skipped_outbound == 3
    assert report.evaluated == 0
    assert report.rejections == 0
    assert report.blacklisted_count == 0
    assert harness.events == []


@pytest.mark.asyncio
async def test_an_unrecognised_badge_is_evaluated_rather_than_skipped(policy):
    """A wording change on the platform must not silently disable triage."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", status="[未读]", descriptor=DESCRIPTOR)])
    classifier = FakeClassifier({REJECTION_TEXT: True})

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert report.skipped_outbound == 0
    assert report.blacklisted_count == 1
    assert len(classifier.calls) == 1


@pytest.mark.asyncio
async def test_outbound_cards_do_not_consume_the_scan_budget(policy):
    """max_scan_depth bounds LLM evaluations, so a long outbound backlog cannot starve it."""
    harness = Harness(
        [
            card(DELIVERED_TEXT, sender="巫女士", status="[送达]"),
            card(DELIVERED_TEXT, sender="高佳慧", status="[送达]"),
            card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR),
        ],
        viewport_size=3,
    )
    classifier = FakeClassifier({REJECTION_TEXT: True})

    report = await triage_run(
        harness,
        classifier=classifier,
        policy=policy,
        settings=ChatAcknowledgmentSettings(max_scan_depth=1),
    ).scan()

    assert report.skipped_outbound == 2
    assert report.evaluated == 1
    assert report.blacklisted_count == 1
    assert len(classifier.calls) == 1


@pytest.mark.asyncio
async def test_a_long_first_screen_terminates_at_the_scan_ceiling(policy):
    """Skipping is free, so the LLM budget alone cannot bound a wide screen."""
    harness = EndlessOutboundHarness()
    classifier = FakeClassifier(default=True)

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert classifier.calls == []
    assert report.scanned == MAX_INSPECTED_CARDS
    assert report.stop_reason is StopReason.SCAN_CEILING


@pytest.mark.asyncio
async def test_a_configured_ceiling_bounds_the_run(policy):
    """The terminal bound is the module's own configuration, not a handler loop edit."""
    harness = EndlessOutboundHarness()

    report = await triage_run(harness, policy=policy, max_inspected_cards=5).scan()

    assert report.scanned == 5
    assert report.stop_reason is StopReason.SCAN_CEILING


@pytest.mark.asyncio
async def test_mixed_list_evaluates_only_the_untagged_cards(policy):
    other_rejection = "岗位已招满，感谢您的关注。"
    harness = Harness(
        [
            card(INVITATION_TEXT, sender="张先生", descriptor="深至科技 | 后端"),
            card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR),
            card(DELIVERED_TEXT, sender="巫女士", status="[送达]"),
            card(other_rejection, sender="宋女士", descriptor="磐基技术 | 技术总监"),
        ],
        viewport_size=4,
    )
    classifier = FakeClassifier({REJECTION_TEXT: True, other_rejection: True})

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert report.scanned == 4
    assert report.skipped_outbound == 1
    assert report.evaluated == 3
    assert report.preserved == 1
    assert report.blacklisted_count == 2
    assert policy.company_blacklist == [COMPANY, "磐基技术"]


# ---------------------------------------------------------------------------
# Slice #206: safe preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_positive_invitation_is_preserved_untouched(policy):
    """#205-AC8: a genuine invitation must never be touched or blacklisted."""
    harness = Harness([card(INVITATION_TEXT, sender="张先生", descriptor="深至科技 | 后端")])

    report = await triage_run(
        harness, classifier=FakeClassifier({INVITATION_TEXT: False}), policy=policy
    ).scan()

    assert "open:" not in "".join(harness.events)
    assert not [e for e in harness.events if e.startswith("send:")]
    assert not [e for e in harness.events if e.startswith("disinterest:")]
    assert report.preserved == 1
    assert report.blacklisted_count == 0
    assert policy.company_blacklist == []


@pytest.mark.asyncio
async def test_llm_failure_preserves_message_conservatively(policy):
    """A classifier outage must never cause a blacklist entry or an acknowledgment."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])

    class ExplodingClassifier:
        def classify(self, message_text: str, sender_name: str = "") -> RejectionVerdict:
            return RejectionVerdict(is_rejection=False, rationale="llm down", error="boom")

    report = await triage_run(harness, classifier=ExplodingClassifier(), policy=policy).scan()

    assert report.preserved == 1
    assert report.rejections == 0
    assert report.blacklisted_count == 0
    assert policy.company_blacklist == []
    assert not [e for e in harness.events if e.startswith(("open:", "send:", "disinterest:"))]


# ---------------------------------------------------------------------------
# Slice #207: traversal, deduplication and dry-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_classifies_without_any_write_action(policy, config_path):
    """#207: dry_run logs proposed triage but performs zero mobile and zero file writes."""
    before = config_path.read_text(encoding="utf-8")
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])
    classifier = FakeClassifier({REJECTION_TEXT: True})

    report = await triage_run(
        harness,
        classifier=classifier,
        policy=policy,
        settings=ChatAcknowledgmentSettings(dry_run=True),
    ).scan()

    assert classifier.calls == [("严胜", REJECTION_TEXT)]
    assert not [e for e in harness.events if e.startswith(("open:", "send:", "disinterest:"))]
    assert config_path.read_text(encoding="utf-8") == before
    assert policy.company_blacklist == []
    assert report.dry_run is True
    assert report.rejections == 1
    assert report.blacklisted_count == 0
    # The rejection was judged and narrated, but nothing was acknowledged on the device.
    assert report.acknowledged == 0


@pytest.mark.asyncio
async def test_empty_list_stops_without_interaction(policy):
    harness = Harness([])

    report = await triage_run(harness, policy=policy).scan()

    assert report.scanned == 0
    assert report.stop_reason is StopReason.EMPTY_LIST
    assert harness.events == []


@pytest.mark.asyncio
async def test_all_outbound_list_ends_when_the_screen_holds_nothing_new(policy):
    """#207: skipping must never stall the loop into an infinite scan."""
    harness = Harness(
        [card(DELIVERED_TEXT, sender=f"招聘者{i}", status="[送达]") for i in range(3)],
        viewport_size=2,
    )
    classifier = FakeClassifier(default=True)

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert classifier.calls == []
    assert report.skipped_outbound == 2
    assert report.stop_reason.value == FIRST_SCREEN_EXHAUSTED
    assert harness.events == []


@pytest.mark.asyncio
async def test_all_positive_list_ends_when_the_screen_holds_nothing_new(policy):
    texts = ["方便聊聊吗？", "方便发一下简历吗？", "我们约个面试吧"]
    harness = Harness([card(t, sender=f"招聘者{i}") for i, t in enumerate(texts)], viewport_size=2)
    classifier = FakeClassifier(default=False)

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert report.evaluated == 2
    assert report.preserved == 2
    assert report.stop_reason.value == FIRST_SCREEN_EXHAUSTED
    assert len(classifier.calls) == 2


@pytest.mark.asyncio
async def test_max_scan_depth_bounds_the_scan(policy):
    texts = [f"抱歉，暂不匹配 #{i}" for i in range(5)]
    harness = Harness([card(t, sender=f"招聘者{i}") for i, t in enumerate(texts)], viewport_size=2)
    classifier = FakeClassifier({t: True for t in texts})

    report = await triage_run(
        harness,
        classifier=classifier,
        policy=policy,
        settings=ChatAcknowledgmentSettings(max_scan_depth=2, dry_run=True),
    ).scan()

    assert report.evaluated == 2
    assert report.scanned == 2
    assert report.stop_reason is StopReason.MAX_SCAN_DEPTH
    assert len(classifier.calls) == 2


@pytest.mark.asyncio
async def test_visited_keys_prevent_reprocessing_preserved_messages(policy):
    """#207: a preserved card stays in the viewport and must not be re-classified."""
    texts = ["方便聊聊吗？", "方便发简历吗？"]
    harness = Harness([card(t, sender=f"招聘者{i}") for i, t in enumerate(texts)], viewport_size=2)
    classifier = FakeClassifier(default=False)

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert len(classifier.calls) == len(texts)
    assert len(report.visited_keys) == len(texts)


@pytest.mark.asyncio
async def test_failed_disinterest_is_reported_and_recovers_to_list(policy, config_path):
    harness = Harness(
        [card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)], mark_disinterest_ok=False
    )

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    ).scan()

    assert report.failed == 1
    assert report.acknowledged == 0
    # The employer stays blacklisted even though the chat sequence failed.
    assert report.blacklisted_count == 1
    assert "chat_back" in harness.events


@pytest.mark.asyncio
async def test_scan_stops_when_the_platform_never_returns_to_the_list(policy):
    """Reading cards off an unknown screen could click an unrelated control."""
    harness = Harness(
        [
            card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR),
            card(INVITATION_TEXT, sender="张先生", descriptor="深至科技 | 后端"),
        ],
        return_to_list_ok=False,
    )

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    ).scan()

    assert report.stop_reason is StopReason.LOST_LIST
    assert report.acknowledged == 1
    # The invitation was never reached, because the scan stopped at the lost list.
    assert report.preserved == 0


# ---------------------------------------------------------------------------
# Slice #239: the scan reads the opening screen and never pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cards_below_the_fold_are_never_reached(policy):
    """#239: no scrolling, so a run's reach is exactly one screen."""
    texts = [f"我们感谢您的投递 #{i}" for i in range(6)]
    harness = Harness(
        [card(t, sender=f"招聘者{i}", descriptor=f"企业{i} | 算法") for i, t in enumerate(texts)],
        viewport_size=3,
    )
    classifier = FakeClassifier(default=False)

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert [call[1] for call in classifier.calls] == texts[:3]
    assert report.scanned == 3
    assert report.stop_reason.value == FIRST_SCREEN_EXHAUSTED
    assert len(harness.events) == 0, "a first-screen scan touches nothing but the screen it read"


@pytest.mark.asyncio
async def test_the_screen_is_re_read_after_an_acknowledgment_moves_a_card_up(policy):
    """#239 AC: an acknowledged card leaves the list, so the next one becomes reachable.

    The scan is bounded by the screen rather than by a page count, so the cards that
    move up into the space an acknowledgment freed are still judged -- while the card
    that was below the fold of the *first* read is only reached once a card above it
    is gone.
    """
    first, second = "我们感谢您的投递 #1", "我们感谢您的投递 #2"
    invitations = ["方便约个时间聊聊吗？", "方便发一下简历吗？", "我们约个面试吧"]
    harness = Harness(
        [
            card(first, sender="严胜", descriptor=DESCRIPTOR),
            card(second, sender="宋女士", descriptor="磐基技术 | 技术总监"),
            *[card(t, sender=f"招聘者{i}") for i, t in enumerate(invitations)],
        ],
        viewport_size=3,
    )
    classifier = FakeClassifier({first: True, second: True})

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert [call[1] for call in classifier.calls] == [first, second, *invitations]
    assert report.scanned == 5
    assert report.acknowledged == 2
    assert report.preserved == 3
    assert policy.company_blacklist == [COMPANY, "磐基技术"]
    # The two invitations that were below the fold were reached only because the two
    # acknowledged cards above them left the list.
    assert report.stop_reason.value == FIRST_SCREEN_EXHAUSTED


@pytest.mark.asyncio
async def test_a_preserved_card_is_not_judged_twice_across_re_reads(policy):
    """The screen is re-read after every acknowledgment; a card that stays is not re-sent."""
    first, second = "我们感谢您的投递 #1", "我们感谢您的投递 #2"
    harness = Harness(
        [
            card(first, sender="严胜", descriptor=DESCRIPTOR),
            card("方便聊聊吗？", sender="张先生"),
            card(second, sender="宋女士", descriptor="磐基技术 | 技术总监"),
        ],
        viewport_size=3,
    )
    classifier = FakeClassifier({first: True, second: True})

    report = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert [call[1] for call in classifier.calls] == [first, "方便聊聊吗？", second]
    assert report.preserved == 1
    assert report.acknowledged == 2
    assert len(report.visited_keys) == 3


@pytest.mark.asyncio
async def test_a_second_run_over_an_unchanged_screen_judges_it_again(policy):
    """Nothing is carried between runs: a preserved card is judged on every dispatch.

    The price of having no cursor. It is bounded by the width of the screen and by
    `max_scan_depth`, and the run ends at the same place either way.
    """
    texts = ["方便聊聊吗？", "方便发一下简历吗？"]
    harness = Harness([card(t, sender=f"招聘者{i}") for i, t in enumerate(texts)], viewport_size=3)
    classifier = FakeClassifier(default=False)

    first = await triage_run(harness, classifier=classifier, policy=policy).scan()
    second = await triage_run(harness, classifier=classifier, policy=policy).scan()

    assert first.preserved == 2
    assert second.preserved == 2
    assert second.stop_reason.value == FIRST_SCREEN_EXHAUSTED
    assert len(classifier.calls) == 4


# ---------------------------------------------------------------------------
# Entry: the run narrates the navigation it needed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovers_into_the_list_from_an_arbitrary_screen(policy):
    """#228: a dispatch that lands mid-app navigates itself onto the list and scans."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)], on_list=False)
    lines, log = recording_log()

    report = await triage_run(
        harness,
        classifier=FakeClassifier({REJECTION_TEXT: True}),
        policy=policy,
        log=log,
    ).scan()

    assert report.rejections == 1
    assert any("自愈导航" in line for line in lines)


@pytest.mark.asyncio
async def test_a_run_stops_within_one_card_when_the_task_is_cancelled(policy):
    """Cancellation is observed at the same point as ever: the top of a screen read."""
    reads = 0

    async def cancelled() -> bool:
        nonlocal reads
        reads += 1
        return reads > 1

    harness = Harness(
        [
            card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR),
            card(INVITATION_TEXT, sender="张先生", descriptor="深至科技 | 后端"),
        ],
        viewport_size=1,
    )

    report = await triage_run(
        harness,
        classifier=FakeClassifier({REJECTION_TEXT: True}),
        policy=policy,
        is_cancelled=cancelled,
    ).scan()

    assert report.stop_reason is StopReason.CANCELLED
    assert report.scanned == 1
    # The card behind the cancelled one was never read, let alone judged.
    assert report.preserved == 0


@pytest.mark.asyncio
async def test_an_unreachable_list_ends_the_run_before_any_card_is_read(policy):
    harness = Harness([card(REJECTION_TEXT)], on_list=False, open_list_ok=False)
    lines, log = recording_log()

    report = await triage_run(
        harness, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy, log=log
    ).scan()

    assert report.stop_reason is StopReason.LIST_UNREACHABLE
    assert report.scanned == 0
    assert any("无法进入" in line for line in lines)
