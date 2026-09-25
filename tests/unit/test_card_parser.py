"""
tests/unit/test_card_parser.py
==============================
The Card Parser: text in, facets out, no device involved.

These are the tests the extraction exists to make possible. Before it, exercising a
classification decision meant scripting a fake `find_elements` accessibility tree, so
the seam under test was the driver protocol rather than the parsing rule. Now each
branch of the classifier is a row in a table.

`test_extraction_is_frozen.py`-style characterization lives with the page-object tests
that were kept; this file pins the decisions themselves.
"""

import ast
from pathlib import Path

import pytest

from boss_agent.card_parser import (
    CLASSIFIER_RULES,
    CardFacets,
    classify_text_nodes,
    company_duplicates_card_title,
    needs_text_fallback,
    parse_card,
    parse_company_scale_industry,
    parse_recruiter_info,
)
from boss_agent.models import PLATFORM_BADGE_MARKERS, normalize_recruiter_name

REPO_ROOT = Path(__file__).parents[2]


def _facets(**kwargs) -> CardFacets:
    return CardFacets(**kwargs)


# --------------------------------------------------------------------------- #
# Priority-1 locator reads
# --------------------------------------------------------------------------- #


def test_complete_locator_reads_are_interpreted_without_any_text_pass() -> None:
    parsed = parse_card(
        _facets(
            title="大模型算法工程师 &@",
            company="深至科技 100-499人 人工智能",
            recruiter="钟先生 · 猎头顾问",
            salary="40-60K",
            location="上海",
            tags=("10年以上", "硕士"),
        )
    )
    assert parsed is not None
    assert parsed.title == "大模型算法工程师"
    assert parsed.company_name == "深至科技"
    assert parsed.company_scale == "100-499人"
    assert parsed.industry == "人工智能"
    assert parsed.recruiter_name == "钟先生"
    assert parsed.recruiter_title == "猎头顾问"
    assert parsed.is_headhunter is True
    assert parsed.tags == ("10年以上", "硕士")


def test_a_company_that_is_really_the_title_is_rejected_then_recovered_from_text() -> None:
    """Boss's recommendation cards render the position name in the company row.

    Left alone it poisons the fingerprint — the same job saves twice under different
    keys — and smuggles blacklisted employers past company-name screening.
    """
    facets = _facets(title="Senior AI Agent Engineer（英语口语）", company="Senior AI Agent Engineer（英语口语）")
    assert needs_text_fallback(facets) is True, (
        "the fallback decision must be made on the interpreted reads: the raw company "
        "read looks complete but interprets to nothing"
    )

    parsed = parse_card(facets, ["塔塔 1000-999人 计算机软件"])
    assert parsed is not None
    assert parsed.company_name == "塔塔"
    assert parsed.company_scale == "1000-999人"
    assert parsed.industry == "计算机软件"


def test_a_complete_card_never_consults_its_text_nodes() -> None:
    """The fallback is a fallback: an explicit read is never overridden by raw text."""
    parsed = parse_card(
        _facets(title="算法工程师", company="深至科技", recruiter="王女士"),
        ["某个不该被采纳的公司名", "不该出现的薪水 99-100万"],
    )
    assert parsed is not None
    assert parsed.company_name == "深至科技"
    assert parsed.salary_range == ""


def test_an_incomplete_card_is_skipped_rather_than_persisted() -> None:
    assert parse_card(_facets(title="算法工程师")) is None
    assert parse_card(_facets(company="深至科技")) is None
    assert parse_card(_facets(title="算法工程师", company="未知公司")) is None


def test_a_location_read_holding_the_recruiter_title_is_moved_to_the_recruiter() -> None:
    """A known mis-read: the city locator returns the recruiter's title instead."""
    parsed = parse_card(
        _facets(title="算法工程师", company="深至科技", location="猎头顾问")
    )
    assert parsed is not None
    assert parsed.location == ""
    assert parsed.recruiter_title == "猎头顾问"
    assert parsed.is_headhunter is True


