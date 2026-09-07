"""Read-only Provider Aggregator HTTP surface."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from aieos.platform.ai.api.v1.dependencies import (
    provider_runtime_projection,
    resolve_trusted_context,
)
from aieos.platform.ai.api.v1.models import (
    CapabilityRouteResponse,
    ProviderAggregatorResponse,
    ProviderCandidateResponse,
)
from aieos.platform.ai.composition import ProviderRuntimeProjection
from aieos.platform.api.problems import ProblemDetails
from aieos.platform.security.context import TrustedSecurityContext

router = APIRouter(prefix="/api/v1", tags=["platform-ai"])


def _problem_responses(*statuses: int) -> dict[int, dict[str, object]]:
    return {
        status: {"model": ProblemDetails, "description": "RFC 9457 Problem Details"}
        for status in statuses
    }


_GET_RESPONSES = _problem_responses(400, 401, 403, 422, 500, 503)


def _to_response(projection: ProviderRuntimeProjection) -> ProviderAggregatorResponse:
    return ProviderAggregatorResponse(
        active_provider_id=projection.active_provider_id,
        active_model_id=projection.active_model_id,
        mode=projection.mode,
        providers=[
            ProviderCandidateResponse(
                provider_id=item.provider_id,
                display_name=item.display_name,
                configured=item.configured,
                active=item.active,
                model_id=item.model_id,
                development_only=item.development_only,
            )
            for item in projection.providers
        ],
        capability_routes=[
            CapabilityRouteResponse(
                capability_id=item.capability_id,
                display_name=item.display_name,
                provider_id=item.provider_id,
                model_id=item.model_id,
            )
            for item in projection.capability_routes
        ],
    )


@router.get(
    "/platform/ai/providers",
    response_model=ProviderAggregatorResponse,
    operation_id="platform_ai_providers_get",
    responses=_GET_RESPONSES,
)
def platform_ai_providers_get(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    projection: Annotated[
        ProviderRuntimeProjection, Depends(provider_runtime_projection)
    ],
) -> ProviderAggregatorResponse:
    """Read-only runtime projection of the active Model Gateway provider."""
    del context
    return _to_response(projection)
