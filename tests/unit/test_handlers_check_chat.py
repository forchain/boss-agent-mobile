"""
tests.unit.test_handlers_check_chat
===================================
Unit tests for CheckChatHandler: New Greeting Inbox traversal, explicit
rejection classification, polite acknowledgment and disinterest feedback
(Issues #206 and #207).
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent.broker.models import TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.pages import ChatInboxMessage
from boss_agent.rejection import (
    DEFAULT_REJECTION_REPLY_TEXT,
    DISINTEREST_REASON,
    ChatAcknowledgmentSettings,
    RejectionVerdict,
)
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers import check_chat as check_chat_module
from boss_agent.worker.handlers.check_chat import CheckChatHandler

REJECTION_TEXT = "抱歉，您的经历与岗位要求不太匹配。"
INVITATION_TEXT = "您好，方便约个时间聊聊吗？"


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


class Harness:
    """In-memory New Greeting Inbox + chat simulator recording every write action."""

    def __init__(
        self,
        message_texts: list[str],
        *,
        viewport_size: int = 2,
        on_inbox: bool = True,
        open_inbox_ok: bool = True,
        mark_disinterest_ok: bool = True,
        return_to_inbox_ok: bool = True,
        open_message_ok: bool = True,
    ) -> None:
        self.messages = [
            ChatInboxMessage(sender_name=f"招聘者{i}", message_text=text)
            for i, text in enumerate(message_texts)
        ]
        self.viewport_size = viewport_size
        self.window = 0
        self.removed: set[str] = set()
        self.on_inbox = on_inbox
        self.open_inbox_ok = open_inbox_ok
        self.mark_disinterest_ok = mark_disinterest_ok
        self.return_to_inbox_ok = return_to_inbox_ok
        self.open_message_ok = open_message_ok
        self.events: list[str] = []

    # --- ChatInboxPage contract -------------------------------------------------
    def is_on_inbox(self, timeout_sec: float = 2.0) -> bool:
        return self.on_inbox

    def open_inbox(self, timeout_sec: float = 5.0) -> bool:
        self.on_inbox = self.open_inbox_ok
        return self.open_inbox_ok

    def extract_visible_messages(self, max_items: int = 10) -> list[ChatInboxMessage]:
        window = self.messages[self.window : self.window + self.viewport_size]
        return [m for m in window if m.key not in self.removed][:max_items]

    def scroll_inbox(self) -> None:
        self.events.append("scroll")
        if self.window < len(self.messages):
            self.window += 1

    def open_message(self, message: ChatInboxMessage) -> bool:
        if not self.open_message_ok:
            return False
        self.events.append(f"open:{message.message_text}")
        return True

    def mark_disinterest(self, reason: str = DISINTEREST_REASON, timeout_sec: float = 3.0) -> bool:
        if not self.mark_disinterest_ok:
            self.events.append("disinterest:failed")
            return False
        self.events.append(f"disinterest:{reason}")
        # The platform drops the conversation from the inbox on success.
        for message in self.messages:
            if message.message_text in self.currently_opened:
                self.removed.add(message.key)
        return True

    def wait_for_inbox_return(self, timeout_sec: float = 5.0) -> bool:
        self.events.append("inbox_return")
        if self.return_to_inbox_ok:
            self.window = 0
            self.messages = [m for m in self.messages if m.key not in self.removed]
        return self.return_to_inbox_ok

    # --- test helpers -----------------------------------------------------------
    @property
    def currently_opened(self) -> set[str]:
        return {
            m.message_text for m in self.messages if f"open:{m.message_text}" in self.events
        }


class FakeChatPage:
    def __init__(self, harness: Harness) -> None:
        self.harness = harness

    def send_message(self, message: str, timeout_sec: float = 5.0) -> bool:
        self.harness.events.append(f"send:{message}")
        return True

    def navigate_back(self, timeout_sec: float = 3.0) -> bool:
        self.harness.events.append("chat_back")
        return True


def make_handler(classifier=None):
    """Handler pinned to default settings so tests never read a developer's local config."""
    return CheckChatHandler(classifier=classifier, settings=ChatAcknowledgmentSettings())


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
        patch.object(check_chat_module, "ChatInboxPage", return_value=harness),
        patch.object(check_chat_module, "ChatPage", return_value=FakeChatPage(harness)),
    ):
        result = await handler.handle(task, broker, context)
    return result, await broker.get_task(task.id)


