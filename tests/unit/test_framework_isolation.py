"""Unit tests verifying architectural isolation of droid_agent_core."""

from pathlib import Path


def test_droid_agent_core_has_zero_boss_keywords_or_imports():
    """Verify that droid_agent_core does not import or mention Boss-specific concepts."""
    core_dir = Path(__file__).resolve().parent.parent.parent / "src" / "droid_agent_core"
    assert core_dir.exists(), f"Framework core directory not found: {core_dir}"

    forbidden_keywords = ["bosszhipin", "hpbr", "job_listing", "job_detail", "boss_agent"]

    for py_file in core_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8").lower()
        for kw in forbidden_keywords:
            assert kw not in content, (
                f"Architecture violation: '{kw}' found in agnostic framework file: {py_file}"
            )
