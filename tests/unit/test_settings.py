"""
tests.unit.test_settings
========================
Unit tests verifying centralized configuration loading, PocketBase State Stream URL
resolution hierarchy, precedence rules, and URL normalization.
"""

from pathlib import Path
from unittest.mock import patch

from boss_agent.broker.pocketbase_adapter import PocketBaseTaskBroker
from boss_agent.settings import (
    load_settings,
    resolve_pocketbase_data_dir,
    resolve_pocketbase_db_path,
    resolve_pocketbase_url,
)
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


def test_resolve_pocketbase_db_path_default():
    """Fallback default database path is .boss_agent/pb_data/data.db when no inputs provided."""
    with patch.dict("os.environ", {}, clear=True):
        db_path = resolve_pocketbase_db_path(config_path="/non/existent/settings.yaml")
        assert db_path == Path(".boss_agent/pb_data/data.db")


def test_resolve_pocketbase_data_dir_default():
    """Fallback default data directory is .boss_agent/pb_data when no inputs provided."""
    with patch.dict("os.environ", {}, clear=True):
        data_dir = resolve_pocketbase_data_dir(config_path="/non/existent/settings.yaml")
        assert data_dir == Path(".boss_agent/pb_data")


def test_resolve_pocketbase_db_path_from_config(tmp_path: Path):
    """Database path specified in config file is respected."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_db_path: '/custom/path/my_data.db'\n", encoding="utf-8")

    with patch.dict("os.environ", {}, clear=True):
        db_path = resolve_pocketbase_db_path(config_path=custom_yaml)
        assert db_path == Path("/custom/path/my_data.db")


def test_resolve_pocketbase_db_path_from_alias(tmp_path: Path):
    """Alias pb_db_path in config file is parsed and respected."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pb_db_path: '/opt/pb/db.sqlite'\n", encoding="utf-8")

    with patch.dict("os.environ", {}, clear=True):
        db_path = resolve_pocketbase_db_path(config_path=custom_yaml)
        assert db_path == Path("/opt/pb/db.sqlite")


def test_resolve_pocketbase_db_path_inferred_from_data_dir(tmp_path: Path):
    """When only data_dir is given in config, data.db is appended."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_data_dir: '/var/lib/pocketbase/data'\n", encoding="utf-8")

    with patch.dict("os.environ", {}, clear=True):
        db_path = resolve_pocketbase_db_path(config_path=custom_yaml)
        assert db_path == Path("/var/lib/pocketbase/data/data.db")


def test_resolve_pocketbase_data_dir_from_config(tmp_path: Path):
    """pocketbase_data_dir from config file is resolved correctly."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pb_data_dir: '/var/pb_data'\n", encoding="utf-8")

    with patch.dict("os.environ", {}, clear=True):
        data_dir = resolve_pocketbase_data_dir(config_path=custom_yaml)
        assert data_dir == Path("/var/pb_data")


def test_resolve_pocketbase_data_dir_inferred_from_db_path(tmp_path: Path):
    """When only db_path is provided in config, data directory resolves to its parent."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_db_path: '/custom/storage/pb/data.db'\n", encoding="utf-8")

    with patch.dict("os.environ", {}, clear=True):
        data_dir = resolve_pocketbase_data_dir(config_path=custom_yaml)
        assert data_dir == Path("/custom/storage/pb")


def test_resolve_pocketbase_db_path_env_overrides_file(tmp_path: Path):
    """Environment variables PB_DB_PATH / PB_DATA_DIR override config file."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_db_path: '/from/file/data.db'\n", encoding="utf-8")

    with patch.dict("os.environ", {"PB_DB_PATH": "/from/env/override.db"}, clear=True):
        db_path = resolve_pocketbase_db_path(config_path=custom_yaml)
        assert db_path == Path("/from/env/override.db")

    with patch.dict("os.environ", {"PB_DATA_DIR": "/from/env/dir"}, clear=True):
        db_path = resolve_pocketbase_db_path(config_path=custom_yaml)
        assert db_path == Path("/from/env/dir/data.db")


