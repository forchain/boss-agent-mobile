"""
tests.unit.test_llm_client
==========================
Unit tests for the LLM clients in droid_agent_core — both the OpenAI-compatible
protocol and the native Anthropic Messages protocol — plus the factory dispatch
that selects between them.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from droid_agent_core.llm import (
    AnthropicChatClient,
    LLMAuthError,
    LLMConfig,
    LLMDecisionClient,
    LLMError,
    LLMTimeoutError,
    OpenAIChatClient,
)


def _anthropic_config(**overrides) -> LLMConfig:
    """A bare-version Anthropic endpoint, the shape a host ships."""
    base = {
        "provider": "anthropic",
        "api_key": "sk-test-key",
        "base_url": "https://api.minimax.cn/anthropic",
        "model": "MiniMax-M3",
    }
    base.update(overrides)
    return LLMConfig(**base)


def _anthropic_response(*text_blocks: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": block} for block in text_blocks],
        "usage": {"input_tokens": 12, "output_tokens": 34},
    }
    return response


def test_llm_config_defaults():
    config = LLMConfig()
    assert config.provider == "openai"
    assert config.model == "MiniMax-M3"
    assert config.base_url == "https://api.minimaxi.com/v1"
    assert config.temperature == 0.2


def test_llm_config_from_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")
    monkeypatch.setenv("LLM_BASE_URL", "https://custom.api.com/v1")
    monkeypatch.setenv("LLM_MODEL", "custom-model")

    config = LLMConfig.from_env_or_file()
    assert config.api_key == "test-key-123"
    assert config.base_url == "https://custom.api.com/v1"
    assert config.model == "custom-model"


def test_llm_config_from_yaml(tmp_path):
    config_file = tmp_path / "llm.local.yaml"
    config_file.write_text(
        """
base_url: "https://yaml.api.com/v1"
api_key: "yaml-key-456"
model: "yaml-model"
temperature: 0.5
timeout_sec: 45.0
""",
        encoding="utf-8",
    )

    config = LLMConfig.from_env_or_file(config_path=config_file)
    assert config.base_url == "https://yaml.api.com/v1"
    assert config.api_key == "yaml-key-456"
    assert config.model == "yaml-model"
    assert config.temperature == 0.5
    assert config.timeout_sec == 45.0


def test_llm_config_from_settings_yaml(tmp_path):
    config_file = tmp_path / "settings.local.yaml"
    config_file.write_text(
        """
