"""
tests.unit.test_handlers_check_chat
===================================
Unit tests for CheckChatHandler: 仅沟通 list traversal, Outbound Message Indicator
skipping, explicit rejection classification, company blacklist ingestion, the
polite acknowledgment sequence and the execution-time cursor that bounds paging
(Issues #206-#208, #239).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import ScreeningPolicy
from boss_agent.pages import CommunicationCard
from boss_agent.rejection import (
    DEFAULT_REJECTION_REPLY_TEXT,
    DISINTEREST_REASON,
    ChatAcknowledgmentSettings,
    RejectionVerdict,
)
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers import check_chat as check_chat_module
from boss_agent.worker.handlers.check_chat import CheckChatHandler

#: The `stop_reason` AC-4 mandates, pinned as a literal rather than imported from the
#: handler: the value is the contract the issue specifies, so a rename inside the
#: handler must fail this suite rather than silently move the contract with it.
REACHED_LAST_EXECUTION_TIME = "reached_last_execution_time"

REJECTION_TEXT = "我们感谢您的投递，但您的专业技能与我们目前的职位需求并不完全吻合。"
INVITATION_TEXT = "您好，方便约个时间聊聊吗？"
DELIVERED_TEXT = "我对这个岗位很感兴趣，期待您的回复~"

COMPANY = "传音控股"
DESCRIPTOR = f"{COMPANY} | 算法工程师"


class FakeClassifier:
    """Deterministic stand-in for the LLM rejection judge."""

    def __init__(self, verdicts: dict[str, bool] | None = None, default: bool = False) -> None:
        self.verdicts = verdicts or {}
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def classify(self, message_text: str, sender_name: str = "") -> RejectionVerdict:
        self.calls.append((sender_name, message_text))
        is_rejection = self.verdicts.get(message_text, self.default)
        return RejectionVerdict(
            is_rejection=is_rejection,
            confidence=0.9,
            rationale="rejection" if is_rejection else "positive",
        )


def card(
    text: str,
    *,
    sender: str = "招聘者",
    status: str = "",
    descriptor: str = "",
    stamp: str = "",
) -> CommunicationCard:
    """Build one 仅沟通 card: an inbound rejection unless a badge says otherwise."""
    return CommunicationCard(
        sender_name=sender,
        message_text=text,
        outbound_status=status,
        company_position=descriptor,
        card_time=stamp,
        row_text=f"{sender}\n{text}",
    )


class Harness:
    """In-memory 仅沟通 list + chat simulator recording every write action."""

    def __init__(
        self,
        cards: list[CommunicationCard],
        *,
        viewport_size: int = 2,
        on_list: bool = True,
        open_list_ok: bool = True,
        mark_disinterest_ok: bool = True,
        return_to_list_ok: bool = True,
        open_message_ok: bool = True,
    ) -> None:
        self.cards = cards
        self.viewport_size = viewport_size
        self.window = 0
        self.removed: set[str] = set()
        self.on_list = on_list
        self.open_list_ok = open_list_ok
        self.mark_disinterest_ok = mark_disinterest_ok
        self.return_to_list_ok = return_to_list_ok
        self.open_message_ok = open_message_ok
        self.events: list[str] = []

    # --- CommunicationListPage contract -----------------------------------------
    def is_on_list(self, timeout_sec: float = 2.0) -> bool:
        return self.on_list

    def open_list(self, timeout_sec: float = 5.0) -> bool:
        self.on_list = self.open_list_ok
        return self.open_list_ok

    def extract_visible_messages(self, max_items: int = 10) -> list[CommunicationCard]:
        window = self.cards[self.window : self.window + self.viewport_size]
        return [m for m in window if m.key not in self.removed][:max_items]

    def scroll_list(self) -> None:
        self.events.append("scroll")
        if self.window < len(self.cards):
            self.window += 1

    def open_message(self, message: CommunicationCard) -> bool:
        if not self.open_message_ok:
            return False
        self.events.append(f"open:{message.message_text}")
        return True

    def mark_disinterest(self, timeout_sec: float = 3.0) -> bool:
        if not self.mark_disinterest_ok:
            self.events.append("disinterest:failed")
            return False
        self.events.append(f"disinterest:{DISINTEREST_REASON}")
        # The platform drops the conversation from the list on success.
        for message in self.cards:
            if message.message_text in self.currently_opened:
                self.removed.add(message.key)
        return True

    def wait_for_list_return(self, timeout_sec: float = 5.0) -> bool:
        self.events.append("list_return")
        if self.return_to_list_ok:
            self.window = 0
            self.cards = [m for m in self.cards if m.key not in self.removed]
        return self.return_to_list_ok

    # --- test helpers -----------------------------------------------------------
    @property
    def currently_opened(self) -> set[str]:
        return {m.message_text for m in self.cards if f"open:{m.message_text}" in self.events}


class FakeChatPage:
    def __init__(self, harness: Harness) -> None:
        self.harness = harness

    def send_message(self, message: str, timeout_sec: float = 5.0) -> bool:
        self.harness.events.append(f"send:{message}")
        return True

    def navigate_back(self, timeout_sec: float = 3.0) -> bool:
        self.harness.events.append("chat_back")
        return True


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """A throwaway config file: a real run must never write the repo's own config."""
    path = tmp_path / "settings.local.yaml"
    path.write_text(
        "# 公司黑名单（一票否决，受直招保护守卫约束）\ncompany_blacklist: []\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def policy(config_path: Path) -> ScreeningPolicy:
    return ScreeningPolicy.load_default(config_path=config_path)


def make_handler(classifier=None, policy=None, settings=None):
    """Handler pinned to explicit settings/policy so no test reads or writes local config."""
    return CheckChatHandler(
        classifier=classifier,
        settings=settings or ChatAcknowledgmentSettings(),
        policy=policy or ScreeningPolicy(),
    )


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def context():
    return WorkerContext(
        config=WorkerConfig(worker_id="test-worker-chat"),
        driver=MagicMock(),
    )


async def _run_async(broker, context, harness, handler, payload=None):
    """Execute one CHECK_CHAT task against the harness and return (result, finished_task)."""
    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload=payload or {})
    with (
        patch.object(check_chat_module, "CommunicationListPage", return_value=harness),
        patch.object(check_chat_module, "ChatPage", return_value=FakeChatPage(harness)),
    ):
        result = await handler.handle(task, broker, context)
    return result, await broker.get_task(task.id)


