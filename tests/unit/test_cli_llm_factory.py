"""
tests.unit.test_cli_llm_factory
==============================
The evaluation scripts' LLM seam, after ticket #419.

``evaluate_match.py``, ``parse_resume.py``, ``refine_greeting.py`` and
``refine_screening.py`` each used to name ``OpenAIChatClient`` in their
``build_llm_client``. A user who pointed a script at an Anthropic Messages
endpoint got a client that spoke the wrong wire protocol — a 404 or a silently
empty verdict, depending on how forgiving the endpoint was.

Each now delegates to ``boss_agent.adapters.outbound.llm.llm_config.create_llm_client`` and returns the
``LLMDecisionClient`` abstraction, so which protocol is spoken is decided once, by
the configuration, rather than four times by hard-coded imports.

What is asserted here is the routing, not the network: these are fast-tier tests,
so the endpoints are never reached and the API key is a literal.
"""

import ast
from pathlib import Path

import pytest
import scripts.evaluate_match as evaluate_match
import scripts.parse_resume as parse_resume
import scripts.refine_greeting as refine_greeting
import scripts.refine_screening as refine_screening

from boss_agent import main as composition
from droid_agent_core.llm import (
    AnthropicChatClient,
    LLMConfig,
    LLMDecisionClient,
    OpenAIChatClient,
)

SCRIPTS = [
    ("evaluate_match", evaluate_match),
    ("parse_resume", parse_resume),
    ("refine_greeting", refine_greeting),
    ("refine_screening", refine_screening),
]

# A literal, never a real secret: no test here reaches an endpoint.
_TEST_KEY = "unit-test-key-not-a-secret"


def _config_json(provider: str) -> str:
    return f'{{"provider": "{provider}", "api_key": "{_TEST_KEY}"}}'


@pytest.mark.parametrize("name, module", SCRIPTS, ids=[n for n, _ in SCRIPTS])
def test_script_exposes_factory_backed_builder(name: str, module) -> None:
    """Every script's builder is annotated against the abstraction, not a concrete client."""
    hint = module.build_llm_client.__annotations__["return"]
    # These modules do not defer annotations, so the hint arrives as the class itself.
    hint_name = hint if isinstance(hint, str) else hint.__name__
    assert hint_name == "LLMDecisionClient", hint_name


@pytest.mark.parametrize("name, module", SCRIPTS, ids=[n for n, _ in SCRIPTS])
@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", OpenAIChatClient),
        ("anthropic", AnthropicChatClient),
    ],
)
def test_script_routes_provider_to_its_protocol(
    name: str, module, provider: str, expected: type
) -> None:
    """The configured protocol — not a hard-coded import — decides the client."""
    client = module.build_llm_client(_config_json(provider))
    assert isinstance(client, expected)


@pytest.mark.parametrize("name, module", SCRIPTS, ids=[n for n, _ in SCRIPTS])
@pytest.mark.parametrize("legacy", ["minimax", "deepseek", "", "unknown-vendor"])
def test_script_converges_legacy_provider_to_default_protocol(
    name: str, module, legacy: str
) -> None:
    """A vendor naming no bespoke client still gets a working client, not a crash.

    These values predate protocol-keyed providers and name a model house, which
    speaks the default (OpenAI-compatible) protocol.
    """
    client = module.build_llm_client(_config_json(legacy))
    assert isinstance(client, OpenAIChatClient)


@pytest.mark.parametrize("name, module", SCRIPTS, ids=[n for n, _ in SCRIPTS])
def test_script_client_is_a_decision_client(name: str, module) -> None:
    """Whatever the protocol, the script holds the abstraction it needs to call."""
    client = module.build_llm_client(_config_json("anthropic"))
    assert isinstance(client, LLMDecisionClient)
    # The seam every caller actually depends on.
    assert callable(client.evaluate_text_match)


@pytest.mark.parametrize("name, module", SCRIPTS, ids=[n for n, _ in SCRIPTS])
def test_script_client_carries_the_resolved_configuration(name: str, module) -> None:
    """The factory receives the resolved config, so base_url and model survive routing."""
    client = module.build_llm_client(
        '{"provider": "anthropic", "api_key": "unit-test-key-not-a-secret",'
        ' "base_url": "https://api.minimax.cn/anthropic", "model": "MiniMax-M3"}'
    )
    assert isinstance(client.config, LLMConfig)
    assert client.config.base_url == "https://api.minimax.cn/anthropic"
    assert client.config.model == "MiniMax-M3"


