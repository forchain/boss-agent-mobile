"""
tests/unit/test_config_realm.py
===============================
The Configuration Realm's own behavior: one chain, one defaults table, one mask guard,
one cache with an explicit invalidation seam.

Resolution tests assert *resolved values* at the module interface, never the file-walk
mechanics — which file won is already pinned by `test_settings.py`, which exercises the
same engine through the compatibility facade.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from boss_agent import config_realm

REPO_ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """Each test starts and ends with an empty cache, and a clean override environment."""
    for _key, names in config_realm.ENV_OVERRIDES:
        for name in names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CHAT_REJECTION_REPLY_TEXT", raising=False)
    config_realm.invalidate_cache()
    yield
    config_realm.invalidate_cache()


# --------------------------------------------------------------------------- #
# The one defaults table
# --------------------------------------------------------------------------- #


def test_defaults_are_the_baseline_when_no_file_sets_a_key(tmp_path: Path) -> None:
    """A chain that resolves to nothing still yields the declared table."""
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    merged = config_realm.load_settings(config_path=empty)
    for key, expected in config_realm.DEFAULTS.items():
        if expected is None:
            continue
        assert merged[key] == expected, key


def test_the_chat_block_defaults_are_declared_once() -> None:
    """The nested block's values come from the rejection module, not a second copy."""
    from boss_agent.rejection import DEFAULT_MAX_SCAN_DEPTH, DEFAULT_REJECTION_REPLY_TEXT

    assert config_realm.CHAT_DEFAULTS["rejection_reply_text"] == DEFAULT_REJECTION_REPLY_TEXT
    assert config_realm.CHAT_DEFAULTS["max_scan_depth"] == DEFAULT_MAX_SCAN_DEPTH


def test_the_shipped_template_is_the_chain_floor() -> None:
    """`example` is the floor, so a fresh checkout behaves like the documented template."""
    assert config_realm.CONFIG_CHAIN[-1] == Path("config/settings.example.yaml")
    assert config_realm.resolve_chain() == list(config_realm.CONFIG_CHAIN)


# --------------------------------------------------------------------------- #
# One precedence chain and one env layer
# --------------------------------------------------------------------------- #


def test_a_higher_precedence_file_wins(tmp_path: Path) -> None:
    low = tmp_path / "example.yaml"
    low.write_text("model: 'from-example'\ntemperature: 0.1\n", encoding="utf-8")
    high = tmp_path / "local.yaml"
    high.write_text("model: 'from-local'\n", encoding="utf-8")

    merged = config_realm.load_chain([high, low])
    assert merged["model"] == "from-local"
    assert merged["temperature"] == 0.1, "a key the higher file omits keeps the lower value"


@pytest.mark.parametrize(
    ("env_name", "env_value", "key"),
    [
        ("POCKETBASE_URL", "http://env.example:9000", "pocketbase_url"),
        ("APPIUM_SERVER_URL", "http://env-appium.example:1", "server_url"),
        ("LLM_MODEL", "env-model", "model"),
        ("LLM_BASE_URL", "https://env.example/v1", "base_url"),
    ],
)
def test_the_environment_sits_above_every_file(
    tmp_path: Path, monkeypatch, env_name: str, env_value: str, key: str
) -> None:
    file = tmp_path / "s.yaml"
    file.write_text(f"{key}: 'from-file'\n", encoding="utf-8")
    monkeypatch.setenv(env_name, env_value)

    assert config_realm.load_settings(config_path=file)[key] == env_value


def test_the_nested_chat_block_deep_merges(tmp_path: Path) -> None:
    """A partial override must not drop a sibling key (ADR 0010)."""
    low = tmp_path / "example.yaml"
    low.write_text("chat:\n  rejection_reply_text: '收到 谢谢'\n  max_scan_depth: 30\n", encoding="utf-8")
    high = tmp_path / "local.yaml"
    high.write_text("chat:\n  rejection_reply_text: '感谢回复'\n", encoding="utf-8")

    merged = config_realm.load_chain([high, low])
    assert merged["chat"]["rejection_reply_text"] == "感谢回复"
    assert merged["chat"]["max_scan_depth"] == 30


# --------------------------------------------------------------------------- #
# The masked-secret guard
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    ["••••abcd", "sk-••••1234", "****", "prefix****suffix", "your-api-key-here"],
)
def test_a_masked_or_placeholder_key_is_never_ingested(tmp_path: Path, value: str) -> None:
    """No chain may read a display value as a secret (the guard is in the merge, once)."""
    file = tmp_path / "s.yaml"
    file.write_text(f"api_key: '{value}'\n", encoding="utf-8")
    assert config_realm.load_settings(config_path=file)["api_key"] is None