@pytest.mark.asyncio
async def test_confirmed_rejection_runs_full_acknowledgment_sequence(broker, context):
    """#206: open chat -> send polite reply -> disinterest(重复推荐) -> return to inbox."""
    harness = Harness([REJECTION_TEXT])
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}))

    result, task = await _run_async(broker, context, harness, handler)

    assert result.success is True
    assert harness.events.index(f"open:{REJECTION_TEXT}") < harness.events.index(
        f"send:{DEFAULT_REJECTION_REPLY_TEXT}"
    )
    assert harness.events.index(f"send:{DEFAULT_REJECTION_REPLY_TEXT}") < harness.events.index(
        f"disinterest:{DISINTEREST_REASON}"
    )
    assert harness.events[-1] == "inbox_return"
    assert result.output["acknowledged"] == 1
    assert result.output["rejections"] == 1
    assert any("拒信" in log for log in task.logs)


@pytest.mark.asyncio
async def test_positive_invitation_is_preserved_untouched(broker, context):
    """#206/#205-AC6: a genuine invitation must never be touched or marked disinterested."""
    harness = Harness([INVITATION_TEXT])
    handler = make_handler(classifier=FakeClassifier({INVITATION_TEXT: False}))

    result, _ = await _run_async(broker, context, harness, handler)

    assert "open:" not in "".join(harness.events)
    assert not [e for e in harness.events if e.startswith("send:")]
    assert not [e for e in harness.events if e.startswith("disinterest:")]
    assert result.output["preserved"] == 1
    assert result.output["acknowledged"] == 0


@pytest.mark.asyncio
async def test_dry_run_classifies_without_any_write_action(broker, context):
    """#207: dry_run logs proposed triage but performs zero mobile write actions."""
    harness = Harness([REJECTION_TEXT])
    classifier = FakeClassifier({REJECTION_TEXT: True})
    handler = make_handler(classifier=classifier)

    result, task = await _run_async(
        broker, context, harness, handler, payload={"dry_run": True}
    )

    assert classifier.calls == [("招聘者0", REJECTION_TEXT)]
    assert not [e for e in harness.events if e.startswith(("open:", "send:", "disinterest:"))]
    assert result.output["dry_run"] is True
    assert result.output["rejections"] == 1
    assert result.output["acknowledged"] == 0
    assert any("DRY-RUN" in log for log in task.logs)


@pytest.mark.asyncio
async def test_mixed_inbox_only_acknowledges_rejections(broker, context):
    other_rejection = "岗位已招满，感谢您的关注。"
    harness = Harness([INVITATION_TEXT, REJECTION_TEXT, other_rejection], viewport_size=3)
    handler = CheckChatHandler(
        classifier=FakeClassifier({REJECTION_TEXT: True, other_rejection: True})
    )

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["scanned"] == 3
    assert result.output["preserved"] == 1
    assert result.output["acknowledged"] == 2
    assert f"open:{INVITATION_TEXT}" not in harness.events


@pytest.mark.asyncio
async def test_empty_inbox_stops_without_interaction(broker, context):
    harness = Harness([])
    handler = make_handler(classifier=FakeClassifier())

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.success is True
    assert result.output["scanned"] == 0
    assert result.output["stop_reason"] == "empty_inbox"
    assert harness.events == []


@pytest.mark.asyncio
async def test_all_positive_inbox_terminates_by_bounded_scrolling(broker, context):
    """#207: preserving messages must never stall the loop in an infinite scan."""
    texts = ["方便聊聊吗？", "方便发一下简历吗？", "我们约个面试吧"]
    harness = Harness(texts, viewport_size=2)
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier)

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["scanned"] == 3
    assert result.output["preserved"] == 3
    assert result.output["stop_reason"] in ("stalled", "empty_inbox")
    assert len(classifier.calls) == 3


@pytest.mark.asyncio
async def test_max_scan_depth_bounds_the_scan(broker, context):
    texts = [f"抱歉，暂不匹配 #{i}" for i in range(5)]
    harness = Harness(texts, viewport_size=2)
    classifier = FakeClassifier({t: True for t in texts})
    handler = make_handler(classifier=classifier)

    result, _ = await _run_async(
        broker, context, harness, handler, payload={"max_scan_depth": 2, "dry_run": True}
    )

    assert result.output["scanned"] == 2
    assert result.output["max_scan_depth"] == 2
    assert result.output["stop_reason"] == "max_scan_depth"
    assert len(classifier.calls) == 2


