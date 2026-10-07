"""
tests/unit/test_pages_package_facade.py
======================================
The backward-compatibility seam of the `boss_agent.pages` package (spec #395, ticket #398).

The monolith `pages.py` became a package of five feature submodules behind a facade, but every
existing caller — `feed_pipeline`, `chat_triage`, `workflows`, the worker handlers, and dozens of
unit tests — still imports from `boss_agent.pages`. So the facade's public surface is a contract in
its own right, and these tests pin the things that would break it quietly:

* every symbol the legacy module exported still resolves on `boss_agent.pages`, and is the *same
  object* the owning submodule holds — a facade that re-bound a name instead of re-exporting it
  would leave two classes with the same name and different `isinstance` behaviour;
* the surface is *exactly* those 36 names — the 29 the monolith reached only by importing them
  (`parse_card`, `wait_until`, `AuthStatus`, `time`, `logging`, ...) stay gone on purpose, because
  an unused facade import still executes its module and that is the cost the split removed;
* each submodule imports on its own, so a chat-triage consumer can load `pages.communication`
  without reaching for job-feed or filter-dialog adapters;
* the observability seam survives the move: `logger` keeps the name `boss_agent.pages` (tests patch
  it by string), `ui_logger` stays the shared `droid_agent_core.ui` channel, and one `Console`
  instance is shared, so console output formatting is byte-identical to the monolith's.
"""

import importlib
import logging

import pytest
from rich.console import Console

from boss_agent import pages
from boss_agent.job_entities import JobCardBrief

#: Every public name the monolith *defined*, mapped to the submodule that now owns it. The
#: left-hand side is the contract: a name missing here is a symbol a caller can no longer import.
#:
#: The list is exactly the 35 names the monolith *defined* plus `JobCardBrief`, the one domain
#: type callers reach through the facade. It is deliberately not the monolith's full 65-name
#: attribute namespace — see `NARROWED_LEGACY_NAMES` and the docstring of
#: `src/boss_agent/pages/__init__.py`, which state why the other 29 are gone on purpose.
LEGACY_PUBLIC_SURFACE: dict[str, str] = {
    # base.py
    "logger": "base",
    "ui_logger": "base",
    "console": "base",
    "BACK_INTERVAL_SEC": "base",
    "_log_selector_lookup": "base",
    "_log_info": "base",
    "_log_warn": "base",
    "_log_error": "base",
    "BaseBossPage": "base",
    # system.py
    "StartupDialogPage": "system",
    "LoginPage": "system",
    # job_feed.py
    "LocatedJobCard": "job_feed",
    "JobListPage": "job_feed",
    "SearchPage": "job_feed",
    "FILTER_OPTION_SYNONYMS": "job_feed",
    "FilterDialogPage": "job_feed",
    "IndustryFilterDialogPage": "job_feed",
    # job_detail.py
    "COMMUTE_DISTANCE_PATTERN": "job_detail",
    "COMMUTE_PROBE_MAX_SCROLLS": "job_detail",
    "COMMUTE_PROBE_SCROLL_STRIDE_RATIO": "job_detail",
    "COMMUTE_PROBE_STALLED_SWIPES": "job_detail",
    "JobDetailPage": "job_detail",
    # communication.py
    "CARD_DESCRIPTOR_SEPARATOR": "communication",
    "FULLWIDTH_CARD_DESCRIPTOR_SEPARATOR": "communication",
    "BACK_BUTTON_KEY": "communication",
    "CARD_CHILD_TEXT_XPATH": "communication",
    "LIST_RECOVERY_MAX_STEPS": "communication",
    "_normalize_card_descriptors": "communication",
    "OUTBOUND_STATUS_MARKERS": "communication",
    "_whitespace_digest": "communication",
    "compute_card_key": "communication",
    "parse_company_from_descriptor": "communication",
    "CommunicationCard": "communication",
    "ChatPage": "communication",
    "CommunicationListPage": "communication",
    # Domain symbols that live elsewhere but are imported *through* `pages` by callers today.
    "JobCardBrief": "job_feed",
}

FEATURE_SUBMODULES = ("base", "system", "job_feed", "job_detail", "communication")

#: Names the monolith bound but the facade deliberately does not re-export: the helpers it
#: imported and never defined (`parse_card`, `wait_until`, `classify_chat_button`, ...) and the
#: stdlib/typing that leaked through it (`time`, `logging`, `re`, `dataclass`, `Any`, ...).
#:
#: Parity with the monolith is exact for the symbols it *defined*; its full attribute namespace
#: shrank, and this is the contract for that. Nothing in the repo reaches any of these through
#: the facade — each has an owner it is imported from directly — so the narrowing is a boundary,
#: not a regression. It is pinned here so nobody "fixes" it later by re-adding an unused import:
#: an unused import on the facade still executes the module it names, and that is the slow
#: 仅沟通-triage-runs-pay-for-job-discovery cost the split existed to remove.
NARROWED_LEGACY_NAMES = (
    "parse_card",
    "wait_until",
    "classify_chat_button",
    "AuthStatus",
    "JobPosting",
    "FilterConfig",
    "ParsedCard",
    "CardFacets",
    "ChatButtonState",
    "JobLocationLine",
    "PLATFORM_BADGE_MARKERS",
    "LocatorRegistry",
    "UISelector",
    "HumanizedGestureExecutor",
    "DISINTEREST_REASON",
    "calculate_probe_coordinate",
    "get_global_locator_registry",
    "jd_is_truncated",
    "needs_text_fallback",
    # stdlib and typing the monolith leaked through its own namespace
    "time",
    "logging",
    "re",
    "hashlib",
    "contextlib",
    "dataclass",
    "Any",
    "Callable",
    "Point",
    "Console",
)


