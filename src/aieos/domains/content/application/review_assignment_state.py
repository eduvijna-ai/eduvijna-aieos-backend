"""Resolve current-slot assignment facts under Content UoW lock."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aieos.domains.content.application.errors import ReviewAssignmentNotFound
from aieos.domains.content.application.ports import (
    ContentUnitOfWork,
    ReviewAssignmentRepository,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId, ReviewAssignmentId
from aieos.domains.content.domain.review_assignment import ActiveReviewAssignment


def resolve_active_assignment(
    repo: ReviewAssignmentRepository,
    *,
    content_id: ContentId,
    version_id: ContentVersionId,
    now: datetime,
) -> ActiveReviewAssignment | None:
    slot = repo.get_slot_for_update(content_id, version_id)
    if slot is None or slot.active_assignment_id is None:
        return None
    return load_active_assignment(
        repo,
        assignment_id=slot.active_assignment_id,
        content_id=content_id,
        version_id=version_id,
        now=now,
    )


def load_active_assignment(
    repo: ReviewAssignmentRepository,
    *,
    assignment_id: ReviewAssignmentId,
    content_id: ContentId,
    version_id: ContentVersionId,
    now: datetime,
) -> ActiveReviewAssignment | None:
    assigned = repo.get_assigned(assignment_id)
    if assigned is None:
        return None
    if assigned.content_id != content_id or assigned.version_id != version_id:
        return None
    revoked = repo.has_revoked(assignment_id)
    expired = now >= assigned.valid_until
    if revoked or expired:
        return ActiveReviewAssignment(
            assignment_id=assignment_id,
            tenant_id=assigned.tenant_id,
            content_id=assigned.content_id,
            version_id=assigned.version_id,
            reviewer_principal_id=assigned.reviewer_principal_id,
            issuer_principal_id=assigned.issuer_principal_id,
            class_ref=assigned.class_ref,
            issued_at=assigned.issued_at,
            valid_until=assigned.valid_until,
            revoked=revoked or expired,
        )
    return ActiveReviewAssignment(
        assignment_id=assignment_id,
        tenant_id=assigned.tenant_id,
        content_id=assigned.content_id,
        version_id=assigned.version_id,
        reviewer_principal_id=assigned.reviewer_principal_id,
        issuer_principal_id=assigned.issuer_principal_id,
        class_ref=assigned.class_ref,
        issued_at=assigned.issued_at,
        valid_until=assigned.valid_until,
        revoked=False,
    )


def require_assigned_for_version(
    repo: ReviewAssignmentRepository,
    *,
    tenant_id: UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    assignment_id: ReviewAssignmentId,
    now: datetime,
) -> ActiveReviewAssignment:
    active = load_active_assignment(
        repo,
        assignment_id=assignment_id,
        content_id=content_id,
        version_id=version_id,
        now=now,
    )
    if active is None or active.tenant_id != tenant_id:
        raise ReviewAssignmentNotFound("assignment not found for version")
    return active