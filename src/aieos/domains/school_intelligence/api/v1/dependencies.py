"""School Intelligence HTTP dependencies.

resolve_trusted_context is reused from the Content HTTP surface.
"""

from __future__ import annotations

from fastapi import Request

from aieos.domains.content.api.v1.dependencies import resolve_trusted_context
from aieos.domains.school_intelligence.application.errors import (
    SchoolContextUnavailable,
)
from aieos.domains.school_intelligence.application.intelligence import (
    GetPrincipalSchoolIntelligenceService,
)

__all__ = [
    "get_principal_school_intelligence_service",
    "resolve_trusted_context",
]


def get_principal_school_intelligence_service(
    request: Request,
) -> GetPrincipalSchoolIntelligenceService:
    service = getattr(
        request.app.state, "get_principal_school_intelligence_service", None
    )
    if service is None:
        raise SchoolContextUnavailable(
            "School Context is temporarily unavailable"
        )
    return service