def test_resolve_pocketbase_data_dir_env_overrides_file(tmp_path: Path):
    """Environment variable PB_DATA_DIR overrides config file."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_data_dir: '/from/file/pb_data'\n", encoding="utf-8")

    with patch.dict("os.environ", {"PB_DATA_DIR": "/from/env/pb_data"}, clear=True):
        data_dir = resolve_pocketbase_data_dir(config_path=custom_yaml)
        assert data_dir == Path("/from/env/pb_data")


def test_resolve_pocketbase_db_path_explicit_arg_overrides_all(tmp_path: Path):
    """Explicit argument overrides both environment variables and config files."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_db_path: '/from/file/data.db'\n", encoding="utf-8")

    with patch.dict("os.environ", {"PB_DB_PATH": "/from/env/data.db"}, clear=True):
        db_path = resolve_pocketbase_db_path(
            explicit_path="/from/explicit/custom.db",
            config_path=custom_yaml,
        )
        assert db_path == Path("/from/explicit/custom.db")


def test_resolve_pocketbase_data_dir_explicit_arg_overrides_all(tmp_path: Path):
    """Explicit directory argument overrides both environment variables and config files."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text("pocketbase_data_dir: '/from/file/pb_data'\n", encoding="utf-8")

    with patch.dict("os.environ", {"PB_DATA_DIR": "/from/env/pb_data"}, clear=True):
        data_dir = resolve_pocketbase_data_dir(
            explicit_dir="/from/explicit/data_dir",
            config_path=custom_yaml,
        )
        assert data_dir == Path("/from/explicit/data_dir")


def test_load_settings_fallback_without_yaml_module(tmp_path: Path):
    """load_settings correctly parses simple key-value YAML files when yaml module is None."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text(
        """
# Comment line
pocketbase_url: "http://fallback-url:8090"
pocketbase_data_dir: "/data/fallback"
pocketbase_db_path: "/data/fallback/data.db"
device: 'emulator-5558'
""",
        encoding="utf-8",
    )

    with (
        patch("boss_agent.settings.yaml", None),
        patch.dict("os.environ", {}, clear=True),
    ):
        settings = load_settings(config_path=custom_yaml)
        assert settings["pocketbase_url"] == "http://fallback-url:8090"
        assert settings["pocketbase_data_dir"] == "/data/fallback"
        assert settings["pocketbase_db_path"] == "/data/fallback/data.db"
        assert settings["device"] == "emulator-5558"


def test_load_settings_fallback_strips_inline_comments(tmp_path: Path):
    """load_settings strips trailing inline comments when yaml module is None."""
    custom_yaml = tmp_path / "settings.local.yaml"
    custom_yaml.write_text(
        """
pocketbase_db_path: ".boss_agent/pb_data/data.db" # PocketBase SQLite data.db path (env: PB_DB_PATH)
pocketbase_data_dir: ".boss_agent/pb_data" \t# PocketBase data directory (env: PB_DATA_DIR)
pocketbase_url: "http://127.0.0.1:8090" # URL
""",
        encoding="utf-8",
    )

    with (
        patch("boss_agent.settings.yaml", None),
        patch.dict("os.environ", {}, clear=True),
    ):
        settings = load_settings(config_path=custom_yaml)
        assert settings["pocketbase_db_path"] == ".boss_agent/pb_data/data.db"
        assert settings["pocketbase_data_dir"] == ".boss_agent/pb_data"
        assert settings["pocketbase_url"] == "http://127.0.0.1:8090"


def test_resolve_git_common_root():
    """resolve_git_common_root correctly resolves repository root via git-common-dir."""
    from boss_agent.settings import resolve_git_common_root

    root = resolve_git_common_root()
    assert isinstance(root, Path)
    assert root.exists()
    assert (root / ".git").exists() or (root / "pyproject.toml").exists()


def test_resolve_pocketbase_db_path_with_common_root(tmp_path: Path):
    """When resolve_common_root=True, relative paths are anchored to git common root."""
    from boss_agent.settings import resolve_git_common_root

    with patch.dict("os.environ", {}, clear=True):
        db_path = resolve_pocketbase_db_path(
            config_path="/non/existent/settings.yaml",
            resolve_common_root=True,
        )
        expected = resolve_git_common_root() / ".boss_agent/pb_data/data.db"
        assert db_path == expected


def test_resolve_pocketbase_data_dir_with_common_root(tmp_path: Path):
    """When resolve_common_root=True, relative data dir is anchored to git common root."""
    from boss_agent.settings import resolve_git_common_root

    with patch.dict("os.environ", {}, clear=True):
        data_dir = resolve_pocketbase_data_dir(
            config_path="/non/existent/settings.yaml",
            resolve_common_root=True,
        )
        expected = resolve_git_common_root() / ".boss_agent/pb_data"
        assert data_dir == expected

