"""
tests.unit.test_candidate_entities
==================================
Fast-tier unit tests for the pure CandidateProfile domain entity (Issue #383, Spec #382).

The entity is a leaf module: stdlib only, no LLM framework, no filesystem, no network.
Every test here therefore runs in-process with no mocks, no API keys, and no I/O.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from boss_agent.candidate_entities import CandidateProfile

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE_FILE = REPO_ROOT / "src/boss_agent/candidate_entities.py"

# The 11 persisted keys are a DB contract (candidate_memory_store DDL); the order here
# mirrors the dataclass field order so a dropped or renamed field fails loudly.
EXPECTED_FIELDS = [
    "name",
    "years_of_experience",
    "education",
    "core_skills",
    "work_experiences",
    "projects",
    "project_highlights",
    "target_positions",
    "raw_summary",
    "raw_resume_text",
    "profile_document",
]


def test_schema_exposes_exactly_eleven_fields():
    """The entity carries the full 11-field schema from the memory.py migration."""
    assert [f.name for f in dataclasses.fields(CandidateProfile)] == EXPECTED_FIELDS


def test_zero_argument_construction_is_valid():
    """auto_apply.py constructs StructuredCandidateProfile() with no arguments at all."""
    profile = CandidateProfile()

    assert profile.name == "求职者"
    assert profile.years_of_experience == 0
    assert profile.education == []
    assert profile.core_skills == []
    assert profile.work_experiences == []
    assert profile.projects == []
    assert profile.project_highlights == []
    assert profile.target_positions == []
    assert profile.raw_summary == ""
    assert profile.raw_resume_text == ""
    assert profile.profile_document == ""


def test_single_keyword_construction_is_valid():
    """test_job_matching.py:68 constructs with exactly one keyword argument."""
    profile = CandidateProfile(name="李四")

    assert profile.name == "李四"
    assert profile.core_skills == []


def test_mutable_defaults_are_not_shared_between_instances():
    """A field(default_factory=list) mistake would leak state across profiles."""
    first = CandidateProfile()
    second = CandidateProfile()

    first.core_skills.append("Python")

    assert second.core_skills == []


@pytest.mark.parametrize("field_name", EXPECTED_FIELDS)
def test_every_field_has_a_default(field_name):
    """No field may be required: production callers pass zero or one arguments.

    A list field declares its default through ``default_factory``, so a field counts as
    defaulted when either ``default`` or ``default_factory`` is populated.
    """
    (spec,) = [f for f in dataclasses.fields(CandidateProfile) if f.name == field_name]

    assert (
        spec.default is not dataclasses.MISSING or spec.default_factory is not dataclasses.MISSING
    )


def test_to_dict_never_returns_none_for_array_fields():
    """candidate_memory_store persists these six; a None would break the column set."""
    profile = CandidateProfile(
        education=None,  # type: ignore[arg-type]
        core_skills=None,  # type: ignore[arg-type]
        work_experiences=None,  # type: ignore[arg-type]
        projects=None,  # type: ignore[arg-type]
        project_highlights=None,  # type: ignore[arg-type]
        target_positions=None,  # type: ignore[arg-type]
    )

    data = profile.to_dict()

    for key in (
        "education",
        "core_skills",
        "work_experiences",
        "projects",
        "project_highlights",
        "target_positions",
    ):
        assert data[key] == [], f"{key} must serialize to a list, never None"


def test_to_dict_returns_exactly_eleven_persisted_keys():
    """to_dict() is the persisted DB contract -- no extra keys, no dropped keys."""
    data = CandidateProfile(name="张三").to_dict()

    assert list(data.keys()) == EXPECTED_FIELDS
    assert len(data) == 11


def test_to_dict_collapses_raw_summary_onto_profile_document():
    """Both text fields are stored identical; the profile document wins when present."""
    profile = CandidateProfile(raw_summary="只有总结", profile_document="  # 画像文档  ")

    data = profile.to_dict()

    assert data["profile_document"] == "# 画像文档"
    assert data["raw_summary"] == "# 画像文档"


def test_from_dict_round_trip_preserves_populated_fields():
    """A populated payload survives from_dict -> to_dict -> from_dict unchanged."""
    data = {
        "name": "张三",
        "years_of_experience": 8,
        "education": [{"school": "沙迦美国大学", "degree": "硕士", "major": "计算机工程"}],
        "core_skills": ["Python", "FastAPI"],
        "work_experiences": [{"company": "前沿人工智能实验室", "role": "首席 Agent 架构师"}],
        "projects": [{"name": "Boss Agent Mobile", "tech_stack": ["Python", "Appium"]}],
        "project_highlights": [{"name": "项目1", "description": "描述1"}],
        "target_positions": ["AI Agent 专家"],
        "raw_resume_text": "原始简历全文",
    }

    profile = CandidateProfile.from_dict(data)
    round_tripped = CandidateProfile.from_dict(profile.to_dict())

    assert round_tripped.name == "张三"
    assert round_tripped.years_of_experience == 8
    assert round_tripped.core_skills == ["Python", "FastAPI"]
    assert round_tripped.education == data["education"]
    assert round_tripped.work_experiences == data["work_experiences"]
    assert round_tripped.projects == data["projects"]
    assert round_tripped.target_positions == ["AI Agent 专家"]
    assert round_tripped.raw_resume_text == "原始简历全文"


@pytest.mark.parametrize(
    "null_fields",
    [
        {},
        {"education": None, "core_skills": None, "work_experiences": None},
        {"projects": None, "project_highlights": None, "target_positions": None},
        {"education": [], "core_skills": [], "work_experiences": [], "projects": []},
    ],
)
def test_from_dict_coerces_null_collection_fields_to_lists(null_fields):
    """None collection fields must never survive as None -- a consumer would iterate them."""
    profile = CandidateProfile.from_dict({"name": "王五", **null_fields})

    for key in ("education", "core_skills", "work_experiences", "projects", "project_highlights"):
        assert isinstance(getattr(profile, key), list)
    assert isinstance(profile.target_positions, list)


def test_from_dict_defaults_name_to_placeholder_when_absent():
    """An absent or empty name falls back to the shared Chinese placeholder."""
    assert CandidateProfile.from_dict({}).name == "求职者"
    assert CandidateProfile.from_dict({"name": None}).name == "求职者"
    assert CandidateProfile.from_dict({"name": ""}).name == "求职者"


def test_from_dict_flattens_dict_shaped_skills_to_key_value_strings():
    """A nested mapping flattens to one 'key: a, b, c' string per key, order preserved."""
    profile = CandidateProfile.from_dict(
        {
            "name": "周黄金",
            "core_skills": {
                "AI与智能体": ["Claude", "Codex", "Langchain"],
                "编程语言": ["Python", "Golang"],
            },
        }
    )

    assert profile.core_skills == [
        "AI与智能体: Claude, Codex, Langchain",
        "编程语言: Python, Golang",
    ]


def test_from_dict_flattens_dict_shaped_skills_with_scalar_values():
    """A mapping whose values are scalars still renders as 'key: value'."""
    profile = CandidateProfile.from_dict({"core_skills": {"语言": "Python", "框架": "FastAPI"}})

    assert profile.core_skills == ["语言: Python", "框架: FastAPI"]


def test_from_dict_flattens_nested_dicts_inside_a_skill_list():
    """A list of single-key dicts is the other shape the LLM emits; it flattens the same way."""
    profile = CandidateProfile.from_dict(
        {"core_skills": [{"编程语言": ["Python", "Go"]}, {"框架": "FastAPI"}]}
    )

    assert profile.core_skills == ["编程语言: Python, Go", "框架: FastAPI"]


def test_from_dict_keeps_plain_string_skills_untouched():
    """The already-flat shape is the happy path and must pass through verbatim."""
    profile = CandidateProfile.from_dict({"core_skills": ["Python", "FastAPI"]})

    assert profile.core_skills == ["Python", "FastAPI"]


def test_from_dict_promotes_project_highlights_into_projects_and_back():
    """Either collection may arrive alone; from_dict derives the other side from it."""
    from_highlights = CandidateProfile.from_dict(
        {"project_highlights": [{"name": "项目1", "description": "描述1"}]}
    )
    assert from_highlights.projects[0]["name"] == "项目1"

    from_projects = CandidateProfile.from_dict(
        {"projects": [{"name": "项目2", "description": "描述2", "achievements": "指标"}]}
    )
    assert from_projects.project_highlights == [{"name": "项目2", "description": "描述2"}]


def test_from_dict_lifts_raw_summary_into_profile_document():
    """Legacy payloads carry only raw_summary; the document is backfilled from it."""
    profile = CandidateProfile.from_dict({"raw_summary": "  一段总结  "})

    assert profile.profile_document == "一段总结"
    assert profile.raw_summary == "一段总结"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [(19, 19), ("19", 19), (0, 0), (None, 0), ("", 0), ("not-a-number", 0)],
)
def test_from_dict_coerces_years_of_experience_to_int(raw_value, expected):
    """A non-numeric experience value degrades to 0 rather than raising."""
    assert CandidateProfile.from_dict({"years_of_experience": raw_value}).years_of_experience == (
        expected
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "just a string",
        42,
        {"core_skills": "Python"},
        {"core_skills": {"框架": {"nested": "deep"}}},
        {"projects": "not-a-list"},
        {"work_experiences": [{"no_keys_at_all": 1}]},
    ],
)
def test_from_dict_survives_malformed_input_without_raising(payload):
    """Garbage in must not raise -- from_dict runs on untrusted LLM output."""
    profile = CandidateProfile.from_dict(payload)

    assert isinstance(profile.core_skills, list)
    assert isinstance(profile.target_positions, list)
    assert profile.to_dict()["profile_document"] == ""


# The fixture below uses CJK *compatibility* ideographs (U+2F20 etc.), not the normal
# U+7855 form. Only `unicodedata.normalize("NFKC", ...)` folds them together, which is
# why the extractor is pinned to normalizing its input.
CJK_COMPAT_TEXT = "硕⼠毕业于沙迦美国⼤学计算机⼯程专业\n"


def test_from_dict_self_heals_education_from_profile_document_alone():
    """The hard requirement: no normalize() call and no raw_resume_text.

    `from_dict` must heal education from the profile document by itself.
    """
    data = {
        "name": "周黄金",
        "years_of_experience": 19,
        "education": [],
        "profile_document": (
            "- **教育背景**:\n"
            "  - 沙迦美国大学（American University of Sharjah）| 硕士 | 计算机工程（2019 - 2022）\n"
            "  - 湘潭大学 | 本科 | 计算机科学与技术（2004 - 2008）"
        ),
    }

    profile = CandidateProfile.from_dict(data)

    assert len(profile.education) >= 2
    assert any(e["degree"] == "硕士" and "沙迦美国大学" in e["school"] for e in profile.education)
    assert any(e["degree"] == "本科" and "湘潭大学" in e["school"] for e in profile.education)


def test_education_entries_use_school_degree_major_keys():
    """format_for_prompt and the storage layer both read these three exact key names."""
    profile = CandidateProfile.from_dict(
        {"profile_document": "- 湘潭大学 | 本科 | 计算机科学与技术"}
    )

    assert set(profile.education[0]) == {"school", "degree", "major"}


def test_self_healing_applies_nfkc_normalization_to_compatibility_ideographs():
    """Compatibility ideographs must fold to their normal forms, or nothing matches."""
    profile = CandidateProfile.from_dict(
        {
            "profile_document": (
                "- 沙迦美国大学 | 硕⼠ | 计算机⼯程\n- 湘潭大学 | 本科 | 计算机科学与技术"
            )
        }
    )

    assert len(profile.education) >= 2
    assert any(e["degree"] == "硕士" and "沙迦美国大学" in e["school"] for e in profile.education)
    assert any(e["degree"] == "本科" and "湘潭大学" in e["school"] for e in profile.education)


def test_education_extraction_strategy_one_pipe_separated():
    """Strategy 1: '- School | Degree | Major'."""
    profile = CandidateProfile.from_dict(
        {"profile_document": "- 清华大学 | 硕士 | 人工智能\n- 北京大学 | 学士 | 数学"}
    )

    assert [e["school"] for e in profile.education] == ["清华大学", "北京大学"]
    assert [e["degree"] for e in profile.education] == ["硕士", "学士"]


def test_education_extraction_strategy_two_narrative():
    """Strategy 2: '硕士毕业于沙迦美国大学计算机工程专业', with 本科 as the default degree."""
    profile = CandidateProfile.from_dict(
        {"profile_document": "硕士毕业于沙迦美国大学计算机工程专业，擅长工程研究。"}
    )

    assert profile.education[0]["school"] == "沙迦美国大学"
    assert profile.education[0]["degree"] == "硕士"

    defaulted = CandidateProfile.from_dict({"profile_document": "本科毕业于湘潭大学计算机专业。"})
    assert defaulted.education[0]["degree"] == "本科"


def test_education_extraction_strategy_three_space_delimited():
    """Strategy 3: '清华大学 硕士 人工智能'."""
    profile = CandidateProfile.from_dict({"profile_document": "- 清华大学 硕士 人工智能方向"})

    assert profile.education[0]["school"] == "清华大学"
    assert profile.education[0]["degree"] == "硕士"
    assert "人工智能" in profile.education[0]["major"]


def test_education_extraction_deduplicates_by_base_school_name():
    """The same school listed twice with and without its English name yields one entry."""
    profile = CandidateProfile.from_dict(
        {
            "profile_document": (
                "- 沙迦美国大学（American University of Sharjah）| 硕士 | 计算机工程\n"
                "- 沙迦美国大学 | 硕士 | 计算机工程"
            )
        }
    )

    assert len(profile.education) == 1


def test_explicit_education_is_never_overwritten_by_healing():
    """Healing is a fallback: a supplied education list is the authority."""
    supplied = [{"school": "自定义大学", "degree": "博士", "major": "物理"}]

    profile = CandidateProfile.from_dict(
        {"education": supplied, "profile_document": "- 清华大学 | 硕士 | 人工智能"}
    )

    assert profile.education == supplied


def test_education_healing_falls_back_to_raw_resume_text_when_document_is_empty():
    """With no document, the resume text is the only corpus available."""
    profile = CandidateProfile.from_dict(
        {"profile_document": "", "raw_resume_text": "- 湘潭大学 | 本科 | 计算机科学与技术"}
    )

    assert profile.education[0]["school"] == "湘潭大学"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"profile_document": ""},
        {"profile_document": "没有教育背景的一段普通文字"},
        {"profile_document": "-  |  |  "},
        {"profile_document": "毕业于"},
    ],
)
def test_education_healing_on_unparseable_text_yields_empty_list_without_raising(payload):
    """Unparseable text must produce an empty list, never an exception."""
    profile = CandidateProfile.from_dict(payload)

    assert profile.education == []
    assert isinstance(profile.education, list)


def test_narrative_healing_folds_compatibility_ideographs_from_raw_text():
    """NFKC is what makes this narrative line parseable at all.

    ``硕⼠``/``⼤学``/``⼯程`` are U+2F20-range compatibility forms, so without
    normalization the narrative regex matches nothing and the entry is silently lost.
    """
    profile = CandidateProfile.from_dict({"raw_resume_text": CJK_COMPAT_TEXT})

    assert len(profile.education) == 1
    assert profile.education[0]["school"] == "沙迦美国大学"
    assert profile.education[0]["degree"] == "硕士"


def test_format_for_prompt_renders_metadata_block():
    """The metadata block is the fixed header of every greeting prompt."""
    profile = CandidateProfile(
        name="张三",
        years_of_experience=8,
        education=[{"school": "北京大学", "degree": "硕士", "major": "计算机应用技术"}],
        core_skills=["Python", "LLM Agent"],
        target_positions=["AI Agent 专家"],
    )

    prompt = profile.format_for_prompt()

    assert "姓名: 张三" in prompt
    assert "工作经验: 8年" in prompt
    assert "教育背景: 北京大学 (硕士 - 计算机应用技术)" in prompt
    assert "核心技能栈:\n- Python\n- LLM Agent" in prompt
    assert "期望职位: AI Agent 专家" in prompt


def test_format_for_prompt_falls_back_to_unstated_markers():
    """Empty collections render as 未注明 rather than as a blank line."""
    prompt = CandidateProfile().format_for_prompt()

    assert "教育背景: 未注明" in prompt
    assert "核心技能栈:\n未注明" in prompt
    assert "期望职位: 未注明" in prompt


def test_format_for_prompt_renders_work_and_project_sections():
    """Work and project rendering is pinned downstream by test_resume_memory.py."""
    profile = CandidateProfile(
        name="资深技术专家",
        years_of_experience=12,
        work_experiences=[
            {
                "company": "前沿人工智能实验室",
                "role": "首席 Agent 架构师",
                "start_date": "2023-01",
                "end_date": "至今",
                "department": "移动端自动化工程部",
                "responsibilities": "主导 Appium 与大模型推理编排的底层架构设计。",
                "achievements": "将 Boss 直聘自动化投递成功率由 65% 提升至 98.5%",
                "raw_details": "深入研究 Android 无障碍与 UI Automator 协议。",
            }
        ],
        projects=[
            {
                "name": "Boss Agent Mobile",
                "role": "项目负责人",
                "start_date": "2024-03",
                "end_date": "2024-09",
                "tech_stack": ["Python", "FastAPI", "Appium", "SvelteKit", "PocketBase"],
                "description": "基于大模型与 Android 自动化全闭环求职智能体系统。",
                "achievements": "实现端到端任务流认领。",
                "raw_details": "引入 State Stream Broker。",
            }
        ],
        raw_resume_text="这是候选人的完整原始简历全文，包含开源项目与专利列表。",
    )

    prompt = profile.format_for_prompt()

    assert "工作经历 (无损完整履历):" in prompt
    assert (
        "- 【前沿人工智能实验室】首席 Agent 架构师 (2023-01 ~ 至今) | 部门: 移动端自动化工程部"
        in prompt
    )
    assert "  工作职责: 主导 Appium 与大模型推理编排的底层架构设计。" in prompt
    assert "  核心业绩与量化成果: 将 Boss 直聘自动化投递成功率由 65% 提升至 98.5%" in prompt
    assert "  详细履历: 深入研究 Android 无障碍与 UI Automator 协议。" in prompt
    assert "项目经历 (完整架构与指标):" in prompt
    assert "【Boss Agent Mobile】 (角色: 项目负责人)" in prompt
    assert "技术栈: Python, FastAPI, Appium, SvelteKit, PocketBase" in prompt
    assert "  项目背景与架构: 基于大模型与 Android 自动化全闭环求职智能体系统。" in prompt
    assert "  技术攻坚细节: 引入 State Stream Broker。" in prompt


def test_format_for_prompt_appends_ground_truth_resume_text():
    """The unabridged resume is appended after the structured sections."""
    profile = CandidateProfile(raw_resume_text="包含开源项目与专利列表的全文")

    prompt = profile.format_for_prompt()

    assert prompt.endswith("[原始简历无损语料 (Ground Truth 参考)]\n包含开源项目与专利列表的全文")


def test_format_for_prompt_includes_lossless_document_when_markdown_like():
    """A '#'-bearing document is emitted as the lossless profile block."""
    prompt = CandidateProfile(profile_document="# 候选人全景画像\n\n## 1. 概览").format_for_prompt()

    assert "[候选人结构化全景画像 (Lossless Profile Document)]" in prompt
    assert "# 候选人全景画像" in prompt


def test_format_for_prompt_includes_lossless_document_when_long():
    """A document over 200 characters also qualifies as lossless."""
    prompt = CandidateProfile(profile_document="详" * 201).format_for_prompt()

    assert "[候选人结构化全景画像 (Lossless Profile Document)]" in prompt


def test_format_for_prompt_omits_sections_when_there_is_nothing_to_say():
    """No work or projects means no empty section headers."""
    prompt = CandidateProfile(name="张三").format_for_prompt()

    assert "工作经历" not in prompt
    assert "项目经历" not in prompt
    assert "[原始简历无损语料 (Ground Truth 参考)]" not in prompt


def test_format_for_prompt_falls_back_to_highlights_when_projects_are_empty():
    """Highlights are the legacy project shape and still render a project section."""
    profile = CandidateProfile(
        projects=[],
        project_highlights=[{"name": "项目1", "description": "描述1"}],
    )

    prompt = profile.format_for_prompt()

    assert "【项目1】" in prompt
    assert "  项目背景与架构: 描述1" in prompt


def test_format_for_prompt_tolerates_entries_missing_every_optional_key():
    """Sparse entries must render rather than raise -- the data comes from an LLM."""
    prompt = CandidateProfile(work_experiences=[{}], projects=[{}]).format_for_prompt()

    assert "- 【】" in prompt


def test_format_for_prompt_on_a_default_profile_is_exactly_the_metadata_block():
    """The empty profile is the baseline every greeting path can hit."""
    assert CandidateProfile().format_for_prompt() == (
        "姓名: 求职者\n工作经验: 0年\n教育背景: 未注明\n核心技能栈:\n未注明\n期望职位: 未注明"
    )


def test_structured_alias_is_an_identity_alias_not_a_subclass():
    """screening.py:835 dispatches on isinstance(profile, StructuredCandidateProfile)."""
    from boss_agent.candidate_entities import StructuredCandidateProfile

    assert StructuredCandidateProfile is CandidateProfile
    assert isinstance(CandidateProfile(name="张三"), StructuredCandidateProfile)


def test_memory_reexports_the_same_class_object():
    """memory.py must hand out the identical class, or isinstance() silently fails."""
    from boss_agent.candidate_entities import StructuredCandidateProfile as canonical
    from boss_agent.memory import StructuredCandidateProfile as legacy

    assert legacy is canonical is CandidateProfile


def test_memory_reexports_candidate_profile_by_its_canonical_name():
    """The new name is importable from the legacy module too."""
    from boss_agent.memory import CandidateProfile as canonical

    assert canonical is CandidateProfile


def test_memory_still_exports_its_legacy_surface():
    """12 test files, 3 scripts, and __init__.py import these four names from memory."""
    from boss_agent import memory

    for symbol in (
        "ProfileNormalizer",
        "ResumeMemoryManager",
        "ResumeTextExtractor",
        "StructuredCandidateProfile",
    ):
        assert hasattr(memory, symbol), f"memory.{symbol} was dropped"


def test_profile_normalizer_education_extraction_delegates_to_the_entity_module():
    """There must be exactly one implementation of the education heuristic."""
    from boss_agent.candidate_entities import extract_education_from_text
    from boss_agent.memory import ProfileNormalizer

    corpus = "- 沙迦美国大学 | 硕士 | 计算机工程\n- 湘潭大学 | 本科 | 计算机科学与技术"

    assert ProfileNormalizer._extract_education_from_text(corpus) == extract_education_from_text(
        corpus
    )
    assert len(ProfileNormalizer._extract_education_from_text(corpus)) == 2


def test_entities_facade_reexports_both_candidate_profile_names():
    """The facade is the public entry point and must expose both spellings."""
    from boss_agent import entities

    assert entities.CandidateProfile is CandidateProfile
    assert entities.StructuredCandidateProfile is CandidateProfile
    assert "CandidateProfile" in entities.__all__
    assert "StructuredCandidateProfile" in entities.__all__


def test_entities_facade_keeps_its_pre_existing_exports():
    """Adding the candidate exports must not disturb the rest of the facade."""
    from boss_agent import entities

    for name in (
        "CandidateProfile",
        "FilterConfig",
        "JobCardBrief",
        "JobLocationLine",
        "JobPosting",
        "JobRecord",
        "SavedSearch",
        "SearchConfig",
        "_saved_search_max_jobs_default",
    ):
        assert name in entities.__all__, f"entities.__all__ lost {name}"


# A leaf entity must be importable without dragging an LLM stack, a tracing SDK, or any
# I/O in with it. Proving that at runtime would need a fresh interpreter, but the fast
# tier forbids spawning processes (tests/unit/conftest.py::fast_unit_boundary_guard), so
# this walks the import graph statically instead. That is a stronger claim than a
# sys.modules diff: it proves what *could* load, not merely what did.
FORBIDDEN_IN_CLOSURE = {
    "langsmith",
    "rich",
    "requests",
    "httpx",
    "openai",
    "droid_agent_core",
    "boss_agent.memory",
    "boss_agent.llm_config",
    "boss_agent.graph",
    "boss_agent.candidate_memory_store",
}

PACKAGE_ROOT = REPO_ROOT / "src" / "boss_agent"


def _known_first_party_modules() -> dict[str, Path]:
    """Map every importable boss_agent module name to its source file."""
    modules: dict[str, Path] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT / "src").with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    return modules


def _direct_imports(path: Path, module_name: str) -> set[str]:
    """Return the absolute names a module imports AT IMPORT TIME, relatives resolved.

    Only top-level statements count. A function-local import -- such as
    ``rejection.py`` deferring ``llm_config`` to call time -- does not run when the
    module is imported, so counting it would over-approximate what a plain import loads.

    Note this deliberately analyses ``entities.py`` rather than executing an import.
    Importing *any* submodule first executes ``boss_agent/__init__.py``, which eagerly
    pulls in ``memory`` (and through it langsmith/rich/droid_agent_core) under
    ``contextlib.suppress(ImportError)``. That is package-initialiser behaviour, outside
    this ticket's scope, and it is the reason the guarantee is proven per module.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module_name.rsplit(".", 1)[0] if "." in module_name else ""

    resolved: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for imported in node.names:
                resolved.add(imported.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                anchor = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                resolved.add(".".join([*anchor, base] if base else anchor))
            elif base:
                resolved.add(base)
    return resolved


def _first_party_import_closure(entry: str) -> set[str]:
    """Every boss_agent module transitively reachable from `entry`, including itself."""
    known = _known_first_party_modules()
    seen: set[str] = set()
    queue = [entry]
    while queue:
        current = queue.pop()
        if current in seen or current not in known:
            continue
        seen.add(current)
        queue.extend(_direct_imports(known[current], current))
    return seen


def test_candidate_entities_reaches_no_other_first_party_module():
    """The strongest form of the purity rule: this module is a genuine leaf.

    A zero-length closure cannot be weakened later by someone adding a first-party
    import, because that first-party module would appear here.
    """
    assert _first_party_import_closure("boss_agent.candidate_entities") == {
        "boss_agent.candidate_entities"
    }


@pytest.mark.parametrize("entry", ["boss_agent.candidate_entities", "boss_agent.entities"])
def test_entity_import_closure_excludes_llm_and_network_modules(entry):
    """Neither the entity nor the facade can reach an LLM client, tracer, or network lib."""
    closure = _first_party_import_closure(entry)

    offending = {
        name
        for module in closure
        for name in _direct_imports(_known_first_party_modules()[module], module)
        if name in FORBIDDEN_IN_CLOSURE
    }
    assert not offending, f"{entry} can transitively import {sorted(offending)}"


def test_candidate_entities_imports_only_stdlib_at_the_ast_level():
    """Direct-import allowlist, mirroring test_entity_module_split_expand.py's house style."""
    tree = ast.parse(SOURCE_FILE.read_text(encoding="utf-8"))

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                imported_modules.add(imported.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert imported_modules <= {"__future__", "re", "unicodedata", "dataclasses", "typing"}, (
        f"candidate_entities.py must import stdlib only, found: {imported_modules}"
    )


def test_candidate_entities_never_references_the_memory_module():
    """A single reference would reintroduce the candidate_entities -> memory cycle."""
    source = SOURCE_FILE.read_text(encoding="utf-8")

    assert "boss_agent.memory" not in source
    assert "from .memory" not in source
    assert "from boss_agent import memory" not in source


def test_candidate_entities_does_not_import_json_or_pathlib():
    """The entity serializes to plain dicts; it has no file or JSON concern of its own."""
    tree = ast.parse(SOURCE_FILE.read_text(encoding="utf-8"))

    imported_modules = {
        imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
    }

    assert imported_modules.isdisjoint({"json", "pathlib", "os", "io"})
