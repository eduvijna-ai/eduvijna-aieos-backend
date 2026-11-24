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
    ContentUnitOfWork,
    ReviewAuthorizationPort,
)
from aieos.domains.content.application.review_assignment_state import load_active_assignment
from aieos.domains.content.application.school_context_reviewer_eligibility import (
    SchoolContextReviewerEligibility,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId, ReviewAssignmentId
from aieos.domains.content.domain.review_assignment import ActiveReviewAssignment
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID
from aieos.domains.education.worksheet_hint_safety import version_requires_worksheet_hint_attestation
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
    authorization: ReviewAuthorizationPort,
    principal_classification: CurrentPrincipalClassificationAuthority,
    reviewer_eligibility: SchoolContextReviewerEligibility,
    now: datetime,
) -> ActiveReviewAssignment:
    """Full O64-03-C predicate before any future private disclosure/mutation."""
    repo = uow.review_assignments
    principal_classification.require_current_human_principal(ctx.principal_id)
    if ctx.effective_actor_id != ctx.principal_id:
        raise ReviewAssignmentForbidden(
            "effective actor must align with reviewer principal"
        )
    authorization.authorize(
        tenant_id=ctx.execution_tenant_id,
        principal_id=ctx.principal_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        capability=CONTENT_REVIEW_DECIDE,
    )
    reviewer_eligibility.require_reviewer_class_eligibility(
        ctx.execution_tenant_id, ctx.principal_id, ctx.class_ref
    )
    head = uow.contents.get_head_for_update(ctx.content_id)
    if head is None:
        raise ReviewAssignmentNotAllowed("content head missing")
    version = uow.versions.get(ctx.version_id)
    if version is None or version.content_id != ctx.content_id:
        raise ReviewAssignmentNotAllowed("version missing")
    if head.current_version_id != ctx.version_id:
        raise ReviewAssignmentNotAllowed("version is not current")
    if head.stewardship_state != StewardshipState.IN_REVIEW.value:
        if ctx.mode is ReviewerUseMode.PROTECTED_EFFECT:
            raise ReviewAssignmentNotAllowed("version not IN_REVIEW")
    if str(version.schema_id) != WORKSHEET_SCHEMA_ID:
        raise ReviewAssignmentNotAllowed("assignment guard requires worksheet content")
    payload = version.payload.body
    if not version_requires_worksheet_hint_attestation(
        schema_id=str(version.schema_id),
        schema_version=int(version.schema_version),
        payload=payload,
    ):
        raise ReviewAssignmentNotAllowed("assignment guard requires hint-bearing worksheet@2")
    active = load_active_assignment(
        repo,
        assignment_id=ctx.assignment_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        now=now,
    )
    if active is None:
        raise ReviewAssignmentConflict("assignment not found")
    if active.reviewer_principal_id != ctx.principal_id:
        raise ReviewAssignmentForbidden("principal is not the assigned reviewer")
    if active.class_ref != ctx.class_ref.strip():
        raise ReviewAssignmentForbidden("class_ref does not match issuance")
    if active.revoked and ctx.mode is ReviewerUseMode.PROTECTED_EFFECT:
        raise ReviewAssignmentConflict("assignment is revoked or expired")
    if version.created_by_principal_id == ctx.principal_id:
        raise ReviewAssignmentForbidden("reviewer must be distinct from version author")
    if uow.reviews.get_for_version(ctx.content_id, ctx.version_id) is not None:
        if ctx.mode is ReviewerUseMode.PROTECTED_EFFECT:
            raise ReviewAssignmentNotAllowed("version already has terminal decision")
    return active