# ---------------------------------------------------------------------------
# Slice #206: single-card triage and blacklist ingestion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirmed_rejection_blacklists_company_then_acknowledges(
    broker, context, policy, config_path
):
    """#206: descriptor -> company_blacklist -> persisted config -> polite close."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.success is True
    assert result.output["rejections"] == 1
    assert result.output["blacklisted"] == 1
    assert result.output["blacklisted_companies"] == [COMPANY]
    assert policy.company_blacklist == [COMPANY]
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["company_blacklist"] == [COMPANY]
    assert harness.events.index(f"send:{DEFAULT_REJECTION_REPLY_TEXT}") > 0
    assert f"disinterest:{DISINTEREST_REASON}" in harness.events
    assert any("黑名单" in log for log in task.logs)
    assert any("拒信" in log for log in task.logs)


@pytest.mark.asyncio
async def test_blacklist_is_written_before_the_chat_is_opened(broker, context, policy, config_path):
    """A failed acknowledgment must not cost the employer its blacklist entry."""
    harness = Harness(
        [card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)], open_message_ok=False
    )
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["blacklisted"] == 1
    assert result.output["failed"] == 1
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["company_blacklist"] == [COMPANY]


@pytest.mark.asyncio
async def test_employer_is_parsed_from_a_company_position_descriptor(broker, context, policy):
    harness = Harness([card(REJECTION_TEXT, sender="宋女士", descriptor="磐基技术 | 技术总监")])
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["blacklisted_companies"] == ["磐基技术"]


@pytest.mark.asyncio
async def test_no_rewrite_when_the_company_is_already_blacklisted(
    broker, context, config_path, policy
):
    """Rejecting the same employer twice must not duplicate or re-write the entry."""
    before = config_path.read_text(encoding="utf-8")
    policy.add_company_to_blacklist(COMPANY)
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.output["blacklisted"] == 0
    assert config_path.read_text(encoding="utf-8") == before
    assert any("已在公司黑名单中" in log for log in task.logs)


@pytest.mark.asyncio
async def test_masked_company_is_refused_by_the_guardrail(broker, context, policy, config_path):
    harness = Harness([card(REJECTION_TEXT, sender="HR", descriptor="某中型人工智能公司 | 算法")])
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.output["blacklisted"] == 0
    assert result.output["guardrail_blocked"] == 1
    assert policy.company_blacklist == []
    assert any("黑名单保护生效" in log for log in task.logs)
    # The rejection is still closed politely.
    assert result.output["acknowledged"] == 1


@pytest.mark.asyncio
async def test_headhunter_agency_is_refused_by_the_guardrail(broker, context, policy):
    harness = Harness(
        [card(REJECTION_TEXT, sender="陈格", descriptor="杭州脉享人力资源 | 大模型算法")]
    )
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["blacklisted"] == 0
    assert result.output["guardrail_blocked"] == 1
    assert policy.company_blacklist == []


@pytest.mark.asyncio
async def test_unparseable_descriptor_skips_blacklisting_but_still_acknowledges(
    broker, context, policy
):
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor="")])
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.output["blacklisted"] == 0
    assert result.output["acknowledged"] == 1
    assert any("无法从" in log for log in task.logs)


# ---------------------------------------------------------------------------
# Slice #206: Outbound Message Indicator skipping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_cards_are_skipped_without_any_llm_call(broker, context, policy):
    """#206/#205-AC2: waiting on a recruiter reply costs zero tokens and zero navigation."""
    harness = Harness(
        [
            card(
                DELIVERED_TEXT, sender="巫女士", status="[送达]", descriptor="相信光网络科技 | 其他"
            ),
            card(
                "收到，谢谢", sender="陈格", status="[已读]", descriptor="杭州脉享人力资源 | 算法"
            ),
            card(
                "收到 谢谢", sender="宋女士", status="[草稿]", descriptor="磐基技术 | 技术总监"
            ),
        ]
    )
    classifier = FakeClassifier(default=True)
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert classifier.calls == []
    assert result.output["skipped_outbound"] == 3
    assert result.output["evaluated"] == 0
    assert result.output["rejections"] == 0
    assert result.output["blacklisted"] == 0
    assert harness.events == ["scroll", "scroll", "scroll"]


