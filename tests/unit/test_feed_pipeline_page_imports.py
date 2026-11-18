"""
tests/unit/test_feed_pipeline_page_imports.py
==============================================
Where the job-feed engine takes its page models from (spec #395, ticket #400, #463).

The feed engine used to bind every screen through the ``pages`` package facade, so a
change to the 仅沟通 list or the filter dialogs reached the engine too. ``JobFeedPipeline``
now lives in core and receives a :class:`FeedDeviceWorld`; it imports no page model at
all. Production composition is ``adapters.outbound.device.pages.feed_bundle``'s, and that
bundle must name each concrete page from the submodule that owns it.

The previous form of this guard kept an empty ``FEED_OWNED_PAGE_MODELS = {}`` mapping and
looped over it, so its first rule could never fail. The rules below are non-vacuous: an
empty result is asserted directly, and the bundle's imports are asserted against a
non-empty expected set read from relative imports, which is how the bundle actually
spells them.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
PAGES_ROOT = REPO_ROOT / "src" / "boss_agent" / "adapters" / "outbound" / "device" / "pages"
PAGES_PACKAGE = "boss_agent.adapters.outbound.device.pages"
FEED_PIPELINE = REPO_ROOT / "src" / "boss_agent" / "core" / "job" / "use_cases" / "feed_pipeline.py"
FEED_BUNDLE = PAGES_ROOT / "feed_bundle.py"

#: The page-layer submodules on disk, so an imported *name* can be read as the submodule it
#: names rather than as a facade symbol.
PAGE_SUBMODULES = frozenset(
    path.stem for path in PAGES_ROOT.glob("*.py") if path.stem != "__init__"
)

#: The concrete page models the feed bundle composes, and the submodule that owns each.
FEED_OWNED_PAGE_MODELS: dict[str, str] = {
    "StartupDialogPage": "system",
    "JobListPage": "job_feed",
    "SearchPage": "job_feed",
    "FilterDialogPage": "job_feed",
    "IndustryFilterDialogPage": "job_feed",
    "JobDetailPage": "job_detail",
    "ChatPage": "communication",
}


def _page_imports(
    path: Path, package: str = PAGES_PACKAGE, *, relative_base: str | None = None
) -> list[tuple[str, str, int]]:
    """Every page-package import in `path` as (module, symbol, lineno).

    Relative imports are resolved against ``relative_base`` (the package that owns the
    file), so the bundle's ``from .communication import ChatPage`` is read as
    ``boss_agent.adapters.outbound.device.pages.communication``. All three spellings that
    reach the layer are read: direct module imports, ``ast.Import``, and facade imports
    whose bound name is itself a submodule.
    """
    base = relative_base or package
    found: list[tuple[str, str, int]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package or alias.name.startswith(f"{package}."):
                    found.append((alias.name, alias.asname or alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module:
                module = f"{base}.{node.module}"
            elif node.level:
                module = base
            else:
                module = node.module or ""
            if module != package and not module.startswith(f"{package}."):
                continue
            for alias in node.names:
                found.append((module, alias.name, node.lineno))
                submodule = f"{module}.{alias.name}"
                if module == package and alias.name in PAGE_SUBMODULES:
                    found.append((submodule, alias.name, node.lineno))
    return found


#: Every spelling that reaches job discovery, pinned against synthetic sources: if the
#: parser stops reading one of them, a page model could reach the engine unseen.
PAGE_IMPORT_SPELLINGS = (
    f"from {PAGES_PACKAGE}.job_detail import JobDetailPage",
    f"import {PAGES_PACKAGE}.job_feed",
    f"import {PAGES_PACKAGE}.job_feed as feed",
    f"from {PAGES_PACKAGE} import job_feed",
    f"from {PAGES_PACKAGE} import job_feed as feed",
    "from .communication import ChatPage",
    "from . import job_feed",
)


@pytest.mark.parametrize("source", PAGE_IMPORT_SPELLINGS, ids=PAGE_IMPORT_SPELLINGS)
def test_the_guard_reads_every_spelling_that_reaches_the_page_layer(
    source: str, tmp_path: Path
) -> None:
    """Prove the source map is reachable, not just present."""
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")

    assert _page_imports(probe, PAGES_PACKAGE, relative_base=PAGES_PACKAGE), (
        f"the parser cannot see {source!r}, so neither rule can fail"
    )


def test_feed_pipeline_imports_no_page_objects() -> None:
    """The engine receives its device world as ports; it never reaches for a page."""
    imports = _page_imports(FEED_PIPELINE, PAGES_PACKAGE)

    assert imports == [], f"core feed pipeline imports page objects: {imports}"


def test_feed_bundle_takes_each_page_model_from_the_submodule_that_owns_it() -> None:
    """Production composition names each concrete screen from its feature submodule."""
    imports = _page_imports(FEED_BUNDLE, PAGES_PACKAGE, relative_base=PAGES_PACKAGE)

    actual = {
        symbol: module.removeprefix(f"{PAGES_PACKAGE}.")
        for module, symbol, _ in imports
        if module != PAGES_PACKAGE
    }
    assert {symbol: actual.get(symbol) for symbol in FEED_OWNED_PAGE_MODELS} == (
        FEED_OWNED_PAGE_MODELS
    )


def test_feed_bundle_imports_no_page_model_through_the_facade() -> None:
    """A facade import would drag every screen back into one bundle again."""
    facade_imports = [
        (symbol, lineno)
        for module, symbol, lineno in _page_imports(
            FEED_BUNDLE, PAGES_PACKAGE, relative_base=PAGES_PACKAGE
        )
        if module == PAGES_PACKAGE
    ]

    assert facade_imports == [], (
        f"feed bundle imports page models through the facade: {facade_imports}"
    )
