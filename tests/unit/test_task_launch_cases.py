"""
tests/unit/test_task_launch_cases.py
====================================
The AutomationTask launch contract — the Python half.

``config/task_launch.cases.json`` is consumed by this tier *and* by
``web/src/tests/taskLaunchCases.test.ts``. Neither builder owns the contract; the case
table does, including the one-per-payload rule for how a run states its depth (#302). The divergences it pins were real: ``min_score`` was 75 in the launch
modal, 70 in the scheduler and absent from "run scheduled now"; the scheduler sent
``preview_only=False`` where every web builder sent ``True``, so the same SavedSearch
produced inverted execution depth depending on who dispatched it.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from boss_agent import task_launch
from boss_agent.enums import TargetAction
from boss_agent.feed_pipeline import FeedStreamConfig
from boss_agent.rejection import ChatAcknowledgmentSettings
from boss_agent.search_entities import FilterConfig, SavedSearch, SearchConfig

FIXTURE_PATH = Path(__file__).parents[2] / "config" / "task_launch.cases.json"


def _fixture() -> dict:
    assert FIXTURE_PATH.is_file(), (
        f"{FIXTURE_PATH} is the cross-language launch contract pin; both the pytest and "
        "vitest tiers read it."
    )
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


CASES = _fixture()["cases"]


#: Every key any builder of this repo has ever used to answer "does this run send?".
#: A new payload answers it exactly once, with `target_action` (issue #302) — and the rule
#: is checked by *both* language tiers against this same fixture, because "the builders
#: agree" is only evidence while it is measured.
ALL_DEPTH_KEYS = frozenset(
    {"target_action", "action", "preview_only", "auto_send", "send_greeting", "dry_run", "mode"}
)


def assert_depth_stated_once(case: dict, payload: dict) -> None:
    """The one shared payload shape's depth invariant: a single expression, no legacy pair."""
    if case["kind"] not in ("search", "direct_apply"):
        return
    stated = set(payload) & ALL_DEPTH_KEYS
    assert stated == {"target_action"}, f"{case['case']} states depth as {sorted(stated)}"


def _saved_search(spec: dict | None) -> SavedSearch | None:
    if spec is None:
        return None
    return SavedSearch(
        id=spec["id"],
        name=spec["name"],
        search=SearchConfig(keyword=spec["keyword"]),
        filter=FilterConfig(),
        enable_search=spec["enable_search"],
        enable_filter=spec["enable_filter"],
        target_action=spec["target_action"],
        max_jobs=spec["max_jobs"],
    )


def _direct_apply_target(spec: dict | None) -> task_launch.DirectApplyTarget | None:
    if spec is None:
        return None
    return task_launch.DirectApplyTarget(
        job_id=spec["job_id"],
        title=spec["title"],
        company_name=spec["company_name"],
        greeting_message=spec["greeting_message"],
    )


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_python_builder_matches_the_shared_case(case: dict) -> None:
    """Every shipped case is one the Python builder must reproduce exactly."""
    kwargs = {
        "kind": task_launch.TaskKind(case["kind"]),
        "source": task_launch.LaunchSource(case["source"]),
        "search": _saved_search(case["search"]),
        "mode": None if case["mode"] is None else task_launch.LaunchMode(case["mode"]),
        "job": _direct_apply_target(case.get("job")),
    }
    if case["min_score"] is not None:
        kwargs["min_score"] = case["min_score"]
    # A case can hand the builder depth keys a caller is not allowed to author. That is the
    # declaration issue #302 removes from the wire, so it arrives here as an extra kwarg
    # rather than as a payload field.
    kwargs.update(case.get("depth_keys") or {})

    ack = ChatAcknowledgmentSettings(**(case["chat"] or {}))
    if case.get("expect_error"):
        # A case may state something the contract must refuse instead of a payload to build.
        with (
            patch("boss_agent.task_launch.resolve_chat_acknowledgment_settings", return_value=ack),
            pytest.raises(task_launch.LaunchContractError),
        ):
            task_launch.build_launch(**kwargs)
        return

    with patch("boss_agent.task_launch.resolve_chat_acknowledgment_settings", return_value=ack):
        launch = task_launch.build_launch(**kwargs)

    assert launch.task_type.value == case["expected"]["task_type"]
    # Provenance is a task attribute, not a payload key.
    assert launch.source.value == case["expected"]["source"]
    for key in case["contract"]:
        assert launch.payload.get(key) == case["expected"]["payload"][key], key
    assert_depth_stated_once(case, launch.payload)


def test_the_declared_defaults_are_the_shared_ones() -> None:
    defaults = _fixture()["defaults"]
    assert defaults["min_score"] == task_launch.MIN_SCORE
    assert defaults["max_jobs"] == task_launch.DEFAULT_MAX_JOBS
    assert defaults["preview_timeout_sec"] == task_launch.DEFAULT_PREVIEW_TIMEOUT_SEC
    assert defaults["direct_apply_min_score"] == task_launch.DIRECT_APPLY_MIN_SCORE


def test_the_job_ceiling_is_the_schemas_declared_default() -> None:
    """The launcher aliases the schema's default instead of restating the number.

    The fixture pins the launcher to itself; this pins it to the declaration, which is
    what stops a second literal from drifting the way 20-vs-30 once did. Exercise the
    fallback branch, where `DEFAULT_MAX_JOBS` is what actually reaches the payload.
    """
    from boss_agent.broker.collection_schema import SAVED_SEARCH_MAX_JOBS

    assert task_launch.DEFAULT_MAX_JOBS == SAVED_SEARCH_MAX_JOBS

    search = SavedSearch(
        id="s",
        name="策略",
        search=SearchConfig(keyword="agent"),
        filter=FilterConfig(),
        max_jobs=0,  # no ceiling of its own, so the baseline applies
    )
    launch = task_launch.build_search_launch(search, source=task_launch.LaunchSource.MANUAL)
    assert launch.payload["max_jobs"] == SAVED_SEARCH_MAX_JOBS