@pytest.mark.asyncio
async def test_an_unrecognised_badge_is_evaluated_rather_than_skipped(broker, context, policy):
    """A wording change on the platform must not silently disable triage."""
    harness = Harness(
        [
            card(REJECTION_TEXT, sender="严胜", status="[未读]", descriptor=DESCRIPTOR),
        ]
    )
    classifier = FakeClassifier({REJECTION_TEXT: True})
    handler = make_handler(classifier=classifier, policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.output["skipped_outbound"] == 0
    assert result.output["blacklisted"] == 1
    assert len(classifier.calls) == 1
    assert any("未知状态标签" in log for log in task.logs)


@pytest.mark.asyncio
async def test_outbound_cards_do_not_consume_the_scan_budget(broker, context, policy):
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
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler, payload={"max_scan_depth": 1})

    assert result.output["skipped_outbound"] == 2
    assert result.output["evaluated"] == 1
    assert result.output["blacklisted"] == 1
    assert len(classifier.calls) == 1


@pytest.mark.asyncio
async def test_an_endless_outbound_backlog_terminates_at_the_scan_ceiling(
    broker, context, policy, monkeypatch
):
    """Skipping is free, so the LLM budget alone cannot stop an unbounded list."""
    monkeypatch.setattr(check_chat_module, "MAX_INSPECTED_CARDS", 5)
    harness = Harness(
        [card(DELIVERED_TEXT, sender=f"招聘者{i}", status="[送达]") for i in range(20)],
        viewport_size=2,
    )
    classifier = FakeClassifier(default=True)
    handler = make_handler(classifier=classifier, policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert classifier.calls == []
    assert result.output["scanned"] == 5
    assert result.output["stop_reason"] == "scan_ceiling"
    assert any("上限" in log for log in task.logs)


@pytest.mark.asyncio
async def test_mixed_list_evaluates_only_the_untagged_cards(broker, context, policy):
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
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["scanned"] == 4
    assert result.output["skipped_outbound"] == 1
    assert result.output["evaluated"] == 3
    assert result.output["preserved"] == 1
    assert result.output["blacklisted"] == 2
    assert policy.company_blacklist == [COMPANY, "磐基技术"]


# ---------------------------------------------------------------------------
# Slice #206: safe preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_positive_invitation_is_preserved_untouched(broker, context, policy):
    """#205-AC8: a genuine invitation must never be touched or blacklisted."""
    harness = Harness([card(INVITATION_TEXT, sender="张先生", descriptor="深至科技 | 后端")])
    handler = make_handler(classifier=FakeClassifier({INVITATION_TEXT: False}), policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert "open:" not in "".join(harness.events)
    assert not [e for e in harness.events if e.startswith("send:")]
    assert not [e for e in harness.events if e.startswith("disinterest:")]
    assert result.output["preserved"] == 1
    assert result.output["blacklisted"] == 0
    assert policy.company_blacklist == []


@pytest.mark.asyncio
async def test_llm_failure_preserves_message_conservatively(broker, context, policy):
    """A classifier outage must never cause a blacklist entry or an acknowledgment."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])

    class ExplodingClassifier:
        def classify(self, message_text: str, sender_name: str = "") -> RejectionVerdict:
            return RejectionVerdict(is_rejection=False, rationale="llm down", error="boom")

    handler = make_handler(classifier=ExplodingClassifier(), policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["preserved"] == 1
    assert result.output["rejections"] == 0
    assert result.output["blacklisted"] == 0
    assert policy.company_blacklist == []
    assert not [e for e in harness.events if e.startswith(("open:", "send:", "disinterest:"))]


# ---------------------------------------------------------------------------
# Slice #207: traversal, deduplication, pagination and dry-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_classifies_without_any_write_action(broker, context, policy, config_path):
    """#207: dry_run logs proposed triage but performs zero mobile and zero file writes."""
    before = config_path.read_text(encoding="utf-8")
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])
    classifier = FakeClassifier({REJECTION_TEXT: True})
    handler = make_handler(classifier=classifier, policy=policy)

    result, task = await _run_async(broker, context, harness, handler, payload={"dry_run": True})

    assert classifier.calls == [("严胜", REJECTION_TEXT)]
    assert not [e for e in harness.events if e.startswith(("open:", "send:", "disinterest:"))]
    assert config_path.read_text(encoding="utf-8") == before
    assert policy.company_blacklist == []
    assert result.output["dry_run"] is True
    assert result.output["rejections"] == 1
    assert result.output["blacklisted"] == 0
    assert any("DRY-RUN" in log for log in task.logs)


