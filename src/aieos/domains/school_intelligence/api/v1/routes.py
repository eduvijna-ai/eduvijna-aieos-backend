"""Principal School Intelligence HTTP v1. Calls application services only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from aieos.domains.school_intelligence.api.v1.dependencies import (
    get_principal_school_intelligence_service,
    resolve_trusted_context,
)
from aieos.domains.school_intelligence.api.v1.models import (
    AssignmentLifecycleResponse,
    EvaluationCoverageAmongSubmittedResponse,
    PrincipalSchoolIntelligenceClassCardResponse,
    PrincipalSchoolIntelligenceResponse,
    PrincipalSchoolIntelligenceSummaryResponse,
    SchoolIntelligenceEvaluationPolicyResponse,
    SchoolIntelligenceTimeWindowResponse,
)
from aieos.domains.school_intelligence.application.intelligence import (
    GetPrincipalSchoolIntelligenceService,
)
from aieos.domains.school_intelligence.application.models import (
    PrincipalSchoolIntelligenceClassCard,
    PrincipalSchoolIntelligenceReadModel,
    PrincipalSchoolIntelligenceSummary,
)
from aieos.platform.api.problems import ProblemDetails
from aieos.platform.security.context import TrustedSecurityContext

router = APIRouter(prefix="/api/v1", tags=["principal-os"])


def _problem_responses(*statuses: int) -> dict[int, dict[str, object]]:
    return {
        status: {"model": ProblemDetails, "description": "RFC 9457 Problem Details"}
        for status in statuses
    }


_GET_RESPONSES = _problem_responses(400, 401, 403, 422, 500, 503)


def _lifecycle(model) -> AssignmentLifecycleResponse:
    return AssignmentLifecycleResponse(
        active=model.active,
        closed=model.closed,
        cancelled=model.cancelled,
    )


def _coverage(model) -> EvaluationCoverageAmongSubmittedResponse:
    return EvaluationCoverageAmongSubmittedResponse(
        submitted_count=model.submitted_count,
        current_policy_evaluated_count=model.current_policy_evaluated_count,
    )


def _summary(
    model: PrincipalSchoolIntelligenceSummary,
) -> PrincipalSchoolIntelligenceSummaryResponse:
    return PrincipalSchoolIntelligenceSummaryResponse(
        in_scope_class_count=model.in_scope_class_count,
        classes_with_assignment_activity_count=(
            model.classes_with_assignment_activity_count
        ),
        teaching_assignment_count=model.teaching_assignment_count,
        assignment_lifecycle=_lifecycle(model.assignment_lifecycle),
        learner_submission_count=model.learner_submission_count,
        current_policy_evaluation_count=model.current_policy_evaluation_count,
        submitted_but_not_current_policy_evaluated_count=(
            model.submitted_but_not_current_policy_evaluated_count
        ),
        evaluation_coverage_among_submitted=_coverage(
            model.evaluation_coverage_among_submitted
        ),
        classes_with_recorded_classroom_assessment_count=(
            model.classes_with_recorded_classroom_assessment_count
        ),
        assignments_with_recorded_classroom_assessment_count=(
            model.assignments_with_recorded_classroom_assessment_count
        ),
        completed_teaching_execution_count=model.completed_teaching_execution_count,
        remediation_activity_count=model.remediation_activity_count,
    )


def _class_card(
    model: PrincipalSchoolIntelligenceClassCard,
) -> PrincipalSchoolIntelligenceClassCardResponse:
    return PrincipalSchoolIntelligenceClassCardResponse(
        class_ref=model.class_ref,
        display_label=model.display_label,
        has_assignment_activity=model.has_assignment_activity,
        teaching_assignment_count=model.teaching_assignment_count,
        assignment_lifecycle=_lifecycle(model.assignment_lifecycle),
        learner_submission_count=model.learner_submission_count,
        current_policy_evaluation_count=model.current_policy_evaluation_count,
        submitted_but_not_current_policy_evaluated_count=(
            model.submitted_but_not_current_policy_evaluated_count
        ),
        evaluation_coverage_among_submitted=_coverage(
            model.evaluation_coverage_among_submitted
        ),
        has_recorded_classroom_assessment=model.has_recorded_classroom_assessment,
        assignments_with_recorded_classroom_assessment_count=(
            model.assignments_with_recorded_classroom_assessment_count
        ),
        completed_teaching_execution_count=model.completed_teaching_execution_count,
        remediation_activity_count=model.remediation_activity_count,
    )


def _to_response(
    model: PrincipalSchoolIntelligenceReadModel,
) -> PrincipalSchoolIntelligenceResponse:
    return PrincipalSchoolIntelligenceResponse(
        generated_at=model.generated_at,
        projection_mode=model.projection_mode,
        time_window=SchoolIntelligenceTimeWindowResponse(
            mode=model.time_window.mode,
            start=model.time_window.start,
            end=model.time_window.end,
        ),
        evaluation_policy=SchoolIntelligenceEvaluationPolicyResponse(
            policy_id=model.evaluation_policy.policy_id,
            policy_version=model.evaluation_policy.policy_version,
        ),
        sources=list(model.sources),
        summary=_summary(model.summary),
        classes=[_class_card(item) for item in model.classes],
    )


@router.get(
    "/principal-os/school-intelligence",
    response_model=PrincipalSchoolIntelligenceResponse,
    operation_id="principal_os_school_intelligence_get",
    responses=_GET_RESPONSES,
)
def principal_os_school_intelligence_get(
    ctx: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetPrincipalSchoolIntelligenceService,
        Depends(get_principal_school_intelligence_service),
    ],
) -> PrincipalSchoolIntelligenceResponse:
    result = service.get(ctx.tenant_id, ctx.principal_id)
    return _to_response(result)
