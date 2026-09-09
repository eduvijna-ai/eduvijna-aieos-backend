"""Teacher Assessment Intelligence — assignment-scoped derived-on-read projection.

GET path only. Never ensures/evaluates or mutates Learning / Assessment /
Teaching / ClassroomAssessment / Improve.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from aieos.domains.assessment.application.errors import (
    ClassRefNotAssignable,
    EvaluationLineageConflict,
    SchoolContextUnavailable,
)
from aieos.domains.assessment.application.models import (
    LEARNER_EVALUATION_STATE_CURRENT,
    LEARNER_EVALUATION_STATE_NOT_CURRENT,
    LEARNER_EVALUATION_STATE_NOT_EVALUATED,
    IntelligenceFrequentlyMissedQuestion,
    IntelligenceLearnerProjection,
    IntelligenceObjectiveEvidenceCount,
    IntelligenceQuestionOutcomeCounts,
    LearnerAssessmentEvaluationItemReadModel,
    LearnerAssessmentObjectiveEvidenceReadModel,
    TeacherAssessmentIntelligenceReadModel,
)
from aieos.domains.assessment.application.ports import (
    ASSESSMENT_ASSIGNMENT_INTELLIGENCE_READ,
    AssessmentUnitOfWorkFactory,
    ClassroomAssessmentAuthorization,
)
from aieos.domains.assessment.domain.evaluation import LearnerAssessmentEvaluation
from aieos.domains.assessment.domain.evaluation_policy_v1 import (
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION,
)
from aieos.domains.assessment.domain.evaluation_vocabulary import (
    EvaluationMethod,
    ItemOutcome,
    ObjectiveEvidenceResult,
)
from aieos.domains.teaching.application import errors as teaching_errors
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
)

CURRENT_EVALUATION_POLICY_ID = DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID
CURRENT_EVALUATION_POLICY_VERSION = DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION


def _require_current_class_ref(
    class_authority: SchoolContextClassAuthority,
    execution_tenant_id: UUID,
    principal_id: UUID,
    class_ref: str,
):
    try:
        return class_authority.require_assignable_class_ref(
            execution_tenant_id, principal_id, class_ref
        )
    except teaching_errors.ClassRefNotAssignable as exc:
        raise ClassRefNotAssignable(str(exc)) from exc
    except teaching_errors.SchoolContextUnavailable as exc:
        raise SchoolContextUnavailable(str(exc)) from exc
    except teaching_errors.SchoolContextContractError as exc:
        raise SchoolContextUnavailable(str(exc)) from exc


def _is_current_policy(evaluation: LearnerAssessmentEvaluation) -> bool:
    return (
        evaluation.evaluation_policy_id == CURRENT_EVALUATION_POLICY_ID
        and evaluation.evaluation_policy_version == CURRENT_EVALUATION_POLICY_VERSION
    )


def _item_read_models(
    evaluation: LearnerAssessmentEvaluation,
) -> tuple[LearnerAssessmentEvaluationItemReadModel, ...]:
    return tuple(
        LearnerAssessmentEvaluationItemReadModel(
            question_id=item.question_id,
            question_type=item.question_type,
            outcome=item.outcome.value,
            evaluation_method=item.evaluation_method.value,
            objective_ids=item.objective_ids,
            response_kind=item.response_kind,
        )
        for item in evaluation.items
    )


def _objective_read_models(
    evaluation: LearnerAssessmentEvaluation,
) -> tuple[LearnerAssessmentObjectiveEvidenceReadModel, ...]:
    return tuple(
        LearnerAssessmentObjectiveEvidenceReadModel(
            objective_id=row.objective_id,
            result=row.result.value,
        )
        for row in evaluation.objective_evidence
    )


def _project_learners(
    submissions: tuple,
    evaluations: tuple[LearnerAssessmentEvaluation, ...],
) -> tuple[IntelligenceLearnerProjection, ...]:
    by_submission: dict[UUID, list[LearnerAssessmentEvaluation]] = defaultdict(list)
    for evaluation in evaluations:
        by_submission[evaluation.submission_id].append(evaluation)

    learners: list[IntelligenceLearnerProjection] = []
    for submission in submissions:
        rows = by_submission.get(submission.submission_id, [])
        current = next((row for row in rows if _is_current_policy(row)), None)
        if current is not None:
            learners.append(
                IntelligenceLearnerProjection(
                    learner_principal_id=submission.learner_principal_id,
                    submission_id=submission.submission_id,
                    evaluation_state=LEARNER_EVALUATION_STATE_CURRENT,
                    evaluation_id=current.evaluation_id.value,
                    evaluation_policy_id=current.evaluation_policy_id,
                    evaluation_policy_version=current.evaluation_policy_version,
                    evaluated_at=current.evaluated_at,
                    items=_item_read_models(current),
                    objective_evidence=_objective_read_models(current),
                )
            )
            continue
        if rows:
            learners.append(
                IntelligenceLearnerProjection(
                    learner_principal_id=submission.learner_principal_id,
                    submission_id=submission.submission_id,
                    evaluation_state=LEARNER_EVALUATION_STATE_NOT_CURRENT,
                    evaluation_id=None,
                    evaluation_policy_id=None,
                    evaluation_policy_version=None,
                    evaluated_at=None,
                    items=(),
                    objective_evidence=(),
                )
            )
            continue
        learners.append(
            IntelligenceLearnerProjection(
                learner_principal_id=submission.learner_principal_id,
                submission_id=submission.submission_id,
                evaluation_state=LEARNER_EVALUATION_STATE_NOT_EVALUATED,
                evaluation_id=None,
                evaluation_policy_id=None,
                evaluation_policy_version=None,
                evaluated_at=None,
                items=(),
                objective_evidence=(),
            )
        )
    return tuple(learners)


def _question_distributions(
    current_evaluations: tuple[LearnerAssessmentEvaluation, ...],
) -> tuple[IntelligenceQuestionOutcomeCounts, ...]:
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            ItemOutcome.CORRECT.value: 0,
            ItemOutcome.INCORRECT.value: 0,
            ItemOutcome.UNANSWERED.value: 0,
            ItemOutcome.OPEN_RESPONSE_UNEVALUATED.value: 0,
            ItemOutcome.UNEVALUATED_POLICY_REJECT.value: 0,
        }
    )
    for evaluation in current_evaluations:
        for item in evaluation.items:
            counts[item.question_id][item.outcome.value] += 1
    return tuple(
        IntelligenceQuestionOutcomeCounts(
            question_id=question_id,
            correct=bucket[ItemOutcome.CORRECT.value],
            incorrect=bucket[ItemOutcome.INCORRECT.value],
            unanswered=bucket[ItemOutcome.UNANSWERED.value],
            open_response_unevaluated=bucket[
                ItemOutcome.OPEN_RESPONSE_UNEVALUATED.value
            ],
            unevaluated_policy_reject=bucket[
                ItemOutcome.UNEVALUATED_POLICY_REJECT.value
            ],
        )
        for question_id, bucket in sorted(counts.items(), key=lambda pair: pair[0])
    )


def _frequently_missed(
    current_evaluations: tuple[LearnerAssessmentEvaluation, ...],
) -> tuple[IntelligenceFrequentlyMissedQuestion, ...]:
    incorrect_counts: dict[str, int] = defaultdict(int)
    for evaluation in current_evaluations:
        for item in evaluation.items:
            if item.outcome is not ItemOutcome.INCORRECT:
                continue
            # UNANSWERED is never treated as missed; only deterministic INCORRECT.
            if item.evaluation_method is not EvaluationMethod.DETERMINISTIC_CONTENT_ANSWER:
                continue
            incorrect_counts[item.question_id] += 1
    ranked = sorted(
        incorrect_counts.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return tuple(
        IntelligenceFrequentlyMissedQuestion(
            question_id=question_id, incorrect_count=count
        )
        for question_id, count in ranked
        if count > 0
    )


def _objective_rollups(
    current_evaluations: tuple[LearnerAssessmentEvaluation, ...],
) -> tuple[IntelligenceObjectiveEvidenceCount, ...]:
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            ObjectiveEvidenceResult.INSUFFICIENT_EVIDENCE.value: 0,
            ObjectiveEvidenceResult.DEMONSTRATED_ON_SUBMITTED_ITEMS.value: 0,
            ObjectiveEvidenceResult.MIXED_ON_SUBMITTED_ITEMS.value: 0,
            ObjectiveEvidenceResult.NOT_YET_DEMONSTRATED_ON_SUBMITTED_ITEMS.value: 0,
        }
    )
    for evaluation in current_evaluations:
        for row in evaluation.objective_evidence:
            counts[row.objective_id][row.result.value] += 1
    return tuple(
        IntelligenceObjectiveEvidenceCount(
            objective_id=objective_id,
            insufficient_evidence=bucket[
                ObjectiveEvidenceResult.INSUFFICIENT_EVIDENCE.value
            ],
            demonstrated_on_submitted_items=bucket[
                ObjectiveEvidenceResult.DEMONSTRATED_ON_SUBMITTED_ITEMS.value
            ],
            mixed_on_submitted_items=bucket[
                ObjectiveEvidenceResult.MIXED_ON_SUBMITTED_ITEMS.value
            ],
            not_yet_demonstrated_on_submitted_items=bucket[
                ObjectiveEvidenceResult.NOT_YET_DEMONSTRATED_ON_SUBMITTED_ITEMS.value
            ],
        )
        for objective_id, bucket in sorted(counts.items(), key=lambda pair: pair[0])
    )


class GetAssignmentAssessmentIntelligenceService:
    """Read-only Teacher Assessment Intelligence query composition."""

    def __init__(
        self,
        uow_factory: AssessmentUnitOfWorkFactory,
        class_authority: SchoolContextClassAuthority,
        authorization: ClassroomAssessmentAuthorization,
    ) -> None:
        self._uow_factory = uow_factory
        self._class_authority = class_authority
        self._authorization = authorization

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        assignment_id: UUID,
    ) -> TeacherAssessmentIntelligenceReadModel:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_ASSIGNMENT_INTELLIGENCE_READ,
        )
        with self._uow_factory(execution_tenant_id) as preview:
            preview_assignment = preview.teaching_composition.load_assignment_lineage(
                assignment_id
            )
        class_target = _require_current_class_ref(
            self._class_authority,
            execution_tenant_id,
            principal_id,
            preview_assignment.class_ref,
        )
        with self._uow_factory(execution_tenant_id) as uow:
            assignment = uow.teaching_composition.load_assignment_lineage(assignment_id)
            if assignment.class_ref != class_target.class_ref:
                raise EvaluationLineageConflict(
                    "TeachingAssignment class_ref does not match current authority"
                )
            submissions = uow.learner_submissions.list_for_teaching_assignment(
                assignment.assignment_id
            )
            evaluations = uow.learner_assessment_evaluations.list_for_teaching_assignment(
                assignment.assignment_id
            )
            current_evaluations = tuple(
                row for row in evaluations if _is_current_policy(row)
            )
            learners = _project_learners(submissions, evaluations)
            return TeacherAssessmentIntelligenceReadModel(
                teaching_assignment_id=assignment.assignment_id,
                class_ref=assignment.class_ref,
                content_id=assignment.content_id,
                content_version_id=assignment.content_version_id,
                evaluation_policy_id=CURRENT_EVALUATION_POLICY_ID,
                evaluation_policy_version=CURRENT_EVALUATION_POLICY_VERSION,
                submitted_learner_count=len(submissions),
                evaluated_learner_count=len(current_evaluations),
                learners=learners,
                question_distributions=_question_distributions(current_evaluations),
                frequently_missed_questions=_frequently_missed(current_evaluations),
                objective_evidence_rollups=_objective_rollups(current_evaluations),
            )