@pytest.mark.asyncio
async def test_empty_list_stops_without_interaction(broker, context, policy):
    harness = Harness([])
    handler = make_handler(classifier=FakeClassifier(), policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.success is True
    assert result.output["scanned"] == 0
    assert result.output["stop_reason"] == "empty_list"
    assert harness.events == []


@pytest.mark.asyncio
async def test_all_outbound_list_terminates_by_bounded_scrolling(broker, context, policy):
    """#207: skipping must never stall the loop into an infinite scan."""
    harness = Harness(
        [card(DELIVERED_TEXT, sender=f"招聘者{i}", status="[送达]") for i in range(3)],
        viewport_size=2,
    )
    classifier = FakeClassifier(default=True)
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert classifier.calls == []
    assert result.output["skipped_outbound"] == 3
    assert result.output["stop_reason"] in ("stalled", "empty_list")


@pytest.mark.asyncio
async def test_all_positive_list_terminates_by_bounded_scrolling(broker, context, policy):
    texts = ["方便聊聊吗？", "方便发一下简历吗？", "我们约个面试吧"]
    harness = Harness([card(t, sender=f"招聘者{i}") for i, t in enumerate(texts)], viewport_size=2)
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["evaluated"] == 3
    assert result.output["preserved"] == 3
    assert result.output["stop_reason"] in ("stalled", "empty_list")
    assert len(classifier.calls) == 3


@pytest.mark.asyncio
async def test_max_scan_depth_bounds_the_scan(broker, context, policy):
    texts = [f"抱歉，暂不匹配 #{i}" for i in range(5)]
    harness = Harness([card(t, sender=f"招聘者{i}") for i, t in enumerate(texts)], viewport_size=2)
    classifier = FakeClassifier({t: True for t in texts})
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(
        broker, context, harness, handler, payload={"max_scan_depth": 2, "dry_run": True}
    )

    assert result.output["evaluated"] == 2
    assert result.output["scanned"] == 2
    assert result.output["max_scan_depth"] == 2
    assert result.output["stop_reason"] == "max_scan_depth"
    assert len(classifier.calls) == 2


@pytest.mark.asyncio
async def test_visited_keys_prevent_reprocessing_preserved_messages(broker, context, policy):
    """#207: a preserved card stays in the viewport and must not be re-classified."""
    texts = ["方便聊聊吗？", "方便发简历吗？"]
    harness = Harness([card(t, sender=f"招聘者{i}") for i, t in enumerate(texts)], viewport_size=2)
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert len(classifier.calls) == len(texts)
    assert len(result.output["visited_keys"]) == len(texts)
    assert len(set(result.output["visited_keys"])) == len(texts)


@pytest.mark.asyncio
async def test_reply_text_and_scan_depth_come_from_payload(broker, context, policy):
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, _ = await _run_async(
        broker,
        context,
        harness,
        handler,
        payload={"rejection_reply_text": "谢谢您的回复，祝招聘顺利。", "max_scan_depth": 7},
    )

    assert "send:谢谢您的回复，祝招聘顺利。" in harness.events
    assert result.output["reply_text"] == "谢谢您的回复，祝招聘顺利。"
    assert result.output["max_scan_depth"] == 7


@pytest.mark.asyncio
async def test_failed_disinterest_is_reported_and_recovers_to_list(
    broker, context, policy, config_path
):
    harness = Harness(
        [card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)], mark_disinterest_ok=False
    )
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.success is True
    assert result.output["failed"] == 1
    assert result.output["acknowledged"] == 0
    # The employer stays blacklisted even though the chat sequence failed.
    assert result.output["blacklisted"] == 1
    assert "chat_back" in harness.events
    assert any("不感兴趣" in log for log in task.logs)


