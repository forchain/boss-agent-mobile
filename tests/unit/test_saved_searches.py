"""Unit tests for SavedSearch, the SavedSearchStore seam, and SmokeHarness integration."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import boss_agent
from boss_agent import saved_search_store
from boss_agent.errors import TransportError
from boss_agent.job_entities import JobPosting
from boss_agent.saved_search_store import InMemorySavedSearchStore, resolve_saved_search_store
from boss_agent.search_entities import FilterConfig, SavedSearch, SearchConfig
from boss_agent.workflows import SmokeHarness, TakeoverHandler

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


def test_smoke_harness_with_saved_search_id():
    """The harness reads a named preset through an injected store, not a global (#394).

    Injection is what makes this a seam: the harness resolves no store of its own, so
    the test supplies the same defaults the resolver seeds for a database-less run.
    """
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 50, "y": 50, "width": 100, "height": 50}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深 Agent 专家"

    def mock_find_elements(by, value):
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "chat" in value or "editText_with_scrollbar" in value or "btn_chat" in value:
            return []
        return [mock_btn]

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        saved_search_id="default_agent_search",
        saved_search_store=resolve_saved_search_store(prefer_database=False),
    )

    assert harness.search_config.keyword == "agent"
    assert harness.filter_config.industries == ["在线教育", "游戏", "人工智能"]

    job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)
    assert job.title == "资深 Agent 专家"


@pytest.mark.asyncio
async def test_smoke_harness_resolves_a_named_preset_from_inside_a_running_loop():
    """The harness is synchronous but the store is not, and that gap is bridged (#394).

    A harness built from an async worker or a LangGraph step already has a running loop
    in this thread; ``asyncio.run`` there would raise, and the store read would have to
    be threaded out of the constructor entirely. This pins the bridging as something the
    harness does for its caller rather than something each caller has to arrange.
    """
    harness = SmokeHarness(
        driver=MagicMock(),
        saved_search_id="default_agent_search",
        saved_search_store=resolve_saved_search_store(prefer_database=False),
    )

    assert harness.search_config.keyword == "agent"
    assert harness.filter_config.industries == ["在线教育", "游戏", "人工智能"]


def test_smoke_harness_names_the_available_presets_when_the_id_is_unknown():
    """A miss must stay actionable: the store answers ``None``, so the harness re-raises.

    Letting the ``None`` through would drop the constructor into its no-preset branch and
    run an unfiltered sweep against a live device under a preset the operator never chose.
    """
    store = InMemorySavedSearchStore(
        {
            "only_preset": SavedSearch(
                id="only_preset",
                name="Only Preset",
                search=SearchConfig(keyword="rust"),
                filter=FilterConfig(education="本科"),
            )
        }
    )

    with pytest.raises(KeyError) as excinfo:
        SmokeHarness(driver=MagicMock(), saved_search_id="missing", saved_search_store=store)

    assert "missing" in str(excinfo.value)
    assert "only_preset" in str(excinfo.value)


def test_smoke_harness_does_not_hide_an_unreachable_store():
    """A dead PocketBase must surface, not degrade into an empty store's settings.

    The retired registry swallowed a dead PocketBase; falling back here would silently
    run the smoke sweep with an empty store's worth of settings — the operator would see
    a completed run and no indication that the preset they named never arrived.
    """

    class _UnreachableStore(InMemorySavedSearchStore):
        async def get_saved_search(self, search_id: str) -> SavedSearch | None:
            raise TransportError("PocketBase get_saved_search failed: connection refused")

    with pytest.raises(TransportError, match="connection refused"):
        SmokeHarness(
            driver=MagicMock(),
            saved_search_id="default_agent_search",
            saved_search_store=_UnreachableStore(),
        )


def test_smoke_harness_prefers_a_supplied_preset_over_a_named_one():
    """Precedence is unchanged: an explicit object wins and the store is left alone."""

    class _UnusableStore(InMemorySavedSearchStore):
        async def get_saved_search(self, search_id: str) -> SavedSearch | None:
            raise AssertionError("the store must not be consulted when a preset is supplied")

    preset = SavedSearch(
        id="explicit_preset",
        name="Explicit Preset",
        search=SearchConfig(keyword="from_object"),
        filter=FilterConfig(education="本科"),
    )

    harness = SmokeHarness(
        driver=MagicMock(),
        saved_search=preset,
        saved_search_id="default_agent_search",
        saved_search_store=_UnusableStore(),
    )

    assert harness.search_config.keyword == "from_object"
    assert harness.filter_config.education == "本科"
