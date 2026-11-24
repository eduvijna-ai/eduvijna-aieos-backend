"""Reusable O64-03-C reviewer-use guard (internal; no HTTP activation in G2-I01)."""

from __future__ import annotations

from collections.abc import Callable
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
    ContentUnitOfWork,
    ReviewAssignmentAuthorizationPort,
    ReviewAuthorizationPort,
)
from aieos.domains.content.application.review_assignment_commands_common import (
    revalidate_reviewer_locked,
    validate_revoke_issuer_locked,
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
    mode: ReviewerUseMode = ReviewerUseMode.PROTECTED_EFFECT


@dataclass(frozen=True, slots=True)
class ReviewerUseGuardDependencies:
    """Explicit G2-I02 integration seam for denial audit (not invoked in G2-I01)."""

    assign_authorization: ReviewAssignmentAuthorizationPort
    authorization: ReviewAuthorizationPort
    principal_classification: CurrentPrincipalClassificationAuthority
    reviewer_eligibility: SchoolContextReviewerEligibility
    issuer_class_authority: SchoolContextClassAuthority
    clock: Callable[[], datetime]


def enforce_reviewer_use_guard(
    uow: ContentUnitOfWork,
    *,
    ctx: ReviewerUseGuardContext,
    deps: ReviewerUseGuardDependencies,
) -> ActiveReviewAssignment:
    """Full O64-03-C predicate before any future private disclosure/mutation."""
    if ctx.mode is ReviewerUseMode.TERMINAL_RECEIPT:
        raise ReviewAssignmentNotAllowed(
            "terminal receipt authority requires G2-I02 proven binding"
        )
    if ctx.effective_actor_id != ctx.principal_id:
        raise ReviewAssignmentForbidden(
            "effective actor must align with reviewer principal"
        )
    head = uow.contents.get_head_for_update(ctx.content_id)
    if head is None or head.tenant_id != ctx.execution_tenant_id:
        raise ReviewAssignmentNotAllowed("content head missing")
    if head.current_version_id != ctx.version_id:
        raise ReviewAssignmentNotAllowed("version is not current")
    if uow.review_assignments.get_slot_for_update(ctx.content_id, ctx.version_id) is None:
        raise ReviewAssignmentConflict("assignment slot missing")
    version = uow.versions.get(ctx.version_id)
    if version is None:
        raise ReviewAssignmentNotAllowed("version missing")
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
    content_row = uow.contents.get(ctx.content_id)
    if content_row is None or content_row.tenant_id != ctx.execution_tenant_id:
        raise ReviewAssignmentNotAllowed("content missing")
    issuer_id = content_row.owner_principal_id
    canonical_class = validate_revoke_issuer_locked(
        uow,
        execution_tenant_id=ctx.execution_tenant_id,
        issuer_principal_id=issuer_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        assign_authorization=deps.assign_authorization,
        review_authorization=deps.authorization,
        principal_classification=deps.principal_classification,
        issuer_class_authority=deps.issuer_class_authority,
    )
    if version.created_by_principal_id == ctx.principal_id:
        raise ReviewAssignmentForbidden("reviewer must be distinct from version author")
    deps.principal_classification.require_current_human_principal(ctx.principal_id)
    revalidate_reviewer_locked(
        uow,
        execution_tenant_id=ctx.execution_tenant_id,
        reviewer_principal_id=ctx.principal_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        class_ref=canonical_class,
        review_authorization=deps.authorization,
        principal_classification=deps.principal_classification,
        reviewer_eligibility=deps.reviewer_eligibility,
    )
    validate_revoke_issuer_locked(
        uow,
        execution_tenant_id=ctx.execution_tenant_id,
        issuer_principal_id=issuer_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        assign_authorization=deps.assign_authorization,
        review_authorization=deps.authorization,
        principal_classification=deps.principal_classification,
        issuer_class_authority=deps.issuer_class_authority,
    )
    effect_at = deps.clock()
    active = resolve_slot_active_assignment(
        uow.review_assignments,
        tenant_id=ctx.execution_tenant_id,
        content_id=ctx.content_id,
        version_id=ctx.version_id,
        now=effect_at,
    )
    if active is None:
        raise ReviewAssignmentConflict("no active assignment binding at effect time")
    if active.assignment_id != ctx.assignment_id:
        raise ReviewAssignmentConflict("assignment is not the current slot binding")
    if active.revoked:
        raise ReviewAssignmentConflict("assignment is revoked or expired")
    if active.reviewer_principal_id != ctx.principal_id:
        raise ReviewAssignmentForbidden("principal is not the assigned reviewer")
    if active.class_ref != canonical_class:
        raise ReviewAssignmentForbidden("class_ref does not match governed provenance")
    if active.issuer_principal_id != issuer_id:
        raise ReviewAssignmentForbidden("issuer does not match content owner")
    return active