"""Provider-neutral Model Gateway composition for development runtimes.

Selects and constructs a StructuredModelGateway from environment or an
injected gateway. Domain code must not import provider SDKs from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aieos.platform.ai.config import (
    DEFAULT_AI_MODEL,
    DEFAULT_GROQ_MODEL,
    ENV_GROQ_API_KEY,
    ENV_OPENAI_API_KEY,
    load_groq_provider_config_from_env,
    load_openai_provider_config_from_env,
    provider_key_configured,
    require_supported_ai_provider,
    selected_ai_provider,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import StructuredModelGateway
from aieos.platform.ai.providers.groq import GroqStructuredModelGateway
from aieos.platform.ai.providers.openai import OpenAIStructuredModelGateway
from aieos.platform.capabilities.models import (
    CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
    CAPABILITY_EDUCATION_GENERATE_WORKSHEET,
    CAPABILITY_TEACHER_OS_ASSISTANT_RESPOND,
)

PROVIDER_MODE_REAL = "REAL"
PROVIDER_MODE_DEVELOPMENT_FAKE = "DEVELOPMENT_FAKE"

FAKE_PROVIDER_ID = "fake"
FAKE_MODEL_ID = "fake-model"

_PROVIDER_DISPLAY_NAMES = {
    "groq": "Groq",
    "openai": "OpenAI",
    FAKE_PROVIDER_ID: "Development Fake",
}

_CAPABILITY_ROUTES: tuple[tuple[str, str], ...] = (
    (CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT, "Preparation Kit"),
    (CAPABILITY_TEACHER_OS_ASSISTANT_RESPOND, "AI Assistant"),
    (CAPABILITY_EDUCATION_GENERATE_WORKSHEET, "Worksheet Generation"),
)


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    provider_id: str
    display_name: str
    configured: bool
    active: bool
    model_id: str | None
    development_only: bool


@dataclass(frozen=True, slots=True)
class CapabilityRoute:
    capability_id: str
    display_name: str
    provider_id: str
    model_id: str


@dataclass(frozen=True, slots=True)
class ProviderRuntimeProjection:
    active_provider_id: str
    active_model_id: str
    mode: str
    providers: tuple[ProviderCandidate, ...]
    capability_routes: tuple[CapabilityRoute, ...]


@dataclass(frozen=True, slots=True)
class ConfiguredModelProvider:
    gateway: StructuredModelGateway
    provider_id: str
    model_id: str
    projection: ProviderRuntimeProjection


def provider_runtime_mode(provider_id: str) -> str:
    if provider_id == FAKE_PROVIDER_ID:
        return PROVIDER_MODE_DEVELOPMENT_FAKE
    return PROVIDER_MODE_REAL


def build_provider_runtime_projection(
    *,
    provider_id: str,
    model_id: str,
    gateway_composed: bool,
    environ: Mapping[str, str] | None = None,
) -> ProviderRuntimeProjection:
    groq_configured = provider_key_configured(ENV_GROQ_API_KEY, environ)
    openai_configured = provider_key_configured(ENV_OPENAI_API_KEY, environ)
    groq_model = model_id if provider_id == "groq" else DEFAULT_GROQ_MODEL
    openai_model = model_id if provider_id == "openai" else DEFAULT_AI_MODEL
    providers = (
        ProviderCandidate(
            provider_id="groq",
            display_name=_PROVIDER_DISPLAY_NAMES["groq"],
            configured=groq_configured,
            active=provider_id == "groq",
            model_id=groq_model if groq_configured or provider_id == "groq" else None,
            development_only=False,
        ),
        ProviderCandidate(
            provider_id="openai",
            display_name=_PROVIDER_DISPLAY_NAMES["openai"],
            configured=openai_configured,
            active=provider_id == "openai",
            model_id=openai_model if openai_configured or provider_id == "openai" else None,
            development_only=False,
        ),
        ProviderCandidate(
            provider_id=FAKE_PROVIDER_ID,
            display_name=_PROVIDER_DISPLAY_NAMES[FAKE_PROVIDER_ID],
            configured=True,
            active=provider_id == FAKE_PROVIDER_ID,
            model_id=FAKE_MODEL_ID,
            development_only=True,
        ),
    )
    routes: tuple[CapabilityRoute, ...] = ()
    if gateway_composed:
        routes = tuple(
            CapabilityRoute(
                capability_id=capability_id,
                display_name=display_name,
                provider_id=provider_id,
                model_id=model_id,
            )
            for capability_id, display_name in _CAPABILITY_ROUTES
        )
    mode = (
        PROVIDER_MODE_DEVELOPMENT_FAKE
        if provider_id == FAKE_PROVIDER_ID or not gateway_composed
        else PROVIDER_MODE_REAL
    )
    return ProviderRuntimeProjection(
        active_provider_id=provider_id,
        active_model_id=model_id,
        mode=mode,
        providers=providers,
        capability_routes=routes,
    )


def compose_configured_model_provider(
    environ: Mapping[str, str] | None = None,
    *,
    injected_gateway: StructuredModelGateway | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    groq_client: Any | None = None,
    openai_client: Any | None = None,
) -> ConfiguredModelProvider:
    """Select and construct the active Model Gateway.

    Explicit openai/groq without a valid key fails closed. Unset provider in
    development composition uses Fake. Injected gateways are used as-is.
    """
    if injected_gateway is not None:
        active_provider_id = provider_id or FAKE_PROVIDER_ID
        active_model_id = model_id or FAKE_MODEL_ID
        projection = build_provider_runtime_projection(
            provider_id=active_provider_id,
            model_id=active_model_id,
            gateway_composed=True,
            environ=environ,
        )
        return ConfiguredModelProvider(
            gateway=injected_gateway,
            provider_id=active_provider_id,
            model_id=active_model_id,
            projection=projection,
        )

    selected = selected_ai_provider(environ)
    if selected is None or selected == FAKE_PROVIDER_ID:
        if selected == FAKE_PROVIDER_ID:
            require_supported_ai_provider(selected)
        active_provider_id = FAKE_PROVIDER_ID
        active_model_id = FAKE_MODEL_ID
        gateway: StructuredModelGateway = FakeStructuredModelGateway()
    else:
        require_supported_ai_provider(selected)
        if selected == "groq":
            config = load_groq_provider_config_from_env(environ)
            gateway = GroqStructuredModelGateway(config, client=groq_client)
            active_provider_id = config.provider_id
            active_model_id = config.model_id
        else:
            openai_config = load_openai_provider_config_from_env(
                None if environ is None else dict(environ)
            )
            gateway = OpenAIStructuredModelGateway(
                openai_config, client=openai_client
            )
            active_provider_id = openai_config.provider_id
            active_model_id = openai_config.model_id

    projection = build_provider_runtime_projection(
        provider_id=active_provider_id,
        model_id=active_model_id,
        gateway_composed=True,
        environ=environ,
    )
    return ConfiguredModelProvider(
        gateway=gateway,
        provider_id=active_provider_id,
        model_id=active_model_id,
        projection=projection,
    )