device: "emulator-5554"
server_url: "http://127.0.0.1:4723"
provider: "openai"
base_url: "https://settings-api.com/v1"
api_key: "settings-key-789"
model: "settings-model"
temperature: 0.7
timeout_sec: 90.0
max_tokens: 8192
langsmith_tracing: true
langsmith_api_key: "lsv2_test"
langsmith_project: "my-project"
""",
        encoding="utf-8",
    )

    with patch.dict("os.environ", {}, clear=True):
        config = LLMConfig.from_env_or_file(config_path=config_file)
        assert config.base_url == "https://settings-api.com/v1"
        assert config.api_key == "settings-key-789"
        assert config.model == "settings-model"
        assert config.temperature == 0.7
        assert config.timeout_sec == 90.0
        assert config.max_tokens == 8192
        assert config.langsmith_tracing is True
        assert config.langsmith_api_key == "lsv2_test"
        assert config.langsmith_project == "my-project"


def test_openai_client_chat_completion_success():
    config = LLMConfig(
        api_key="sk-test-key",
        base_url="https://api.minimaxi.com/v1",
        model="MiniMax-M3",
    )
    client = OpenAIChatClient(config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello! I am a helpful assistant.",
                }
            }
        ]
    }

    with patch("requests.post", return_value=mock_response) as mock_post:
        result = client.chat_completion([{"role": "user", "content": "Hi"}])
        assert result == "Hello! I am a helpful assistant."

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test-key"
        assert kwargs["json"]["model"] == "MiniMax-M3"
        assert kwargs["json"]["messages"] == [{"role": "user", "content": "Hi"}]
        assert kwargs["json"]["thinking"] == {"type": "disabled"}


def test_openai_client_minimax_thinking_disabled():
    config = LLMConfig(
        api_key="sk-test-key",
        base_url="https://api.minimaxi.com/v1",
        model="MiniMax-M3",
    )
    client = OpenAIChatClient(config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}]
    }

    with patch("requests.post", return_value=mock_response) as mock_post:
        # MiniMax automatically injects thinking: {"type": "disabled"}
        client.chat_completion([{"role": "user", "content": "test"}])
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["thinking"] == {"type": "disabled"}

        # Custom extra_payload can override or add options
        client.chat_completion(
            [{"role": "user", "content": "test"}],
            extra_payload={"thinking": {"type": "enabled"}},
        )
        _, kwargs2 = mock_post.call_args
        assert kwargs2["json"]["thinking"] == {"type": "enabled"}


def test_openai_client_chat_completion_json():
    config = LLMConfig(api_key="sk-test-key")
    client = OpenAIChatClient(config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    # Simulate markdown json code fence in response
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": '```json\n{"match_score": 95, "greeting": "Hello"}\n```',
                }
            }
        ]
    }

    with patch("requests.post", return_value=mock_response) as mock_post:
        parsed = client.chat_completion_json([{"role": "user", "content": "Analyze"}])
        assert parsed["match_score"] == 95
        assert parsed["greeting"] == "Hello"

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["response_format"] == {"type": "json_object"}


def test_openai_client_response_format_fallback_on_400():
    config = LLMConfig(api_key="sk-test-key")
    client = OpenAIChatClient(config)

    # First call with response_format fails with 400, second call without response_format succeeds
    mock_fail = MagicMock()
    mock_fail.status_code = 400
    mock_fail.text = "response_format is not supported"

    mock_success = MagicMock()
    mock_success.status_code = 200
    mock_success.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Fallback success",
                }
            }
        ]
    }

    with patch("requests.post", side_effect=[mock_fail, mock_success]) as mock_post:
        result = client.chat_completion(
            [{"role": "user", "content": "Hello"}],
            response_format={"type": "json_object"},
        )
        assert result == "Fallback success"
        assert mock_post.call_count == 2
        # First call has response_format
        assert "response_format" in mock_post.call_args_list[0][1]["json"]
        # Second call does not have response_format
        assert "response_format" not in mock_post.call_args_list[1][1]["json"]


def test_openai_client_auth_error():
    config = LLMConfig(api_key="invalid-key")
    client = OpenAIChatClient(config)

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    with patch("requests.post", return_value=mock_response), pytest.raises(LLMAuthError):
        client.chat_completion([{"role": "user", "content": "Hi"}])


def test_openai_client_timeout_error():
    config = LLMConfig(api_key="sk-test-key")
    client = OpenAIChatClient(config)

    with (
        patch("requests.post", side_effect=requests.exceptions.Timeout("Timed out")),
        pytest.raises(LLMTimeoutError),
    ):
        client.chat_completion([{"role": "user", "content": "Hi"}])


def test_robust_json_parsing_edge_cases():
    # 1. Unescaped inner quotes on single line
    raw1 = '{"name": "周黄金", "raw_summary": "19年经验，参与"某"核心项目主导"架构"设计。"}'
    parsed1 = OpenAIChatClient._robust_parse_json(raw1)
    assert parsed1["name"] == "周黄金"
    assert "架构" in parsed1["raw_summary"]

    # 2. Control characters & newlines inside string literals
    raw2 = '{\n  "name": "周黄金",\n  "raw_summary": "第一行内容\n第二行内容"\n}'
    parsed2 = OpenAIChatClient._robust_parse_json(raw2)
    assert parsed2["name"] == "周黄金"

    # 3. Truncated JSON auto-repair
    raw3 = '{"name": "周黄金", "years_of_experience": 19, "project_highlights": [{"name": "项目1", "description": "描述1'
    parsed3 = OpenAIChatClient._robust_parse_json(raw3)
    assert parsed3["name"] == "周黄金"
    assert parsed3["years_of_experience"] == 19
    assert len(parsed3["project_highlights"]) == 1

    # 4. Trailing commas
    raw4 = '{"name": "周黄金", "skills": ["Python", "FastAPI",],}'
    parsed4 = OpenAIChatClient._robust_parse_json(raw4)
    assert parsed4["name"] == "周黄金"
    assert len(parsed4["skills"]) == 2

    # 5. Invalid array containing key-value pairs (e.g. core_skills: [ "AI": [...] ])
    raw5 = '{\n  "name": "周黄金",\n  "core_skills": [\n    "AI与智能体": ["Claude", "Codex"],\n    "编程语言": ["Python", "Golang"]\n  ]\n}'
    parsed5 = OpenAIChatClient._robust_parse_json(raw5)
    assert parsed5["name"] == "周黄金"
    assert "AI与智能体" in parsed5["core_skills"]


def test_llm_config_ignores_masked_api_key(tmp_path):
    config_file = tmp_path / "settings.local.yaml"
    config_file.write_text(
        """
