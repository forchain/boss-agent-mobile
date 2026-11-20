"""
tests.unit.test_composition_confinement
=======================================
Where concrete adapters may be constructed (spec #443, ADR 0021).

``boss_agent/main.py`` is the single composition root: the only module that knows
every concrete adapter class. Adapters may of course construct each other inside
``adapters/``; everywhere else in production code -- the core packages and the CLI
scripts -- composition has to go through the root.

The CLI rule is sharper than "no adapter classes": a script may import a helper
function from an adapter (the verification runner composes one run from
``feed_verification``) but must not build an adapter, and must not construct an LLM
client itself. Tests are deliberately out of scope -- constructing the in-memory
doubles is the test seam.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
SRC_ROOT = REPO_ROOT / "src" / "boss_agent"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
ADAPTERS_ROOT = SRC_ROOT / "adapters"
COMPOSITION_ROOT = SRC_ROOT / "main.py"
ADAPTER_MODULE_PREFIX = "boss_agent.adapters"


def _adapter_class_names() -> set[str]:
    """Every class the adapter layer declares, so an import can be read as a class."""
    names: set[str] = set()
    for path in ADAPTERS_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names.update(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return names


def _adapter_construction_violations(path: Path, adapter_classes: set[str]) -> list[str]:
    """Calls in `path` that construct a class imported from the adapter layer."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_classes: dict[str, str] = {}
    imported_modules: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == ADAPTER_MODULE_PREFIX or node.module.startswith(
                f"{ADAPTER_MODULE_PREFIX}."
            ):
                for alias in node.names:
                    if alias.name in adapter_classes:
                        imported_classes[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == ADAPTER_MODULE_PREFIX or alias.name.startswith(
                    f"{ADAPTER_MODULE_PREFIX}."
                ):
                    imported_modules[alias.asname or alias.name.split(".")[-1]] = alias.name

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in imported_classes:
            violations.append(f"{func.id} (line {node.lineno})")
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in imported_modules
            and func.attr in adapter_classes
        ):
            violations.append(f"{func.value.id}.{func.attr} (line {node.lineno})")
    return violations


def _production_files() -> list[Path]:
    """Production modules outside the adapter layer and the composition root."""
    files = [
        path
        for path in sorted(SRC_ROOT.rglob("*.py"))
        if not path.is_relative_to(ADAPTERS_ROOT) and path != COMPOSITION_ROOT
    ]
    return files + sorted(SCRIPTS_ROOT.rglob("*.py"))


def test_concrete_adapters_are_built_only_in_adapters_and_main() -> None:
    """A core module or a CLI script that builds an adapter has bypassed the root."""
    adapter_classes = _adapter_class_names()
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {violation}"
        for path in _production_files()
        for violation in _adapter_construction_violations(path, adapter_classes)
    ]

    assert offenders == [], (
        "concrete adapters constructed outside adapters/ and main.py: " + "; ".join(offenders)
    )


def _script_bypass_violations(source: str, label: str = "<source>") -> list[str]:
    """Return script calls that invoke composition-root-only helpers directly or by attribute."""
    forbidden_calls = {"create_llm_client", "load_settings", "run_sync"}
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if called in forbidden_calls:
            offenders.append(f"{label}:{node.lineno} ({called})")
    return offenders


def test_scripts_do_not_construct_the_llm_client_or_bypass_main() -> None:
    """Only the composition root resolves settings, bridges async, or maps an LLM client."""
    offenders: list[str] = []
    for path in sorted(SCRIPTS_ROOT.rglob("*.py")):
        offenders.extend(
            _script_bypass_violations(
                path.read_text(encoding="utf-8"),
                label=str(path.relative_to(REPO_ROOT)),
            )
        )

    assert offenders == [], "scripts wire adapters directly instead of using main.py: " + ", ".join(
        offenders
    )


def test_script_bypass_guard_catches_both_bare_and_attribute_calls() -> None:
    """An attribute call like ``config_realm.load_settings()`` cannot sneak past the guard."""
    assert _script_bypass_violations("load_settings()\n", "a.py") == ["a.py:1 (load_settings)"]
    assert _script_bypass_violations("config_realm.load_settings()\n", "b.py") == [
        "b.py:1 (load_settings)"
    ]
    assert _script_bypass_violations("llm_config.create_llm_client()\n", "c.py") == [
        "c.py:1 (create_llm_client)"
    ]
    assert _script_bypass_violations("async_bridge.run_sync(coro)\n", "d.py") == [
        "d.py:1 (run_sync)"
    ]
    assert _script_bypass_violations("load_runner_settings()\n", "ok.py") == []


def test_handlers_verification_and_resume_memory_do_not_compose_llm_or_broker() -> None:
    """Handlers, verification adapters, and resume memory receive injected dependencies."""
    guarded_paths = [
        *sorted((ADAPTERS_ROOT / "inbound" / "worker" / "handlers").rglob("*.py")),
        ADAPTERS_ROOT / "inbound" / "verification" / "feed_verification.py",
        ADAPTERS_ROOT / "outbound" / "resume" / "memory.py",
        ADAPTERS_ROOT / "outbound" / "resume" / "graph.py",
    ]
    offenders: list[str] = []
    for path in guarded_paths:
        source = path.read_text(encoding="utf-8")
        if "boss_agent.broker" in source:
            offenders.append(f"{path.relative_to(REPO_ROOT)}: imports boss_agent.broker")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if called in {"create_llm_client", "build_feed_device_world", "build_triage_world"}:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno} (calls {called})"
                    )

    assert offenders == [], "non-root adapters compose dependencies directly: " + "; ".join(
        offenders
    )


def test_retired_broker_facade_fails_loudly() -> None:
    """``boss_agent.broker`` is retired; importing it raises ``ModuleNotFoundError``."""
    import importlib

    import pytest

    assert not (SRC_ROOT / "broker").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("boss_agent.broker")


def test_retired_droid_agent_core_fails_loudly() -> None:
    """``droid_agent_core`` was integrated, not shimmed; importing it must fail loudly."""
    import importlib

    import pytest

    assert not (SRC_ROOT.parent / "droid_agent_core").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("droid_agent_core")


def _top_level_imports(path: Path) -> set[str]:
    """Top-level module names imported by ``path`` (relative imports have no top level)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_appium_and_selenium_imports_are_confined_to_the_appium_adapter() -> None:
    """Only ``adapters/outbound/device/appium/`` may name the Android automation stack."""
    appium_root = ADAPTERS_ROOT / "outbound" / "device" / "appium"
    forbidden = {"appium", "selenium"}
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {sorted(forbidden & imports)}"
        for path in sorted(SRC_ROOT.rglob("*.py"))
        if not path.is_relative_to(appium_root)
        and (imports := _top_level_imports(path)) & forbidden
    ]

    assert offenders == [], "appium/selenium imported outside the Appium adapter: " + "; ".join(
        offenders
    )


def test_the_guard_sees_an_adapter_class_constructed_by_import(tmp_path: Path) -> None:
    """Prove the rule is reachable: an imported adapter class call is a violation."""
    adapter_classes = _adapter_class_names()
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from boss_agent.adapters.memory.task_broker import InMemoryTaskBroker\n"
        "broker = InMemoryTaskBroker()\n",
        encoding="utf-8",
    )

    assert "InMemoryTaskBroker" in adapter_classes
    assert "InMemoryTaskBroker (line 2)" in _adapter_construction_violations(probe, adapter_classes)
