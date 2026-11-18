"""
tests.unit.test_core_isolation
==============================
The dependency direction into ``boss_agent.core`` (spec #443, ADR 0021).

Core is Domain + Application: it imports the standard library, its own package tree,
and the framework's LLM decision abstraction. It must not import an adapter, and it
must not import a framework (pydantic, langsmith, langgraph, langchain, rich,
requests, yaml, Appium/selenium, or anything not yet invented).

The scan is an **allowlist**: a top-level module is allowed only when it is standard
library, ``boss_agent.core``, or ``droid_agent_core``. A new framework therefore
fails the guard the day it is imported, instead of waiting for someone to add it to a
denylist that was never going to be updated.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
CORE_ROOT = REPO_ROOT / "src" / "boss_agent" / "core"

#: Third-party packages core may depend on. The framework is the one approved deviation
#: (ADR 0002): its LLM decision abstraction is the interface every caller holds.
ALLOWED_THIRD_PARTY = frozenset({"droid_agent_core"})

ALLOWED_TOP_LEVEL = frozenset(sys.stdlib_module_names) | {"boss_agent"} | ALLOWED_THIRD_PARTY

#: Named in ADR 0021 so a regression is reported in the vocabulary the ADR uses.
FORBIDDEN_FRAMEWORKS = (
    "pydantic",
    "langsmith",
    "langgraph",
    "langchain",
    "rich",
    "requests",
    "yaml",
    "Appium",
    "appium",
    "selenium",
)


def _violations(source: str) -> list[str]:
    """Every absolute import that breaks the allowlist, read from `source`."""
    violations: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules = [node.module]
        else:
            continue
        for module in modules:
            top_level = module.split(".", 1)[0]
            if top_level not in ALLOWED_TOP_LEVEL or (
                top_level == "boss_agent"
                and module != "boss_agent.core"
                and not module.startswith("boss_agent.core.")
            ):
                violations.append(module)
    return violations


def test_core_imports_only_stdlib_core_and_the_framework() -> None:
    """The whole core package points inward; adapters and frameworks are out of reach."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {module}"
        for path in sorted(CORE_ROOT.rglob("*.py"))
        for module in _violations(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], "core imports an adapter or a forbidden framework: " + "; ".join(
        offenders
    )


@pytest.mark.parametrize("framework", FORBIDDEN_FRAMEWORKS)
def test_the_allowlist_rejects_every_framework_the_adr_names(framework: str) -> None:
    """Prove the guard is live for each named framework, not merely absent from core."""
    assert _violations(f"import {framework}") == [framework]
    assert _violations(f"from {framework} import thing") == [framework]


def test_the_allowlist_rejects_a_framework_nobody_has_added_to_a_denylist() -> None:
    """A brand-new dependency fails without a guard edit, which is the point."""
    assert _violations("import some_new_framework") == ["some_new_framework"]


def test_the_allowlist_rejects_any_adapter_import() -> None:
    """Depending on an adapter would drag its transport stack into core."""
    assert _violations("from boss_agent.adapters.memory import task_broker") == [
        "boss_agent.adapters.memory"
    ]
    assert _violations("import boss_agent.main") == ["boss_agent.main"]


def test_the_allowlist_accepts_stdlib_core_and_the_framework() -> None:
    """The guard must not reject the imports core is allowed to make."""
    assert _violations("import json") == []
    assert _violations("from boss_agent.core.shared import enums") == []
    assert _violations("from droid_agent_core.llm import LLMDecisionClient") == []
