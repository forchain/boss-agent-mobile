"""
tests.unit.test_check_chat_telemetry
====================================
The dashboard's contract around one CHECK_CHAT dispatch (spec #267, ticket #270).

Structured result, human-readable summary line and the absence of scan vocabulary in
the dispatcher are all pinned here, against the in-memory broker, so no future
refactor can silently change what an operator sees on the Task Management Dashboard.
The behaviour behind these numbers is pinned by ``test_chat_triage.py``.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _chat_triage_harness import (
    DESCRIPTOR,
    INVITATION_TEXT,
    REJECTION_TEXT,
    FakeClassifier,
    Harness,
    card,
    scripted_pages,
    throwaway_config,
)

from boss_agent.broker.models import TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import ScreeningPolicy
from boss_agent.rejection import ChatAcknowledgmentSettings
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers import check_chat as check_chat_module
from boss_agent.worker.handlers.check_chat import CheckChatHandler

#: Every key the dashboard reads off a finished CHECK_CHAT task. Pinned as a literal
#: set: a key added, dropped or renamed must fail this suite rather than reach the
#: dashboard's triage tab unnoticed.
TELEMETRY_KEYS = frozenset(
    {
        "dry_run",
        "reply_text",
        "max_scan_depth",
        "scanned",
        "evaluated",
        "skipped_outbound",
        "rejections",
        "blacklisted",
        "blacklisted_companies",
        "guardrail_blocked",
        "acknowledged",
        "preserved",
        "failed",
        "stop_reason",
        "visited_keys",
    }
)

OUTBOUND_TEXT = "我对这个岗位很感兴趣，期待您的回复~"
OTHER_REJECTION_TEXT = "岗位已招满，感谢您的关注。"


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """A throwaway config file: a real run must never write the repo's own config."""
    return throwaway_config(tmp_path / "settings.local.yaml")


@pytest.fixture
def context() -> WorkerContext:
    return WorkerContext(config=WorkerConfig(worker_id="telemetry"), driver=MagicMock())


def make_handler(harness: Harness, policy: ScreeningPolicy, settings=None) -> CheckChatHandler:
    return CheckChatHandler(
        classifier=FakeClassifier({REJECTION_TEXT: True, OTHER_REJECTION_TEXT: True}),
        settings=settings or ChatAcknowledgmentSettings(),
        policy=policy,
        pages=scripted_pages(harness),
    )


def summary_line(task) -> str:
    """The run's summary as it renders on the dashboard, without the log timestamp."""
    line = next(line for line in task.logs if "Finished CHECK_CHAT:" in line)
    return line.split("] ", 1)[-1]


@pytest.mark.asyncio
async def test_a_full_pass_pins_every_output_key_and_value(context, config_path):
    """One run over a mixed screen: every counter and every key, frozen."""
    policy = ScreeningPolicy.load_default(config_path=config_path)
    harness = Harness(
        [
            card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR),
            card(OUTBOUND_TEXT, sender="巫女士", status="[送达]"),
            card(INVITATION_TEXT, sender="张先生", descriptor="深至科技 | 后端"),
            card(
                OTHER_REJECTION_TEXT,
                sender="HR",
                descriptor="某中型人工智能公司 | 算法",
            ),
        ],
        viewport_size=4,
    )
    broker = InMemoryTaskBroker()
    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})

    result = await make_handler(harness, policy).handle(task, broker, context)

    assert result.success is True
    assert set(result.output) == TELEMETRY_KEYS
    # Every value, except the opaque card keys, which are pinned by count below.
    assert {k: v for k, v in result.output.items() if k != "visited_keys"} == {
        "dry_run": False,
        "reply_text": "收到 谢谢",
        "max_scan_depth": 30,
        "scanned": 4,
        "evaluated": 3,
        "skipped_outbound": 1,
        "rejections": 2,
        "blacklisted": 1,
        "blacklisted_companies": ["传音控股"],
        "guardrail_blocked": 1,
        "acknowledged": 2,
        "preserved": 1,
        "failed": 0,
        "stop_reason": "first_screen_exhausted",
    }
    assert len(result.output["visited_keys"]) == 4


@pytest.mark.asyncio
async def test_a_full_pass_pins_the_summary_line(context, config_path):
    """The line the dashboard renders verbatim, down to its wording and ordering."""
    policy = ScreeningPolicy.load_default(config_path=config_path)
    harness = Harness(
        [
            card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR),
            card(OUTBOUND_TEXT, sender="巫女士", status="[送达]"),
            card(INVITATION_TEXT, sender="张先生", descriptor="深至科技 | 后端"),
            card(
                OTHER_REJECTION_TEXT,
                sender="HR",
                descriptor="某中型人工智能公司 | 算法",
            ),
        ],
        viewport_size=4,
    )
    broker = InMemoryTaskBroker()
    finished = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={})

    await make_handler(harness, policy).handle(finished, broker, context)

    assert summary_line(await broker.get_task(finished.id)) == (
        "Finished CHECK_CHAT: scanned 4 card(s), evaluated 3 message(s), "
        "1 skipped as outbound, 2 rejection(s) detected, "
        "1 company(ies) blacklisted [传音控股], 1 blocked by guardrails, "
        "2 acknowledged, 1 preserved, 0 failed "
        "(stop_reason=first_screen_exhausted, dry_run=False)"
    )


@pytest.mark.asyncio
async def test_a_dry_run_reports_a_rehearsal_and_never_a_write(context, config_path):
    """演练 keeps every telemetry key, and says so in the summary."""
    before = config_path.read_text(encoding="utf-8")
    policy = ScreeningPolicy.load_default(config_path=config_path)
    harness = Harness([card(REJECTION_TEXT, sender="严胜", descriptor=DESCRIPTOR)])
    broker = InMemoryTaskBroker()
    task = await broker.create_task(task_type=TaskType.CHECK_CHAT, payload={"dry_run": True})

    result = await make_handler(harness, policy).handle(task, broker, context)

    assert set(result.output) == TELEMETRY_KEYS
    assert {k: v for k, v in result.output.items() if k != "visited_keys"} == {
        "dry_run": True,
        "reply_text": "收到 谢谢",
        "max_scan_depth": 30,
        "scanned": 1,
        "evaluated": 1,
        "skipped_outbound": 0,
        "rejections": 1,
        "blacklisted": 0,
        "blacklisted_companies": [],
        "guardrail_blocked": 0,
        "acknowledged": 0,
        "preserved": 0,
        "failed": 0,
        "stop_reason": "first_screen_exhausted",
    }
    assert config_path.read_text(encoding="utf-8") == before
    assert summary_line(await broker.get_task(task.id)) == (
        "Finished CHECK_CHAT: scanned 1 card(s), evaluated 1 message(s), "
        "0 skipped as outbound, 1 rejection(s) detected, "
        "0 company(ies) blacklisted, 0 blocked by guardrails, "
        "0 acknowledged, 0 preserved, 0 failed "
        "(stop_reason=first_screen_exhausted, dry_run=True)"
    )


def test_the_dispatcher_keeps_no_scan_vocabulary():
    """The scan's constants and per-card helpers all live with the Chat Triage module."""
    leftovers = [
        name
        for name in (
            "VIEWPORT_SIZE",
            "MAX_INSPECTED_CARDS",
            "PREVIEW_CHARS",
            "FIRST_SCREEN_EXHAUSTED",
            "TriageKind",
            "TriageOutcome",
            "_preview",
        )
        if hasattr(check_chat_module, name)
    ]

    assert leftovers == []
