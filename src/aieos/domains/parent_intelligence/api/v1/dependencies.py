"""Parent Intelligence HTTP dependencies.

resolve_trusted_context is reused from the Content HTTP surface.
"""

from __future__ import annotations

from fastapi import Request

from aieos.domains.content.api.v1.dependencies import resolve_trusted_context
from aieos.domains.parent_intelligence.application.errors import (
    ParentLearnerAccessUnavailable,
)
from aieos.domains.parent_intelligence.application.intelligence import (
    GetParentIntelligenceService,
)

__all__ = [
    "get_parent_intelligence_service",
    "resolve_trusted_context",
]


def get_parent_intelligence_service(
    request: Request,
) -> GetParentIntelligenceService:
    service = getattr(request.app.state, "get_parent_intelligence_service", None)
    if service is None:
        raise ParentLearnerAccessUnavailable(
            "Parent Learner Access is temporarily unavailable"
        )
    return service
