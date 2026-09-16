"""LLM providers, configured exclusively through environment variables.

No credential is ever read from the command line, a file in the repository or a default
value: API keys come from the environment of the process only.

| Variable                 | Used by             | Default                          |
|--------------------------|---------------------|----------------------------------|
| SRE_AGENT_LLM_PROVIDER   | all                 | ``none``                         |
| SRE_AGENT_LLM_MODEL      | all                 | provider specific (required for openai) |
| SRE_AGENT_LLM_TIMEOUT    | all                 | ``120`` (seconds)                |
| ANTHROPIC_API_KEY        | anthropic           | (resolved by the Anthropic SDK)  |
| OPENAI_API_KEY           | openai              | required                         |
| OPENAI_BASE_URL          | openai              | ``https://api.openai.com/v1``    |
| OLLAMA_HOST              | ollama              | ``http://localhost:11434``       |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

PROVIDERS = ("none", "anthropic", "openai", "ollama")

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "ollama": "llama3.1",
}


class LLMError(RuntimeError):
    """Raised when a provider is misconfigured or a completion fails."""


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, prompt: str) -> str: ...


@dataclass
class LLMSettings:
    provider: str = "none"
    model: str | None = None
    timeout: float = 120.0
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_host: str = "http://localhost:11434"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMSettings:
        env = os.environ if env is None else env
        provider = env.get("SRE_AGENT_LLM_PROVIDER", "none").strip().lower() or "none"
        if provider not in PROVIDERS:
            raise LLMError(
                f"Unsupported SRE_AGENT_LLM_PROVIDER={provider!r}; expected one of {PROVIDERS}"
            )
        try:
            timeout = float(env.get("SRE_AGENT_LLM_TIMEOUT", "120"))
        except ValueError as exc:
            raise LLMError("SRE_AGENT_LLM_TIMEOUT must be a number of seconds") from exc
        return cls(
            provider=provider,
            model=env.get("SRE_AGENT_LLM_MODEL") or None,
            timeout=timeout,
            openai_api_key=env.get("OPENAI_API_KEY") or None,
            openai_base_url=env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            ollama_host=env.get("OLLAMA_HOST") or "http://localhost:11434",
        )


class AnthropicProvider:
    """Claude through the official Anthropic Python SDK.

    Install with ``pip install 'k8s-sre-agent[anthropic]'``.
    """

    name = "anthropic"

    def __init__(self, model: str, timeout: float, client: Any | None = None) -> None:
        self.model = model
        if client is None:
            try:
                import anthropic  # noqa: PLC0415 - optional dependency
            except ImportError as exc:
                raise LLMError(
                    "The anthropic provider needs the optional dependency: "
                    "pip install 'k8s-sre-agent[anthropic]'"
                ) from exc
            # Credentials are resolved by the SDK from the environment
            # (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile).
            client = anthropic.Anthropic(timeout=timeout)
        self._client = client

    def complete(self, system: str, prompt: str) -> str:
        import anthropic  # noqa: PLC0415

        try:
            response: Any = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "adaptive"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic authentication failed: check ANTHROPIC_API_KEY") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"Anthropic model not found: {self.model}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached, retry later") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Cannot reach the Anthropic API") from exc

        if response.stop_reason == "refusal":
            raise LLMError("The model declined to analyse this incident")
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise LLMError("The model returned an empty answer")
        return text


class OpenAICompatibleProvider:
    """Any OpenAI-compatible Chat Completions endpoint (OpenAI, Azure proxy, vLLM, LiteLLM...)."""

    name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def complete(self, system: str, prompt: str) -> str:
        try:
            resp = self._client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"]["content"])
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"OpenAI-compatible API error {exc.response.status_code}") from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"OpenAI-compatible request failed: {exc}") from exc


class OllamaProvider:
    """A local or remote Ollama server: no data leaves your infrastructure."""

    name = "ollama"

    def __init__(
        self, model: str, host: str, timeout: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.model = model
        self._client = httpx.Client(base_url=host.rstrip("/"), timeout=timeout, transport=transport)

    def complete(self, system: str, prompt: str) -> str:
        try:
            resp = self._client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            resp.raise_for_status()
            return str(resp.json()["message"]["content"])
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Ollama API error {exc.response.status_code}") from exc
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc


def build_provider(settings: LLMSettings) -> LLMProvider | None:
    """Return the configured provider, or ``None`` when LLM analysis is disabled."""
    if settings.provider == "none":
        return None
    model = settings.model or DEFAULT_MODELS.get(settings.provider)
    if not model:
        raise LLMError(
            f"SRE_AGENT_LLM_MODEL is required when SRE_AGENT_LLM_PROVIDER={settings.provider}"
        )
    if settings.provider == "anthropic":
        return AnthropicProvider(model=model, timeout=settings.timeout)
    if settings.provider == "openai":
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is required when SRE_AGENT_LLM_PROVIDER=openai")
        return OpenAICompatibleProvider(
            model=model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.timeout,
        )
    return OllamaProvider(model=model, host=settings.ollama_host, timeout=settings.timeout)