@pytest.mark.asyncio
async def test_scan_stops_when_the_platform_never_returns_to_the_list(broker, context, policy):
    """Reading cards off an unknown screen could click an unrelated control."""
    harness = Harness(
        [
            card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR),
            card(INVITATION_TEXT, sender="张先生", descriptor="深至科技 | 后端"),
        ],
        return_to_list_ok=False,
    )
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.output["stop_reason"] == "lost_list"
    assert result.output["acknowledged"] == 1
    # The invitation was never reached, because the scan stopped at the lost list.
    assert result.output["preserved"] == 0
    assert any("终止本次扫描" in log for log in task.logs)


# ---------------------------------------------------------------------------
# Slice #239: the execution cursor bounds paging, the first viewport never
# ---------------------------------------------------------------------------

#: A stamp no cursor a test can seed will ever be newer than.
STALE_STAMP = "2020-01-01"

#: The stamp the platform renders for a message that has just arrived.
FRESH_STAMP = "刚刚"

#: A stamp the parser cannot read: age unknown, so never a reason to stop.
UNREADABLE_STAMP = "你已投递"


async def _seed_prior_run(broker, *, dry_run: bool = False) -> datetime:
    """Record one finished CHECK_CHAT run, as the worker daemon would on success."""
    prior = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={"dry_run": dry_run})
    await broker.claim_task(prior.id, worker_id="seed-worker")
    finished = await broker.update_task_status(prior.id, status=TaskStatus.SUCCESS)
    return finished.updated


