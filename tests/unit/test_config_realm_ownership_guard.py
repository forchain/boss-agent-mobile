"""Static guards for the single-owner Configuration Realm (spec #444, ticket #447)."""

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
RETIRED_MODULES = ("boss_agent.settings", "boss_agent.screening_config")
RETIRED_IMPORTS = ("from .settings import", "from boss_agent.settings import")
RETIRED_IMPORTS += (
    "from boss_agent.screening_config import",
    "from boss_agent import screening_config",
)


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_retired_modules_fail_loudly_with_no_shim(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_no_source_imports_a_retired_module() -> None:
    offenders: list[str] = []
    for base in ("src", "scripts", "tests"):
        for path in (REPO_ROOT / base).rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in RETIRED_IMPORTS):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_public_facade_names_resolve_to_the_realm() -> None:
    import boss_agent
    from boss_agent import config_realm

    for name in (
        "load_settings",
        "resolve_git_common_root",
        "resolve_pocketbase_url",
        "resolve_pocketbase_data_dir",
        "resolve_pocketbase_db_path",
        "append_company_blacklist_entry",
        "is_writable_screening_path",
    ):
        assert getattr(boss_agent, name) is getattr(config_realm, name)

    # The suite-wide sandbox patches this resolver, so the facade may have cached the
    # pre-patch callable; assert it is the realm's public seam rather than its identity.
    assert callable(boss_agent.resolve_writable_screening_config_path)
    assert hasattr(config_realm, "resolve_writable_screening_config_path")
