"""Internal issue reviewer assignment (ADR-AIEOS-064 O64-03-A). No HTTP surface."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from aieos.domains.content.application.audit import (
    MutationAuditProvenance,
    content_version_ref,
    insert_required_content_audit,
    review_assignment_ref,
)
from aieos.domains.content.application.errors import (
    ContentApplicationError,
    IdempotencyKeyReused,
    PersistenceInvariantViolation,
    ReviewAssignmentConflict,
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
)
from aieos.domains.content.application.ports import (
    ContentUnitOfWorkFactory,
    ReviewAssignmentAuthorizationPort,
    ReviewAuthorizationPort,
)
from aieos.domains.content.application.review_assignment_commands_common import (
    validate_issue_issuer_locked,
    validate_issue_targets_locked,
)
from aieos.domains.content.application.review_assignment_denial_audit import (
    CONTENT_REVIEW_ASSIGN_DENIED,
    REASON_EFFECTIVE_ACTOR_MISMATCH,
    AssignmentDenialRecord,
    ReviewAssignmentDenialAuditPort,
    record_assignment_denial,
    reason_for_assignment_error,
)
from aieos.domains.content.application.review_assignment_state import (
    assert_replay_entitlement_active,
    resolve_slot_active_assignment,
)
from aieos.domains.content.application.school_context_reviewer_eligibility import (
    SchoolContextReviewerEligibility,
)
from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    ReviewAssignmentId,
)
from aieos.domains.content.domain.review_assignment import (
    ReviewAssignmentAssignedEvent,
)
from aieos.domains.teaching.application.errors import SchoolContextUnavailable
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthority
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_ASSIGN_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security import UnauthorizedError
from aieos.platform.security.audit import SecurityAuditAction
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)

_ASSIGNMENT_TTL = timedelta(days=7)
_DENIED_EXCEPTIONS = (
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
    UnauthorizedError,
    SchoolContextUnavailable,
)


def _record_assign_denial(
    denial_audit: ReviewAssignmentDenialAuditPort,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    correlation_id: UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    exc: BaseException,
) -> None:
    record_assignment_denial(
        denial_audit,
        AssignmentDenialRecord(
            tenant_id=tenant_id,
            principal_id=principal_id,
            action=CONTENT_REVIEW_ASSIGN_DENIED,
            correlation_id=correlation_id,
            reason_code=reason_for_assignment_error(exc),
            content_id=content_id.value,
            version_id=version_id.value,
        ),
    )


class IssueReviewAssignmentService:
    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        assign_authorization: ReviewAssignmentAuthorizationPort,
        review_authorization: ReviewAuthorizationPort,
        principal_classification: CurrentPrincipalClassificationAuthority,
        issuer_class_authority: SchoolContextClassAuthority,
        reviewer_eligibility: SchoolContextReviewerEligibility,
        denial_audit: ReviewAssignmentDenialAuditPort,
        *,
        idempotency_retention: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be positive")
        self._uow_factory = uow_factory
        self._assign_authorization = assign_authorization
        self._review_authorization = review_authorization
        self._principal_classification = principal_classification
        self._issuer_class_authority = issuer_class_authority
        self._reviewer_eligibility = reviewer_eligibility
        self._denial_audit = denial_audit
        self._idempotency_retention = idempotency_retention
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue(
        self,
        execution_tenant_id: UUID,
        issuer_principal_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        reviewer_principal_id: UUID,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
    ) -> ReviewAssignmentId:
        if event_context.effective_actor_id != issuer_principal_id:
            record_assignment_denial(
                self._denial_audit,
                AssignmentDenialRecord(
                    tenant_id=execution_tenant_id,
                    principal_id=issuer_principal_id,
                    action=CONTENT_REVIEW_ASSIGN_DENIED,
                    correlation_id=event_context.correlation_id,
                    reason_code=REASON_EFFECTIVE_ACTOR_MISMATCH,
                    content_id=content_id.value,
                    version_id=version_id.value,
                ),
            )
            raise ReviewAssignmentForbidden("effective actor must align with issuer")
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=issuer_principal_id,
            operation=CONTENT_REVIEW_ASSIGN_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        fingerprint = fingerprint_material(
            {
                "content_id": str(content_id),
                "version_id": str(version_id),
                "reviewer_principal_id": str(reviewer_principal_id),
            }
        )
        try:
            with self._uow_factory(execution_tenant_id) as uow:
                uow.idempotency.acquire_scope(scope)
                stored = uow.idempotency.get(scope)
                uow.contents.get_head_for_update(content_id)
                class_ref = validate_issue_issuer_locked(
                    uow,
                    execution_tenant_id=execution_tenant_id,
                    issuer_principal_id=issuer_principal_id,
                    content_id=content_id,
                    version_id=version_id,
                    assign_authorization=self._assign_authorization,
                    review_authorization=self._review_authorization,
                    principal_classification=self._principal_classification,
                    issuer_class_authority=self._issuer_class_authority,
                )
                slot_now = self._clock()
                uow.review_assignments.ensure_slot(
                    tenant_id=execution_tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    now=slot_now,
                )
                if stored is not None:
                    if stored.request_fingerprint_sha256 != fingerprint:
                        raise IdempotencyKeyReused(
                            "idempotency key reused with different body"
                        )
                    validate_issue_targets_locked(
                        uow,
                        execution_tenant_id=execution_tenant_id,
                        issuer_principal_id=issuer_principal_id,
                        reviewer_principal_id=reviewer_principal_id,
                        content_id=content_id,
                        version_id=version_id,
                        class_ref=class_ref,
                        review_authorization=self._review_authorization,
                        principal_classification=self._principal_classification,
                        reviewer_eligibility=self._reviewer_eligibility,
                    )
                    assignment_id = ReviewAssignmentId(stored.result_content_id)
                    assigned = uow.review_assignments.get_assigned(assignment_id)
                    if (
                        assigned is None
                        or assigned.tenant_id != execution_tenant_id
                        or assigned.content_id != content_id
                        or assigned.version_id != version_id
                    ):
                        raise PersistenceInvariantViolation(
                            "idempotent assign outcome does not match stored issuance"
                        )
                    if (
                        assigned.issuer_principal_id != issuer_principal_id
                        or assigned.class_ref != class_ref
                        or assigned.reviewer_principal_id != reviewer_principal_id
                    ):
                        raise PersistenceInvariantViolation(
                            "idempotent assign outcome identity mismatch"
                        )
                    validate_issue_issuer_locked(
                        uow,
                        execution_tenant_id=execution_tenant_id,
                        issuer_principal_id=issuer_principal_id,
                        content_id=content_id,
                        version_id=version_id,
                        assign_authorization=self._assign_authorization,
                        review_authorization=self._review_authorization,
                        principal_classification=self._principal_classification,
                        issuer_class_authority=self._issuer_class_authority,
                    )
                    validate_issue_targets_locked(
                        uow,
                        execution_tenant_id=execution_tenant_id,
                        issuer_principal_id=issuer_principal_id,
                        reviewer_principal_id=reviewer_principal_id,
                        content_id=content_id,
                        version_id=version_id,
                        class_ref=class_ref,
                        review_authorization=self._review_authorization,
                        principal_classification=self._principal_classification,
                        reviewer_eligibility=self._reviewer_eligibility,
                    )
                    effective_now = self._clock()
                    if effective_now < slot_now:
                        raise ReviewAssignmentNotAllowed("invalid assignment effect time")
                    active = resolve_slot_active_assignment(
                        uow.review_assignments,
                        tenant_id=execution_tenant_id,
                        content_id=content_id,
                        version_id=version_id,
                        now=effective_now,
                    )
                    assert_replay_entitlement_active(
                        active,
                        assignment_id=assignment_id,
                        issuer_principal_id=issuer_principal_id,
                        reviewer_principal_id=reviewer_principal_id,
                        class_ref=class_ref,
                    )
                    return assignment_id
                validate_issue_targets_locked(
                    uow,
                    execution_tenant_id=execution_tenant_id,
                    issuer_principal_id=issuer_principal_id,
                    reviewer_principal_id=reviewer_principal_id,
                    content_id=content_id,
                    version_id=version_id,
                    class_ref=class_ref,
                    review_authorization=self._review_authorization,
                    principal_classification=self._principal_classification,
                    reviewer_eligibility=self._reviewer_eligibility,
                )
                validate_issue_issuer_locked(
                    uow,
                    execution_tenant_id=execution_tenant_id,
                    issuer_principal_id=issuer_principal_id,
                    content_id=content_id,
                    version_id=version_id,
                    assign_authorization=self._assign_authorization,
                    review_authorization=self._review_authorization,
                    principal_classification=self._principal_classification,
                    issuer_class_authority=self._issuer_class_authority,
                )
                validate_issue_targets_locked(
                    uow,
                    execution_tenant_id=execution_tenant_id,
                    issuer_principal_id=issuer_principal_id,
                    reviewer_principal_id=reviewer_principal_id,
                    content_id=content_id,
                    version_id=version_id,
                    class_ref=class_ref,
                    review_authorization=self._review_authorization,
                    principal_classification=self._principal_classification,
                    reviewer_eligibility=self._reviewer_eligibility,
                )
                effective_now = self._clock()
                if effective_now < slot_now:
                    raise ReviewAssignmentNotAllowed("invalid assignment effect time")
                active = resolve_slot_active_assignment(
                    uow.review_assignments,
                    tenant_id=execution_tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    now=effective_now,
                )
                if active is not None and not active.revoked:
                    raise ReviewAssignmentConflict("active assignment already bound")
                head = uow.contents.get_head_for_update(content_id)
                if head is None:
                    raise ReviewAssignmentNotAllowed("content head missing")
                valid_until = effective_now + _ASSIGNMENT_TTL
                assignment_id = ReviewAssignmentId(uuid7())
                event = ReviewAssignmentAssignedEvent(
                    assignment_id=assignment_id,
                    tenant_id=execution_tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    reviewer_principal_id=reviewer_principal_id,
                    issuer_principal_id=issuer_principal_id,
                    class_ref=class_ref,
                    issued_at=effective_now,
                    valid_until=valid_until,
                    correlation_id=event_context.correlation_id,
                )
                uow.review_assignments.insert_assigned(event)
                uow.review_assignments.set_active_assignment(
                    content_id=content_id,
                    version_id=version_id,
                    active_assignment_id=assignment_id,
                    now=effective_now,
                )
                revision_before = int(head.aggregate_revision)
                insert_required_content_audit(
                    uow,
                    tenant_id=execution_tenant_id,
                    action=SecurityAuditAction.CONTENT_REVIEW_ASSIGN,
                    content_id=content_id.value,
                    resource_revision_before=revision_before,
                    resource_revision_after=revision_before,
                    related_resource_refs=(
                        content_version_ref(version_id.value),
                        review_assignment_ref(assignment_id.value),
                    ),
                    mutation_event_context=event_context,
                    audit_provenance=audit_provenance,
                    occurred_at=effective_now,
                )
                uow.idempotency.insert(
                    IdempotencyOutcome(
                        tenant_id=execution_tenant_id,
                        principal_id=issuer_principal_id,
                        operation=CONTENT_REVIEW_ASSIGN_V1,
                        key_sha256=scope.key_sha256,
                        request_fingerprint_sha256=fingerprint,
                        result_content_id=assignment_id.value,
                        result_version_id=version_id.value,
                        result_review_decision_id=None,
                        result_publication_id=None,
                        result_aggregate_revision=revision_before,
                        created_at=effective_now,
                        expires_at=effective_now + self._idempotency_retention,
                    )
                )
                uow.commit()
                return assignment_id
        except _DENIED_EXCEPTIONS as exc:
            _record_assign_denial(
                self._denial_audit,
                tenant_id=execution_tenant_id,
                principal_id=issuer_principal_id,
                correlation_id=event_context.correlation_id,
                content_id=content_id,
                version_id=version_id,
                exc=exc,
            )
            raise
        except (PersistenceInvariantViolation, ReviewAssignmentConflict) as exc:
            _record_assign_denial(
                self._denial_audit,
                tenant_id=execution_tenant_id,
                principal_id=issuer_principal_id,
                correlation_id=event_context.correlation_id,
                content_id=content_id,
                version_id=version_id,
                exc=exc,
            )
            raise
        except ContentApplicationError:
            raise