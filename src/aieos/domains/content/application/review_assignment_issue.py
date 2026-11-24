"""Internal issue reviewer assignment (ADR-AIEOS-064 O64-03-A). No HTTP surface."""

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
    ReviewAssignmentConflict,
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
)
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
    ContentUnitOfWorkFactory,
    ReviewAssignmentAuthorizationPort,
    ReviewAuthorizationPort,
)
from aieos.domains.content.application.review_assignment_provenance import (
    mandatory_authoring_class_ref,
    provenance_authoring_principal_id,
)
from aieos.domains.content.application.review_assignment_state import resolve_active_assignment
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
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID
from aieos.domains.education.worksheet_hint_safety import version_requires_worksheet_hint_attestation
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthority
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_ASSIGN_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)

_ASSIGNMENT_TTL = timedelta(days=7)


class IssueReviewAssignmentService:
    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        assign_authorization: ReviewAssignmentAuthorizationPort,
        review_authorization: ReviewAuthorizationPort,
        principal_classification: CurrentPrincipalClassificationAuthority,
        issuer_class_authority: SchoolContextClassAuthority,
        reviewer_eligibility: SchoolContextReviewerEligibility,
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
        self._reviewer_eligibility = reviewer_eligibility
        self._idempotency_retention = idempotency_retention

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
        now: datetime | None = None,
    ) -> ReviewAssignmentId:
        effective = event_context.effective_actor_id
        if effective != issuer_principal_id:
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
        if reviewer_principal_id == issuer_principal_id:
            raise ReviewAssignmentNotAllowed("reviewer must be distinct from issuer")
        self._principal_classification.require_current_human_principal(reviewer_principal_id)
        self._review_authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=reviewer_principal_id,
            content_id=content_id,
            version_id=version_id,
            capability=CONTENT_REVIEW_DECIDE,
        )
        issued_at = now if now is not None else datetime.now(UTC)
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
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            stored = uow.idempotency.get(scope)
            if stored is not None:
                if stored.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key reused with different body")
                return ReviewAssignmentId(stored.result_content_id)
            head = uow.contents.get_head_for_update(content_id)
            if head is None:
                raise ReviewAssignmentNotAllowed("content not found")
            content_row = uow.contents.get(content_id)
            if content_row is None or content_row.owner_principal_id != issuer_principal_id:
                raise ReviewAssignmentForbidden("issuer must be current content owner")
            if head.current_version_id != version_id:
                raise ReviewAssignmentNotAllowed("version is not current")
            if head.stewardship_state != StewardshipState.IN_REVIEW.value:
                raise ReviewAssignmentNotAllowed("version must be IN_REVIEW")
            version = uow.versions.get(version_id)
            if version is None or version.content_id != content_id:
                raise ReviewAssignmentNotAllowed("version not found")
            if str(version.schema_id) != WORKSHEET_SCHEMA_ID:
                raise ReviewAssignmentNotAllowed("hint-bearing worksheet required")
            if not version_requires_worksheet_hint_attestation(
                schema_id=str(version.schema_id),
                schema_version=int(version.schema_version),
                payload=version.payload.body,
            ):
                raise ReviewAssignmentNotAllowed("hint-bearing worksheet@2 required")
            provenance = uow.versions.get_provenance(version_id)
            class_ref = mandatory_authoring_class_ref(provenance)
            author_id = provenance_authoring_principal_id(provenance)
            if author_id != issuer_principal_id or author_id != version.created_by_principal_id:
                raise ReviewAssignmentForbidden(
                    "issuer must be immutable hint-version creator"
                )
            self._issuer_class_authority.require_assignable_class_ref(
                execution_tenant_id, issuer_principal_id, class_ref
            )
            self._reviewer_eligibility.require_reviewer_class_eligibility(
                execution_tenant_id, reviewer_principal_id, class_ref
            )
            if uow.reviews.get_for_version(content_id, version_id) is not None:
                raise ReviewAssignmentNotAllowed("terminal decision already exists")
            if uow.worksheet_hint_attestations.get_for_version(content_id, version_id):
                raise ReviewAssignmentConflict("attestation already exists")
            active = resolve_active_assignment(
                uow.review_assignments,
                content_id=content_id,
                version_id=version_id,
                now=issued_at,
            )
            if active is not None and not active.revoked:
                raise ReviewAssignmentConflict("active assignment already bound")
            assignment_id = ReviewAssignmentId(uuid7())
            valid_until = issued_at + _ASSIGNMENT_TTL
            event = ReviewAssignmentAssignedEvent(
                event_id=uuid7(),
                assignment_id=assignment_id,
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_id=version_id,
                reviewer_principal_id=reviewer_principal_id,
                issuer_principal_id=issuer_principal_id,
                class_ref=class_ref,
                issued_at=issued_at,
                valid_until=valid_until,
                correlation_id=event_context.correlation_id,
            )
            uow.review_assignments.ensure_slot(
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_id=version_id,
                now=issued_at,
            )
            uow.review_assignments.insert_assigned(event)
            uow.review_assignments.set_active_assignment(
                content_id=content_id,
                version_id=version_id,
                active_assignment_id=assignment_id,
                now=issued_at,
            )
            revision_before = int(head.aggregate_revision)
            insert_required_content_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.CONTENT_REVIEW_ASSIGN,
                primary=content_primary_ref(content_id.value, revision_before),
                resource_after=content_primary_ref(content_id.value, revision_before),
                related=(content_version_ref(version_id.value), review_assignment_ref(assignment_id.value)),
                event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=issued_at,
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
                    expires_at=issued_at + self._idempotency_retention,
                )
            )
            uow.commit()
            return assignment_id