# --------------------------------------------------------------------------- #
# The classifier, branch by branch
# --------------------------------------------------------------------------- #

#: (case, text nodes, expected subset of the parsed card). Each row exercises one branch
#: of the ordered rule list; adding a Boss card layout variant is a row here.
CLASSIFIER_CASES: list[tuple[str, list[str], dict]] = [
    (
        "salary-first",
        ["大模型算法工程师", "40-60K", "深至科技 100-499人 人工智能"],
        {"title": "大模型算法工程师", "salary_range": "40-60K", "company_name": "深至科技"},
    ),
    (
        "recruiter-with-attached-city",
        ["算法工程师", "深至科技", "冯女士·总经理助理 上海"],
        {"recruiter_name": "冯女士", "recruiter_title": "总经理助理", "location": "上海"},
    ),
    (
        "standalone-city",
        ["算法工程师", "深至科技", "深圳"],
        {"location": "深圳"},
    ),
    (
        "requirement-tags",
        ["算法工程师", "深至科技", "5-10年", "本科"],
        {"tags": ("5-10年", "本科")},
    ),
    (
        "company-with-scale-and-industry",
        ["算法工程师", "深至科技 1000-9999人 游戏"],
        {"company_name": "深至科技", "company_scale": "1000-9999人", "industry": "游戏"},
    ),
    (
        "standalone-scale",
        ["算法工程师", "深至科技", "10000人以上"],
        {"company_scale": "10000人以上"},
    ),
    (
        "long-line-becomes-the-digest",
        ["算法工程师", "深至科技", "负责企业级大模型应用与 Agent 工作流平台的建设与落地"],
        {"snippet": "负责企业级大模型应用与 Agent 工作流平台的建设与落地"},
    ),
    (
        "short-line-becomes-a-tag",
        ["算法工程师", "深至科技", "分布式技术"],
        {"tags": ("分布式技术",)},
    ),
    (
        "headhunter-recruiter-line",
        ["算法工程师", "深至科技", "林先生·资深猎头顾问"],
        {"recruiter_name": "林先生", "recruiter_title": "资深猎头顾问", "is_headhunter": True},
    ),
]


@pytest.mark.parametrize(
    ("case", "text_nodes", "expected"),
    CLASSIFIER_CASES,
    ids=[case for case, _nodes, _expected in CLASSIFIER_CASES],
)
def test_classifier_branch(case: str, text_nodes: list[str], expected: dict) -> None:
    """One row of the ordered rule table: text nodes in, facets out."""
    parsed = parse_card(CardFacets(), text_nodes)
    assert parsed is not None, f"{case}: the classifier produced no usable card"
    for field, want in expected.items():
        assert getattr(parsed, field) == want, f"{case}: {field}"


def test_badge_markers_are_never_classified_as_content() -> None:
    """Platform chrome is dropped before any rule sees it."""
    parsed = parse_card(CardFacets(), ["猎", "新", "急", "热", "置顶", "算法工程师", "深至科技"])
    assert parsed is not None
    assert parsed.title == "算法工程师"
    assert parsed.company_name == "深至科技"
    assert not PLATFORM_BADGE_MARKERS & set(parsed.tags)


def test_the_rule_list_is_the_extension_point() -> None:
    """The classifier is an ordered rule list, not a branch chain in a 228-line method."""
    assert len(CLASSIFIER_RULES) == 8
    # Ordering is load-bearing: a recruiter line must be claimed before the location
    # rule can mistake "招聘专员" for a city.
    names = [rule.__name__ for rule in CLASSIFIER_RULES]
    assert names.index("_rule_recruiter") < names.index("_rule_location")
    assert names.index("_rule_salary") == 0, "salary leads: it is the most distinctive"


def test_a_later_rule_cannot_steal_a_claimed_field() -> None:
    """A rule only claims a node while its field is still empty."""
    parsed = parse_card(CardFacets(), ["算法工程师", "深至科技", "上海", "北京"])
    assert parsed is not None
    assert parsed.location == "上海", "the first location wins; the second is not a location field"