@pytest.mark.asyncio
async def test_a_successful_run_supplies_the_next_run_cursor(broker, context, policy):
    """AC: the previous run's completion time is what the next run measures against."""
    cursor = await _seed_prior_run(broker)
    harness = Harness([card(INVITATION_TEXT, sender="张先生", stamp=FRESH_STAMP)])
    handler = make_handler(classifier=FakeClassifier(default=False), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.output["last_execution_time"] == cursor.isoformat()
    assert any("上次执行时间" in log for log in task.logs)


@pytest.mark.asyncio
async def test_the_first_viewport_is_scanned_in_full_despite_the_cursor(broker, context, policy):
    """AC: page one is unconditional -- a fresh state can hide behind an old stamp."""
    await _seed_prior_run(broker)
    texts = ["我们感谢您的投递 #1", "我们感谢您的投递 #2", "我们感谢您的投递 #3"]
    harness = Harness(
        [card(t, sender=f"招聘者{i}", stamp=STALE_STAMP) for i, t in enumerate(texts)],
        viewport_size=2,
    )
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    # Both cards of the opening viewport are judged even though both predate the cursor.
    assert [call[1] for call in classifier.calls] == texts[:2]
    assert result.output["scanned"] == 2
    assert result.output["preserved"] == 2


@pytest.mark.asyncio
async def test_paging_stops_before_triaging_a_card_older_than_the_last_run(
    broker, context, policy
):
    """AC: crossing the cursor ends the run, and says so in the log and the output."""
    await _seed_prior_run(broker)
    harness = Harness(
        [
            card(INVITATION_TEXT, sender="张先生", stamp=FRESH_STAMP),
            card(REJECTION_TEXT, sender="严胜", stamp=STALE_STAMP, descriptor=DESCRIPTOR),
            card("岗位已招满，感谢您的关注。", sender="宋女士", stamp=STALE_STAMP),
        ],
        viewport_size=1,
    )
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier, policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert [call[1] for call in classifier.calls] == [INVITATION_TEXT]
    assert result.output["scanned"] == 1
    assert result.output["stop_reason"] == REACHED_LAST_EXECUTION_TIME
    # The employer behind the older card was never judged, so nothing was blacklisted.
    assert policy.company_blacklist == []
    assert any("Time Truncation" in log for log in task.logs)
    assert any(REACHED_LAST_EXECUTION_TIME in log for log in task.logs)


@pytest.mark.asyncio
async def test_the_opening_page_stays_exempt_after_an_acknowledgment_round_trip(
    broker, context, policy
):
    """AC-3: judging a card on screen one must not end the opening page's exemption.

    Acknowledging a rejection is the main path, and it round-trips through the chat and
    back to the *same* screen, which the run then re-reads. Keying the exemption on the
    loop's first pass rather than on having scrolled would put the cursor in force over
    cards this run never scrolled past -- skipping exactly the edge of the window the
    exemption exists to protect.
    """
    await _seed_prior_run(broker)
    stale_texts = ["方便约个时间聊聊吗？", "方便发一下简历吗？"]
    harness = Harness(
        [
            card(REJECTION_TEXT, sender="严胜", stamp=FRESH_STAMP, descriptor=DESCRIPTOR),
            card(stale_texts[0], sender="张先生", stamp=STALE_STAMP),
            card(stale_texts[1], sender="宋女士", stamp=STALE_STAMP),
        ],
        viewport_size=3,
    )
    classifier = FakeClassifier({REJECTION_TEXT: True})
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert [call[1] for call in classifier.calls] == [REJECTION_TEXT, *stale_texts]
    assert result.output["scanned"] == 3
    assert policy.company_blacklist == [COMPANY]
    assert result.output["stop_reason"] == REACHED_LAST_EXECUTION_TIME


@pytest.mark.asyncio
async def test_the_boundary_viewport_still_judges_cards_that_are_not_certainly_older(
    broker, context, policy
):
    """An unreadable stamp is unknown, not old: it is judged, not skipped."""
    await _seed_prior_run(broker)
    harness = Harness(
        [
            card(INVITATION_TEXT, sender="张先生", stamp=FRESH_STAMP),
            card(REJECTION_TEXT, sender="严胜", stamp=STALE_STAMP),
            card("方便发一下简历吗？", sender="宋女士", stamp=UNREADABLE_STAMP),
        ],
        viewport_size=2,
    )
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert "方便发一下简历吗？" in [call[1] for call in classifier.calls]
    assert result.output["stop_reason"] == REACHED_LAST_EXECUTION_TIME


@pytest.mark.asyncio
async def test_an_unreadable_stamp_never_truncates_the_scan(broker, context, policy):
    """Nothing the run can read as old means nothing to stop on: keep scanning."""
    await _seed_prior_run(broker)
    texts = ["方便聊聊吗？", "方便发简历吗？", "我们约个面试吧"]
    harness = Harness(
        [card(t, sender=f"招聘者{i}", stamp=UNREADABLE_STAMP) for i, t in enumerate(texts)],
        viewport_size=2,
    )
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert [call[1] for call in classifier.calls] == texts
    assert result.output["stop_reason"] == "empty_list"


@pytest.mark.asyncio
async def test_without_a_cursor_the_configured_scan_bounds_still_apply(broker, context, policy):
    """AC: a first-ever run keeps the safe depth and card ceiling, not a time cut."""
    texts = [f"我们感谢您的投递 #{i}" for i in range(5)]
    harness = Harness(
        [card(t, sender=f"招聘者{i}", stamp=STALE_STAMP) for i, t in enumerate(texts)],
        viewport_size=2,
    )
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler, payload={"max_scan_depth": 2})

    assert result.output["last_execution_time"] is None
    assert result.output["evaluated"] == 2
    assert result.output["stop_reason"] == "max_scan_depth"
    assert len(classifier.calls) == 2


