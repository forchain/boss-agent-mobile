"""
tests/unit/test_depth_single_intent.py
======================================
One depth expression, and every payload shape that is still in somebody's queue (#302).

PR #297's root cause was structural rather than incidental: the send gate needed
``auto_send`` *and* ``not preview_only``, so a caller that wrote one key and forgot the
other produced a task that looked perfectly valid and drafted instead of greeting. #298 took
the choice away from the callers; this ticket takes the second key away from the wire.

The migration rule is what makes that safe. Depth is read in exactly one of three ways, and
a run says which:

* a payload that states neither legacy key — the shape the builders produce now — follows its
  ``target_action``, the single declared intent;
* a payload that states *both* legacy keys is read by the gate its writer targeted, including
  the combinations that contradict each other, because those exist in real queues;
* a payload that states *exactly one* legacy key is refused rather than defaulted. That shape
  is the original bug, and the only honest answer is an error the operator can read.
"""

import json

import pytest

from boss_agent.feed_pipeline import (
    DEPTH_DECLARED,
    DEPTH_LEGACY_PAIR,
    DEPTH_UNSTATED,
    FeedContractError,
    FeedStreamConfig,
)
from boss_agent.models import FilterConfig, SavedSearch, SearchConfig
from boss_agent.task_launch import (
    DirectApplyTarget,
    LaunchContractError,
    LaunchSource,
    TaskKind,
    build_launch,
)

#: Every key a payload has ever used to answer the depth question, across every builder
#: this repo has shipped. A new payload must answer it exactly once.
ALL_DEPTH_KEYS = frozenset(
    {"target_action", "action", "preview_only", "auto_send", "send_greeting", "dry_run", "mode"}
)


def _depth_keys(payload: dict) -> set[str]:
    """The depth keys a payload actually states."""
    return set(payload) & ALL_DEPTH_KEYS


#: The reported run, verbatim out of the State Stream Broker (task `68pxw2b0dijvn01`,
#: strategy `[测试]打招呼`). It is the shape a *currently queued* task can still have, and
#: the one this change must not reinterpret: drafted, never sent.
REPORTED_TASK_PAYLOAD = {
    "auto_send": False,
    "enable_filter": False,
    "enable_search": True,
    "filter": {"company_scales": [], "industries": []},
    "keyword": "Agent",
    "max_jobs": 10,
    "min_score": 70,
    "preview_only": True,
    "preview_timeout_sec": 3,
    "rerun_of": "hgvqylbc80myb8m",
    "saved_search_id": "o0u4647gut54gh0",
    "search_id": "o0u4647gut54gh0",
    "search_name": "[测试]打招呼",
    "target_action": "auto_apply",
}

#: The 定向投递 payload as the jobs page hand-built it before PR #297: no `target_action`, no
#: depth key at all. It has to keep doing what it always did — draft, don't send — even
#: though the handler names the run AUTO_APPLY once it is claimed.
LEGACY_DIRECT_APPLY_PAYLOAD = {
    "keyword": "AI Agent 平台工程师",
    "direct_job_id": "rec-9",
    "greeting_message": "李工您好，我在面板里改过这版。",
    "company_name": "智元创新",
    "job_title": "AI Agent 平台工程师",
    "candidate_profile": {"name": "周黄金"},
}


def test_a_new_payload_states_its_depth_once():
    """No `preview_only`, no `auto_send`, no mode: `target_action` is the whole statement."""
    search = SavedSearch(
        id="s",
        name="自动沟通",
        search=SearchConfig(keyword="agent"),
        filter=FilterConfig(),
        target_action="auto_apply",
    )
    payload = build_launch(TaskKind.SEARCH, source=LaunchSource.MANUAL, search=search).payload

    # Exactly one key answers "does this run put a greeting on the wire".
    assert _depth_keys(payload) == {"target_action"}

    config = FeedStreamConfig.from_payload(payload)
    assert config.send_greeting is True
    assert config.depth_expression == DEPTH_DECLARED

    save_only = build_launch(
        TaskKind.SEARCH,
        source=LaunchSource.SCHEDULER,
        search=SavedSearch(id="s2", name="存JD", search=SearchConfig(keyword="agent"),
                           target_action="save_jd"),
    ).payload
    assert FeedStreamConfig.from_payload(save_only).send_greeting is False