# --- Configuration Realm: the protocol is resolved, not just documented (#419) --------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("  Anthropic  ", "anthropic"),
        # Pre-protocol vendor names converge instead of failing: they name a model
        # house that speaks the default protocol, and an existing config must keep
        # loading rather than crash on startup.
        ("minimax", "openai"),
        ("deepseek", "openai"),
        ("", "openai"),
        (None, "openai"),
        (123, "openai"),
    ],
)
def test_realm_normalizes_provider_to_a_known_protocol(raw, expected: str) -> None:
    from boss_agent.adapters.outbound.config.config_realm import LLM_PROTOCOLS, normalize_provider

    assert normalize_provider(raw) == expected
    assert normalize_provider(raw) in LLM_PROTOCOLS


def test_realm_settings_resolve_a_legacy_provider_to_the_default_protocol() -> None:
    """A saved config still naming a vendor must load, on a working protocol."""
    from boss_agent.adapters.outbound.config.config_realm import llm_settings

    resolved = llm_settings({"provider": "deepseek"})
    assert resolved.provider == "openai"


def test_realm_settings_keep_an_explicit_anthropic_provider() -> None:
    from boss_agent.adapters.outbound.config.config_realm import llm_settings

    assert llm_settings({"provider": "anthropic"}).provider == "anthropic"


# --- Finalization fixes (#463): exact pre-refactor behavior ---------------------------

#: A masked display value, never a real secret.
_MASKED_KEY = "sk-cp-j••••••••••••uG8w"


def _masked_config_json() -> str:
    return (
        '{"provider": "openai", "base_url": "https://api.minimaxi.com/v1",'
        f' "api_key": "{_MASKED_KEY}", "model": "MiniMax-M3"}}'
    )


def test_masked_key_is_a_missing_key_unless_the_caller_opts_out() -> None:
    """The shared builder falls back on a mask by default, and passes it through on request.

    ``parse_resume.py`` (and ``evaluate_match.py``) always treated a masked key as
    missing. ``refine_greeting.py`` / ``refine_screening.py`` only fell back on a
    genuinely absent key, so a masked value reached the wire and failed auth the way it
    did before the composition root existed.
    """
    fallback = composition.build_llm_client(_masked_config_json())
    assert "•" not in (fallback.config.api_key or "")

    passthrough = composition.build_llm_client(
        _masked_config_json(), treat_masked_key_as_missing=False
    )
    assert passthrough.config.api_key == _MASKED_KEY


@pytest.mark.parametrize(
    "module", [refine_greeting, refine_screening], ids=["refine_greeting", "refine_screening"]
)
def test_refine_scripts_opt_out_of_the_masked_key_fallback(module) -> None:
    """Both refinement scripts wire the builder with the pre-refactor key rule."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "build_llm_client"
    ]

    assert calls, f"{module.__name__} no longer calls the composition root's builder"
    assert any(
        keyword.arg == "treat_masked_key_as_missing"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
        for call in calls
        for keyword in call.keywords
    ), f"{module.__name__} must pass treat_masked_key_as_missing=False"


def test_parse_resume_honors_the_config_timeout_and_token_budget() -> None:
    """An inline ``--llm-config`` carries the budget, and the script must not override it."""
    client = parse_resume.build_llm_client(
        '{"provider": "openai", "api_key": "unit-test-key-not-a-secret",'
        ' "timeout_sec": 45.0, "max_tokens": 1234}'
    )

    assert client.config.timeout_sec == 45.0
    assert client.config.max_tokens == 1234


def test_parse_resume_keeps_its_pre_refactor_default_budget() -> None:
    """With no budget in the inline config, the resume lifecycle keeps its 300s/16384 run."""
    client = composition.build_llm_client(
        '{"provider": "openai", "api_key": "unit-test-key-not-a-secret"}',
        allow_config_path=True,
    )

    assert client.config.timeout_sec == 300.0
    assert client.config.max_tokens == 16384
