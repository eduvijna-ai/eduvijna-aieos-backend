"""Ensure current-policy LearnerAssessmentEvaluation for submissions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.assessment.application.audit import (
    MutationAuditProvenance,
    insert_required_evaluation_audit,
)
from aieos.domains.assessment.application.errors import (
    ClassRefNotAssignable,
    EvaluationLineageConflict,
    IdempotencyKeyReused,
    LearnerSubmissionNotFound,
    PersistenceInvariantViolation,
    SchoolContextUnavailable,
)
from aieos.domains.assessment.application.evaluation_views import (
    EvaluationAssignmentView,
    EvaluationSubmissionView,
)
from aieos.domains.assessment.application.models import (
    LearnerAssessmentEvaluationReadModel,
    learner_assessment_evaluation_read_model,
)
from aieos.domains.assessment.application.ports import (
    ASSESSMENT_LEARNER_EVALUATION_ENSURE,
    AssessmentUnitOfWork,
    AssessmentUnitOfWorkFactory,
    ClassroomAssessmentAuthorization,
)
from aieos.domains.assessment.domain.evaluation import LearnerAssessmentEvaluation
from aieos.domains.assessment.domain.evaluation_input import LearnerAssessmentEvaluationRequest
from aieos.domains.assessment.domain.evaluation_policy_v1 import (
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION,
    DeterministicLearnerAssessmentEvaluatorV1,
)
from aieos.domains.assessment.domain.errors import InvalidLearnerAssessmentEvaluationError
from aieos.domains.assessment.domain.identities import EvaluationId
from aieos.domains.teaching.application import errors as teaching_errors
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    ASSESSMENT_ASSIGNMENT_EVALUATIONS_ENSURE_V1,
    ASSESSMENT_LEARNER_EVALUATION_ENSURE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.resources import ResourceRef

CURRENT_EVALUATION_POLICY_ID = DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID
CURRENT_EVALUATION_POLICY_VERSION = DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION
_EVALUATOR = DeterministicLearnerAssessmentEvaluatorV1()


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


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


def _single_fingerprint(submission_id: UUID) -> str:
    return fingerprint_material(
        {
            "submission_id": str(submission_id),
            "evaluation_policy_id": CURRENT_EVALUATION_POLICY_ID,
            "evaluation_policy_version": CURRENT_EVALUATION_POLICY_VERSION,
        }
    )


def _batch_fingerprint(assignment_id: UUID) -> str:
    return fingerprint_material(
        {
            "assignment_id": str(assignment_id),
            "evaluation_policy_id": CURRENT_EVALUATION_POLICY_ID,
            "evaluation_policy_version": CURRENT_EVALUATION_POLICY_VERSION,
        }
    )


def _validate_lineage(
    submission: EvaluationSubmissionView,
    assignment: EvaluationAssignmentView,
) -> None:
    if (
        submission.tenant_id != assignment.tenant_id
        or submission.teaching_assignment_id != assignment.assignment_id
        or submission.class_ref != assignment.class_ref
        or submission.content_id != assignment.content_id
        or submission.content_version_id != assignment.content_version_id
    ):
        raise EvaluationLineageConflict(
            "LearnerSubmission does not match TeachingAssignment lineage"
        )


def _verify_stored_provenance(
    stored: LearnerAssessmentEvaluation,
    submission: EvaluationSubmissionView,
) -> None:
    if (
        stored.tenant_id != submission.tenant_id
        or stored.learner_principal_id != submission.learner_principal_id
        or stored.submission_id != submission.submission_id
        or stored.attempt_id != submission.attempt_id
        or stored.teaching_assignment_id != submission.teaching_assignment_id
        or stored.content_id != submission.content_id
        or stored.content_version_id != submission.content_version_id
        or stored.class_ref != submission.class_ref
        or stored.evaluation_policy_id != CURRENT_EVALUATION_POLICY_ID
        or stored.evaluation_policy_version != CURRENT_EVALUATION_POLICY_VERSION
    ):
        raise PersistenceInvariantViolation(
            "stored LearnerAssessmentEvaluation provenance disagrees with submission"
        )


def _related_refs(submission: EvaluationSubmissionView) -> tuple[ResourceRef, ...]:
    return (
        ResourceRef("learning.submission", submission.submission_id, None),
        ResourceRef("learning.attempt", submission.attempt_id, None),
        ResourceRef("teaching.assignment", submission.teaching_assignment_id, None),
        ResourceRef("content.version", submission.content_version_id, None),
    )


def _ensure_evaluation(
    uow: AssessmentUnitOfWork,
    submission: EvaluationSubmissionView,
    assignment: EvaluationAssignmentView,
    *,
    evaluated_at: datetime,
    event_context: MutationEventContext,
    audit_provenance: MutationAuditProvenance,
) -> LearnerAssessmentEvaluation:
    _validate_lineage(submission, assignment)
    existing = uow.learner_assessment_evaluations.get_by_business_identity(
        submission_id=submission.submission_id,
        evaluation_policy_id=CURRENT_EVALUATION_POLICY_ID,
        evaluation_policy_version=CURRENT_EVALUATION_POLICY_VERSION,
    )
    if existing is not None:
        _verify_stored_provenance(existing, submission)
        return existing
    questions = uow.exact_content.load_evaluation_questions(
        content_id=submission.content_id,
        content_version_id=submission.content_version_id,
    )
    try:
        request = LearnerAssessmentEvaluationRequest(
            tenant_id=submission.tenant_id,
            learner_principal_id=submission.learner_principal_id,
            submission_id=submission.submission_id,
            attempt_id=submission.attempt_id,
            teaching_assignment_id=submission.teaching_assignment_id,
            content_id=submission.content_id,
            content_version_id=submission.content_version_id,
            class_ref=submission.class_ref,
            questions=questions,
            responses=submission.responses,
            evaluated_at=evaluated_at,
        )
        candidate = _EVALUATOR.evaluate(request)
    except InvalidLearnerAssessmentEvaluationError as exc:
        raise PersistenceInvariantViolation(
            "evaluation request violated the evaluator contract"
        ) from exc
    persisted = uow.learner_assessment_evaluations.insert(candidate)
    _verify_stored_provenance(persisted, submission)
    if persisted.evaluation_id == candidate.evaluation_id:
        insert_required_evaluation_audit(
            uow,
            tenant_id=submission.tenant_id,
            evaluation_id=persisted.evaluation_id.value,
            related_resource_refs=_related_refs(submission),
            mutation_event_context=event_context,
            audit_provenance=audit_provenance,
            occurred_at=evaluated_at,
        )
    return persisted


class EnsureLearnerAssessmentEvaluationService:
    def __init__(
        self,
        uow_factory: AssessmentUnitOfWorkFactory,
        class_authority: SchoolContextClassAuthority,
        authorization: ClassroomAssessmentAuthorization,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._class_authority = class_authority
        self._authorization = authorization
        self._idempotency_retention = idempotency_retention

    def ensure_submission(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        submission_id: UUID,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> LearnerAssessmentEvaluationReadModel:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_LEARNER_EVALUATION_ENSURE,
        )
        evaluated_at = _now(now)
        fingerprint = _single_fingerprint(submission_id)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=ASSESSMENT_LEARNER_EVALUATION_ENSURE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as preview:
            preview_submission = preview.learner_submissions.get(submission_id)
        if preview_submission is None:
            raise LearnerSubmissionNotFound(
                "LearnerSubmission is not visible in the execution tenant"
            )
        class_target = _require_current_class_ref(
            self._class_authority,
            execution_tenant_id,
            principal_id,
            preview_submission.class_ref,
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.learner_assessment_evaluations.get(
                    EvaluationId(existing.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent evaluation outcome is not visible"
                    )
                submission = uow.learner_submissions.get(submission_id)
                if submission is None:
                    raise LearnerSubmissionNotFound(
                        "LearnerSubmission is not visible in the execution tenant"
                    )
                if submission.class_ref != class_target.class_ref:
                    raise EvaluationLineageConflict(
                        "LearnerSubmission class_ref does not match current authority"
                    )
                assignment = uow.teaching_composition.load_assignment_lineage(
                    submission.teaching_assignment_id
                )
                _validate_lineage(submission, assignment)
                _verify_stored_provenance(replayed, submission)
                return learner_assessment_evaluation_read_model(replayed)

            submission = uow.learner_submissions.get(submission_id)
            if submission is None:
                raise LearnerSubmissionNotFound(
                    "LearnerSubmission is not visible in the execution tenant"
                )
            if submission.class_ref != class_target.class_ref:
                raise EvaluationLineageConflict(
                    "LearnerSubmission class_ref does not match current authority"
                )
            assignment = uow.teaching_composition.load_assignment_lineage(
                submission.teaching_assignment_id
            )
            persisted = _ensure_evaluation(
                uow,
                submission,
                assignment,
                evaluated_at=evaluated_at,
                event_context=event_context,
                audit_provenance=audit_provenance,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=persisted.evaluation_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=0,
                    created_at=evaluated_at,
                    expires_at=evaluated_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return learner_assessment_evaluation_read_model(persisted)

    def ensure_assignment(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        assignment_id: UUID,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> None:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_LEARNER_EVALUATION_ENSURE,
        )
        evaluated_at = _now(now)
        fingerprint = _batch_fingerprint(assignment_id)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=ASSESSMENT_ASSIGNMENT_EVALUATIONS_ENSURE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
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
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                assignment = uow.teaching_composition.load_assignment_lineage(
                    assignment_id
                )
                if assignment.class_ref != class_target.class_ref:
                    raise EvaluationLineageConflict(
                        "TeachingAssignment class_ref does not match current authority"
                    )
                return
            assignment = uow.teaching_composition.load_assignment_lineage(
                assignment_id
            )
            if assignment.class_ref != class_target.class_ref:
                raise EvaluationLineageConflict(
                    "TeachingAssignment class_ref does not match current authority"
                )
            submissions = uow.learner_submissions.list_for_teaching_assignment(
                assignment.assignment_id
            )
            for submission in submissions:
                _ensure_evaluation(
                    uow,
                    submission,
                    assignment,
                    evaluated_at=evaluated_at,
                    event_context=event_context,
                    audit_provenance=audit_provenance,
                )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=assignment.assignment_id,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=0,
                    created_at=evaluated_at,
                    expires_at=evaluated_at + self._idempotency_retention,
                )
            )
            uow.commit()
