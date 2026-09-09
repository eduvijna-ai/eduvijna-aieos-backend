"""Assessment application command and read models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.assessment.domain.classroom_assessment import ClassroomAssessment
from aieos.domains.assessment.domain.evaluation import LearnerAssessmentEvaluation
from aieos.domains.assessment.domain.lifecycle import AssessmentLifecycleState
from aieos.domains.assessment.domain.result import ClassResultLevel


@dataclass(frozen=True, slots=True)
class RecordClassroomAssessmentCommand:
    class_ref: str
    content_id: UUID
    content_version_id: UUID
    class_result_level: ClassResultLevel | str
    class_result_note: str | None = None
    work_id: UUID | None = None
    execution_id: UUID | None = None
    assignment_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CorrectClassroomAssessmentCommand:
    class_result_level: ClassResultLevel | str
    class_result_note: str | None = None


@dataclass(frozen=True, slots=True)
class ListClassroomAssessmentsQuery:
    class_ref: str | None = None
    work_id: UUID | None = None
    execution_id: UUID | None = None
    assignment_id: UUID | None = None
    lifecycle_state: AssessmentLifecycleState | str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ClassroomAssessmentReadModel:
    assessment_id: UUID
    teacher_principal_id: UUID
    class_ref: str
    content_id: UUID
    content_version_id: UUID
    class_result_level: str
    class_result_note: str | None
    lifecycle_state: str
    work_id: UUID | None
    execution_id: UUID | None
    assignment_id: UUID | None
    aggregate_revision: int
    recorded_at: datetime
    voided_at: datetime | None
    created_at: datetime
    updated_at: datetime


def classroom_assessment_read_model(
    assessment: ClassroomAssessment,
) -> ClassroomAssessmentReadModel:
    return ClassroomAssessmentReadModel(
        assessment_id=assessment.assessment_id.value,
        teacher_principal_id=assessment.teacher_principal_id,
        class_ref=assessment.class_ref,
        content_id=assessment.content_id,
        content_version_id=assessment.content_version_id,
        class_result_level=assessment.class_result_level.value,
        class_result_note=assessment.class_result_note,
        lifecycle_state=assessment.lifecycle_state.value,
        work_id=assessment.work_id,
        execution_id=assessment.execution_id,
        assignment_id=assessment.assignment_id,
        aggregate_revision=int(assessment.aggregate_revision),
        recorded_at=assessment.recorded_at,
        voided_at=assessment.voided_at,
        created_at=assessment.created_at,
        updated_at=assessment.updated_at,
    )


@dataclass(frozen=True, slots=True)
class LearnerAssessmentEvaluationItemReadModel:
    question_id: str
    question_type: str
    outcome: str
    evaluation_method: str
    objective_ids: tuple[str, ...]
    response_kind: str | None


@dataclass(frozen=True, slots=True)
class LearnerAssessmentObjectiveEvidenceReadModel:
    objective_id: str
    result: str


@dataclass(frozen=True, slots=True)
class LearnerAssessmentEvaluationReadModel:
    evaluation_id: UUID
    learner_principal_id: UUID
    submission_id: UUID
    attempt_id: UUID
    teaching_assignment_id: UUID
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    evaluation_policy_id: str
    evaluation_policy_version: int
    evaluated_at: datetime
    created_at: datetime
    items: tuple[LearnerAssessmentEvaluationItemReadModel, ...]
    objective_evidence: tuple[LearnerAssessmentObjectiveEvidenceReadModel, ...]


def learner_assessment_evaluation_read_model(
    evaluation: LearnerAssessmentEvaluation,
) -> LearnerAssessmentEvaluationReadModel:
    return LearnerAssessmentEvaluationReadModel(
        evaluation_id=evaluation.evaluation_id.value,
        learner_principal_id=evaluation.learner_principal_id,
        submission_id=evaluation.submission_id,
        attempt_id=evaluation.attempt_id,
        teaching_assignment_id=evaluation.teaching_assignment_id,
        content_id=evaluation.content_id,
        content_version_id=evaluation.content_version_id,
        class_ref=evaluation.class_ref,
        evaluation_policy_id=evaluation.evaluation_policy_id,
        evaluation_policy_version=evaluation.evaluation_policy_version,
        evaluated_at=evaluation.evaluated_at,
        created_at=evaluation.created_at,
        items=tuple(
            LearnerAssessmentEvaluationItemReadModel(
                question_id=item.question_id,
                question_type=item.question_type,
                outcome=item.outcome.value,
                evaluation_method=item.evaluation_method.value,
                objective_ids=item.objective_ids,
                response_kind=item.response_kind,
            )
            for item in evaluation.items
        ),
        objective_evidence=tuple(
            LearnerAssessmentObjectiveEvidenceReadModel(
                objective_id=row.objective_id,
                result=row.result.value,
            )
            for row in evaluation.objective_evidence
        ),
    )
