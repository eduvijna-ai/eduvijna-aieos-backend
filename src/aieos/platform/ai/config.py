"""Environment-backed AI provider configuration. Secrets stay in env only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_AI_PROVIDER = "openai"
DEFAULT_AI_MODEL = "gpt-5.6-terra"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_MAX_OUTPUT_TOKENS = 8000
DEFAULT_GROQ_MAX_OUTPUT_TOKENS = 16000
DEFAULT_TIMEOUT_SECONDS = 75.0
DEFAULT_GROQ_TIMEOUT_SECONDS = 180.0

ENV_AI_PROVIDER = "AIEOS_AI_PROVIDER"
ENV_AI_MODEL = "AIEOS_AI_MODEL"
ENV_OPENAI_API_KEY = "AIEOS_OPENAI_API_KEY"
ENV_GROQ_API_KEY = "AIEOS_GROQ_API_KEY"
ENV_AI_MAX_OUTPUT_TOKENS = "AIEOS_AI_MAX_OUTPUT_TOKENS"
ENV_AI_TIMEOUT_SECONDS = "AIEOS_AI_TIMEOUT_SECONDS"
ENV_GENERATION_LEASE_SECONDS = "AIEOS_GENERATION_LEASE_SECONDS"

DEFAULT_GENERATION_LEASE_SECONDS = 120

SUPPORTED_AI_PROVIDERS = frozenset({"fake", "openai", "groq"})
GROQ_OPENAI_COMPATIBLE_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True, slots=True)
class OpenAIProviderConfig:
    provider_id: str
    model_id: str
    api_key: str
    max_output_tokens: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("OpenAI API key is required for the OpenAI adapter")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class GroqProviderConfig:
    provider_id: str
    model_id: str
    api_key: str
    base_url: str
    max_output_tokens: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if self.provider_id != "groq":
            raise ValueError("Groq adapter provider_id must be groq")
        if not self.api_key:
            raise ValueError("Groq API key is required for the Groq adapter")
        if not self.model_id:
            raise ValueError("Groq model_id must be a non-empty model identifier")
        if not self.base_url:
            raise ValueError("Groq base_url is required")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


def _env_mapping(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def selected_ai_provider(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the explicit AIEOS_AI_PROVIDER value, or None when unset.

    Unset is distinct from fake/openai/groq. Callers must not treat a missing
    selection as an implicit real provider.
    """
    env = _env_mapping(environ)
    raw = env.get(ENV_AI_PROVIDER)
    if raw is None:
        return None
    provider = raw.strip()
    if not provider:
        return None
    return provider


def require_supported_ai_provider(provider: str) -> str:
    if provider not in SUPPORTED_AI_PROVIDERS:
        raise ValueError(
            f"unsupported AIEOS_AI_PROVIDER={provider!r}; "
            "expected fake, openai, or groq"
        )
    return provider


def provider_key_configured(
    env_name: str, environ: Mapping[str, str] | None = None
) -> bool:
    """Harmless configured/not-configured probe. Never returns the secret."""
    env = _env_mapping(environ)
    return bool((env.get(env_name) or "").strip())


def _positive_int(raw: str, *, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(raw: str, *, name: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def load_openai_provider_config_from_env(
    environ: dict[str, str] | None = None,
) -> OpenAIProviderConfig:
    """Load OpenAI adapter config. Never logs or returns the key for persistence."""
    env = _env_mapping(environ)
    provider = selected_ai_provider(env)
    if provider is None:
        provider = DEFAULT_AI_PROVIDER
    if provider != "openai":
        raise ValueError(f"unsupported AIEOS_AI_PROVIDER={provider!r}; expected openai")
    model = (env.get(ENV_AI_MODEL) or DEFAULT_AI_MODEL).strip()
    if not model:
        raise ValueError("AIEOS_AI_MODEL must be a non-empty model identifier")
    api_key = (env.get(ENV_OPENAI_API_KEY) or "").strip()
    if not api_key:
        raise ValueError("AIEOS_OPENAI_API_KEY is not set")
    raw_tokens = (env.get(ENV_AI_MAX_OUTPUT_TOKENS) or str(DEFAULT_MAX_OUTPUT_TOKENS)).strip()
    max_tokens = _positive_int(raw_tokens, name="AIEOS_AI_MAX_OUTPUT_TOKENS")
    raw_timeout = (env.get(ENV_AI_TIMEOUT_SECONDS) or str(DEFAULT_TIMEOUT_SECONDS)).strip()
    timeout = _positive_float(raw_timeout, name="AIEOS_AI_TIMEOUT_SECONDS")
    return OpenAIProviderConfig(
        provider_id="openai",
        model_id=model,
        api_key=api_key,
        max_output_tokens=max_tokens,
        timeout_seconds=timeout,
    )


def load_groq_provider_config_from_env(
    environ: Mapping[str, str] | None = None,
) -> GroqProviderConfig:
    """Load Groq adapter config. Never logs or returns the key for persistence."""
    env = _env_mapping(environ)
    provider = selected_ai_provider(env)
    if provider != "groq":
        raise ValueError(f"unsupported AIEOS_AI_PROVIDER={provider!r}; expected groq")
    model = (env.get(ENV_AI_MODEL) or DEFAULT_GROQ_MODEL).strip()
    if not model:
        raise ValueError("AIEOS_AI_MODEL must be a non-empty model identifier")
    api_key = (env.get(ENV_GROQ_API_KEY) or "").strip()
    if not api_key:
        raise ValueError("AIEOS_GROQ_API_KEY is not set")
    raw_tokens = (
        env.get(ENV_AI_MAX_OUTPUT_TOKENS) or str(DEFAULT_GROQ_MAX_OUTPUT_TOKENS)
    ).strip()
    max_tokens = _positive_int(raw_tokens, name="AIEOS_AI_MAX_OUTPUT_TOKENS")
    raw_timeout = (
        env.get(ENV_AI_TIMEOUT_SECONDS) or str(DEFAULT_GROQ_TIMEOUT_SECONDS)
    ).strip()
    timeout = _positive_float(raw_timeout, name="AIEOS_AI_TIMEOUT_SECONDS")
    return GroqProviderConfig(
        provider_id="groq",
        model_id=model,
        api_key=api_key,
        base_url=GROQ_OPENAI_COMPATIBLE_BASE_URL,
        max_output_tokens=max_tokens,
        timeout_seconds=timeout,
    )


def load_generation_lease_seconds(
    environ: dict[str, str] | None = None,
) -> int:
    """Lease duration for RUNNING GenerationRun recovery (NON_PRODUCTION default)."""
    env = _env_mapping(environ)
    raw = (
        env.get(ENV_GENERATION_LEASE_SECONDS) or str(DEFAULT_GENERATION_LEASE_SECONDS)
    ).strip()
    return _positive_int(raw, name="AIEOS_GENERATION_LEASE_SECONDS")