def test_one_text_node_is_claimed_by_exactly_one_rule() -> None:
    """A node that could match several rules lands in one field, not several.

    "3-5年" is a candidate for the requirement-tag rule and the tags/snippet tail;
    the first rule to claim it must stop the walk for that node, or the same line
    ends up counted as two different facets.
    """
    from boss_agent.card_parser import _Draft

    draft = _Draft(title="算法工程师", company="深至科技")
    classify_text_nodes(["3-5年"], draft)
    assert draft.tags == ["3-5年"]
    assert draft.snippet == ""


# --------------------------------------------------------------------------- #
# Pure classifiers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("钟先生 · 猎头顾问", ("钟先生", "猎头顾问", True)),
        ("钟先生·猎头顾问", ("钟先生", "猎头顾问", True)),
        ("钟先生 ·", ("钟先生", "", False)),
        ("农女士 · 高级招聘专员", ("农女士", "高级招聘专员", False)),
        ("张先生", ("张先生", "", False)),
        ("", ("", "", False)),
    ],
)
def test_parse_recruiter_info(raw: str, expected: tuple) -> None:
    assert parse_recruiter_info(raw) == expected


def test_a_trailing_separator_is_not_mistaken_for_a_title() -> None:
    """The space split must run only when no separator is present."""
    assert parse_recruiter_info("钟先生 ·") == ("钟先生", "", False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("某中型人工智能公司 100-499人 人工智能", ("某中型人工智能公司", "100-499人", "人工智能")),
        ("深至科技 100-499人 人工智能", ("深至科技", "100-499人", "人工智能")),
        ("未知初创企业", ("未知初创企业", "", "")),
        ("未知公司", ("", "", "")),
    ],
)
def test_parse_company_scale_industry(raw: str, expected: tuple) -> None:
    assert parse_company_scale_industry(raw) == expected


@pytest.mark.parametrize(
    ("company", "title", "expected"),
    [
        ("算法工程师", "算法工程师", True),
        ("算法工程师 &@", "算法工程师", True),
        ("深至科技", "算法工程师", False),
        ("", "算法工程师", False),
    ],
)
def test_company_duplicates_card_title(company: str, title: str, expected: bool) -> None:
    assert company_duplicates_card_title(company, title) is expected


# --------------------------------------------------------------------------- #
# One declaration site for the rules the parser shares with the domain
# --------------------------------------------------------------------------- #


def test_the_recruiter_normalizer_is_the_one_the_fingerprint_uses() -> None:
    """The parser and the Job Fingerprint cannot disagree about the recruiter."""
    from boss_agent.models import compute_job_fingerprint

    for raw in ("钟先生 · 猎头顾问", "李女士•HR", "王先生・招聘", "赵先生·"):
        assert parse_recruiter_info(raw)[0] == normalize_recruiter_name(raw)
        # Same key for the name alone and the name with Boss's appended title.
        assert compute_job_fingerprint("深至科技", "算法工程师", raw) == compute_job_fingerprint(
            "深至科技", "算法工程师", parse_recruiter_info(raw)[0]
        )


PURE_DOMAIN_MODULES = (
    "src/boss_agent/models.py",
    "src/boss_agent/screening.py",
    "src/boss_agent/feed_records.py",
    "src/boss_agent/graph.py",
    "src/boss_agent/card_parser.py",
)


@pytest.mark.parametrize("module", PURE_DOMAIN_MODULES)
def test_pure_domain_modules_never_import_the_page_objects(module: str) -> None:
    """The import-graph guard: a decision module must not pull in Appium.

    `JobCardBrief` used to live in `pages.py`, so the Candidate Screener imported the
    device-side module just to read text off a card.
    """
    tree = ast.parse((REPO_ROOT / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("pages", "boss_agent.pages"):
            pytest.fail(f"{module} imports the Appium-side page objects (line {node.lineno})")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "boss_agent.pages", module
