"""Authoritative LearnerAttempt submit command."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.learning.application.audit import insert_required_learning_audit
from aieos.domains.learning.application.command_support import (
    assignment_or_not_found,
    conceal_membership_denied,
    require_attempt_matches_assignment,
    require_human_learner,
    require_locked_assignment_consumable,
    require_membership,
    require_owner,
)
from aieos.domains.learning.application.errors import (
    AttemptAlreadySubmitted,
    AttemptConcurrencyConflict,
    AttemptNotFound,
    IdempotencyKeyReused,
    InvalidLearnerRequest,
    LearnerClassMembershipDenied,
    PersistenceInvariantViolation,
)
from aieos.domains.learning.application.learner_membership import (
    SchoolContextLearnerMembershipAuthority,
)
from aieos.domains.learning.application.models import (
    AttemptReadModel,
    MutationAuditProvenance,
    attempt_read_model,
)
from aieos.domains.learning.domain.identities import AggregateRevision, AttemptId
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.domains.learning.domain.submit import (
    transition_in_progress_attempt_to_submitted,
)
from aieos.platform.events.learning_events import attempt_submitted_outbox
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    LEARNING_ATTEMPT_SUBMIT_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.resources import ResourceRef
from aieos.platform.runtime.student_learning_command import (
    SqlAlchemyStudentLearningCommandUnitOfWorkFactory,
)
from aieos.platform.security.audit import SecurityAuditAction
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


class SubmitAttemptService:
    def __init__(
        self,
        uow_factory: SqlAlchemyStudentLearningCommandUnitOfWorkFactory,
        membership: SchoolContextLearnerMembershipAuthority,
        classification: CurrentPrincipalClassificationAuthority,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._membership = membership
        self._classification = classification
        self._idempotency_retention = idempotency_retention

    def submit(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        attempt_id: UUID,
        *,
        expected_aggregate_revision: int,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> AttemptReadModel:
        require_human_learner(self._classification, principal_id)
        submitted_at = _now(now)
        try:
            typed_attempt_id = AttemptId(attempt_id)
            expected = AggregateRevision(expected_aggregate_revision)
        except Exception as exc:
            raise InvalidLearnerRequest("attempt identity is invalid") from exc
        fingerprint = fingerprint_material(
            {
                "attempt_id": str(attempt_id),
                "expected_aggregate_revision": expected_aggregate_revision,
            }
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=LEARNING_ATTEMPT_SUBMIT_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as preview:
            previewed = preview.attempts.get(typed_attempt_id)
            if previewed is None:
                raise AttemptNotFound("LearnerAttempt is not visible")
            require_owner(previewed, principal_id)
            class_ref = previewed.class_ref
        try:
            require_membership(
                self._membership, execution_tenant_id, principal_id, class_ref
            )
        except LearnerClassMembershipDenied as exc:
            raise conceal_membership_denied(exc)

        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.attempts.get(AttemptId(existing.result_content_id))
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent submit outcome is not visible"
                    )
                responses = tuple(
                    uow.responses.list_for_attempt(replayed.attempt_id)
                )
                return attempt_read_model(replayed, responses)

            previewed = uow.attempts.get(typed_attempt_id)
            if previewed is None:
                raise AttemptNotFound("LearnerAttempt is not visible")
            require_owner(previewed, principal_id)
            locked_assignment = assignment_or_not_found(
                uow.get_assignment_for_update(previewed.teaching_assignment_id)
            )
            require_locked_assignment_consumable(locked_assignment, now=submitted_at)
            locked_attempt = uow.attempts.get_for_update(typed_attempt_id)
            if locked_attempt is None:
                raise AttemptNotFound("LearnerAttempt is not visible")
            require_owner(locked_attempt, principal_id)
            if locked_attempt.lifecycle_state is AttemptLifecycleState.SUBMITTED:
                raise AttemptAlreadySubmitted(
                    "SUBMITTED LearnerAttempt rejects a second submit"
                )
            require_attempt_matches_assignment(locked_attempt, locked_assignment)
            if int(locked_attempt.aggregate_revision) != int(expected):
                raise AttemptConcurrencyConflict(
                    "LearnerAttempt aggregate_revision did not match expected_revision"
                )
            working = tuple(uow.responses.list_for_attempt(locked_attempt.attempt_id))
            submitted_attempt, submission = (
                transition_in_progress_attempt_to_submitted(
                    locked_attempt,
                    working,
                    submitted_at=submitted_at,
                    assignment_revision_at_submit=locked_assignment.aggregate_revision,
                    due_at_at_submit=locked_assignment.due_at,
                )
            )
            uow.persist_pure_submit_transition(
                submitted_attempt, submission, expected_revision=expected
            )
            uow.outbox.insert(
                attempt_submitted_outbox(
                    tenant_id=execution_tenant_id,
                    attempt_id=submitted_attempt.attempt_id.value,
                    submission_id=submission.submission_id.value,
                    teaching_assignment_id=submitted_attempt.teaching_assignment_id,
                    content_id=submitted_attempt.content_id,
                    content_version_id=submitted_attempt.content_version_id,
                    class_ref=submitted_attempt.class_ref,
                    submitted_at=submitted_attempt.submitted_at,
                    aggregate_revision=int(submitted_attempt.aggregate_revision),
                    context=event_context,
                    created_at=submitted_at,
                )
            )
            insert_required_learning_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.LEARNING_ATTEMPT_SUBMIT,
                attempt_id=submitted_attempt.attempt_id.value,
                resource_revision_before=int(expected),
                resource_revision_after=int(submitted_attempt.aggregate_revision),
                related_resource_refs=(
                    ResourceRef(
                        "teaching.assignment", locked_assignment.assignment_id, None
                    ),
                    ResourceRef("learning.submission", submission.submission_id.value, None),
                ),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=submitted_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=submitted_attempt.attempt_id.value,
                    result_version_id=submission.submission_id.value,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(submitted_attempt.aggregate_revision),
                    created_at=submitted_at,
                    expires_at=submitted_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return attempt_read_model(submitted_attempt, working)
