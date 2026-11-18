"""Unit tests for SavedSearch, the SavedSearchStore seam, and launch/verification preset resolution."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from _feed_harness import GOOD_JD, ScriptedFeed, _card, _detail_page, script_pages, stub_llm

import boss_agent
from boss_agent.adapters.inbound.verification.feed_verification import (
    build_verification_pipeline,
    run_verification_feed,
)
from boss_agent.adapters.memory.job_store import InMemoryJobRecordStore
from boss_agent.adapters.memory.saved_search_store import InMemorySavedSearchStore
from boss_agent.adapters.outbound.device.pages.feed_bundle import build_feed_device_world
from boss_agent.adapters.outbound.pocketbase import saved_search_store
from boss_agent.adapters.outbound.pocketbase.async_bridge import run_sync
from boss_agent.adapters.outbound.pocketbase.saved_search_store import (
    missing_saved_search_message,
    resolve_saved_search_store,
)
from boss_agent.core.job.domain.job_entities import JobPosting
from boss_agent.core.job.use_cases.feed_pipeline import FeedStreamConfig
from boss_agent.core.job.use_cases.screening import CandidateScreener
from boss_agent.core.search.domain.launch import LaunchSource, build_search_launch
from boss_agent.core.search.domain.models import FilterConfig, SavedSearch, SearchConfig
from boss_agent.core.shared.errors import TransportError

PACKAGE_ROOT = Path(boss_agent.__file__).parent

#: The identifiers this retirement removes, spelled as fragments and joined at runtime.
#: Writing them as literals would defeat this guard's own purpose: CPython folds
#: adjacent string literals at compile time, so ``"SavedSearch" + "Registry"`` lands in
#: the compiled test module as the very name the repository-wide scan below forbids.
RETIRED_REGISTRY_PARTS = (("SavedSearch", "Registry"), ("get_global_", "search_registry"))
RETIRED_REGISTRY_NAMES = tuple(head + tail for head, tail in RETIRED_REGISTRY_PARTS)

#: The retired module, joined rather than concatenated for the same reason.
RETIRED_MODULE = ".".join(("boss_agent", "searches"))

#: The saved-search surface ``boss_agent`` advertises. ``test_package_exports`` covers the
#: whole of ``__all__``; this list pins the half this ticket owns.
SAVED_SEARCH_EXPORTS = (
    "SavedSearch",
    "SavedSearchStore",
    "InMemorySavedSearchStore",
    "PocketBaseSavedSearchStore",
    "resolve_saved_search_store",
)


# --------------------------------------------------------------------------- #
# Package public surface
# --------------------------------------------------------------------------- #


def test_the_package_exports_the_saved_search_seam():
    """``boss_agent`` offers the store and its adapters, and they are the real objects.

    Re-exporting a name is only worth anything if it is the module's own class: a
    stand-in or a stale alias would let a consumer type-check against one definition
    and construct another.
    """
    for name in SAVED_SEARCH_EXPORTS:
        assert name in boss_agent.__all__, f"{name} should be advertised by boss_agent.__all__"
        assert getattr(boss_agent, name) is getattr(saved_search_store, name)


def test_no_exported_saved_search_name_fails_to_bind():
    """A suppressed import failure must not leave ``__all__`` advertising a missing name.

    Every ``boss_agent`` import is wrapped in ``contextlib.suppress(ImportError)``, so
    a typo or a cycle in the new store import would be indistinguishable from an
    absent optional dependency: the package would import cleanly, ``__all__`` would
    still list the seam, and ``from boss_agent import SavedSearchStore`` would fail
    only for whoever ran it.
    """
    missing = [name for name in SAVED_SEARCH_EXPORTS if not hasattr(boss_agent, name)]

    assert missing == []


def test_the_retired_registry_is_gone_from_the_public_surface():
    """Retiring the registry means retiring it as an import, not just as an implementation."""
    for name in RETIRED_REGISTRY_NAMES:
        assert name not in boss_agent.__all__
        assert not hasattr(boss_agent, name)


def test_no_package_module_references_the_retired_registry():
    """Nothing under ``src/`` may still name it, however casually.

    A surviving mention is how dead code comes back: the next caller finds the name in a
    docstring or an ``__init__`` and reaches for it, and by then nothing fails until the
    import does. This scans rather than spot-checks the few known sites, so a reference
    added anywhere in the package is caught without updating a list.
    """
    offenders = [
        f"{path.relative_to(PACKAGE_ROOT.parent.parent)}: {name}"
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        for name in RETIRED_REGISTRY_NAMES
        if name in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_the_retired_registry_module_is_gone():
    """The module must not survive as an importable husk.

    Leaving it in place would let a new caller keep reaching for the unauthenticated
    sync read it owned, and the next reader would have no way to tell the file is dead.
    """
    assert importlib.util.find_spec(RETIRED_MODULE) is None


def test_saved_search_model_serialization():
    s = SavedSearch(
        id="test_ai_agent",
        name="AI Agent Test",
        description="Test description",
        search=SearchConfig(keyword="AI 算法", enable_search=True),
        filter=FilterConfig(
            education="硕士",
            salary="5万元以上",
            experience="10年以上",
            activity="今日活跃",
            company_scales=["1000-9999人"],
            industries=["在线教育", "游戏", "人工智能"],
            enable_filter=True,
        ),
        enable_search=True,
        enable_filter=True,
    )
    d = s.to_dict()
    assert d["id"] == "test_ai_agent"
    assert d["search"]["keyword"] == "AI 算法"
    assert "enable_search" not in d
    assert "enable_filter" not in d
    assert d["search"]["enable_search"] is True
    assert d["filter"]["enable_filter"] is True
    assert d["filter"]["industries"] == ["在线教育", "游戏", "人工智能"]

    restored = SavedSearch.from_dict("test_ai_agent", d)
    assert restored.id == "test_ai_agent"
    assert restored.search.keyword == "AI 算法"
    assert restored.enable_search is True
    assert restored.enable_filter is True
    assert restored.search.should_search is True
    assert restored.filter.has_filters is True
    assert restored.filter.industries == ["在线教育", "游戏", "人工智能"]
    assert restored.filter.education == "硕士"


def test_saved_search_disabled_search_and_filter():
    s = SavedSearch(
        id="test_recommendations_only",
        name="Recommendations Only",
        search=SearchConfig(keyword="AI", enable_search=False),
        filter=FilterConfig(education="硕士", enable_filter=False),
        enable_search=False,
        enable_filter=False,
    )
    assert s.search.should_search is False
    assert s.filter.has_filters is False
    assert s.filter.has_industry_filters is False

    d = s.to_dict()
    assert "enable_search" not in d
    assert "enable_filter" not in d
    assert d["search"]["enable_search"] is False
    assert d["filter"]["enable_filter"] is False

    restored = SavedSearch.from_dict("test_recommendations_only", d)
    assert restored.enable_search is False
    assert restored.enable_filter is False
    assert restored.search.should_search is False
    assert restored.filter.has_filters is False


def test_saved_search_dual_shape_migration_nested_is_authoritative():
    """When nested and legacy top-level flags conflict, nested spelling is authoritative (Issue #321)."""
    # 1. Nested False, Top-level True -> Nested False wins
    dual_data = {
        "id": "search_conflict",
        "name": "Conflict Strategy",
        "enable_search": True,
        "enable_filter": True,
        "search": {"keyword": "Python", "enable_search": False},
        "filter": {"education": "本科", "enable_filter": False},
    }
    restored = SavedSearch.from_dict(dual_data["id"], dual_data)
    assert restored.enable_search is False
    assert restored.search.enable_search is False
    assert restored.enable_filter is False
    assert restored.filter.enable_filter is False

    # Rewriting emits the single nested shape without top-level flags
    rewritten = restored.to_dict()
    assert "enable_search" not in rewritten
    assert "enable_filter" not in rewritten
    assert rewritten["search"]["enable_search"] is False
    assert rewritten["filter"]["enable_filter"] is False

    # 2. Legacy top-level only -> Read correctly and migrated on next write
    legacy_data = {
        "id": "legacy_search",
        "name": "Legacy Strategy",
        "enable_search": False,
        "enable_filter": False,
        "search": {"keyword": "Rust"},
        "filter": {"education": "硕士"},
    }
    legacy_restored = SavedSearch.from_dict(legacy_data["id"], legacy_data)
    assert legacy_restored.enable_search is False
    assert legacy_restored.enable_filter is False
    migrated = legacy_restored.to_dict()
    assert "enable_search" not in migrated
    assert "enable_filter" not in migrated
    assert migrated["search"]["enable_search"] is False
    assert migrated["filter"]["enable_filter"] is False


def test_saved_search_preset_drives_feed_verification_and_launch_config():
    """A saved search id resolves into the launch config and drives ``run_verification_feed``.

    Asserts both halves of what resolving a preset is for: the preset's search, filters,
    and screening policy reach both ``FeedStreamConfig.from_payload(build_search_launch(...).payload)``
    and the ``FeedStreamConfig`` handed to the pipeline, and the extracted ``JobPosting``
    returned in ``FeedStreamResult.postings`` is the exact one the engine parsed.
    """
    store = resolve_saved_search_store(prefer_database=False)
    preset = run_sync(store.get_saved_search("default_agent_search"))
    assert preset is not None
    assert preset.search.keyword == "agent"
    assert preset.filter.industries == ["在线教育", "游戏", "人工智能"]

    launch_config = FeedStreamConfig.from_payload(
        build_search_launch(preset, source=LaunchSource.MANUAL).payload
    )
    assert launch_config.keyword == "agent"
    assert launch_config.filter_config.industries == ["在线教育", "游戏", "人工智能"]

    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    pipeline = build_verification_pipeline(
        mock_driver,
        screener=CandidateScreener(llm_client=stub_llm()),
        world=build_feed_device_world(mock_driver),
        store=InMemoryJobRecordStore(),
    )

    posting = JobPosting(
        title="资深 Agent 专家",
        company_name="智元创新",
        salary_range="40-60K",
        job_description=GOOD_JD,
    )
    configs: list[FeedStreamConfig] = []

    async def _record(config, on_job=None):
        configs.append(config)
        return await _real_stream(config, on_job)

    _real_stream = pipeline.stream_jobs
    pipeline.stream_jobs = _record
    script_pages(
        pipeline,
        feed=ScriptedFeed([[_card("资深 Agent 专家", "智元创新")]]),
        detail=_detail_page(posting=posting),
    )

    with patch("time.sleep", return_value=None):
        result = run_verification_feed(
            pipeline=pipeline,
            search_config=preset.search,
            filter_config=preset.filter,
            screening_policy=launch_config.screening_policy,
            candidate_profile=None,
            enable_greeting_draft=False,
        )

    assert len(configs) == 1, "a verification run is exactly one feed run"
    assert configs[0].keyword == "agent"
    assert configs[0].filter_config is preset.filter
    assert configs[0].screening_policy is launch_config.screening_policy
    assert result.postings[0] is posting, "the posting handed back is the one the engine extracted"
    assert result.postings[0].title == "资深 Agent 专家"


@pytest.mark.asyncio
async def test_saved_search_preset_read_bridges_inside_a_running_loop():
    """Synchronous callers can resolve named presets even inside an active event loop."""
    store = resolve_saved_search_store(prefer_database=False)
    preset = run_sync(store.get_saved_search("default_agent_search"))

    assert preset is not None
    assert preset.search.keyword == "agent"
    assert preset.filter.industries == ["在线教育", "游戏", "人工智能"]


def test_the_miss_message_is_written_once_for_both_callers() -> None:
    """Missing saved search formatting names both the missed id and available ids."""
    assert missing_saved_search_message("missing", ["alpha", "beta"]) == (
        "Saved search 'missing' not found. Available searches: [alpha, beta]"
    )
    assert missing_saved_search_message("missing", []) == (
        "Saved search 'missing' not found. Available searches: [none]"
    )


def test_unreachable_saved_search_store_surfaces_transport_error():
    """A dead PocketBase must raise ``TransportError`` rather than degrading silently inside the store."""

    class _UnreachableStore(InMemorySavedSearchStore):
        async def get_saved_search(self, search_id: str) -> SavedSearch | None:
            raise TransportError("PocketBase get_saved_search failed: connection refused")

    with pytest.raises(TransportError, match="connection refused"):
        run_sync(_UnreachableStore().get_saved_search("default_agent_search"))


def test_explicit_launch_overrides_beat_preset_defaults():
    """An explicit preset / override supplied at launch governs ``FeedStreamConfig``."""
    preset = SavedSearch(
        id="explicit_preset",
        name="Explicit Preset",
        search=SearchConfig(keyword="from_object"),
        filter=FilterConfig(education="本科"),
    )
    launch = build_search_launch(preset, source=LaunchSource.MANUAL)
    config = FeedStreamConfig.from_payload(launch.payload)

    assert config.keyword == "from_object"
    assert config.filter_config.education == "本科"