@pytest.mark.parametrize("value", ["sk-live-abcdef", "eyJhbGciOi.real.token"])
def test_a_real_key_is_ingested(tmp_path: Path, value: str) -> None:
    file = tmp_path / "s.yaml"
    file.write_text(f"api_key: '{value}'\n", encoding="utf-8")
    assert config_realm.load_settings(config_path=file)["api_key"] == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("", False), ("sk-real", False), ("••••x", True), ("ab****cd", True)],
)
def test_is_mask_placeholder(value, expected: bool) -> None:
    assert config_realm.is_mask_placeholder(value) is expected


def test_an_unusable_key_is_reported_as_such(tmp_path: Path) -> None:
    """Callers ask the accessor instead of re-deriving the predicate."""
    masked = tmp_path / "masked.yaml"
    masked.write_text("api_key: '••••'\n", encoding="utf-8")
    assert config_realm.llm_settings(config_path=masked).has_usable_api_key is False

    real = tmp_path / "real.yaml"
    real.write_text("api_key: 'sk-live'\n", encoding="utf-8")
    assert config_realm.llm_settings(config_path=real).has_usable_api_key is True


# --------------------------------------------------------------------------- #
# The cache and its invalidation seam
# --------------------------------------------------------------------------- #


def test_a_file_edit_is_visible_without_an_explicit_invalidation(tmp_path: Path) -> None:
    """The cache is keyed on the chain's stat, so an edit lands on the next read.

    The Settings Panel writes `settings.local.yaml` while this process may be running;
    a cache that ignored that would pin a stale configuration for the process's life.
    """
    file = tmp_path / "s.yaml"
    file.write_text("model: 'first'\n", encoding="utf-8")
    assert config_realm.load_settings(config_path=file)["model"] == "first"

    file.write_text("model: 'second-longer'\n", encoding="utf-8")
    assert config_realm.load_settings(config_path=file)["model"] == "second-longer"


def test_the_environment_is_part_of_the_cache_key(tmp_path: Path, monkeypatch) -> None:
    """A cached load under one environment is not valid under another."""
    file = tmp_path / "s.yaml"
    file.write_text("model: 'from-file'\n", encoding="utf-8")
    assert config_realm.load_settings(config_path=file)["model"] == "from-file"

    monkeypatch.setenv("LLM_MODEL", "from-env")
    assert config_realm.load_settings(config_path=file)["model"] == "from-env"


def test_the_legacy_file_is_part_of_the_cache_key(tmp_path: Path, monkeypatch) -> None:
    """It is resolved relative to the working directory, so two loads can differ on it."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "llm.local.yaml").write_text("api_key: 'sk-from-cwd'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("boss_agent.settings.DEFAULT_CONFIG_SEARCH_PATHS", [])

    assert config_realm.load_settings()["api_key"] == "sk-from-cwd"


def test_explicit_invalidation_is_available(tmp_path: Path) -> None:
    file = tmp_path / "s.yaml"
    file.write_text("model: 'one'\n", encoding="utf-8")
    first = config_realm.load_settings(config_path=file)

    config_realm.invalidate_cache()
    file.write_text("model: 'two'\n", encoding="utf-8")
    assert config_realm.load_settings(config_path=file)["model"] == "two"
    assert first["model"] == "one", "a returned mapping is the caller's own copy"


# --------------------------------------------------------------------------- #
# The script-facing verb
# --------------------------------------------------------------------------- #


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "resolve_config.py"), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_the_cli_prints_resolved_key_value_lines() -> None:
    """Scripts read this instead of grep|awk-ing YAML, which broke on any layout change."""
    result = _run_cli("device", "avd_name", "max_tokens")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "device=emulator-5554",
        "avd_name=boss_avd_arm64",
        "max_tokens=262144",
    ]


def test_the_cli_can_print_one_bare_value_for_command_substitution() -> None:
    result = _run_cli("device", "--key", "device")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "emulator-5554"


def test_the_cli_exits_nonzero_when_a_requested_key_is_empty() -> None:
    """So a script can gate on a required setting without parsing anything."""
    result = _run_cli("no_such_setting")
    assert result.returncode == 1
    assert result.stdout.strip() == "no_such_setting="
