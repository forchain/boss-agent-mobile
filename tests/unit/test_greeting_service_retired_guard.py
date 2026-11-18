"""Static guards for the Candidate Screener greeting collapse (spec #444, ticket #448)."""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
RETIRED = "JobMatchGreetingService"


def _imported_names(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "boss_agent.matching"
            or isinstance(node, ast.Import)
        ):
            names.update(alias.name for alias in node.names)
    return names


def test_no_module_imports_the_retired_service() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for base in ("src", "scripts", "tests", "web/src")
        for path in (REPO_ROOT / base).rglob("*.py")
        if RETIRED in _imported_names(path)
    ]
    assert offenders == []


def test_the_symbol_is_gone_from_the_matching_module_and_facade() -> None:
    import boss_agent
    from boss_agent import matching

    assert not hasattr(matching, RETIRED)
    with pytest.raises(AttributeError):
        getattr(boss_agent, RETIRED)


def test_candidate_screener_owns_the_greeting_surface() -> None:
    from boss_agent.screening import CandidateScreener

    for name in ("evaluate_and_draft_greeting", "refine_with_critique", "refine_greeting_prompt"):
        assert callable(getattr(CandidateScreener, name))
