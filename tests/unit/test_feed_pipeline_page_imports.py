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

REPO_ROOT = Path(__file__).parents[2]
FEED_PIPELINE = REPO_ROOT / "src" / "boss_agent" / "feed_pipeline.py"

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
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    sources: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        parts = node.module.split(".")
        if "pages" not in parts:
            continue
        tail = ".".join(parts[parts.index("pages") + 1 :])
        for alias in node.names:
            sources[alias.name] = tail or "<facade>"
    return sources


def test_feed_pipeline_takes_each_page_model_from_the_submodule_that_owns_it() -> None:
    sources = _page_import_sources(FEED_PIPELINE)
    assert {name: sources.get(name) for name in FEED_OWNED_PAGE_MODELS} == FEED_OWNED_PAGE_MODELS


def test_feed_pipeline_reaches_no_page_model_outside_its_own_submodules() -> None:
    sources = _page_import_sources(FEED_PIPELINE)
    unexpected = set(sources) - set(FEED_OWNED_PAGE_MODELS) - {GREETING_DISPATCH_PAGE_MODEL}
    assert unexpected == set(), f"feed_pipeline gained page imports it has no use for: {unexpected}"