api_key: "sk-cp-j••••••••••••uG8w"
base_url: "https://api.minimaxi.com/v1"
model: "MiniMax-M3"
""",
        encoding="utf-8",
    )
    with patch.dict("os.environ", {}, clear=True):
        config = LLMConfig.from_env_or_file(config_path=config_file)
        assert config.api_key is None


def test_openai_chat_client_headers_rejects_masked_key():
    config = LLMConfig(api_key="sk-cp-j••••••••••••uG8w")
    client = OpenAIChatClient(config)
    headers = client._get_headers()
    assert "Authorization" not in headers


def test_evaluate_match_build_llm_client_fallback_on_masked_key():
    from scripts.evaluate_match import build_llm_client

    masked_json = '{"provider":"openai","base_url":"https://api.minimaxi.com/v1","api_key":"sk-cp-j••••••••••••uG8w","model":"MiniMax-M3"}'
    client = build_llm_client(masked_json)
    assert "•" not in str(client.config.api_key or "")


# --------------------------------------------------------------------------
# Anthropic Messages protocol (ticket #417)
# --------------------------------------------------------------------------


def test_anthropic_client_is_an_llm_decision_client():
    assert issubclass(AnthropicChatClient, LLMDecisionClient)


def test_anthropic_client_sends_version_and_api_key_headers():
    client = AnthropicChatClient(_anthropic_config())

    with patch("requests.post", return_value=_anthropic_response("ok")) as mock_post:
        assert client.chat_completion([{"role": "user", "content": "Hi"}]) == "ok"

    _, kwargs = mock_post.call_args
    headers = kwargs["headers"]
    assert headers["x-api-key"] == "sk-test-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["Content-Type"] == "application/json"
    # The Anthropic protocol authenticates with x-api-key, never an OpenAI bearer token.
    assert "Authorization" not in headers


def test_anthropic_client_headers_rejects_masked_key():
    client = AnthropicChatClient(_anthropic_config(api_key="sk-cp-j••••••••••••uG8w"))

    headers = client._get_headers()
    assert "x-api-key" not in headers
    assert headers["anthropic-version"] == "2023-06-01"


def test_anthropic_client_omits_header_when_no_api_key():
    client = AnthropicChatClient(_anthropic_config(api_key=None))

    assert "x-api-key" not in client._get_headers()


def test_anthropic_client_appends_messages_to_versioned_base_url():
    client = AnthropicChatClient(_anthropic_config(base_url="https://api.anthropic.com/v1"))

    with patch("requests.post", return_value=_anthropic_response("ok")) as mock_post:
        client.chat_completion([{"role": "user", "content": "Hi"}])

    args, _ = mock_post.call_args
    assert args[0] == "https://api.anthropic.com/v1/messages"


def test_anthropic_client_normalizes_bare_base_url():
    """A base URL with no version suffix still resolves to the /v1/messages endpoint."""
    client = AnthropicChatClient(_anthropic_config(base_url="https://api.minimax.cn/anthropic"))

    with patch("requests.post", return_value=_anthropic_response("ok")) as mock_post:
        client.chat_completion([{"role": "user", "content": "Hi"}])

    args, _ = mock_post.call_args
    assert args[0] == "https://api.minimax.cn/anthropic/v1/messages"


def test_anthropic_client_tolerates_trailing_slash_in_base_url():
    client = AnthropicChatClient(_anthropic_config(base_url="https://api.minimax.cn/anthropic/"))

    with patch("requests.post", return_value=_anthropic_response("ok")) as mock_post:
        client.chat_completion([{"role": "user", "content": "Hi"}])

    args, _ = mock_post.call_args
    assert args[0] == "https://api.minimax.cn/anthropic/v1/messages"


def test_anthropic_client_splits_system_out_of_the_message_list():
    client = AnthropicChatClient(_anthropic_config())

    messages = [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]
    with patch("requests.post", return_value=_anthropic_response("ok")) as mock_post:
        client.chat_completion(messages)

    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    # system is a top-level parameter of the Messages API, not a turn.
    assert payload["system"] == "You are terse."
    assert payload["messages"] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]


def test_anthropic_client_joins_multiple_system_messages():
    client = AnthropicChatClient(_anthropic_config())

    messages = [
        {"role": "system", "content": "First."},
        {"role": "system", "content": "Second."},
        {"role": "user", "content": "Hi"},
    ]
    with patch("requests.post", return_value=_anthropic_response("ok")) as mock_post:
        client.chat_completion(messages)

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["system"] == "First.\n\nSecond."


def test_anthropic_client_omits_system_when_there_is_none():
    client = AnthropicChatClient(_anthropic_config())

    with patch("requests.post", return_value=_anthropic_response("ok")) as mock_post:
        client.chat_completion([{"role": "user", "content": "Hi"}])

    _, kwargs = mock_post.call_args
    assert "system" not in kwargs["json"]


def test_anthropic_client_sends_required_max_tokens_and_config_temperature():
    client = AnthropicChatClient(_anthropic_config(temperature=0.35, max_tokens=4096))

    with patch("requests.post", return_value=_anthropic_response("ok")) as mock_post:
        client.chat_completion([{"role": "user", "content": "Hi"}])

    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert payload["model"] == "MiniMax-M3"
    assert payload["max_tokens"] == 4096
    assert payload["temperature"] == 0.35


def test_anthropic_client_joins_text_content_blocks():
    client = AnthropicChatClient(_anthropic_config())

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "content": [
            {"type": "text", "text": "first "},
            {"type": "thinking", "thinking": "ignored"},
            {"type": "text", "text": "second"},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }

    with patch("requests.post", return_value=response):
        assert client.chat_completion([{"role": "user", "content": "Hi"}]) == "first second"


def test_anthropic_client_json_mode_uses_the_prompt_not_response_format():
    """Anthropic has no JSON mode, so the contract is prompt-driven plus repair."""
    client = AnthropicChatClient(_anthropic_config())

    with patch(
        "requests.post", return_value=_anthropic_response('{"match_score": 91}')
    ) as mock_post:
        parsed = client.chat_completion_json([{"role": "user", "content": "Analyze"}])

    assert parsed == {"match_score": 91}
    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert "response_format" not in payload
    assert "JSON" in payload["system"]


def test_anthropic_client_json_mode_reuses_the_shared_repair_chain():
    """Markdown fences and token truncation are repaired exactly as the OpenAI path does."""
    client = AnthropicChatClient(_anthropic_config())

    fenced = '```json\n{"match_score": 95, "greeting": "Hello"}\n```'
    with patch("requests.post", return_value=_anthropic_response(fenced)):
        assert client.chat_completion_json([{"role": "user", "content": "A"}]) == {
            "match_score": 95,
            "greeting": "Hello",
        }

    truncated = '{"match_score": 88, "match_reasons": ["Python",'
    with patch("requests.post", return_value=_anthropic_response(truncated)):
        parsed = client.chat_completion_json([{"role": "user", "content": "B"}])
    assert parsed["match_score"] == 88
    assert parsed["match_reasons"] == ["Python"]


def test_anthropic_client_evaluate_text_match():
    client = AnthropicChatClient(_anthropic_config())

    response = _anthropic_response(
        '{"match_score": 82, "match_reasons": ["Go"], "greeting_message": "你好"}'
    )
    with patch("requests.post", return_value=response) as mock_post:
        parsed = client.evaluate_text_match("Go 后端 5 年", "Go 工程师")

    assert parsed["match_score"] == 82
    assert parsed["greeting_message"] == "你好"

    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert "Go 后端 5 年" in payload["messages"][-1]["content"]
    assert "Go 工程师" in payload["messages"][-1]["content"]


@pytest.mark.parametrize("status", [401, 403])
def test_anthropic_client_maps_auth_failures(status):
    client = AnthropicChatClient(_anthropic_config())

    response = MagicMock()
    response.status_code = status
    response.text = "denied"

    with patch("requests.post", return_value=response), pytest.raises(LLMAuthError):
        client.chat_completion([{"role": "user", "content": "Hi"}])


def test_anthropic_client_maps_timeout():
    client = AnthropicChatClient(_anthropic_config())

    with (
        patch("requests.post", side_effect=requests.exceptions.Timeout("Timed out")),
        pytest.raises(LLMTimeoutError),
    ):
        client.chat_completion([{"role": "user", "content": "Hi"}])


def test_anthropic_client_maps_connection_error():
    client = AnthropicChatClient(_anthropic_config())

    with (
        patch("requests.post", side_effect=requests.exceptions.ConnectionError("no route")),
        pytest.raises(LLMError),
    ):
        client.chat_completion([{"role": "user", "content": "Hi"}])


def test_anthropic_client_maps_other_http_errors():
    client = AnthropicChatClient(_anthropic_config())

    response = MagicMock()
    response.status_code = 500
    response.text = "boom"

    with patch("requests.post", return_value=response), pytest.raises(LLMError):
        client.chat_completion([{"role": "user", "content": "Hi"}])


def test_anthropic_client_reuses_the_shared_json_helpers():
    """The repair chain is one implementation, not a second copy per protocol."""
    # The classmethod reaches the mixin through __func__; the staticmethods come back
    # off the class as the plain function they already are.
    assert AnthropicChatClient._robust_parse_json.__func__ is (
        OpenAIChatClient._robust_parse_json.__func__
    )
    assert AnthropicChatClient._extract_json_block is OpenAIChatClient._extract_json_block
    assert AnthropicChatClient._auto_close_json is OpenAIChatClient._auto_close_json


# --------------------------------------------------------------------------
# Factory dispatch (ticket #417)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["anthropic", "Anthropic", " anthropic "])
def test_create_llm_client_dispatches_to_the_anthropic_protocol(provider):
    from boss_agent.llm_config import create_llm_client

    client = create_llm_client(config=LLMConfig(provider=provider))

    assert isinstance(client, AnthropicChatClient)


@pytest.mark.parametrize("provider", ["openai", "OpenAI"])
def test_create_llm_client_dispatches_to_the_openai_protocol(provider):
    from boss_agent.llm_config import create_llm_client

    client = create_llm_client(config=LLMConfig(provider=provider))

    assert isinstance(client, OpenAIChatClient)


@pytest.mark.parametrize("provider", ["minimax", "deepseek", "", "something-else"])
def test_create_llm_client_converges_legacy_providers_to_the_default_protocol(provider):
    from boss_agent.llm_config import create_llm_client

    client = create_llm_client(config=LLMConfig(provider=provider))

    assert isinstance(client, OpenAIChatClient)


def test_create_llm_client_returns_the_shared_abstraction():
    from boss_agent.llm_config import create_llm_client

    client = create_llm_client(config=LLMConfig(provider="anthropic"))

    assert isinstance(client, LLMDecisionClient)
    assert not isinstance(client, OpenAIChatClient)


def test_create_llm_client_passes_the_resolved_config_through():
    from boss_agent.llm_config import create_llm_client

    config = _anthropic_config(model="some-anthropic-model")
    client = create_llm_client(config=config)

    assert client.config is config
    assert client.config.model == "some-anthropic-model"
