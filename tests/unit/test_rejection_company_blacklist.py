"""
tests.unit.test_rejection_company_blacklist
===========================================
Unit tests for rejection-driven company blacklist ingestion: the headhunter
agency guardrail, the `[Company] | [Position]` card descriptor parser, and the
comment-preserving append into the active screening configuration file
(Issues #205 and #208).
"""

import tempfile
from pathlib import Path

import yaml

from boss_agent.models import (
    ScreeningPolicy,
    append_company_blacklist_entry,
    is_headhunter_agency_name,
    is_writable_screening_path,
    resolve_writable_screening_config_path,
)
from boss_agent.pages import parse_company_from_descriptor

# ---------------------------------------------------------------------------
# `[Company] | [Position]` card descriptor
# ---------------------------------------------------------------------------


def test_descriptor_parsing_returns_the_employer_before_the_separator():
    assert parse_company_from_descriptor("传音控股 | 算法工程师") == "传音控股"
    assert parse_company_from_descriptor("磐基技术 | 技术总监") == "磐基技术"
    assert parse_company_from_descriptor("TCL实业 | 大模型算法") == "TCL实业"


def test_descriptor_parsing_keeps_every_pipe_after_the_first():
    assert parse_company_from_descriptor("某某集团 | 后端 | 上海") == "某某集团"


def test_descriptor_parsing_strips_a_leading_sender_name():
    """The row-text fallback can glue the recruiter name onto the descriptor."""
    assert (
        parse_company_from_descriptor("严胜 传音控股 | 算法工程师", sender_name="严胜")
        == "传音控股"
    )
    assert (
        parse_company_from_descriptor("严胜·传音控股 | 算法工程师", sender_name="严胜")
        == "传音控股"
    )


def test_descriptor_parsing_keeps_a_company_that_starts_with_the_sender_name():
    """A sender nickname that prefixes the employer must not truncate the employer.

    Stripping an undelimited prefix turned '小米集团' into the generic token
    '集团', and company_blacklist matching is a substring test -- one card would
    then reject every 集团 employer nationwide.
    """
    assert parse_company_from_descriptor("小米集团 | 算法工程师", sender_name="小米") == "小米集团"
    assert parse_company_from_descriptor("得物App | 运营", sender_name="得物") == "得物App"
    assert (
        parse_company_from_descriptor("华为技术有限公司 | 后端", sender_name="华")
        == "华为技术有限公司"
    )


def test_descriptor_parsing_keeps_the_employer_when_the_sender_is_the_company():
    """An official company account's display name equals its own employer name."""
    assert (
        parse_company_from_descriptor("小米集团 | 算法工程师", sender_name="小米集团") == "小米集团"
    )


def test_descriptor_parsing_accepts_a_fullwidth_separator():
    """Some builds render the divider as a fullwidth bar (U+FF5C), not '|'."""
    assert parse_company_from_descriptor("传音控股｜算法工程师") == "传音控股"
    assert parse_company_from_descriptor("磐基技术 ｜ 技术总监") == "磐基技术"


def test_descriptor_parsing_returns_empty_without_a_separator():
    assert parse_company_from_descriptor("我们感谢您的投递") == ""
    assert parse_company_from_descriptor("") == ""
    assert parse_company_from_descriptor("   ") == ""


def test_descriptor_parsing_rejects_a_blank_employer():
    assert parse_company_from_descriptor(" | 算法工程师") == ""


# ---------------------------------------------------------------------------
# Headhunter agency guardrail (company-name derived: the 仅沟通 card shows no
# recruiter title, so the agency has to be recognised from its own name)
# ---------------------------------------------------------------------------


def test_headhunter_agency_names_are_detected():
    for name in (
        "杭州脉享人力资源",
        "大连麦驰企业管理咨询",
        "上海某某人力资源服务有限公司",
        "深圳前海人才服务",
        "某某劳务派遣",
        "某某猎头",
        "某某人才中介",
    ):
        assert is_headhunter_agency_name(name) is True, f"Expected '{name}' to be an agency."


