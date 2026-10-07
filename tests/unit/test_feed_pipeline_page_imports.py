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
PAGES_ROOT = REPO_ROOT / "src" / "boss_agent" / "pages"
FEED_PIPELINE = REPO_ROOT / "src" / "boss_agent" / "feed_pipeline.py"

#: The page-layer submodules on disk, so an imported *name* can be read as the submodule it
#: names rather than as a facade symbol: `from boss_agent.pages import job_feed` binds the
#: submodule, not the package.
PAGE_SUBMODULES = frozenset(
    path.stem for path in PAGES_ROOT.glob("*.py") if path.stem != "__init__"
)

#: Every page model the feed engine owns, mapped to the submodule that declares it. The facade
#: is spelled ``<facade>`` so a regression that routes one of these back through it is
#: visible as a wrong value rather than as a missing key.
FEED_OWNED_PAGE_MODELS: dict[str, str] = {
    "JobListPage": "job_feed",
    "SearchPage": "job_feed",
    "FilterDialogPage": "job_feed",
    "IndustryFilterDialogPage": "job_feed",
    "LocatedJobCard": "job_feed",
    "JobDetailPage": "job_detail",
    "StartupDialogPage": "system",
}

#: Bound by the greeting dispatch (typing the draft, clicking send, navigating back).
GREETING_DISPATCH_PAGE_MODEL = "ChatPage"


def _page_import_sources(module_path: Path) -> dict[str, str]:
    """Map every name bound from a `pages` import to the submodule it came from.

    A facade import reads as ``<facade>``, so this answers "which file declared the symbol
    this module depends on" from the source tree alone -- no import of `boss_agent` needed.

    All three spellings that reach the layer are read, matching
    ``test_chat_triage_page_imports``: the ``from X import name`` the engine uses today, the
    ``import boss_agent.pages.job_feed`` that is an ``ast.Import`` rather than an
    ``ImportFrom``, and the ``from boss_agent.pages import job_feed`` whose ``node.module``
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
    "from boss_agent.pages.job_detail import JobDetailPage",
    "import boss_agent.pages.job_feed",
    "import boss_agent.pages.job_feed as feed",
    "from .pages import job_feed",
    "from boss_agent.pages import job_feed",
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
