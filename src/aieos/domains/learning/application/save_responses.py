"""Authoritative LearnerAttempt response save (PUT replacement)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.learning.application.audit import insert_required_learning_audit
from aieos.domains.learning.application.command_support import (
    assignment_or_not_found,
    conceal_membership_denied,
    established_idempotent_replay,
    load_learner_resource,
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
    InvalidLearnerRequest,
    LearnerClassMembershipDenied,
    ResponseValidationFailed,
)
from aieos.domains.learning.application.learner_membership import (
    SchoolContextLearnerMembershipAuthority,
)
from aieos.domains.learning.application.models import (
    AttemptReadModel,
    LearnerResource,
    MutationAuditProvenance,
    ResponseWrite,
    attempt_read_model,
)
from aieos.domains.learning.application.ports import (
    StudentLearningCommandUnitOfWorkFactory,
)
from aieos.domains.learning.domain.errors import InvalidAttemptResponseError
from aieos.domains.learning.domain.identities import AggregateRevision, AttemptId
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.domains.learning.domain.response_item import AttemptResponseItem
from aieos.domains.learning.domain.response_kind import AttemptResponseKind
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    LEARNING_ATTEMPT_SAVE_RESPONSES_V1,
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


def _save_fingerprint(
    attempt_id: UUID,
    expected_revision: int,
    writes: Sequence[ResponseWrite],
) -> str:
    return fingerprint_material(
        {
            "attempt_id": str(attempt_id),
            "expected_aggregate_revision": expected_revision,
            "responses": [
                {
                    "question_id": item.question_id,
                    "response_kind": item.response_kind,
                    "choice_value": item.choice_value,
                    "text_value": item.text_value,
                    "boolean_value": item.boolean_value,
                }
                for item in writes
            ],
        }
    )


def validate_response_writes(
    resource: LearnerResource,
    writes: Sequence[ResponseWrite],
    attempt_id: AttemptId,
) -> tuple[AttemptResponseItem, ...]:
    by_id = {question.id: question for question in resource.questions}
    seen: set[str] = set()
    items: list[AttemptResponseItem] = []
    for write in writes:
        if write.question_id in seen:
            raise ResponseValidationFailed("duplicate question_id")
        seen.add(write.question_id)
        question = by_id.get(write.question_id)
        if question is None:
            raise ResponseValidationFailed("unknown question_id")
        if write.response_kind != question.question_type:
            raise ResponseValidationFailed("response_kind mismatch")
        try:
            kind = AttemptResponseKind(write.response_kind)
            item = AttemptResponseItem(
                attempt_id=attempt_id,
                question_id=write.question_id,
                response_kind=kind,
                choice_value=write.choice_value,
                text_value=write.text_value,
                boolean_value=write.boolean_value,
            )
        except (InvalidAttemptResponseError, ValueError) as exc:
            raise ResponseValidationFailed("response value is invalid") from exc
        if (
            kind is AttemptResponseKind.MULTIPLE_CHOICE
            and item.choice_value not in question.options
        ):
            raise ResponseValidationFailed(
                "choice_value is not a learner-visible option"
            )
        items.append(item)
    return tuple(items)


class SaveResponsesService:
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

    def save(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        attempt_id: UUID,
        writes: Sequence[ResponseWrite],
        *,
        expected_aggregate_revision: int,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> AttemptReadModel:
        require_human_learner(self._classification, principal_id)
        saved_at = _now(now)
        try:
            typed_attempt_id = AttemptId(attempt_id)
            expected = AggregateRevision(expected_aggregate_revision)
        except Exception as exc:
            raise InvalidLearnerRequest("attempt identity is invalid") from exc
        fingerprint = _save_fingerprint(attempt_id, expected_aggregate_revision, writes)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=LEARNING_ATTEMPT_SAVE_RESPONSES_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as preview:
            replayed = established_idempotent_replay(
                preview,
                scope=scope,
                fingerprint=fingerprint,
                principal_id=principal_id,
                missing_message="idempotent save outcome is not visible",
            )
            if replayed is not None:
                return replayed
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
            replayed = established_idempotent_replay(
                uow,
                scope=scope,
                fingerprint=fingerprint,
                principal_id=principal_id,
                missing_message="idempotent save outcome is not visible",
            )
            if replayed is not None:
                return replayed

            previewed = uow.attempts.get(typed_attempt_id)
            if previewed is None:
                raise AttemptNotFound("LearnerAttempt is not visible")
            require_owner(previewed, principal_id)
            locked_assignment = assignment_or_not_found(
                uow.get_assignment_for_update(previewed.teaching_assignment_id)
            )
            require_locked_assignment_consumable(locked_assignment, now=saved_at)
            locked_attempt = uow.attempts.get_for_update(typed_attempt_id)
            if locked_attempt is None:
                raise AttemptNotFound("LearnerAttempt is not visible")
            require_owner(locked_attempt, principal_id)
            if locked_attempt.lifecycle_state is AttemptLifecycleState.SUBMITTED:
                raise AttemptAlreadySubmitted(
                    "SUBMITTED LearnerAttempt cannot mutate response working state"
                )
            require_attempt_matches_assignment(locked_attempt, locked_assignment)
            if int(locked_attempt.aggregate_revision) != int(expected):
                raise AttemptConcurrencyConflict(
                    "LearnerAttempt aggregate_revision did not match expected_revision"
                )
            resource = load_learner_resource(uow, locked_assignment)
            items = validate_response_writes(resource, writes, locked_attempt.attempt_id)
            updated = locked_attempt.record_material_response_save(last_saved_at=saved_at)
            uow.persist_working_response_save(
                updated, items, expected_revision=expected
            )
            insert_required_learning_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.LEARNING_ATTEMPT_SAVE_RESPONSES,
                attempt_id=updated.attempt_id.value,
                resource_revision_before=int(expected),
                resource_revision_after=int(updated.aggregate_revision),
                related_resource_refs=(
                    ResourceRef(
                        "teaching.assignment", locked_assignment.assignment_id, None
                    ),
                ),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=saved_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=updated.attempt_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(updated.aggregate_revision),
                    created_at=saved_at,
                    expires_at=saved_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return attempt_read_model(updated, items)