def test_a_targeted_application_states_its_depth_once_too():
    payload = build_launch(
        TaskKind.DIRECT_APPLY,
        source=LaunchSource.MANUAL,
        job=DirectApplyTarget(job_id="rec-1", title="AI Agent 平台工程师", company_name="智元创新"),
    ).payload
    assert _depth_keys(payload) == {"target_action"}
    assert payload["target_action"] == "auto_apply"

    config = FeedStreamConfig.from_payload(payload)
    assert config.send_greeting is True
    assert config.direct_greeting == ""


@pytest.mark.parametrize(
    ("preview_only", "auto_send", "sends"),
    [
        pytest.param(True, False, False, id="draft-only"),
        pytest.param(False, True, True, id="live"),
        pytest.param(True, True, False, id="both-true"),
        pytest.param(False, False, False, id="both-false"),
    ],
)
def test_a_queued_legacy_pair_is_still_read_the_way_its_writer_meant(
    preview_only, auto_send, sends
):
    """Two keys, one answer, and no reinterpretation of anything already in the queue."""
    config = FeedStreamConfig.from_payload(
        {
            "target_action": "auto_apply",
            "keyword": "Agent",
            "preview_only": preview_only,
            "auto_send": auto_send,
        }
    )
    assert config.send_greeting is sends
    assert config.depth_expression == DEPTH_LEGACY_PAIR


def test_the_reported_task_payload_is_read_exactly_as_before():
    """The real queued row, byte for byte: still a draft, never silently promoted to a send."""
    config = FeedStreamConfig.from_payload(json.loads(json.dumps(REPORTED_TASK_PAYLOAD)))

    assert config.send_greeting is False
    assert config.depth_expression == DEPTH_LEGACY_PAIR
    assert config.keyword == "Agent"
    assert config.max_jobs == 10
    assert config.min_score == 70.0


def test_a_payload_that_states_no_depth_at_all_keeps_its_old_default():
    """The pre-#297 定向投递 shape stated nothing, and the default was: do not send.

    The handler names the run AUTO_APPLY after parsing, so the parse-time answer is what
    protects a queued task from becoming a message nobody approved.
    """
    config = FeedStreamConfig.from_payload(dict(LEGACY_DIRECT_APPLY_PAYLOAD))

    assert config.send_greeting is False
    assert config.depth_expression == DEPTH_UNSTATED
    assert config.single_screen is True
    assert config.direct_greeting == "李工您好，我在面板里改过这版。"


@pytest.mark.parametrize("half", [{"auto_send": True}, {"preview_only": False}])
def test_half_a_pair_is_refused_instead_of_defaulted(half):
    """The defect class, made structurally unreachable.

    Before this change `{"auto_send": true}` silently became `preview_only=True` — a draft.
    A payload can no longer be half-written: the worker says so instead of guessing.
    """
    payload = {"target_action": "auto_apply", "keyword": "Agent", **half}
    with pytest.raises(FeedContractError, match="without"):
        FeedStreamConfig.from_payload(payload)


def test_a_producer_cannot_write_the_pair_by_hand():
    """The refusal is on the producing side too, in both directions of "wrong"."""
    search = SavedSearch(
        id="s", name="自动沟通", search=SearchConfig(keyword="agent"), target_action="auto_apply"
    )

    with pytest.raises(LaunchContractError, match="preview_only, auto_send"):
        build_launch(
            TaskKind.SEARCH,
            source=LaunchSource.MANUAL,
            search=search,
            preview_only=False,
            auto_send=True,
        )
    with pytest.raises(LaunchContractError, match="auto_send"):
        build_launch(
            TaskKind.SEARCH, source=LaunchSource.MANUAL, search=search, auto_send=True
        )
    with pytest.raises(LaunchContractError, match="preview_only"):
        build_launch(
            TaskKind.DIRECT_APPLY,
            source=LaunchSource.MANUAL,
            job=DirectApplyTarget(job_id="rec-1"),
            preview_only=True,
        )
    with pytest.raises(LaunchContractError, match="preview_only"):
        build_launch(
            TaskKind.CHAT_CLEANUP,
            source=LaunchSource.MANUAL,
            preview_only=True,
        )


def test_a_cleanup_drill_is_stated_as_a_mode_and_not_as_a_depth():
    """The one surviving second switch is the inbox drill, and it stays a single expression."""
    launch = build_launch(TaskKind.CHAT_CLEANUP, source=LaunchSource.SCHEDULER, mode="draft")
    assert launch.payload["dry_run"] is True
    assert "preview_only" not in launch.payload
    assert "auto_send" not in launch.payload
