"""
tests.unit.test_handlers_check_chat
===================================
Unit tests for the CHECK_CHAT handler seam: how a dispatch resolves its settings and
screening policy, composes a Chat Triage run over the injected device world, and maps
the Triage Report into task telemetry (spec #267, tickets #268-#270).

The triage behaviour itself is pinned by ``test_chat_triage.py`` against
``ChatTriage.scan()``; what is left here is the dispatcher.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from _chat_triage_harness import (
    COMPANY,
    DESCRIPTOR,
    REJECTION_TEXT,
    SCROLL_TO_TOP_EVENT,
    FakeClassifier,
    Harness,
    card,
    scripted_pages,
    throwaway_config,
)

from boss_agent.adapters.inbound.worker.config import WorkerConfig
from boss_agent.adapters.inbound.worker.context import WorkerContext
from boss_agent.adapters.inbound.worker.handlers.check_chat import CheckChatHandler
from boss_agent.adapters.memory.task_broker import InMemoryTaskBroker
from boss_agent.adapters.outbound.config.screening_config import (
    ScreeningConfigBlacklistSink,
    load_screening_policy,
)
from boss_agent.adapters.outbound.device.pages.chat_adapters import (
    ChatActorAdapter,
    CommunicationListAdapter,
)
from boss_agent.adapters.outbound.device.pages.chat_bundle import build_triage_world
from boss_agent.adapters.outbound.device.pages.communication import CommunicationListPage
from boss_agent.core.chat.domain.rejection import (
    DEFAULT_REJECTION_REPLY_TEXT,
    ChatAcknowledgmentSettings,
)
from boss_agent.core.chat.interfaces.device import TriagePages
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.task.domain.models import TaskStatus, TaskType


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """A throwaway config file: a real run must never write the repo's own config."""
    return throwaway_config(tmp_path / "settings.local.yaml")


@pytest.fixture
def policy(config_path: Path) -> ScreeningPolicy:
    return load_screening_policy(config_path=config_path)


@pytest.fixture
def broker() -> InMemoryTaskBroker:
    return InMemoryTaskBroker()


@pytest.fixture
def context() -> WorkerContext:
    return WorkerContext(
        config=WorkerConfig(worker_id="test-worker-chat"),
        driver=MagicMock(),
    )


def make_handler(pages=None, classifier=None, policy=None, settings=None) -> CheckChatHandler:
    """Handler pinned to explicit settings/policy so no test reads or writes local config."""
    return CheckChatHandler(
        classifier=classifier or FakeClassifier(),
        blacklist_sink=ScreeningConfigBlacklistSink(),
        settings=settings or ChatAcknowledgmentSettings(),
        policy=policy or ScreeningPolicy(),
        pages=pages or build_triage_world,
    )


async def _run_async(broker, context, harness, handler, payload=None):
    """Execute one CHECK_CHAT task against the harness and return (result, finished_task)."""
    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload=payload or {})
    result = await handler.handle(task, broker, context)
    return result, await broker.get_task(task.id)


