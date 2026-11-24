"""Shared in-transaction assignment authority validation."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.content.application.errors import (
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
)
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_ASSIGN,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
    ContentUnitOfWork,
    ReviewAssignmentAuthorizationPort,
    ReviewAuthorizationPort,
)
from aieos.domains.content.application.review_assignment_provenance import (
    mandatory_authoring_class_ref,
    provenance_authoring_principal_id,
)
from aieos.domains.content.application.school_context_reviewer_eligibility import (
    SchoolContextReviewerEligibility,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID
from aieos.domains.education.worksheet_hint_safety import version_requires_worksheet_hint_attestation
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthority
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


def validate_issuer_locked(
    uow: ContentUnitOfWork,
    *,
    execution_tenant_id: UUID,
    issuer_principal_id: UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    assign_authorization: ReviewAssignmentAuthorizationPort,
    review_authorization: ReviewAuthorizationPort,
    principal_classification: CurrentPrincipalClassificationAuthority,
    issuer_class_authority: SchoolContextClassAuthority,
) -> str:
    principal_classification.require_current_human_principal(issuer_principal_id)
    assign_authorization.authorize_assign(
        tenant_id=execution_tenant_id,
        principal_id=issuer_principal_id,
        content_id=content_id,
        version_id=version_id,
    )
    review_authorization.authorize(
        tenant_id=execution_tenant_id,
        principal_id=issuer_principal_id,
        content_id=content_id,
        version_id=version_id,
        capability=CONTENT_REVIEW_SUBMIT,
    )
    content_row = uow.contents.get(content_id)
    if content_row is None or content_row.tenant_id != execution_tenant_id:
        raise ReviewAssignmentNotAllowed("content not found")
    if content_row.owner_principal_id != issuer_principal_id:
        raise ReviewAssignmentForbidden("issuer must be current content owner")
    head = uow.contents.get_head_for_update(content_id)
    if head is None or head.tenant_id != execution_tenant_id:
        raise ReviewAssignmentNotAllowed("content head missing")
    if head.current_version_id != version_id:
        raise ReviewAssignmentNotAllowed("version is not current")
    version = uow.versions.get(version_id)
    if version is None or version.content_id != content_id:
        raise ReviewAssignmentNotAllowed("version not found")
    if version.tenant_id != execution_tenant_id:
        raise ReviewAssignmentForbidden("version tenant mismatch")
    provenance = uow.versions.get_provenance(version_id)
    class_ref = mandatory_authoring_class_ref(provenance)
    author_id = provenance_authoring_principal_id(provenance)
    if author_id != issuer_principal_id or author_id != version.created_by_principal_id:
        raise ReviewAssignmentForbidden("issuer must be immutable version creator")
    issuer_class_authority.require_assignable_class_ref(
        execution_tenant_id, issuer_principal_id, class_ref
    )
    return class_ref


def validate_issue_targets_locked(
    uow: ContentUnitOfWork,
    *,
    execution_tenant_id: UUID,
    issuer_principal_id: UUID,
    reviewer_principal_id: UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    class_ref: str,
    review_authorization: ReviewAuthorizationPort,
    principal_classification: CurrentPrincipalClassificationAuthority,
    reviewer_eligibility: SchoolContextReviewerEligibility,
) -> None:
    if reviewer_principal_id == issuer_principal_id:
        raise ReviewAssignmentNotAllowed("reviewer must be distinct from issuer")
    principal_classification.require_current_human_principal(reviewer_principal_id)
    review_authorization.authorize(
        tenant_id=execution_tenant_id,
        principal_id=reviewer_principal_id,
        content_id=content_id,
        version_id=version_id,
        capability=CONTENT_REVIEW_DECIDE,
    )
    reviewer_eligibility.require_reviewer_class_eligibility(
        execution_tenant_id, reviewer_principal_id, class_ref
    )
    head = uow.contents.get_head_for_update(content_id)
    if head is None:
        raise ReviewAssignmentNotAllowed("content head missing")
    if head.stewardship_state != StewardshipState.IN_REVIEW.value:
        raise ReviewAssignmentNotAllowed("version must be IN_REVIEW")
    version = uow.versions.get(version_id)
    if version is None:
        raise ReviewAssignmentNotAllowed("version missing")
    if str(version.schema_id) != WORKSHEET_SCHEMA_ID:
        raise ReviewAssignmentNotAllowed("hint-bearing worksheet required")
    if not version_requires_worksheet_hint_attestation(
        schema_id=str(version.schema_id),
        schema_version=int(version.schema_version),
        payload=version.payload.body,
    ):
        raise ReviewAssignmentNotAllowed("hint-bearing worksheet@2 required")
    if uow.reviews.get_for_version(content_id, version_id) is not None:
        raise ReviewAssignmentNotAllowed("terminal decision already exists")
    if uow.worksheet_hint_attestations.get_for_version(content_id, version_id):
        from aieos.domains.content.application.errors import ReviewAssignmentConflict

        raise ReviewAssignmentConflict("attestation already exists")