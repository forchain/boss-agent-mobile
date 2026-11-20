"""
tests/unit/test_feed_device_seam.py
===================================
The typed device seam the feed engine drives (issue #481).

Chat Triage proved this shape in ADR 0016: a screen hands the engine *data*, and the
engine asks the screen to *act*. The mobile job feed used to break that rule in three
ways at once — cards crossed the seam as raw Appium element handles, the detail page
returned untyped dicts the engine then had to probe with ``getattr``, and the engine's
seven page objects were public attributes a test could rebind after construction.

This module pins the boundary so it cannot rot back:

* the engine's source carries no Appium vocabulary at all — no element tap, no
  ``location["y"]``, no defensive ``getattr`` over a typed entity;
* the engine's screens are private and bound at construction, so the only public seam is
  the device world it was handed;
* the harness that scripts a feed composes a *complete* world, so a suite can never pass
  because a screen was quietly missing.
"""

from __future__ import annotations

import ast
from pathlib import Path

from _feed_harness import ScriptedFeed, _card, scripted_world

from boss_agent.adapters.memory.job_store import InMemoryJobRecordStore
from boss_agent.core.job.interfaces.device import FeedBoundary, FeedDeviceWorld
from boss_agent.core.job.use_cases.feed_pipeline import JobFeedPipeline

REPO_ROOT = Path(__file__).parents[2]
FEED_PIPELINE = REPO_ROOT / "src" / "boss_agent" / "core" / "job" / "use_cases" / "feed_pipeline.py"

#: The seven screens the engine drives. Each was a public attribute once, so each was a
#: seam a test could rebind and a piece of the engine's state a reader could not see.
PAGE_ATTRIBUTES = (
    "startup_page",
    "list_page",
    "search_page",
    "detail_page",
    "chat_page",
    "filter_page",
    "industry_filter_page",
)


def _feed_pipeline_source() -> str:
    return FEED_PIPELINE.read_text(encoding="utf-8")


def _public_page_attributes() -> set[str]:
    """The page attributes the engine exposes publicly, read from the source itself.

    Read off the AST rather than the class body text so a decorated or aliased
    definition cannot hide one.
    """
    tree = ast.parse(_feed_pipeline_source())
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "JobFeedPipeline":
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            for target in item.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    found.add(target.attr)
    return found & set(PAGE_ATTRIBUTES)


def test_the_engine_exposes_no_public_page_attribute() -> None:
    """Every screen is private: the world is the only public seam."""
    assert _public_page_attributes() == set(), (
        f"the feed engine still exposes page objects publicly: {sorted(_public_page_attributes())}"
    )


def test_the_engines_source_carries_no_appium_vocabulary() -> None:
    """No element tap, no driver coordinate, no defensive probe over a typed entity."""
    source = _feed_pipeline_source()

    for needle in (
        ".click()",
        'location["y"]',
        ".location[",
        "getattr(posting",
        "getattr(card",
        "getattr(evaluation",
    ):
        assert needle not in source, f"the feed engine still uses Appium/defensive probe {needle!r}"

    # `getattr` has no reason to appear at all in a module whose collaborators are typed.
    assert "getattr(" not in source, "the feed engine still probes its collaborators defensively"


def test_the_private_screens_are_bound_at_construction() -> None:
    """The pages are there before a run starts, not wired in as it goes."""
    world = scripted_world(feed=ScriptedFeed([[_card("AI Agent 一号", "甲公司")]]))
    pipeline = JobFeedPipeline(world=world, store=InMemoryJobRecordStore())

    assert pipeline.world is world
    assert pipeline._startup_page is world.startup_page
    assert pipeline._list_page is world.list_page
    assert pipeline._search_page is world.search_page
    assert pipeline._detail_page is world.detail_page
    assert pipeline._chat_page is world.chat_page
    assert pipeline._filter_page is world.filter_page
    assert pipeline._industry_filter_page is world.industry_filter_page


def test_the_harness_composes_a_complete_world() -> None:
    """A scripted world has every screen, so no suite passes by accident."""
    world = scripted_world()

    assert isinstance(world, FeedDeviceWorld)
    for page in (
        world.startup_page,
        world.list_page,
        world.search_page,
        world.detail_page,
        world.chat_page,
        world.filter_page,
        world.industry_filter_page,
    ):
        assert page is not None, "every screen in the world must be present"


def test_the_boundary_is_typed_rather_than_a_driver_element() -> None:
    """The feed bottom banner crosses the seam as a value, not as an element."""
    boundary = FeedBoundary(visible=True, y_position=1200)

    assert boundary.visible is True
    assert boundary.y_position == 1200
    assert FeedBoundary().visible is False


def test_the_list_screen_owns_the_card_tap() -> None:
    """Opening a card is the screen's job; the engine asks it to."""
    feed = ScriptedFeed([[_card("AI Agent 一号", "甲公司")]])
    card = feed.extract_visible_job_cards()[0]

    assert feed.open_card(card) is True
    assert feed.opened_cards == [card]