def test_authentic_employers_are_not_flagged_as_agencies():
    for name in ("传音控股", "磐基技术", "TCL实业", "深至科技", "腾讯科技", "埃森哲咨询"):
        assert is_headhunter_agency_name(name) is False, f"Expected '{name}' to be an employer."


def test_headhunter_agency_guardrail_blocks_blacklisting():
    policy = ScreeningPolicy()

    allowed, notice = policy.validate_can_blacklist_company("杭州脉享人力资源")

    assert allowed is False
    assert "黑名单保护生效" in notice
    assert policy.company_blacklist == []


def test_headhunter_flag_still_blocks_blacklisting():
    policy = ScreeningPolicy()

    added, notice = policy.add_company_to_blacklist("深至科技", is_headhunter=True)

    assert added is False
    assert "猎头" in notice
    assert policy.company_blacklist == []


# ---------------------------------------------------------------------------
# Comment-preserving persistence
# ---------------------------------------------------------------------------

EXISTING_CONFIG = """\
# ==============================================================================
# 候选人的筛选偏好
# ==============================================================================
enable_screening: true
# 职位黑名单（一票否决）
title_blacklist: ["销售", "电销"]
# 公司黑名单（一票否决，受直招保护守卫约束）
company_blacklist: []
# 摘要黑名单
jd_blacklist: ["外包"]
"""


def test_append_into_an_inline_empty_list_preserves_comments_and_siblings():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.local.yaml"
        path.write_text(EXISTING_CONFIG, encoding="utf-8")

        assert append_company_blacklist_entry("传音控股", path=path) is True

        content = path.read_text(encoding="utf-8")
        assert "# 候选人的筛选偏好" in content
        assert "# 公司黑名单（一票否决，受直招保护守卫约束）" in content
        assert "# 摘要黑名单" in content

        data = yaml.safe_load(content)
        assert data["company_blacklist"] == ["传音控股"]
        assert data["title_blacklist"] == ["销售", "电销"]
        assert data["jd_blacklist"] == ["外包"]
        assert data["enable_screening"] is True


def test_append_is_idempotent_and_appends_to_an_existing_block_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.local.yaml"
        path.write_text(EXISTING_CONFIG, encoding="utf-8")

        append_company_blacklist_entry("传音控股", path=path)
        append_company_blacklist_entry("磐基技术", path=path)
        assert append_company_blacklist_entry("传音控股", path=path) is False

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["company_blacklist"] == ["传音控股", "磐基技术"]


def test_append_creates_the_config_with_a_full_policy_snapshot(monkeypatch):
    """A brand-new file must not become a policy that drops every other rule."""
    monkeypatch.setattr(
        "boss_agent.models.ScreeningPolicy.load_default",
        classmethod(lambda cls, **kw: ScreeningPolicy()),
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "screening.local.yaml"

        assert append_company_blacklist_entry("传音控股", path=path) is True

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["company_blacklist"] == ["传音控股"]
        for key in ("title_whitelist", "title_blacklist", "jd_blacklist"):
            assert key in data, f"A fresh config must carry '{key}' too."


def test_append_adds_the_key_when_the_config_lacks_it():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.local.yaml"
        path.write_text("enable_screening: true\n# 保留我的注释\n", encoding="utf-8")

        assert append_company_blacklist_entry("传音控股", path=path) is True

        content = path.read_text(encoding="utf-8")
        assert "# 保留我的注释" in content
        assert yaml.safe_load(content)["company_blacklist"] == ["传音控股"]


def test_writable_screening_path_prefers_the_unified_local_settings_file():
    """Writing anywhere else would be shadowed by load_default()'s precedence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "config").mkdir()
        (root / "config" / "settings.example.yaml").write_text("enable_screening: true\n")
        (root / "config" / "settings.local.yaml").write_text("enable_screening: true\n")

        assert resolve_writable_screening_config_path(root) == (
            root / "config" / "settings.local.yaml"
        )


def test_writable_screening_path_never_targets_a_checked_in_example_file():
    """With only an example file present, the target must still be one that wins.

    `config/screening.local.yaml` is the last entry in load_default's precedence,
    so writing there would be shadowed by the shipped example and change nothing.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "config").mkdir()
        (root / "config" / "settings.example.yaml").write_text("enable_screening: true\n")

        resolved = resolve_writable_screening_config_path(root)

        assert resolved == root / "config" / "settings.local.yaml"
        assert ".example." not in resolved.name