@pytest.mark.asyncio
async def test_confirmed_rejection_reaches_the_dashboard_as_task_telemetry(
    broker, context, policy, config_path
):
    """The dispatcher turns the run's report into the output operators already read."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])
    handler = make_handler(
        pages=scripted_pages(harness),
        classifier=FakeClassifier({REJECTION_TEXT: True}),
        policy=policy,
    )

    result, task = await _run_async(broker, context, harness, handler)

    assert result.success is True
    assert result.output["blacklisted_companies"] == [COMPANY]
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["company_blacklist"] == [COMPANY]
    assert any("Finished CHECK_CHAT:" in line for line in task.logs)
    assert any("黑名单" in log for log in task.logs)
    assert any("拒信" in log for log in task.logs)


@pytest.mark.asyncio
async def test_reply_text_and_scan_depth_come_from_payload(broker, context, policy):
    """Malformed overrides degrade to the configured run; valid ones reach the run."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])
    handler = make_handler(
        pages=scripted_pages(harness),
        classifier=FakeClassifier({REJECTION_TEXT: True}),
        policy=policy,
    )

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
async def test_a_cancelled_task_is_reported_as_a_stopped_run(broker, context, policy):
    """Cancellation is observed through the broker the run was composed with."""
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])
    handler = make_handler(
        pages=scripted_pages(harness),
        classifier=FakeClassifier({REJECTION_TEXT: True}),
        policy=policy,
    )

    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})
    await broker.update_task_status(task.id, status=TaskStatus.CANCELLED)

    result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["stop_reason"] == "cancelled"
    assert result.output["scanned"] == 0
    # #388: entering 仅沟通 double-taps 消息 to reset the feed before the cancellation is
    # noticed. Cancellation still costs no card interaction — nothing was opened or
    # closed, and the reset happens exactly once.
    assert harness.events == [SCROLL_TO_TOP_EVENT]


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
    handler = make_handler(
        pages=scripted_pages(harness),
        classifier=FakeClassifier({REJECTION_TEXT: True}),
        policy=policy,
    )

    result, task = await _run_async(broker, context, harness, handler)

    assert result.success is False
    assert result.output is None
    assert any("仅沟通" in log for log in task.logs)
    assert not any("Finished CHECK_CHAT:" in line for line in task.logs)


def test_handler_declares_check_chat_task_type():
    assert make_handler().task_type == TaskType.CHECK_CHAT


@pytest.mark.asyncio
async def test_the_classifier_client_is_resolved_at_the_edge(broker, context, policy, monkeypatch):
    """The classifier no longer reaches for the realm itself; the composition root hands it a client."""
    from boss_agent import main as composition

    harness = Harness([card("方便约个时间聊聊吗？", sender="张先生", descriptor="深至科技 | 后端")])
    llm_client = MagicMock()
    llm_client.chat_completion_json.return_value = {
        "is_rejection": False,
        "confidence": 0.9,
        "rationale": "面试邀约",
    }
    monkeypatch.setattr(composition, "create_llm_client", lambda: llm_client)
    monkeypatch.setattr(composition, "PocketBaseTaskBroker", MagicMock())
    monkeypatch.setattr(composition, "build_triage_world", scripted_pages(harness))
    monkeypatch.setattr(
        composition, "resolve_chat_acknowledgment_settings", ChatAcknowledgmentSettings
    )
    monkeypatch.setattr(composition, "load_screening_policy", lambda: policy)
    handler = composition.build_worker()._handlers[TaskType.CHECK_CHAT]

    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})
    result = await handler.handle(task, broker, context)

    assert result.success is True
    assert llm_client.chat_completion_json.call_count == 1


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

    badge_elem = MagicMock(text="1")

    def mock_find(by, value):
        if "view_chat_item" in value:
            return [card_elem]
        if "tv_count" in value:
            return [badge_elem] if badge_elem.text else []
        if "仅沟通" in value or "不感兴趣" in value or "重复推荐" in value:
            return [MagicMock(text="仅沟通")]
        if "tv_tab_3" in value or "tab_3" in value or "消息" in value:
            return [MagicMock(text="消息")]
        return []

    driver.find_elements.side_effect = mock_find

    context = WorkerContext(config=WorkerConfig(worker_id="real-page"), driver=driver)
    chat_double = MagicMock()

    def _send_and_clear(*a, **k):
        badge_elem.text = ""
        return True

    chat_double.send_message.side_effect = _send_and_clear
    # Only the chat is scripted: the 仅沟通 list stays the real page object reading
    # the mock hierarchy above.
    pages = lambda driver: TriagePages(  # noqa: E731 - a one-line injected seam
        list_reader=CommunicationListAdapter(CommunicationListPage(driver)),
        chat_actor=ChatActorAdapter(chat_double),
    )
    handler = make_handler(
        pages=pages, classifier=FakeClassifier({REJECTION_TEXT: True}), policy=policy
    )

    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})
    result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["blacklisted_companies"] == [COMPANY]
    assert result.output["acknowledged"] == 1
    chat_double.send_message.assert_called_once_with(DEFAULT_REJECTION_REPLY_TEXT, timeout_sec=5.0)