@pytest.mark.asyncio
async def test_visited_keys_prevent_reprocessing_preserved_messages(broker, context):
    """#207: a preserved card stays in the viewport and must not be re-classified."""
    texts = ["方便聊聊吗？", "方便发简历吗？"]
    harness = Harness(texts, viewport_size=2)
    classifier = FakeClassifier(default=False)
    handler = make_handler(classifier=classifier)

    result, _ = await _run_async(broker, context, harness, handler)

    assert len(classifier.calls) == len(texts)
    assert len(result.output["visited_keys"]) == len(texts)
    assert len(set(result.output["visited_keys"])) == len(texts)


@pytest.mark.asyncio
async def test_reply_text_and_scan_depth_come_from_payload(broker, context):
    harness = Harness([REJECTION_TEXT])
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}))

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
async def test_failed_disinterest_is_reported_and_recovers_to_inbox(broker, context):
    harness = Harness([REJECTION_TEXT], mark_disinterest_ok=False)
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}))

    result, task = await _run_async(broker, context, harness, handler)

    assert result.success is True
    assert result.output["failed"] == 1
    assert result.output["acknowledged"] == 0
    assert "chat_back" in harness.events
    assert any("不感兴趣" in log for log in task.logs)


@pytest.mark.asyncio
async def test_missing_driver_fails_fast(broker):
    context = WorkerContext(config=WorkerConfig(worker_id="no-driver"), driver=None)
    handler = make_handler(classifier=FakeClassifier())

    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})
    result = await handler.handle(task, broker, context)

    assert result.success is False
    assert "driver" in (result.error_message or "").lower()


@pytest.mark.asyncio
async def test_unreachable_inbox_fails_the_task(broker, context):
    harness = Harness([REJECTION_TEXT], on_inbox=False, open_inbox_ok=False)
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}))

    result, task = await _run_async(broker, context, harness, handler)

    assert result.success is False
    assert any("新招呼" in log for log in task.logs)


@pytest.mark.asyncio
async def test_llm_failure_preserves_message_conservatively(broker, context):
    """A classifier outage must never cause an acknowledgment to be sent."""
    harness = Harness([REJECTION_TEXT])

    class ExplodingClassifier:
        def classify(self, message_text: str, sender_name: str = "") -> RejectionVerdict:
            return RejectionVerdict(is_rejection=False, rationale="llm down", error="boom")

    handler = make_handler(classifier=ExplodingClassifier())

    result, _ = await _run_async(broker, context, harness, handler)

    assert result.output["preserved"] == 1
    assert result.output["acknowledged"] == 0
    assert not [e for e in harness.events if e.startswith(("open:", "send:", "disinterest:"))]


@pytest.mark.asyncio
async def test_handler_processes_single_item_through_real_inbox_page(broker, context):
    """#206: end-to-end single target resolved from a mock accessibility hierarchy."""
    sender_node = MagicMock(text="李女士")
    text_node = MagicMock(text=REJECTION_TEXT)
    card = MagicMock()
    card.find_elements.side_effect = lambda by, value: (
        [sender_node] if "tv_name" in value else [text_node]
    )

    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    def mock_find(by, value):
        if "view_chat_item" in value:
            return [card]
        if "新招呼" in value or "不感兴趣" in value or "重复推荐" in value:
            return [MagicMock()]
        return []

    driver.find_elements.side_effect = mock_find

    context = WorkerContext(config=WorkerConfig(worker_id="real-page"), driver=driver)
    handler = make_handler(classifier=FakeClassifier({REJECTION_TEXT: True}))

    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})
    chat_double = MagicMock()

    with patch.object(check_chat_module, "ChatPage", return_value=chat_double):
        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["acknowledged"] == 1
    chat_double.send_message.assert_called_once_with(DEFAULT_REJECTION_REPLY_TEXT, timeout_sec=5.0)


def test_handler_declares_check_chat_task_type():
    assert make_handler().task_type == TaskType.CHECK_CHAT
