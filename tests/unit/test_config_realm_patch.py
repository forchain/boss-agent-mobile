"""The Configuration Realm's patch engine and JSON CLI (spec #444, ticket #445)."""

import json
from pathlib import Path

import pytest
import yaml

from boss_agent import config_realm

ORIGINAL = """\
# Operator comment that must survive
device: "emulator-5554"
# A Python-only key the Web layer does not know
future_python_key: "keep-me"
chat:
  # sibling key that a partial patch must not drop
  max_scan_depth: 30
  rejection_reply_text: "收到 谢谢"
"""


@pytest.fixture(autouse=True)
def _fresh_cache():
    config_realm.invalidate_cache()
    yield
    config_realm.invalidate_cache()


def _write(tmp_path: Path, body: str = ORIGINAL) -> Path:
    path = tmp_path / "settings.local.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_patch_changes_only_stated_keys_and_preserves_comments(tmp_path: Path) -> None:
    path = _write(tmp_path)

    config_realm.patch_settings({"daily_greeting_limit": 15}, config_path=path)

    content = path.read_text(encoding="utf-8")
    assert "# Operator comment that must survive" in content
    data = yaml.safe_load(content)
    assert data["daily_greeting_limit"] == 15
    assert data["future_python_key"] == "keep-me"
    assert data["device"] == "emulator-5554"


def test_patch_deep_merges_the_nested_chat_block(tmp_path: Path) -> None:
    path = _write(tmp_path)

    config_realm.patch_settings({"chat": {"dry_run": True}}, config_path=path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["chat"]["dry_run"] is True
    assert data["chat"]["max_scan_depth"] == 30
    assert data["chat"]["rejection_reply_text"] == "收到 谢谢"


@pytest.mark.parametrize(
    "value",
    ["••••••••", "your-api-key-here", "", None],
)
def test_patch_never_persists_a_masked_or_placeholder_secret(tmp_path: Path, value) -> None:
    path = _write(tmp_path, 'api_key: "real-key"\n')

    config_realm.patch_settings({"api_key": value}, config_path=path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["api_key"] == "real-key"


def test_patch_creates_the_target_on_demand(tmp_path: Path) -> None:
    path = tmp_path / "config" / "settings.local.yaml"

    config_realm.patch_settings({"daily_greeting_limit": 3}, config_path=path)

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["daily_greeting_limit"] == 3


def test_blacklist_append_is_idempotent_and_comment_preserving(tmp_path: Path) -> None:
    path = _write(tmp_path)

    assert config_realm.append_company_blacklist_entry("传音控股", path=path) is True
    assert config_realm.append_company_blacklist_entry("传音控股", path=path) is False

    content = path.read_text(encoding="utf-8")
    assert "# Operator comment that must survive" in content
    assert yaml.safe_load(content)["company_blacklist"] == ["传音控股"]


def test_read_config_exposes_settings_and_screening_policy(tmp_path: Path) -> None:
    path = _write(tmp_path)

    view = config_realm.read_config(config_path=path)

    assert view["settings"]["future_python_key"] == "keep-me"
    assert "company_blacklist" in view["screening_policy"]


def _cli(*args: str) -> tuple[int, str, str]:
    from scripts.config_realm_cli import main

    return main(list(args)), "", ""


def test_cli_read_and_patch_round_trip(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path)

    from scripts.config_realm_cli import main

    assert (
        main(
            ["--config-path", str(path), "patch", "--json", json.dumps({"daily_greeting_limit": 7})]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["settings"]["daily_greeting_limit"] == 7

    assert main(["--config-path", str(path), "read"]) == 0
    assert json.loads(capsys.readouterr().out)["settings"]["daily_greeting_limit"] == 7


def test_cli_patch_refuses_invalid_json(tmp_path: Path, capsys) -> None:
    from scripts.config_realm_cli import main

    assert main(["--config-path", str(tmp_path / "x.yaml"), "patch", "--json", "{not json"]) == 2
    assert "error" in json.loads(capsys.readouterr().err)