@pytest.mark.asyncio
async def test_a_dry_run_does_not_become_the_next_run_cursor(broker, context, policy):
    """A dry run acts on nothing, so trusting it would turn its window into a blind spot."""
    await _seed_prior_run(broker, dry_run=True)
    texts = ["方便约个时间聊聊吗？", "我们感谢您的投递 #1", "我们感谢您的投递 #2"]
    harness = Harness(
        [
            card(texts[0], sender="张先生", stamp=FRESH_STAMP),
            card(texts[1], sender="严胜", stamp=STALE_STAMP),
            card(texts[2], sender="宋女士", stamp=STALE_STAMP),
        ],
        viewport_size=1,
    )
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier, policy=policy)

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["last_execution_time"] is None
    assert result.output["stop_reason"] != REACHED_LAST_EXECUTION_TIME
    assert [call[1] for call in classifier.calls] == texts


@pytest.mark.asyncio
async def test_a_second_run_over_the_same_list_reuses_the_first_runs_cursor(broker, context, policy):
    """#239 end to end, through the worker daemon: run one judges the window, run two
    stops at run one's edge.

    Both runs go through `AutomationWorker.run_once`, so the SUCCESS write the cursor is
    derived from is the daemon's own rather than something a test performs on its behalf
    -- that write is the whole basis for reading a completion back. Run two then reads
    the opening page in full and stops, instead of walking the older pages again.
    """
    texts = [f"方便聊聊第 {i} 个岗位吗？" for i in range(6)]
    harness = Harness(
        [card(t, sender=f"招聘者{i}", stamp=STALE_STAMP) for i, t in enumerate(texts)],
        viewport_size=2,
    )
    classifier = FakeClassifier(default=False)
    worker = AutomationWorker(
        config=context.config,
        broker=broker,
        context=context,
        handlers=[make_handler(classifier=classifier, policy=policy)],
    )

    with (
        patch.object(check_chat_module, "CommunicationListPage", return_value=harness),
        patch.object(check_chat_module, "ChatPage", return_value=FakeChatPage(harness)),
    ):
        first = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})
        assert await worker.run_once() is True
        calls_after_first = len(classifier.calls)
        # A new dispatch lands at the top of the list; the double holds its scroll
        # position, so the platform's reset is modelled here.
        harness.window = 0
        second = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})
        assert await worker.run_once() is True

    finished_first = await broker.get_task(first.id)
    finished_second = await broker.get_task(second.id)
    assert finished_first is not None and finished_second is not None

    # The daemon's SUCCESS write is what makes run one readable as a completion.
    assert finished_first.status == TaskStatus.SUCCESS
    assert finished_second.status == TaskStatus.SUCCESS

    # Run one has no history to measure against, so it walks the whole list.
    assert calls_after_first == len(texts)
    assert any("Finished CHECK_CHAT: scanned 6 card(s)" in line for line in finished_first.logs)
    assert any("stop_reason=empty_list" in line for line in finished_first.logs)

    # Run two is told where run one finished, reads the opening page anyway, then stops.
    assert any("上次执行时间" in line for line in finished_second.logs)
    assert any("Time Truncation" in line for line in finished_second.logs)
    assert any("Finished CHECK_CHAT: scanned 2 card(s)" in line for line in finished_second.logs)
    assert len(classifier.calls) - calls_after_first == 2


