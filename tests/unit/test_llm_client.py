"""
tests.unit.test_llm_client
==========================
Unit tests for OpenAI-compatible LLM client and config loader in droid_agent_core.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from droid_agent_core.llm import (
    LLMAuthError,
    LLMConfig,
    LLMTimeoutError,
    OpenAIChatClient,
)


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
