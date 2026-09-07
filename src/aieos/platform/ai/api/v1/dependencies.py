"""HTTP dependencies for the Platform AI Provider Aggregator."""

from __future__ import annotations

from fastapi import Request

from aieos.domains.content.api.v1.dependencies import resolve_trusted_context
from aieos.platform.ai.composition import ProviderRuntimeProjection

__all__ = ["provider_runtime_projection", "resolve_trusted_context"]


def provider_runtime_projection(request: Request) -> ProviderRuntimeProjection:
    projection = getattr(request.app.state, "provider_runtime", None)
    if projection is None:
        raise RuntimeError("Provider runtime projection is not composed")
    return projection