class _FastClockBroker(InMemoryTaskBroker):
    """A broker whose newest completion was stamped by a server clock running ahead."""

    async def list_recent_successful_completions(self, task_type, limit=10):
        task = await self.create_task(task_type=TaskType.CHECK_CHAT)
        return [task.model_copy(update={"updated": datetime.now(UTC) + timedelta(hours=2)})]


@pytest.mark.asyncio
async def test_a_cursor_from_the_future_is_refused_rather_than_truncating_everything(
    context, policy
):
    """Stored stamps and card stamps come from different clocks; a skewed one must not bite."""
    broker = _FastClockBroker()
    harness = Harness(
        [card(INVITATION_TEXT, sender="张先生", stamp=FRESH_STAMP)], viewport_size=1
    )
    handler = make_handler(classifier=FakeClassifier(default=False), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.output["last_execution_time"] is None
    assert result.output["stop_reason"] != REACHED_LAST_EXECUTION_TIME
    assert any("时钟" in log or "未来" in log for log in task.logs)


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_driver_fails_fast(broker, policy):
    context = WorkerContext(config=WorkerConfig(worker_id="no-driver"), driver=None)
    handler = make_handler(classifier=FakeClassifier(), policy=policy)

    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})
    result = await handler.handle(task, broker, context)

    assert result.success is False
    assert "driver" in (result.error_message or "").lower()


@pytest.mark.asyncio
async def test_unreachable_list_fails_the_task(broker, context, policy):
    harness = Harness([card(REJECTION_TEXT)], on_list=False, open_list_ok=False)
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.success is False
    assert any("仅沟通" in log for log in task.logs)


@pytest.mark.asyncio
async def test_recovers_into_the_list_from_an_arbitrary_screen(broker, context, policy):
    """#228: a dispatch that lands mid-app navigates itself onto the list and scans."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)], on_list=False)
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    result, task = await _run_async(broker, context, harness, handler)

    assert result.success is True
    assert result.output["rejections"] == 1
    assert any("自愈导航" in log for log in task.logs)


def test_handler_declares_check_chat_task_type():
    assert make_handler().task_type == TaskType.CHECK_CHAT


@pytest.mark.asyncio
async def test_handler_processes_single_card_through_the_real_page_object(broker, context, policy):
    """#206: end-to-end single target resolved from a mock accessibility hierarchy."""
    sender_node = MagicMock(text="严胜")
    text_node = MagicMock(text=REJECTION_TEXT)
    descriptor_node = MagicMock(text=DESCRIPTOR)
    card_elem = MagicMock(text="严胜\n传音控股 | 算法工程师\n" + REJECTION_TEXT)

    def find_children(by, value):
        if "iv_msg_status" in value:
            return []
        if "tv_msg" in value:
            return [text_node]
        if "tv_name" in value:
            return [sender_node]
        if "company" in value or "job_name" in value or "position" in value:
            return [descriptor_node]
        return []

    card_elem.find_elements.side_effect = find_children

    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    def mock_find(by, value):
        if "view_chat_item" in value:
            return [card_elem]
        if "仅沟通" in value or "不感兴趣" in value or "重复推荐" in value:
            return [MagicMock()]
        return []

    driver.find_elements.side_effect = mock_find

    context = WorkerContext(config=WorkerConfig(worker_id="real-page"), driver=driver)
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy)

    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})
    chat_double = MagicMock()

    with patch.object(check_chat_module, "ChatPage", return_value=chat_double):
        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["blacklisted_companies"] == [COMPANY]
    assert result.output["acknowledged"] == 1
    chat_double.send_message.assert_called_once_with(DEFAULT_REJECTION_REPLY_TEXT, timeout_sec=5.0)
