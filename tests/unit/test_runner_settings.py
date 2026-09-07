"""
tests.unit.test_runner_settings
===============================
Unit tests verifying config-first loader in scripts/run_live_test.py.
"""

from scripts.run_live_test import load_runner_settings


def test_load_runner_settings_defaults():
    settings = load_runner_settings(config_path="/non/existent/path.yaml")
    assert settings["device"] == "emulator-5554"
    assert settings["server_url"] == "http://127.0.0.1:4723"
    assert settings["pocketbase_url"] == "http://127.0.0.1:8090"
    assert "enable_search" not in settings
    assert "enable_filter" not in settings
    assert "search_id" not in settings
    assert "keyword" not in settings
    assert settings["preview_timeout_sec"] == 3.0
    assert settings["enable_greeting"] is True


def test_load_runner_settings_custom_file(tmp_path):
    custom_yaml = tmp_path / "custom_settings.yaml"
    custom_yaml.write_text(
        """
device: "physical-device-123"
server_url: "http://192.168.1.100:4723"
pocketbase_url: "http://192.168.1.100:8090"
resume_path: "/path/to/resume.pdf"
force_refresh_memory: true
preview_timeout_sec: 10.0
enable_greeting: false
""",
        encoding="utf-8",
    )

    settings = load_runner_settings(config_path=custom_yaml)
    assert settings["device"] == "physical-device-123"
    assert settings["server_url"] == "http://192.168.1.100:4723"
    assert settings["pocketbase_url"] == "http://192.168.1.100:8090"
    assert settings["resume_path"] == "/path/to/resume.pdf"
    assert settings["force_refresh_memory"] is True
    assert settings["preview_timeout_sec"] == 10.0
    assert settings["enable_greeting"] is False