def _submodule(name: str):
    return importlib.import_module(f"{pages.__name__}.{name}")


@pytest.mark.parametrize("name", sorted(LEGACY_PUBLIC_SURFACE))
def test_facade_still_exports_every_legacy_symbol(name: str):
    """`from boss_agent.pages import X` must keep working for every X the monolith exported."""
    assert hasattr(pages, name), f"`from boss_agent.pages import {name}` no longer resolves"
    assert name in pages.__all__, f"{name} is exported by attribute but missing from __all__"


@pytest.mark.parametrize(("name", "owner"), sorted(LEGACY_PUBLIC_SURFACE.items()))
def test_facade_reexports_the_owning_submodules_object(name: str, owner: str):
    """A facade name must be the owning submodule's own object, never a rebinding of it."""
    assert getattr(pages, name) is getattr(_submodule(owner), name)


@pytest.mark.parametrize("name", FEATURE_SUBMODULES)
def test_each_feature_submodule_imports_on_its_own(name: str):
    """A consumer must be able to load one feature's adapters without the rest of the package."""
    module = importlib.import_module(f"boss_agent.pages.{name}")
    assert module.__name__ == f"boss_agent.pages.{name}"


def test_facade_reuses_one_logger_per_channel_and_one_console():
    """The logging/console channels the monolith created once are still the same objects."""
    for module_name in FEATURE_SUBMODULES:
        module = _submodule(module_name)
        for channel in ("logger", "ui_logger", "console"):
            if hasattr(module, channel):
                assert getattr(module, channel) is getattr(pages, channel), (
                    f"pages.{module_name}.{channel} is not the object the facade hands out"
                )

    assert pages.logger.name == "boss_agent.pages", (
        "the logger name is a public observability contract — tests patch it by string"
    )
    assert pages.ui_logger.name == "droid_agent_core.ui"
    assert isinstance(pages.logger, logging.Logger)
    assert isinstance(pages.console, Console)


def test_patching_the_facade_logger_still_intercepts_module_logging():
    """`patch("boss_agent.pages.logger.error")` must reach the log call the submodules make."""
    from unittest.mock import patch

    with patch("boss_agent.pages.logger.error") as mock_err_log:
        pages.base._log_error("probe")

    mock_err_log.assert_called_once_with("probe")


def test_job_card_brief_reaches_callers_through_the_facade():
    """`from boss_agent.pages import JobCardBrief` is live in three test modules today."""
    assert pages.JobCardBrief is JobCardBrief


def test_the_facade_surface_is_the_36_names_the_split_defined():
    """The re-exported set is a boundary the split chose, not a drift from the monolith.

    `LEGACY_PUBLIC_SURFACE` is the floor — every name there must resolve. This is the ceiling,
    and it is stated once so a reader who finds `parse_card` gone from the facade learns it was
    a decision rather than a dropped import.
    """
    assert len(LEGACY_PUBLIC_SURFACE) == 36
    assert sorted(pages.__all__) == sorted(LEGACY_PUBLIC_SURFACE)


def test_the_facade_does_not_re_export_the_names_the_monolith_only_imported():
    """29 names the monolith reached by importing them are gone, deliberately.

    Re-adding any of them as an unused facade import would execute the module it names on every
    `import boss_agent.pages`, which is the cost the package split removed. The assertion is
    here to make that trade-off executable: a future edit that re-adds one of these names
    without a caller fails loudly instead of slowing every 仅沟通 run down.
    """
    reachable = [name for name in NARROWED_LEGACY_NAMES if hasattr(pages, name)]

    assert not reachable, (
        "these names resolve on the facade again: "
        f"{reachable}. Either a caller needs it — in which case import it from the module "
        "that defines it — or the facade is paying an import cost nobody uses."
    )


def test_pacing_fixture_reaches_every_pages_submodule(pages_pacing_modules) -> None:
    """The unit tier's pacing fixture must follow the sleeps into the new submodules.

    Patching the facade alone is not enough now that each submodule owns its own `import time`:
    the settling pauses would silently return and the tier would blow its 60-second budget with
    no failure to explain it. Derived from the sources rather than hardcoded, so a submodule that
    grows a `time.sleep` later is covered by this assertion too.
    """
    import inspect
    import pkgutil

    patched = {module.__name__ for module in pages_pacing_modules}
    sleepers = {
        f"{pages.__name__}.{info.name}"
        for info in pkgutil.iter_modules(pages.__path__)
        if "time.sleep("
        in inspect.getsource(importlib.import_module(f"{pages.__name__}.{info.name}"))
    }

    assert sleepers, (
        "no pages submodule owns a settling pause any more — has this test outlived its subject?"
    )
    assert sleepers <= patched, (
        f"these submodules sleep but are not paced: {sorted(sleepers - patched)}"
    )
