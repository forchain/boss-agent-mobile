"""
tests.unit.test_settings
========================
Unit tests verifying centralized configuration loading, PocketBase State Stream URL
resolution hierarchy, precedence rules, and URL normalization.
"""

from pathlib import Path
from unittest.mock import patch

from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker
from boss_agent.settings import load_settings, resolve_pocketbase_url
from boss_agent.worker.config import WorkerConfig


def test_resolve_pocketbase_url_default():
    """When no config files, env vars, or arguments are present, fallback to default."""
    with patch.dict("os.environ", {}, clear=True):
        url = resolve_pocketbase_url(config_path="/non/existent/path.yaml")
        assert url == "http://127.0.0.1:8090"


def test_resolve_pocketbase_url_from_custom_yaml(tmp_path: Path):
    """Configuration file value is used when no CLI argument or env var is present."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_url: 'http://192.168.1.100:8090'\n", encoding="utf-8")

    with patch.dict("os.environ", {}, clear=True):
        url = resolve_pocketbase_url(config_path=custom_yaml)
        assert url == "http://192.168.1.100:8090"


def test_resolve_pocketbase_url_from_pb_url_alias(tmp_path: Path):
    """Configuration file key 'pb_url' is supported as an alias for 'pocketbase_url'."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pb_url: 'http://10.0.0.50:9000'\n", encoding="utf-8")

    with patch.dict("os.environ", {}, clear=True):
        url = resolve_pocketbase_url(config_path=custom_yaml)
        assert url == "http://10.0.0.50:9000"


def test_resolve_pocketbase_url_strips_trailing_slashes(tmp_path: Path):
    """URLs with trailing slashes or whitespace are sanitized."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_url: '  http://192.168.1.200:8090///  '\n", encoding="utf-8")

    with patch.dict("os.environ", {}, clear=True):
        url = resolve_pocketbase_url(config_path=custom_yaml)
        assert url == "http://192.168.1.200:8090"


def test_resolve_pocketbase_url_env_overrides_file(tmp_path: Path):
    """POCKETBASE_URL environment variable takes precedence over file configuration."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_url: 'http://192.168.1.100:8090'\n", encoding="utf-8")

    with patch.dict("os.environ", {"POCKETBASE_URL": "http://env-host:8090/"}, clear=True):
        url = resolve_pocketbase_url(config_path=custom_yaml)
        assert url == "http://env-host:8090"


def test_resolve_pocketbase_url_explicit_arg_overrides_all(tmp_path: Path):
    """Explicit argument overrides both environment variable and configuration file."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_url: 'http://192.168.1.100:8090'\n", encoding="utf-8")

    with patch.dict("os.environ", {"POCKETBASE_URL": "http://env-host:8090"}, clear=True):
        url = resolve_pocketbase_url(
            explicit_url="http://cli-override:8090/", config_path=custom_yaml
        )
        assert url == "http://cli-override:8090"


def test_load_settings_includes_pocketbase_url(tmp_path: Path):
    """load_settings returns merged dictionary including pocketbase_url."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text(
        """
device: "emulator-5556"
pocketbase_url: "http://remote-pb:8090"
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path=custom_yaml)
    assert settings["device"] == "emulator-5556"
    assert settings["pocketbase_url"] == "http://remote-pb:8090"


def test_pocketbase_adapter_defaults_to_resolved_url(tmp_path: Path):
    """PocketBaseTaskBroker defaults base_url to resolved settings when base_url is None."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_url: 'http://adapter-host:8090'\n", encoding="utf-8")

    with (
        patch("boss_agent.settings.DEFAULT_CONFIG_SEARCH_PATHS", [custom_yaml]),
        patch.dict("os.environ", {}, clear=True),
    ):
        broker = PocketBaseTaskBroker(base_url=None)
        assert broker.base_url == "http://adapter-host:8090"



def test_file_precedence_local_overrides_base(tmp_path: Path):
    """Local settings file overrides base settings file."""
    base_yaml = tmp_path / "settings.example.yaml"
    base_yaml.write_text("pocketbase_url: 'http://base-pb:8090'\n", encoding="utf-8")
    local_yaml = tmp_path / "settings.local.yaml"
    local_yaml.write_text("pocketbase_url: 'http://local-pb:8090'\n", encoding="utf-8")

    with (
        patch("boss_agent.settings.DEFAULT_CONFIG_SEARCH_PATHS", [local_yaml, base_yaml]),
        patch.dict("os.environ", {}, clear=True),
    ):
        url = resolve_pocketbase_url()
        assert url == "http://local-pb:8090"


def test_worker_config_contains_pocketbase_url():
    """WorkerConfig includes pocketbase_url field with default value."""
    with (
        patch("boss_agent.settings.DEFAULT_CONFIG_SEARCH_PATHS", []),
        patch.dict("os.environ", {}, clear=True),
    ):
        cfg = WorkerConfig(worker_id="worker-test")
        assert hasattr(cfg, "pocketbase_url")
        assert cfg.pocketbase_url == "http://127.0.0.1:8090"


