"""
tests/unit/test_entity_module_split_expand.py
=============================================
Fast-tier unit tests for the Shared Entity Module Split — Expand step (Issue #311, Spec #303).

Verifies:
1. Enumerations, keyword constants, and identifier helpers have ZERO dependencies on domain entities
   (verified both at the AST level and runtime).
2. The compatibility surface on boss_agent.models keeps all legacy imports functioning.
3. Serialization shapes, verdicts, and field invariants are completely preserved.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import boss_agent.enums as enums
import boss_agent.identifier_helpers as ih
import boss_agent.keyword_constants as kc

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_enums_isolated_ast():
    """Verify boss_agent.enums imports only from standard library enum and __future__."""
    source_file = REPO_ROOT / "src/boss_agent/enums.py"
    tree = ast.parse(source_file.read_text(encoding="utf-8"))

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    # Standard library only, so this module stays a leaf nothing can reach it through.
    # `typing` joins `enum` and `__future__` for the shared channel coercion helper's
    # parameter annotation: a stdlib name carries no import cycle, which is the property
    # this guard exists to protect. Domain modules remain the thing it forbids.
    assert imported_modules <= {"__future__", "enum", "typing"}, (
        f"enums.py must not import domain modules: {imported_modules}"
    )

    # Verify enum values
    assert enums.AuthStatus.AUTHENTICATED == "AUTHENTICATED"
    assert enums.JobRecordStatus.UNMATCHED == "unmatched"
    assert enums.TargetAction.AUTO_APPLY == "auto_apply"
    assert enums.TargetTaskType.AUTO_APPLY == "AUTO_APPLY"
    assert enums.ChatButtonState.COMMUNICATED == "communicated"
    assert enums.ChannelPreference.DIRECT_ONLY == "direct_only"
    assert enums.STATE_RANK[enums.JobRecordStatus.APPLIED] == 3
    assert enums.TARGET_ACTION_RANK[enums.TargetAction.AUTO_APPLY] == 3


def test_keyword_constants_isolated_ast():
    """Verify boss_agent.keyword_constants imports only from standard library re and __future__."""
    source_file = REPO_ROOT / "src/boss_agent/keyword_constants.py"
    tree = ast.parse(source_file.read_text(encoding="utf-8"))

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert imported_modules <= {"__future__", "re"}, (
        f"keyword_constants.py must not import domain modules: {imported_modules}"
    )

    assert "上海" in kc.KNOWN_CITIES
    assert "Python" in kc.COMMON_TECH_TAGS
    assert "猎" in kc.PLATFORM_BADGE_MARKERS
    assert kc.DEFAULT_COMMUNICATION_COOLDOWN_DAYS == 30
    assert kc.MIN_JD_CHARS == 30
    assert "欧阳" in kc.COMMON_COMPOUND_SURNAMES


def test_identifier_helpers_isolated_ast():
    """Verify boss_agent.identifier_helpers imports NO domain entities."""
    source_file = REPO_ROOT / "src/boss_agent/identifier_helpers.py"
    tree = ast.parse(source_file.read_text(encoding="utf-8"))

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    entity_modules = {
        "boss_agent.models",
        "boss_agent.job_entities",
        "boss_agent.candidate_entities",
        "boss_agent.search_entities",
        "boss_agent.screening_policy",
        "boss_agent.entities",
    }
    forbidden = imported_modules.intersection(entity_modules)
    assert not forbidden, f"identifier_helpers.py must not import entity modules: {forbidden}"

    # Identifier helpers
    assert ih.clean_job_title("Python高级开发工程师 &@") == "Python高级开发工程师"
    assert ih.split_recruiter_name("张三 · 猎头顾问") == ("张三", "猎头顾问")
    assert ih.normalize_recruiter_name("李四•HR") == "李四"
    assert ih.parse_recruiter_title("王女士 · 招聘专员") == "王女士"
    assert ih.format_recruiter_greeting_prefix("王女士") == "王女士您好,幸会!"
    assert len(ih.compute_job_fingerprint("科技公司", "开发", "HR")) == 64

    # Classification helpers
    assert ih.is_likely_location("北京市海淀区") is True
    assert ih.is_invalid_company_name("某科技公司") is False
    assert ih.is_invalid_company_name("未知公司") is True
    assert ih.is_masked_company_name("某知名互联网公司") is True
    assert ih.is_masked_company_name("腾讯科技") is False
    assert ih.is_headhunter_agency_name("某某人力资源服务有限公司") is True
    assert (
        ih.is_substantive_jd("这是一段超过三十个字符的有效职位描述内容，符合最低长度标准。") is True
    )


def test_retired_monolith_fails_loudly():
    """Verify boss_agent.models is retired and any attempt to import it fails loudly."""
    import sys

    sys.modules.pop("boss_agent.models", None)
    models_file = REPO_ROOT / "src/boss_agent/models.py"
    assert not models_file.exists(), "src/boss_agent/models.py must be deleted."
    with pytest.raises(ImportError):
        import boss_agent.models  # noqa: F401


def test_entities_behavior_preserving():
    """Verify domain entities retain identical construction, post_init, and serialization behavior."""
    from boss_agent.job_entities import JobCardBrief
    from boss_agent.screening_policy import ScreeningPolicy
    from boss_agent.search_entities import SavedSearch

    # JobCardBrief post_init
    card = JobCardBrief(
        title="测试开发工程师 &@",
        company_name="某某科技",
        recruiter_name="钟先生 · 猎头顾问",
        tags=["Python", "急", "猎头顾问", "自动化"],
    )
    assert card.title == "测试开发工程师"
    assert card.recruiter_name == "钟先生"
    assert card.recruiter_title == "猎头顾问"
    assert card.is_headhunter is True
    assert "急" not in card.tags
    assert "猎头顾问" not in card.tags
    assert len(card.fingerprint) == 64

    # ScreeningPolicy evaluation
    policy = ScreeningPolicy(
        title_blacklist=["销售"],
        company_blacklist=["避雷科技"],
        enable_screening=True,
    )
    passed, reason = policy.evaluate_card(title="电话销售", company_name="好的科技")
    assert passed is False
    assert "命中职位黑名单关键词" in reason

    # SavedSearch serialization
    search = SavedSearch(id="s1", name="Python Agent")
    data = search.to_dict()
    assert data["id"] == "s1"
    assert data["name"] == "Python Agent"
    assert "search" in data
    assert "filter" in data
    assert "screening_policy" in data

    restored = SavedSearch.from_dict(data)
    assert restored.id == search.id
    assert restored.name == search.name
