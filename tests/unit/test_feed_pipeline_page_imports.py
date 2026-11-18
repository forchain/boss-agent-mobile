"""
tests/unit/test_feed_pipeline_page_imports.py
==============================================
Where the job-feed engine takes its page models from (spec #395, ticket #400).

`feed_pipeline` is the heaviest `pages` consumer in the repo, and it used to bind every screen
through the package facade -- so a change to the 仅沟通 list or the rejection-descriptor
parser reached the feed engine's import list too. The engine's own screens now come from the
submodule that owns each one, which is what makes a chat-triage-side edit invisible here.

The greeting dispatch is the one honest exception: `JobFeedPipeline` opens the chat screen to
send the draft it just evaluated, so `ChatPage` is a real runtime dependency rather than a
leftover import. Its source module is deliberately *not* pinned here, so this test only fails
if some *other* page model sneaks into the feed engine's import list -- or if `ChatPage`
migrates away from the facade and something must be re-decided on purpose.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
PAGES_ROOT = REPO_ROOT / "src" / "boss_agent" / "adapters" / "outbound" / "device" / "pages"
PAGES_PACKAGE = "boss_agent.adapters.outbound.device.pages"
FEED_PIPELINE = REPO_ROOT / "src" / "boss_agent" / "core" / "job" / "use_cases" / "feed_pipeline.py"

#: The page-layer submodules on disk, so an imported *name* can be read as the submodule it
#: names rather than as a facade symbol: `from boss_agent.adapters.outbound.device.pages import job_feed` binds the
#: submodule, not the package.
PAGE_SUBMODULES = frozenset(
    path.stem for path in PAGES_ROOT.glob("*.py") if path.stem != "__init__"
)

#: The engine imports no page model at all now: the device world is injected
#: (``core.job.interfaces.device``) and production composition lives in
#: ``boss_agent.adapters.outbound.device.pages.feed_bundle``. The empty mapping is the assertion.
FEED_OWNED_PAGE_MODELS: dict[str, str] = {}

#: Kept as a parameter so the second rule's set arithmetic reads unchanged.
GREETING_DISPATCH_PAGE_MODEL = None


def _page_import_sources(module_path: Path) -> dict[str, str]:
    """Map every name bound from a `pages` import to the submodule it came from.

    A facade import reads as ``<facade>``, so this answers "which file declared the symbol
    this module depends on" from the source tree alone -- no import of `boss_agent` needed.

    All three spellings that reach the layer are read, matching
    ``test_chat_triage_page_imports``: the ``from X import name`` the engine uses today, the
    ``import boss_agent.adapters.outbound.device.pages.job_feed`` that is an ``ast.Import`` rather than an
    ``ImportFrom``, and the ``from boss_agent.adapters.outbound.device.pages import job_feed`` whose ``node.module``
    is the facade and whose imported *name* is the submodule.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    sources: dict[str, str] = {}

    def record(module: str, bound: str) -> None:
        """Bind `bound` to the submodule tail after `pages`, or to ``<facade>``."""
        parts = module.split(".")
        if "pages" not in parts:
            return
        tail = ".".join(parts[parts.index("pages") + 1 :])
        if not tail and bound in PAGE_SUBMODULES:
            # A name imported straight from the package may be the submodule itself:
            # `from .pages import job_feed` binds `job_feed`, not the facade.
            tail = bound
        sources[bound] = tail or "<facade>"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                record(alias.name, alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                record(node.module, alias.name)
    return sources


#: Every spelling that reaches the page layer, pinned against synthetic sources: a parser that
#: stops reading one of them would let a page model into the feed engine unnoticed.
PAGE_IMPORT_SPELLINGS = (
    "from .pages.job_feed import JobListPage",
    "from .pages.communication import ChatPage",
    f"from {PAGES_PACKAGE}.job_detail import JobDetailPage",
    f"import {PAGES_PACKAGE}.job_feed",
    f"import {PAGES_PACKAGE}.job_feed as feed",
    f"from {PAGES_PACKAGE} import job_feed",
    f"from {PAGES_PACKAGE} import job_feed as feed",
)


@pytest.mark.parametrize("source", PAGE_IMPORT_SPELLINGS, ids=PAGE_IMPORT_SPELLINGS)
def test_the_guard_reads_every_spelling_that_reaches_the_page_layer(
    source: str, tmp_path: Path
) -> None:
    """Prove the source map is reachable, not just present.

    Both rules below read `_page_import_sources`, so a spelling it cannot see is a page
    model that arrives in the feed engine with no failure to explain it. Each spelling is
    checked here rather than left to a future import to reveal.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")

    assert _page_import_sources(probe), (
        f"the parser cannot see {source!r}, so neither rule can fail"
    )


def test_feed_pipeline_takes_each_page_model_from_the_submodule_that_owns_it() -> None:
    sources = _page_import_sources(FEED_PIPELINE)
    assert {name: sources.get(name) for name in FEED_OWNED_PAGE_MODELS} == FEED_OWNED_PAGE_MODELS


def test_feed_pipeline_reaches_no_page_model_outside_its_own_submodules() -> None:
    sources = _page_import_sources(FEED_PIPELINE)
    unexpected = set(sources) - set(FEED_OWNED_PAGE_MODELS) - {GREETING_DISPATCH_PAGE_MODEL}
    assert unexpected == set(), f"feed_pipeline gained page imports it has no use for: {unexpected}"
