"""
tests/unit/test_config_defaults_parity.py
=========================================
Cross-language Configuration Realm parity — the Python half.

``config/defaults.fixture.json`` is consumed by this tier *and* by
``web/src/tests/defaultsParity.test.ts``. Neither language owns the baseline; the
fixture does. That matters because the drift this exists to catch was real: the LLM
client loader started from the example *template* and shipped `max_tokens=16384` /
`timeout_sec=300.0` while the realm shipped `262144` / `120.0`, so a config-less LLM
call silently ran on a sixteenth of the documented token ceiling.
"""

import json
from pathlib import Path

import pytest

from boss_agent import config_realm

FIXTURE_PATH = Path(__file__).parents[2] / "config" / "defaults.fixture.json"


def _fixture() -> dict:
    assert FIXTURE_PATH.is_file(), (
        f"{FIXTURE_PATH} is the cross-language Configuration Realm pin; both the pytest "
        "and vitest tiers read it."
    )
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", sorted(_fixture()["shared_defaults"]))
def test_realm_baseline_matches_the_shared_fixture(key: str) -> None:
    expected = _fixture()["shared_defaults"][key]
    assert config_realm.DEFAULTS.get(key) == expected, (
        f"{key!r} drifted from config/defaults.fixture.json — change both, or change "
        "neither."
    )


@pytest.mark.parametrize("key", sorted(_fixture()["chat_defaults"]))
def test_chat_defaults_match_the_shared_fixture(key: str) -> None:
    assert config_realm.CHAT_DEFAULTS[key] == _fixture()["chat_defaults"][key]


def test_the_llm_loader_reads_the_realm_baseline(monkeypatch) -> None:
    """The specific drift: the LLM realm must not ship its own token ceiling.

    Asserted through the public loader rather than the table, because the bug was never
    a wrong constant — it was a second defaults table that nothing compared.
    """
    from boss_agent.llm_config import load_llm_config

    for _key, names in config_realm.ENV_OVERRIDES:
        for name in names:
            monkeypatch.delenv(name, raising=False)
    config_realm.invalidate_cache()

    config = load_llm_config()
    expected = _fixture()["shared_defaults"]
    assert config.max_tokens == expected["max_tokens"]
    assert config.timeout_sec == expected["timeout_sec"]
    assert config.model == expected["model"]
    assert config.base_url == expected["base_url"]


def test_the_framework_fallback_defaults_agree_with_the_realm() -> None:
    """The framework layer is app-agnostic, so its fallback table is a second one.

    It exists only so `droid_agent_core` is usable standalone. Pinning it against the
    same fixture keeps that fallback from becoming a second opinion — which is exactly
    what the 16384-vs-262144 split was.
    """
    from droid_agent_core.llm import FRAMEWORK_DEFAULTS, MASK_MARKERS, PLACEHOLDER_API_KEY

    expected = _fixture()["shared_defaults"]
    for key, value in FRAMEWORK_DEFAULTS.items():
        assert value == expected[key], key

    assert PLACEHOLDER_API_KEY == config_realm.PLACEHOLDER_API_KEY
    assert tuple(MASK_MARKERS) == tuple(config_realm.MASK_MARKERS)


def test_typed_accessors_read_the_same_baseline(monkeypatch) -> None:
    """The accessors are a view of the one table, not a second copy of it.

    Resolved against the shipped template alone, with the environment cleared: the
    point is the *baseline*, and a developer's `settings.local.yaml` or a stray
    `APPIUM_URL` would otherwise be what this test measures.
    """
    for _key, names in config_realm.ENV_OVERRIDES:
        for name in names:
            monkeypatch.delenv(name, raising=False)

    example = Path(__file__).parents[2] / "config" / "settings.example.yaml"
    expected = _fixture()["shared_defaults"]

    llm = config_realm.llm_settings(config_path=example)
    assert llm.max_tokens == expected["max_tokens"]
    assert llm.timeout_sec == expected["timeout_sec"]
    assert llm.model == expected["model"]
    assert llm.base_url == expected["base_url"]

    worker = config_realm.worker_settings(config_path=example)
    assert worker.device == expected["device"]
    assert worker.avd_name == expected["avd_name"]
    assert worker.server_url == expected["server_url"]
    assert worker.run_cleanup_on_startup == expected["run_cleanup_on_startup"]

    assert config_realm.pocketbase_settings(config_path=example).url == expected["pocketbase_url"]


def test_the_shipped_template_agrees_with_the_fixture() -> None:
    """`settings.example.yaml` is the chain's floor, so it must not contradict the table.

    A template that disagreed with the defaults would reintroduce exactly the split the
    LLM loader had: documented behaviour that no default-only run reproduces.
    """
    import yaml

    example = Path(__file__).parents[2] / "config" / "settings.example.yaml"
    assert example.is_file(), "config/settings.example.yaml is missing"
    parsed = yaml.safe_load(example.read_text(encoding="utf-8"))

    for key, expected in _fixture()["shared_defaults"].items():
        assert key in parsed, f"the template does not declare {key!r}"
        assert parsed[key] == expected, f"the template's {key!r} disagrees with the table"

    for key, expected in _fixture()["chat_defaults"].items():
        assert parsed["chat"][key] == expected
