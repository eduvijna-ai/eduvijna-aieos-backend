"""Reusable O64-03-C reviewer-use guard (internal; no HTTP activation in G2-I01)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from aieos.domains.content.application.errors import (
    ReviewAssignmentConflict,
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
)
from aieos.domains.content.application.ports import (
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
from aieos.domains.content.application.review_assignment_state import (
    resolve_slot_active_assignment,
)
from aieos.domains.content.application.school_context_reviewer_eligibility import (
    SchoolContextReviewerEligibility,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId, ReviewAssignmentId
from aieos.domains.content.domain.review_assignment import ActiveReviewAssignment
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID
from aieos.domains.education.worksheet_hint_safety import version_requires_worksheet_hint_attestation
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthority
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


class ReviewerUseMode(StrEnum):
    """Separated modes for later terminal receipt handling (G2-I02)."""

    PROTECTED_EFFECT = "PROTECTED_EFFECT"
    TERMINAL_RECEIPT = "TERMINAL_RECEIPT"


@dataclass(frozen=True, slots=True)
class ReviewerUseGuardContext:
    execution_tenant_id: UUID
    principal_id: UUID
    effective_actor_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    assignment_id: ReviewAssignmentId
    class_ref: str
    mode: ReviewerUseMode = ReviewerUseMode.PROTECTED_EFFECT


def enforce_reviewer_use_guard(
    uow: ContentUnitOfWork,
    *,
    ctx: ReviewerUseGuardContext,
    assign_authorization: ReviewAssignmentAuthorizationPort,
    authorization: ReviewAuthorizationPort,
    principal_classification: CurrentPrincipalClassificationAuthority,
    reviewer_eligibility: SchoolContextReviewerEligibility,
    issuer_class_authority: SchoolContextClassAuthority,
    now: datetime,
) -> ActiveReviewAssignment:
    """Full O64-03-C predicate before any future private disclosure/mutation."""
    if ctx.mode is ReviewerUseMode.TERMINAL_RECEIPT:
        raise ReviewAssignmentNotAllowed(
            "terminal receipt authority requires G2-I02 proven binding"
        )
    repo = uow.review_assignments
    principal_classification.require_current_human_principal(ctx.principal_id)
    if ctx.effective_actor_id != ctx.principal_id:
        raise ReviewAssignmentForbidden(
            "effective actor must align with reviewer principal"
        )
    head = uow.contents.get_head_for_update(ctx.content_id)
    if head is None or head.tenant_id != ctx.execution_tenant_id:
        raise ReviewAssignmentNotAllowed("content head missing")
    if head.current_version_id != ctx.version_id:
        raise ReviewAssignmentNotAllowed("version is not current")
    version = uow.versions.get(ctx.version_id)
    if version is None or version.content_id != ctx.content_id:
        raise ReviewAssignmentNotAllowed("version missing")
    if version.tenant_id != ctx.execution_tenant_id:
        raise ReviewAssignmentForbidden("version tenant mismatch")
    provenance = uow.versions.get_provenance(ctx.version_id)
    canonical_class = mandatory_authoring_class_ref(provenance)
    author_id = provenance_authoring_principal_id(provenance)
    content_row = uow.contents.get(ctx.content_id)
    if content_row is None or content_row.tenant_id != ctx.execution_tenant_id:
        raise ReviewAssignmentNotAllowed("content missing")
    issuer_id = content_row.owner_principal_id
    if issuer_id != author_id or author_id != version.created_by_principal_id:
        raise ReviewAssignmentForbidden("content owner must remain version creator")
    principal_classification.require_current_human_principal(issuer_id)
    assign_authorization.authorize_assign(
        tenant_id=ctx.execution_tenant_id,
        principal_id=issuer_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
    )
    authorization.authorize(
        tenant_id=ctx.execution_tenant_id,
        principal_id=issuer_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        capability=CONTENT_REVIEW_SUBMIT,
    )
    issuer_class_authority.require_assignable_class_ref(
        ctx.execution_tenant_id, issuer_id, canonical_class
    )
    authorization.authorize(
        tenant_id=ctx.execution_tenant_id,
        principal_id=ctx.principal_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        capability=CONTENT_REVIEW_DECIDE,
    )
    reviewer_eligibility.require_reviewer_class_eligibility(
        ctx.execution_tenant_id, ctx.principal_id, canonical_class
    )
    if head.stewardship_state != StewardshipState.IN_REVIEW.value:
        raise ReviewAssignmentNotAllowed("version not IN_REVIEW")
    if str(version.schema_id) != WORKSHEET_SCHEMA_ID:
        raise ReviewAssignmentNotAllowed("assignment guard requires worksheet content")
    if not version_requires_worksheet_hint_attestation(
        schema_id=str(version.schema_id),
        schema_version=int(version.schema_version),
        payload=version.payload.body,
    ):
        raise ReviewAssignmentNotAllowed("assignment guard requires hint-bearing worksheet@2")
    if uow.reviews.get_for_version(ctx.content_id, ctx.version_id) is not None:
        raise ReviewAssignmentNotAllowed("version already has terminal decision")
    active = resolve_slot_active_assignment(
        repo,
        tenant_id=ctx.execution_tenant_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        now=now,
    )
    if active is None or active.assignment_id != ctx.assignment_id:
        raise ReviewAssignmentConflict("assignment is not the current slot binding")
    if active.revoked:
        raise ReviewAssignmentConflict("assignment is revoked or expired")
    if active.reviewer_principal_id != ctx.principal_id:
        raise ReviewAssignmentForbidden("principal is not the assigned reviewer")
    if active.issuer_principal_id != issuer_id:
        raise ReviewAssignmentForbidden("issuer no longer matches content owner")
    if active.class_ref != canonical_class:
        raise ReviewAssignmentForbidden("class_ref does not match governed provenance")
    if version.created_by_principal_id == ctx.principal_id:
        raise ReviewAssignmentForbidden("reviewer must be distinct from version author")
    assign_authorization.authorize_assign(
        tenant_id=ctx.execution_tenant_id,
        principal_id=issuer_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
    )
    authorization.authorize(
        tenant_id=ctx.execution_tenant_id,
        principal_id=ctx.principal_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        capability=CONTENT_REVIEW_DECIDE,
    )
    return active