def test_a_scheduled_and_a_manual_launch_of_one_search_are_the_same_task() -> None:
    """The core promise: "the same SavedSearch" has exactly one meaning.

    The scheduler's own builder sent `preview_only=False` where every web builder sent
    `True`; this is the assertion that keeps that from coming back.
    """
    search = SavedSearch(
        id="s",
        name="策略",
        search=SearchConfig(keyword="agent"),
        filter=FilterConfig(),
        target_action="auto_apply",
    )
    manual = task_launch.build_search_launch(search, source=task_launch.LaunchSource.MANUAL)
    scheduled = task_launch.build_search_launch(search, source=task_launch.LaunchSource.SCHEDULER)

    assert manual.payload == scheduled.payload
    assert manual.task_type == scheduled.task_type
    # Only provenance differs, and it is an attribute rather than a payload key.
    assert manual.source is not scheduled.source


def test_a_malformed_target_action_is_rejected_not_defaulted() -> None:
    """A typo used to fall through to SAVE_JD, quietly turning a greeting run into a save."""
    search = SavedSearch(id="s", search=SearchConfig(keyword="agent"), filter=FilterConfig())
    search.target_action = "auto_appply"

    with pytest.raises(task_launch.LaunchContractError, match="auto_appply"):
        task_launch.build_search_launch(search, source=task_launch.LaunchSource.MANUAL)


def test_a_search_launch_without_a_search_is_refused() -> None:
    with pytest.raises(task_launch.LaunchContractError, match="requires a SavedSearch"):
        task_launch.build_launch(
            task_launch.TaskKind.SEARCH, source=task_launch.LaunchSource.MANUAL
        )


def test_the_min_score_baseline_is_not_a_competing_default() -> None:
    """A caller may choose a stricter threshold; the baseline is what everyone else gets."""
    search = SavedSearch(id="s", search=SearchConfig(keyword="agent"), filter=FilterConfig())

    assert (
        task_launch.build_search_launch(search, source=task_launch.LaunchSource.MANUAL).payload[
            "min_score"
        ]
        == task_launch.MIN_SCORE
    )
    assert (
        task_launch.build_search_launch(
            search, source=task_launch.LaunchSource.MANUAL, min_score=85
        ).payload["min_score"]
        == 85
    )


def test_the_login_probe_carries_provenance_and_nothing_else() -> None:
    """Its handler reads no payload, and the `mode: diagnostic` key had no reader."""
    launch = task_launch.build_login_diagnostic_launch(source=task_launch.LaunchSource.TEST)
    assert launch.payload == {}
    assert launch.source is task_launch.LaunchSource.TEST


def test_legacy_tasks_without_provenance_are_treated_as_manual() -> None:
    """Reclamation must not cancel a task whose origin it cannot prove."""
    assert task_launch.coerce_source(None) is task_launch.LaunchSource.MANUAL
    assert task_launch.coerce_source("nonsense") is task_launch.LaunchSource.MANUAL
    assert task_launch.coerce_source("test") is task_launch.LaunchSource.TEST


def test_an_auto_apply_strategy_launch_reaches_the_dispatch_gate() -> None:
    """The reported bug: a 自动沟通 run drafted a greeting and never sent one.

    The pipeline dispatches only on `auto_send and not preview_only`, so a payload that
    does not satisfy that gate is not an outreach run at all, whatever its strategy
    declares. Read every launch back through the worker's own parser, because the defect
    lived in the gap between the two: the builder wrote a depth no dispatch path could
    reach, and the worker reported it as `Preview Draft Only (Safe Mode)`.
    """
    search = SavedSearch(
        id="s",
        name="自动沟通",
        search=SearchConfig(keyword="agent"),
        filter=FilterConfig(),
        target_action="auto_apply",
    )

    for source in (task_launch.LaunchSource.MANUAL, task_launch.LaunchSource.SCHEDULER):
        payload = task_launch.build_search_launch(search, source=source).payload
        config = FeedStreamConfig.from_payload(payload)
        assert config.target_action is TargetAction.AUTO_APPLY
        # Read back through the worker's own parser, which is now the only place the depth
        # question is answered at all (issue #302).
        assert config.send_greeting, source
        assert config.depth_expression == "declared_target_action", source

    # And a caller cannot buy the old preview depth back. Since issue #298 the only switch
    # left on an outreach run is its Target Action, so declaring a mode is refused outright.
    with pytest.raises(task_launch.LaunchContractError, match="does not take a launch mode"):
        task_launch.build_search_launch(
            search, source=task_launch.LaunchSource.MANUAL, mode=task_launch.LaunchMode.DRAFT
        )


def test_a_directed_application_sends_and_is_not_vetoed_by_the_score_gate() -> None:
    """The human already chose this posting, so neither depth nor threshold may default.

    The 定向投递 payload used to state neither, which left the worker to draft silently
    and let a 70-point threshold refuse a job the operator had just clicked.
    """
    payload = task_launch.build_direct_apply_launch(
        task_launch.DirectApplyTarget(job_id="j1", title="AI Agent 工程师", company_name="煦象"),
        source=task_launch.LaunchSource.MANUAL,
    ).payload
    config = FeedStreamConfig.from_payload(payload)

    assert config.single_screen and config.direct_job_id == "j1"
    assert config.send_greeting
    assert config.min_score == task_launch.DIRECT_APPLY_MIN_SCORE