def test_persist_company_blacklist_writes_to_the_active_config_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.local.yaml"
        path.write_text(EXISTING_CONFIG, encoding="utf-8")
        policy = ScreeningPolicy.load_default(config_path=path)

        added, _ = policy.add_company_to_blacklist("传音控股")
        assert added is True

        written = policy.persist_company_blacklist("传音控股")

        assert written == path
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["company_blacklist"] == ["传音控股"]


def test_load_default_remembers_the_file_it_resolved():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.local.yaml"
        path.write_text(EXISTING_CONFIG, encoding="utf-8")

        policy = ScreeningPolicy.load_default(config_path=path)

        assert Path(policy.source_path or "") == path


def test_persist_never_writes_the_checked_in_example_config(tmp_path, monkeypatch):
    """On a fresh checkout `load_default` resolves the example file; it is read-only.

    The fallback is pinned to a temp file: `resolve_writable_screening_config_path`
    resolves through the git common root, which in a worktree is the shared main
    checkout — a test must never write there.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    example = config_dir / "settings.example.yaml"
    example.write_text("# 请勿修改本文件（示例）\ncompany_blacklist: []\n", encoding="utf-8")
    original = example.read_text(encoding="utf-8")
    fallback = config_dir / "screening.local.yaml"
    monkeypatch.setattr(
        "boss_agent.models.resolve_writable_screening_config_path", lambda *_: fallback
    )

    policy = ScreeningPolicy.load_default(config_path=example)
    written = policy.persist_company_blacklist("传音控股")

    assert written == fallback
    assert example.read_text(encoding="utf-8") == original
    assert "传音控股" not in original


def test_writable_path_check_rejects_examples_and_json():
    assert is_writable_screening_path("config/settings.local.yaml") is True
    assert is_writable_screening_path("config/screening.local.yml") is True
    assert is_writable_screening_path("config/settings.example.yaml") is False
    assert is_writable_screening_path("config/screening.local.json") is False


def test_append_refuses_a_json_store_rather_than_corrupting_it():
    """The appenders are YAML line surgery; a JSON store must be left alone."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.local.json"
        original = '{"enable_screening": true, "company_blacklist": []}'
        path.write_text(original, encoding="utf-8")

        assert append_company_blacklist_entry("传音控股", path=path) is False
        assert path.read_text(encoding="utf-8") == original


def test_persist_reports_nothing_written_when_the_store_is_not_writable(tmp_path, monkeypatch):
    store = tmp_path / "settings.local.json"
    store.write_text('{"company_blacklist": ["a"]}', encoding="utf-8")
    # Pinned so a failing guard cannot escape into the shared checkout's config.
    monkeypatch.setattr(
        "boss_agent.models.resolve_writable_screening_config_path",
        lambda *_: tmp_path / "fallback.local.yaml",
    )
    policy = ScreeningPolicy(company_blacklist=["a", "传音控股"], source_path=str(store))

    assert policy.persist_company_blacklist("传音控股") is None
    assert not (tmp_path / "fallback.local.yaml").exists()


def test_the_suite_never_resolves_a_write_target_inside_the_checkout():
    """Every unpathed write must land in the test sandbox, not the developer's repo.

    `resolve_writable_screening_config_path()` with no root resolves through the git
    common root, which in a worktree is the *shared main checkout*. A single test
    that forgets to pass a path would then edit the host's `settings.local.yaml`,
    which is exactly the leak `tests/conftest.py` exists to make impossible.

    Asserted through the module rather than the name imported at the top of this
    file: the appenders look the function up as a module global at call time, so
    `boss_agent.models` is the attribute the sandbox patches and the one a write
    actually goes through.
    """
    from boss_agent import models
    from boss_agent.settings import resolve_git_common_root

    resolved = models.resolve_writable_screening_config_path()

    assert not str(resolved).startswith(str(resolve_git_common_root())), (
        f"Screening config writes resolve to {resolved}, inside the checkout."
    )
