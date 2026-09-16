from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from k8s_sre_agent.llm import (
    AnthropicProvider,
    LLMError,
    LLMSettings,
    OllamaProvider,
    OpenAICompatibleProvider,
    build_provider,
)


def test_defaults_to_no_provider() -> None:
    settings = LLMSettings.from_env({})
    assert settings.provider == "none"
    assert build_provider(settings) is None


def test_rejects_unknown_provider() -> None:
    with pytest.raises(LLMError, match="Unsupported"):
        LLMSettings.from_env({"SRE_AGENT_LLM_PROVIDER": "skynet"})


def test_openai_requires_api_key_and_model() -> None:
    with pytest.raises(LLMError, match="SRE_AGENT_LLM_MODEL"):
        build_provider(LLMSettings.from_env({"SRE_AGENT_LLM_PROVIDER": "openai"}))
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        build_provider(
            LLMSettings.from_env(
                {"SRE_AGENT_LLM_PROVIDER": "openai", "SRE_AGENT_LLM_MODEL": "some-model"}
            )
        )


def test_settings_read_everything_from_env() -> None:
    settings = LLMSettings.from_env(
        {
            "SRE_AGENT_LLM_PROVIDER": "OLLAMA",
            "SRE_AGENT_LLM_MODEL": "qwen3",
            "SRE_AGENT_LLM_TIMEOUT": "30",
            "OLLAMA_HOST": "http://ollama:11434",
        }
    )
    provider = build_provider(settings)
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen3"
    assert settings.timeout == 30.0


def test_openai_compatible_request_shape() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "## Summary"}}]})

    provider = OpenAICompatibleProvider(
        model="m",
        api_key="test-key",
        base_url="http://llm.local/v1/",
        timeout=5,
        transport=httpx.MockTransport(handler),
    )
    assert provider.complete("sys", "prompt") == "## Summary"
    assert seen["url"] == "http://llm.local/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_openai_compatible_http_error_is_wrapped() -> None:
    provider = OpenAICompatibleProvider(
        model="m",
        api_key="k",
        base_url="http://llm.local/v1",
        timeout=5,
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})),
    )
    with pytest.raises(LLMError, match="401"):
        provider.complete("sys", "prompt")


def test_ollama_request_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert body["stream"] is False
        return httpx.Response(200, json={"message": {"content": "analysis"}})

    provider = OllamaProvider(
        model="llama3.1",
        host="http://ollama:11434",
        timeout=5,
        transport=httpx.MockTransport(handler),
    )
    assert provider.complete("sys", "prompt") == "analysis"


class _FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


def _fake_client(response: Any) -> Any:
    messages = _FakeMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_anthropic_provider_uses_adaptive_thinking_and_extracts_text() -> None:
    pytest.importorskip("anthropic")
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="## Summary\nroot cause"),
        ],
    )
    client, messages = _fake_client(response)
    provider = AnthropicProvider(model="claude-opus-5", timeout=5, client=client)
    assert provider.complete("sys", "prompt") == "## Summary\nroot cause"
    assert messages.kwargs["model"] == "claude-opus-5"
    assert messages.kwargs["thinking"] == {"type": "adaptive"}
    assert messages.kwargs["system"] == "sys"


def test_anthropic_provider_surfaces_refusals() -> None:
    pytest.importorskip("anthropic")
    client, _ = _fake_client(SimpleNamespace(stop_reason="refusal", content=[]))
    provider = AnthropicProvider(model="claude-opus-5", timeout=5, client=client)
    with pytest.raises(LLMError, match="declined"):
        provider.complete("sys", "prompt")
