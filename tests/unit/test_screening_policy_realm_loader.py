"""
tests/unit/test_screening_policy_realm_loader.py
================================================
Behaviour and import graph tests for Screening Policy Configuration Realm loading (Issue #312, Spec #303).

Verifies:
1. The policy loader resolves configuration through the Configuration Realm across
   each precedence level defined by the realm (explicit path -> settings.local.yaml ->
   settings.local.json -> settings.yaml -> settings.example.yaml -> legacy screening file).
2. The loaded ScreeningPolicy matches exact pre-split semantics.
3. Import graph independence: editing screening_policy leaves keyword_constants and
   enums untouched.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

from boss_agent import config_realm
from boss_agent.screening_config import load_screening_policy
from boss_agent.screening_policy import ScreeningPolicy

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_screening_policy_loader_explicit_path(tmp_path: Path):
    """Level 1 precedence: explicit config_path short-circuits the chain."""
    custom_yaml = tmp_path / "custom_screening.yaml"
    custom_yaml.write_text(
        yaml.safe_dump(
            {
                "enable_screening": True,
                "channel_preference": "direct_only",
                "max_commute_distance_km": 25.0,
                "title_whitelist": ["LLM", "Agent"],
                "title_blacklist": ["销售", "管培生"],
                "company_blacklist": ["避雷企业"],
                "jd_blacklist": ["外包", "驻场"],
            }
        ),
        encoding="utf-8",
    )

    policy = load_screening_policy(config_path=custom_yaml)
    assert policy.enable_screening is True
    assert policy.channel_preference == "direct_only"
    assert policy.max_commute_distance_km == 25.0
    assert policy.title_whitelist == ["LLM", "Agent"]
    assert policy.title_blacklist == ["销售", "管培生"]
    assert policy.company_blacklist == ["避雷企业"]
    assert policy.jd_blacklist == ["外包", "驻场"]
    assert policy.source_path == str(custom_yaml)

    # Class method load_default delegation
    policy_class = ScreeningPolicy.load_default(config_path=custom_yaml)
    assert policy_class.to_dict() == policy.to_dict()
    assert policy_class.source_path == policy.source_path


def test_screening_policy_loader_precedence_levels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify Configuration Realm precedence hierarchy across local, json, yaml, and example files."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    example_yaml = config_dir / "settings.example.yaml"
    settings_yaml = config_dir / "settings.yaml"
    settings_local_json = config_dir / "settings.local.json"
    settings_local_yaml = config_dir / "settings.local.yaml"

    # Base template
    example_yaml.write_text(
        yaml.safe_dump(
            {
                "enable_screening": True,
                "channel_preference": "all",
                "max_commute_distance_km": 40.0,
                "title_blacklist": ["销售", "电销"],
                "company_blacklist": ["公司A"],
                "jd_blacklist": ["外包"],
            }
        ),
        encoding="utf-8",
    )

    # 1. Base template only
    chain = [settings_local_yaml, settings_local_json, settings_yaml, example_yaml]
    monkeypatch.setattr(config_realm, "CONFIG_CHAIN", tuple(chain))
    monkeypatch.setattr("boss_agent.settings.DEFAULT_CONFIG_SEARCH_PATHS", chain)
    config_realm.invalidate_cache()
    p1 = load_screening_policy()
    assert p1.company_blacklist == ["公司A"]
    assert p1.channel_preference == "all"
    assert p1.source_path == str(example_yaml)

    # 2. Add settings.yaml (precedence over example)
    settings_yaml.write_text(
        yaml.safe_dump(
            {
                "company_blacklist": ["公司B"],
                "channel_preference": "direct_only",
            }
        ),
        encoding="utf-8",
    )
    config_realm.invalidate_cache()
    p2 = load_screening_policy()
    assert p2.company_blacklist == ["公司B"]
    assert p2.channel_preference == "direct_only"
    assert p2.source_path == str(settings_yaml)

    # 3. Add settings.local.json (precedence over settings.yaml)
    settings_local_json.write_text(
        json.dumps(
            {
                "company_blacklist": ["公司C"],
                "channel_preference": "headhunter_only",
            }
        ),
        encoding="utf-8",
    )
    config_realm.invalidate_cache()
    p3 = load_screening_policy()
    assert p3.company_blacklist == ["公司C"]
    assert p3.channel_preference == "headhunter_only"
    assert p3.source_path == str(settings_local_json)

    # 4. Add settings.local.yaml (highest precedence)
    settings_local_yaml.write_text(
        yaml.safe_dump(
            {
                "company_blacklist": ["公司D"],
                "channel_preference": "direct_only",
                "max_commute_distance_km": 15.0,
            }
        ),
        encoding="utf-8",
    )
    config_realm.invalidate_cache()
    p4 = load_screening_policy()
    assert p4.company_blacklist == ["公司D"]
    assert p4.channel_preference == "direct_only"
    assert p4.max_commute_distance_km == 15.0
    assert p4.source_path == str(settings_local_yaml)


def test_import_graph_independence():
    """Verify editing screening_policy leaves enums and keyword_constants untouched."""
    enums_path = REPO_ROOT / "src/boss_agent/enums.py"
    keywords_path = REPO_ROOT / "src/boss_agent/keyword_constants.py"
    policy_path = REPO_ROOT / "src/boss_agent/screening_policy.py"

    enums_tree = ast.parse(enums_path.read_text(encoding="utf-8"))
    keywords_tree = ast.parse(keywords_path.read_text(encoding="utf-8"))
    policy_tree = ast.parse(policy_path.read_text(encoding="utf-8"))

    def get_imported_modules(tree: ast.AST) -> set[str]:
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    enums_imports = get_imported_modules(enums_tree)
    keywords_imports = get_imported_modules(keywords_tree)
    policy_imports = get_imported_modules(policy_tree)

    # enums and keyword_constants MUST NOT import screening_policy
    assert "boss_agent.screening_policy" not in enums_imports
    assert ".screening_policy" not in enums_imports
    assert "boss_agent.screening_policy" not in keywords_imports
    assert ".screening_policy" not in keywords_imports

    # screening_policy must not import entities
    assert "boss_agent.job_entities" not in policy_imports
    assert "boss_agent.candidate_entities" not in policy_imports
    assert "boss_agent.search_entities" not in policy_imports
    assert "boss_agent.entities" not in policy_imports
    assert "boss_agent.models" not in policy_imports
