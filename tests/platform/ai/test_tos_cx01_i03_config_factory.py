"""TOS-CX01-I03 provider config and composition fail-closed proofs."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aieos.platform.ai.composition import (
    FAKE_MODEL_ID,
    FAKE_PROVIDER_ID,
    PROVIDER_MODE_DEVELOPMENT_FAKE,
    PROVIDER_MODE_REAL,
    compose_configured_model_provider,
)
from aieos.platform.ai.config import (
    DEFAULT_GROQ_MODEL,
    GroqProviderConfig,
    OpenAIProviderConfig,
    load_groq_provider_config_from_env,
    load_openai_provider_config_from_env,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.providers.groq import GroqStructuredModelGateway
from aieos.platform.ai.providers.openai import OpenAIStructuredModelGateway


def test_groq_config_accepted_with_exact_provider_and_model() -> None:
    config = load_groq_provider_config_from_env(
        {
            "AIEOS_AI_PROVIDER": "groq",
            "AIEOS_GROQ_API_KEY": "offline-dummy-key",
        }
    )
    assert isinstance(config, GroqProviderConfig)
    assert config.provider_id == "groq"
    assert config.model_id == DEFAULT_GROQ_MODEL == "openai/gpt-oss-120b"
    assert config.base_url == "https://api.groq.com/openai/v1"


def test_missing_explicit_groq_key_fails() -> None:
    with pytest.raises(ValueError, match="AIEOS_GROQ_API_KEY is not set"):
        load_groq_provider_config_from_env({"AIEOS_AI_PROVIDER": "groq"})


def test_explicit_groq_does_not_silently_fall_back_to_fake() -> None:
    with pytest.raises(ValueError, match="AIEOS_GROQ_API_KEY is not set"):
        compose_configured_model_provider({"AIEOS_AI_PROVIDER": "groq"})


def test_explicit_openai_missing_key_fails_closed() -> None:
    with pytest.raises(ValueError, match="AIEOS_OPENAI_API_KEY is not set"):
        compose_configured_model_provider({"AIEOS_AI_PROVIDER": "openai"})


def test_openai_config_still_works() -> None:
    config = load_openai_provider_config_from_env(
        {
            "AIEOS_AI_PROVIDER": "openai",
            "AIEOS_OPENAI_API_KEY": "offline-dummy-key",
            "AIEOS_AI_MODEL": "gpt-5.6-terra",
        }
    )
    assert isinstance(config, OpenAIProviderConfig)
    assert config.provider_id == "openai"
    assert config.model_id == "gpt-5.6-terra"
    composed = compose_configured_model_provider(
        {
            "AIEOS_AI_PROVIDER": "openai",
            "AIEOS_OPENAI_API_KEY": "offline-dummy-key",
        },
        openai_client=MagicMock(),
    )
    assert isinstance(composed.gateway, OpenAIStructuredModelGateway)
    assert composed.provider_id == "openai"
    assert composed.projection.mode == PROVIDER_MODE_REAL
    assert not isinstance(composed.gateway, FakeStructuredModelGateway)


def test_explicit_fake_works() -> None:
    composed = compose_configured_model_provider({"AIEOS_AI_PROVIDER": "fake"})
    assert isinstance(composed.gateway, FakeStructuredModelGateway)
    assert composed.provider_id == FAKE_PROVIDER_ID
    assert composed.model_id == FAKE_MODEL_ID
    assert composed.projection.mode == PROVIDER_MODE_DEVELOPMENT_FAKE


def test_unset_provider_uses_fake_for_deterministic_development() -> None:
    composed = compose_configured_model_provider({})
    assert isinstance(composed.gateway, FakeStructuredModelGateway)
    assert composed.provider_id == FAKE_PROVIDER_ID
    assert composed.projection.mode == PROVIDER_MODE_DEVELOPMENT_FAKE


def test_groq_composition_constructs_groq_gateway() -> None:
    composed = compose_configured_model_provider(
        {
            "AIEOS_AI_PROVIDER": "groq",
            "AIEOS_GROQ_API_KEY": "offline-dummy-key",
        },
        groq_client=MagicMock(),
    )
    assert isinstance(composed.gateway, GroqStructuredModelGateway)
    assert composed.provider_id == "groq"
    assert composed.model_id == "openai/gpt-oss-120b"
    assert composed.projection.mode == PROVIDER_MODE_REAL
    assert not isinstance(composed.gateway, FakeStructuredModelGateway)
    groq = next(item for item in composed.projection.providers if item.provider_id == "groq")
    assert groq.active is True
    assert groq.configured is True
    fake = next(item for item in composed.projection.providers if item.provider_id == "fake")
    assert fake.active is False
    assert fake.development_only is True
