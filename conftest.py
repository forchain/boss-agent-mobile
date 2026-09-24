"""
conftest.py
===========
Default test-tier plumbing (spec #247, ticket #248).

`pyproject.toml` aims an unadorned `pytest` at the fast unit tier: `testpaths` collects
`tests/unit` only, and `addopts` deselects the `live` (device) and `e2e` (service) markers.
Two deliberate invocations still have to reach the tier they name, and neither gets there
from that configuration alone:

* **A path inside `tests/e2e`** — the default `not e2e` clause would deselect everything the
  developer just pointed at, so that clause is lifted. The `live` guard is never lifted
  (device tests always require `-m live`), and a `-m` the developer typed is never touched.
* **A marker expression that names a tier** (`pytest -m live`, `pytest -m e2e`, or the same
  bundled as `pytest -qm e2e`) — the default `testpaths` leaves it nothing outside `tests/unit`
  to select from, so the whole `tests/` tree becomes the collection root and the expression
  alone decides what runs. An expression that merely filters (`pytest -m "not live"`) is not a
  tier request: it keeps the default scope and filters within it.

An explicit path always wins — nothing here widens a run the developer has already narrowed.
Everything else keeps the default: an unadorned `pytest` runs the unit tier, and a broad
path such as `pytest tests` still deselects E2E. See docs/agents/testing.md; the tests that
pin all of it live in `tests/unit/test_live_marker_isolation.py`.
"""

import re
from pathlib import Path

from pytest import Config

TIER_DIRS = ("tests/unit", "tests/e2e")
TIER_MARKERS = ("e2e", "live")
E2E_DESELECT_CLAUSE = "not e2e"
E2E_DIR = Path(__file__).resolve().parent / "tests" / "e2e"


def _cli_path_arguments(config: Config) -> list[str]:
    """The paths the developer named on the command line, as pytest itself parsed them."""
    return list(getattr(config.known_args_namespace, "file_or_dir", []) or [])


def _cli_marker_expression(config: Config) -> bool:
    """Whether the invocation itself passed `-m`; an `addopts` value never counts.

    pytest's parser accepts `-m` bundled into a short-flag cluster (`-qm e2e`), so each short
    token is scanned for it rather than only its leading characters. Erring towards `True` is
    harmless: widening the collection root also requires the expression to name a tier, and
    an `addopts` default (the only markexpr available when no `-m` was typed) never does.
    """
    return any(
        arg.startswith("-") and not arg.startswith("--") and "m" in arg[1:]
        for arg in config.invocation_params.args
    )


def _names_a_tier_marker(expression: str) -> bool:
    """Whether `-m` *asks for* a tier that lives outside `tests/unit`.

    `live` and `e2e` are requests; a negated mention (`not live`, `not (live)`) is only a
    filter over whatever is already in scope, and must not widen the collection root. The
    expression is tokenized rather than pattern-matched so that spelling cannot change the
    verdict: pytest's own tokenizer ignores extra whitespace and grouping parentheses around
    `not`, and a spelling that slipped past this check would collect — and run — the E2E
    services the tier split exists to keep out of reach.
    """
    tokens = re.findall(r"\w+|[()]", expression)
    for index, token in enumerate(tokens):
        if token not in TIER_MARKERS:
            continue
        preceding = index - 1
        while preceding >= 0 and tokens[preceding] == "(":
            preceding -= 1
        if preceding < 0 or tokens[preceding] != "not":
            return True
    return False


def _targets_e2e_dir(config: Config) -> bool:
    """Whether one of the named paths resolves inside the E2E suite directory."""
    invocation_dir = Path(str(config.invocation_params.dir))
    for arg in _cli_path_arguments(config):
        candidate = Path(arg.split("::")[0])
        if not candidate.is_absolute():
            candidate = invocation_dir / candidate
        if candidate.resolve().is_relative_to(E2E_DIR):
            return True
    return False


def _without_e2e_deselection(markexpr: str) -> str:
    """Drop the `not e2e` clause from the configured default marker expression.

    This assumes the clause is one top-level `and` term — the shape `pyproject.toml` writes,
    which a real collection run in `tests/unit/test_live_marker_isolation.py` re-checks.
    """
    terms = [term.strip() for term in markexpr.split(" and ")]
    if E2E_DESELECT_CLAUSE not in terms:
        return markexpr
    return " and ".join(term for term in terms if term != E2E_DESELECT_CLAUSE)


def pytest_configure(config: Config) -> None:
    if _cli_path_arguments(config):
        if not _cli_marker_expression(config) and _targets_e2e_dir(config):
            config.option.markexpr = _without_e2e_deselection(config.option.markexpr or "")
    elif _cli_marker_expression(config) and _names_a_tier_marker(config.option.markexpr or ""):
        config.args = [str(config.rootpath / tier) for tier in TIER_DIRS]
