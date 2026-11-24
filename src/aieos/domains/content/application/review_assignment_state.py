"""Resolve current-slot assignment facts under Content UoW lock."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aieos.domains.content.application.errors import (
    PersistenceInvariantViolation,
    ReviewAssignmentNotFound,
)
from aieos.domains.content.application.ports import ReviewAssignmentRepository
from aieos.domains.content.domain.identities import ContentId, ContentVersionId, ReviewAssignmentId
from aieos.domains.content.domain.review_assignment import ActiveReviewAssignment


def resolve_slot_active_assignment(
    repo: ReviewAssignmentRepository,
    *,
    tenant_id: UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    now: datetime,
) -> ActiveReviewAssignment | None:
    slot = repo.get_slot_for_update(content_id, version_id)
    if slot is None:
        return None
    if slot.tenant_id != tenant_id:
        raise PersistenceInvariantViolation("assignment slot tenant mismatch")
    if slot.active_assignment_id is None:
        return None
    active = materialize_assignment(
        repo,
        tenant_id=tenant_id,
        assignment_id=slot.active_assignment_id,
        content_id=content_id,
        version_id=version_id,
        now=now,
        require_slot_binding=True,
    )
    if active.assignment_id != slot.active_assignment_id:
        raise PersistenceInvariantViolation("assignment slot pointer corrupt")
    return active


def materialize_assignment(
    repo: ReviewAssignmentRepository,
    *,
    tenant_id: UUID,
    assignment_id: ReviewAssignmentId,
    content_id: ContentId,
    version_id: ContentVersionId,
    now: datetime,
    require_slot_binding: bool = False,
) -> ActiveReviewAssignment:
    assigned = repo.get_assigned(assignment_id)
    if assigned is None:
        if require_slot_binding:
            raise PersistenceInvariantViolation(
                "active assignment slot references missing issuance"
            )
        raise ReviewAssignmentNotFound("assignment issuance not found")
    if assigned.tenant_id != tenant_id:
        raise PersistenceInvariantViolation("assignment tenant mismatch")
    if assigned.content_id != content_id or assigned.version_id != version_id:
        raise PersistenceInvariantViolation("assignment version correlation mismatch")
    revoked = repo.has_revoked(assignment_id)
    expired = now >= assigned.valid_until
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