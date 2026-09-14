import tempfile
from pathlib import Path

import yaml

from boss_agent.models import JobRecord, ScreeningPolicy


def test_screening_policy_load_from_example():
    """Verify ScreeningPolicy loads default rules from example yaml."""
    example_path = Path("config/screening.example.yaml")
    assert example_path.exists(), "config/screening.example.yaml must exist"

    policy = ScreeningPolicy.load_default(config_path=example_path)
    assert policy.enable_screening is True
    assert isinstance(policy.title_blacklist, list)
    assert "销售" in policy.title_blacklist
    assert "外包" in policy.jd_blacklist


def test_screening_policy_save_and_load_roundtrip():
    """Verify saving policy writes clean YAML that loads identically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_config = Path(tmpdir) / "screening.local.yaml"
        policy = ScreeningPolicy(
            title_whitelist=["Python", "Agent"],
            title_blacklist=["销售", "实习"],
            company_blacklist=["避雷科技"],
            jd_blacklist=["驻场", "外包"],
            enable_screening=True,
        )
        saved_path = policy.save_default(config_path=tmp_config)
        assert saved_path == tmp_config
        assert tmp_config.exists()

        # Raw YAML check
        data = yaml.safe_load(tmp_config.read_text(encoding="utf-8"))
        assert data["title_whitelist"] == ["Python", "Agent"]
        assert data["company_blacklist"] == ["避雷科技"]

        # Loader check
        loaded = ScreeningPolicy.load_default(config_path=tmp_config)
        assert loaded.title_whitelist == ["Python", "Agent"]
        assert loaded.title_blacklist == ["销售", "实习"]
        assert loaded.company_blacklist == ["避雷科技"]
        assert loaded.jd_blacklist == ["驻场", "外包"]
        assert loaded.enable_screening is True


def test_job_record_screened_reason_field():
    """Verify JobRecord accepts and serializes screened_reason."""
    rec = JobRecord(
        title="高级测试工程师",
        company_name="某测试外包",
        recruiter_name="HR",
        status="ignored",
        screened_reason="命中岗位摘要/标签黑名单关键词: '外包'",
    )
    assert rec.screened_reason == "命中岗位摘要/标签黑名单关键词: '外包'"
    assert rec.status == "ignored"
