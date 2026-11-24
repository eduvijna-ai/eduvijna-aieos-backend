"""Internal revoke reviewer assignment (ADR-AIEOS-064 O64-03-B). No HTTP surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from aieos.domains.content.application.audit import (
    MutationAuditProvenance,
    content_primary_ref,
    content_version_ref,
    insert_required_content_audit,
    review_assignment_ref,
)
from aieos.domains.content.application.errors import (
    IdempotencyKeyReused,
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
    ReviewAssignmentNotFound,
)
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_SUBMIT,
    ContentUnitOfWorkFactory,
    ReviewAssignmentAuthorizationPort,
    ReviewAuthorizationPort,
)
from aieos.domains.content.application.review_assignment_provenance import (
    mandatory_authoring_class_ref,
    provenance_authoring_principal_id,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId, ReviewAssignmentId
from aieos.domains.content.domain.review_assignment import ReviewAssignmentRevokedEvent
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthority
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_REVOKE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


class RevokeReviewAssignmentService:
    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        assign_authorization: ReviewAssignmentAuthorizationPort,
        review_authorization: ReviewAuthorizationPort,
        principal_classification: CurrentPrincipalClassificationAuthority,
        issuer_class_authority: SchoolContextClassAuthority,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be positive")
        self._uow_factory = uow_factory
        self._assign_authorization = assign_authorization
        self._review_authorization = review_authorization
        self._principal_classification = principal_classification
        self._issuer_class_authority = issuer_class_authority
        self._idempotency_retention = idempotency_retention

    def revoke(
        self,
        execution_tenant_id: UUID,
        issuer_principal_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        assignment_id: ReviewAssignmentId,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> ReviewAssignmentId:
        if event_context.effective_actor_id != issuer_principal_id:
            raise ReviewAssignmentForbidden("effective actor must align with issuer")
        self._principal_classification.require_current_human_principal(issuer_principal_id)
        self._assign_authorization.authorize_assign(
            tenant_id=execution_tenant_id,
            principal_id=issuer_principal_id,
            content_id=content_id,
            version_id=version_id,
        )
        self._review_authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=issuer_principal_id,
            content_id=content_id,
            version_id=version_id,
            capability=CONTENT_REVIEW_SUBMIT,
        )
        revoked_at = now if now is not None else datetime.now(UTC)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=issuer_principal_id,
            operation=CONTENT_REVIEW_REVOKE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        fingerprint = fingerprint_material(
            {
                "content_id": str(content_id),
                "version_id": str(version_id),
                "assignment_id": str(assignment_id),
            }
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            stored = uow.idempotency.get(scope)
            if stored is not None:
                if stored.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key reused with different body")
                return assignment_id
            assigned = uow.review_assignments.get_assigned(assignment_id)
            if (
                assigned is None
                or assigned.tenant_id != execution_tenant_id
                or assigned.content_id != content_id
                or assigned.version_id != version_id
            ):
                raise ReviewAssignmentNotFound("assignment not found for version")
            if uow.review_assignments.has_revoked(assignment_id):
                return assignment_id
            head = uow.contents.get_head_for_update(content_id)
            content_row = uow.contents.get(content_id) if head is not None else None
            if (
                head is None
                or content_row is None
                or content_row.owner_principal_id != issuer_principal_id
            ):
                raise ReviewAssignmentForbidden("issuer must be current content owner")
            version = uow.versions.get(version_id)
            if version is None:
                raise ReviewAssignmentNotAllowed("version not found")
            provenance = uow.versions.get_provenance(version_id)
            class_ref = mandatory_authoring_class_ref(provenance)
            author_id = provenance_authoring_principal_id(provenance)
            if author_id != issuer_principal_id:
                raise ReviewAssignmentForbidden("issuer must be immutable version creator")
            self._issuer_class_authority.require_assignable_class_ref(
                execution_tenant_id, issuer_principal_id, class_ref
            )
            uow.review_assignments.ensure_slot(
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_id=version_id,
                now=revoked_at,
            )
            event = ReviewAssignmentRevokedEvent(
                event_id=uuid7(),
                assignment_id=assignment_id,
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_id=version_id,
                revoked_at=revoked_at,
                revoked_by_principal_id=issuer_principal_id,
                correlation_id=event_context.correlation_id,
            )
            uow.review_assignments.insert_revoked(event)
            slot = uow.review_assignments.get_slot_for_update(content_id, version_id)
            if (
                slot is not None
                and slot.active_assignment_id is not None
                and slot.active_assignment_id == assignment_id
            ):
                uow.review_assignments.set_active_assignment(
                    content_id=content_id,
                    version_id=version_id,
                    active_assignment_id=None,
                    now=revoked_at,
                )
            revision_before = int(head.aggregate_revision)
            insert_required_content_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.CONTENT_REVIEW_REVOKE,
                primary=content_primary_ref(content_id.value, revision_before),
                resource_after=content_primary_ref(content_id.value, revision_before),
                related=(content_version_ref(version_id.value), review_assignment_ref(assignment_id.value)),
                event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=revoked_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=execution_tenant_id,
                    principal_id=issuer_principal_id,
                    operation=CONTENT_REVIEW_REVOKE_V1,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=assignment_id.value,
                    result_version_id=version_id.value,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=revision_before,
                    expires_at=revoked_at + self._idempotency_retention,
                )
            )
            uow.commit()
            return assignment_id