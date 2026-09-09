"""Assessment HTTP dependencies.

resolve_trusted_context is reused from the Content HTTP surface.
"""

from __future__ import annotations

from fastapi import Request

from aieos.domains.content.api.v1.dependencies import resolve_trusted_context
from aieos.domains.assessment.application.evaluation_ensure import (
    EnsureLearnerAssessmentEvaluationService,
)
from aieos.domains.assessment.application.intelligence import (
    GetAssignmentAssessmentIntelligenceService,
)
from aieos.domains.assessment.application.mutations import (
    CorrectClassroomAssessmentService,
    VoidClassroomAssessmentService,
)
from aieos.domains.assessment.application.queries import (
    GetClassroomAssessmentService,
    ListClassroomAssessmentsService,
)
from aieos.domains.assessment.application.record import RecordClassroomAssessmentService

__all__ = [
    "correct_classroom_assessment_service",
    "get_assignment_assessment_intelligence_service",
    "get_classroom_assessment_service",
    "list_classroom_assessments_service",
    "record_classroom_assessment_service",
    "resolve_trusted_context",
    "void_classroom_assessment_service",
    "ensure_learner_assessment_evaluation_service",
]


def _require_school_context(request: Request) -> None:
    if request.app.state.school_context_class_authority is None:
        from aieos.domains.assessment.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "School Context is not composed in this runtime"
        )


def record_classroom_assessment_service(
    request: Request,
) -> RecordClassroomAssessmentService:
    _require_school_context(request)
    service = request.app.state.record_classroom_assessment_service
    if service is None:
        from aieos.domains.assessment.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "ClassroomAssessment commands are not composed in this runtime"
        )
    return service


def correct_classroom_assessment_service(
    request: Request,
) -> CorrectClassroomAssessmentService:
    _require_school_context(request)
    service = request.app.state.correct_classroom_assessment_service
    if service is None:
        from aieos.domains.assessment.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "ClassroomAssessment commands are not composed in this runtime"
        )
    return service


def void_classroom_assessment_service(
    request: Request,
) -> VoidClassroomAssessmentService:
    _require_school_context(request)
    service = request.app.state.void_classroom_assessment_service
    if service is None:
        from aieos.domains.assessment.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "ClassroomAssessment commands are not composed in this runtime"
        )
    return service


def get_classroom_assessment_service(
    request: Request,
) -> GetClassroomAssessmentService:
    return request.app.state.get_classroom_assessment_service


def list_classroom_assessments_service(
    request: Request,
) -> ListClassroomAssessmentsService:
    return request.app.state.list_classroom_assessments_service


def ensure_learner_assessment_evaluation_service(
    request: Request,
) -> EnsureLearnerAssessmentEvaluationService:
    _require_school_context(request)
    service = request.app.state.ensure_learner_assessment_evaluation_service
    if service is None:
        from aieos.domains.assessment.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "LearnerAssessmentEvaluation commands are not composed in this runtime"
        )
    return service


def get_assignment_assessment_intelligence_service(
    request: Request,
) -> GetAssignmentAssessmentIntelligenceService:
    _require_school_context(request)
    service = request.app.state.get_assignment_assessment_intelligence_service
    if service is None:
        from aieos.domains.assessment.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "Teacher Assessment Intelligence is not composed in this runtime"
        )
    return service
