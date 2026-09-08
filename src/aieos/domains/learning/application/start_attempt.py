"""Authoritative LearnerAttempt start command."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.learning.application.audit import insert_required_learning_audit
from aieos.domains.learning.application.command_support import (
    assignment_or_not_found,
    conceal_membership_denied,
    established_idempotent_replay,
    load_learner_resource,
    require_human_learner,
    require_locked_assignment_consumable,
    require_membership,
)
from aieos.domains.learning.application.errors import (
    AttemptInProgressConflict,
    LearnerClassMembershipDenied,
    SecondAttemptNotAuthorized,
)
from aieos.domains.learning.application.learner_membership import (
    SchoolContextLearnerMembershipAuthority,
)
from aieos.domains.learning.application.models import (
    AttemptReadModel,
    MutationAuditProvenance,
    attempt_read_model,
)
from aieos.domains.learning.application.ports import (
    StudentLearningCommandUnitOfWorkFactory,
)
from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.platform.events.learning_events import attempt_started_outbox
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    LEARNING_ATTEMPT_START_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import SecurityAuditAction
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


class StartAttemptService:
    def __init__(
        self,
        uow_factory: StudentLearningCommandUnitOfWorkFactory,
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

    def start(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        assignment_id: UUID,
        *,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> AttemptReadModel:
        require_human_learner(self._classification, principal_id)
        started_at = _now(now)
        fingerprint = fingerprint_material({"assignment_id": str(assignment_id)})
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=LEARNING_ATTEMPT_START_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as preview:
            replayed = established_idempotent_replay(
                preview,
                scope=scope,
                fingerprint=fingerprint,
                principal_id=principal_id,
                missing_message="idempotent start outcome is not visible",
            )
            if replayed is not None:
                return replayed
            previewed = assignment_or_not_found(preview.get_assignment(assignment_id))
            class_ref = previewed.class_ref
        try:
            require_membership(
                self._membership,
                execution_tenant_id,
                principal_id,
                class_ref,
            )
        except LearnerClassMembershipDenied as exc:
            raise conceal_membership_denied(exc)

        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            replayed = established_idempotent_replay(
                uow,
                scope=scope,
                fingerprint=fingerprint,
                principal_id=principal_id,
                missing_message="idempotent start outcome is not visible",
            )
            if replayed is not None:
                return replayed

            locked = assignment_or_not_found(
                uow.get_assignment_for_update(assignment_id)
            )
            require_locked_assignment_consumable(locked, now=started_at)
            load_learner_resource(uow, locked)
            prior = uow.attempts.list_for_learner_assignment(
                principal_id, locked.assignment_id
            )
            if any(
                item.lifecycle_state is AttemptLifecycleState.IN_PROGRESS
                for item in prior
            ):
                raise AttemptInProgressConflict(
                    "an IN_PROGRESS LearnerAttempt already exists"
                )
            if any(
                item.lifecycle_state is AttemptLifecycleState.SUBMITTED
                for item in prior
            ):
                raise SecondAttemptNotAuthorized(
                    "a second LearnerAttempt is not authorized"
                )
            attempt = LearnerAttempt.start_in_progress(
                tenant_id=execution_tenant_id,
                learner_principal_id=principal_id,
                teaching_assignment_id=locked.assignment_id,
                content_id=locked.content_id,
                content_version_id=locked.content_version_id,
                class_ref=locked.class_ref,
                started_at=started_at,
                attempt_number=1,
            )
            uow.attempts.insert(attempt)
            uow.outbox.insert(
                attempt_started_outbox(
                    tenant_id=execution_tenant_id,
                    attempt_id=attempt.attempt_id.value,
                    teaching_assignment_id=attempt.teaching_assignment_id,
                    content_id=attempt.content_id,
                    content_version_id=attempt.content_version_id,
                    class_ref=attempt.class_ref,
                    started_at=attempt.started_at,
                    aggregate_revision=int(attempt.aggregate_revision),
                    context=event_context,
                    created_at=started_at,
                )
            )
            insert_required_learning_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.LEARNING_ATTEMPT_START,
                attempt_id=attempt.attempt_id.value,
                resource_revision_before=None,
                resource_revision_after=int(attempt.aggregate_revision),
                related_resource_refs=(
                    ResourceRef("teaching.assignment", locked.assignment_id, None),
                    ResourceRef("content.version", locked.content_version_id, None),
                ),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=started_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=attempt.attempt_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(attempt.aggregate_revision),
                    created_at=started_at,
                    expires_at=started_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return attempt_read_model(attempt, ())
