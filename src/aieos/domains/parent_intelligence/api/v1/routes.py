"""Parent Intelligence HTTP v1. Calls application services only."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from aieos.domains.parent_intelligence.api.v1.dependencies import (
    get_parent_intelligence_service,
    resolve_trusted_context,
)
from aieos.domains.parent_intelligence.api.v1.models import (
    ParentAssignmentStatusResponse,
    ParentChildCardResponse,
    ParentIntelligenceResponse,
    ParentIntelligenceTimeWindowResponse,
)
from aieos.domains.parent_intelligence.application.intelligence import (
    GetParentIntelligenceService,
)
from aieos.domains.parent_intelligence.application.models import (
    ParentAssignmentStatus,
    ParentChildCard,
    ParentIntelligenceReadModel,
)
from aieos.platform.api.problems import ProblemDetails
from aieos.platform.security.context import TrustedSecurityContext

router = APIRouter(prefix="/api/v1", tags=["parent-os"])


def _problem_responses(*statuses: int) -> dict[int, dict[str, object]]:
    return {
        status: {"model": ProblemDetails, "description": "RFC 9457 Problem Details"}
        for status in statuses
    }


_GET_RESPONSES = _problem_responses(400, 401, 403, 404, 422, 500, 503)


def _assignment(model: ParentAssignmentStatus) -> ParentAssignmentStatusResponse:
    return ParentAssignmentStatusResponse(
        assignment_id=model.assignment_id,
        title=model.title,
        content_type=model.content_type,
        available_from=model.available_from,
        due_at=model.due_at,
        attempt_status=model.attempt_status,
        submitted_at=model.submitted_at,
    )


def _child(model: ParentChildCard) -> ParentChildCardResponse:
    return ParentChildCardResponse(
        learner_principal_id=model.learner_principal_id,
        assignments=[_assignment(item) for item in model.assignments],
    )


def _to_response(model: ParentIntelligenceReadModel) -> ParentIntelligenceResponse:
    return ParentIntelligenceResponse(
        generated_at=model.generated_at,
        projection_mode=model.projection_mode,
        time_window=ParentIntelligenceTimeWindowResponse(
            mode=model.time_window.mode,
            start=model.time_window.start,
            end=model.time_window.end,
        ),
        children=[_child(item) for item in model.children],
    )


@router.get(
    "/parent-os/home",
    response_model=ParentIntelligenceResponse,
    operation_id="parent_os_home_get",
    responses=_GET_RESPONSES,
)
def parent_os_home_get(
    ctx: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetParentIntelligenceService,
        Depends(get_parent_intelligence_service),
    ],
) -> ParentIntelligenceResponse:
    result = service.get_home(ctx.tenant_id, ctx.principal_id)
    return _to_response(result)


@router.get(
    "/parent-os/children/{learner_principal_id}",
    response_model=ParentIntelligenceResponse,
    operation_id="parent_os_child_get",
    responses=_GET_RESPONSES,
)
def parent_os_child_get(
    learner_principal_id: UUID,
    ctx: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetParentIntelligenceService,
        Depends(get_parent_intelligence_service),
    ],
) -> ParentIntelligenceResponse:
    result = service.get_child(
        ctx.tenant_id, ctx.principal_id, learner_principal_id
    )
    return _to_response(result)
