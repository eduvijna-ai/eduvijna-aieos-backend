"""Review assignment authorization denial evidence port (ADR-O64-05)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

CONTENT_REVIEW_ASSIGN_DENIED = "content.review.assign.denied"
CONTENT_REVIEW_REVOKE_DENIED = "content.review.revoke.denied"

REASON_EFFECTIVE_ACTOR_MISMATCH = "effective.actor.mismatch"
REASON_ASSIGN_CAPABILITY = "assign.capability.denied"
REASON_REVOKE_CAPABILITY = "revoke.capability.denied"
REASON_ISSUER_NOT_OWNER = "issuer.not.owner"
REASON_ISSUER_NOT_CREATOR = "issuer.not.creator"
REASON_REVIEWER_CLASS = "reviewer.class.ineligible"
REASON_ISSUER_CLASS = "issuer.class.ineligible"
REASON_PRINCIPAL_KIND = "principal.kind.denied"
REASON_SCHOOL_CONTEXT = "school.context.unavailable"


@dataclass(frozen=True, slots=True)
class AssignmentDenialRecord:
    tenant_id: UUID
    principal_id: UUID
    action: str
    correlation_id: UUID
    reason_code: str
    content_id: UUID | None = None
    version_id: UUID | None = None


class ReviewAssignmentDenialAuditPort(Protocol):
    """Records authorization denials outside rolled-back business transactions."""

    def record(self, record: AssignmentDenialRecord) -> None: ...


class DenialAuditUnavailable(RuntimeError):
    """Mandatory denial evidence sink failed; authorization still denied."""


def record_assignment_denial(
    denial_audit: ReviewAssignmentDenialAuditPort,
    record: AssignmentDenialRecord,
) -> None:
    try:
        denial_audit.record(record)
    except Exception as exc:
        raise DenialAuditUnavailable(
            "authorization denial evidence could not be recorded"
        ) from exc


def reason_for_assignment_error(exc: BaseException) -> str:
    from aieos.domains.content.application.errors import (
        ReviewAssignmentForbidden,
        ReviewAssignmentNotAllowed,
    )
    from aieos.domains.teaching.application.errors import SchoolContextUnavailable

    message = str(exc).lower()
    if isinstance(exc, SchoolContextUnavailable):
        return REASON_SCHOOL_CONTEXT
    if isinstance(exc, ReviewAssignmentForbidden):
        if "owner" in message:
            return REASON_ISSUER_NOT_OWNER
        if "creator" in message:
            return REASON_ISSUER_NOT_CREATOR
        if "class" in message and "reviewer" in message:
            return REASON_REVIEWER_CLASS
        if "class" in message:
            return REASON_ISSUER_CLASS
        if "capability" in message or "denied" in message:
            return REASON_ASSIGN_CAPABILITY
        return REASON_ASSIGN_CAPABILITY
    if isinstance(exc, ReviewAssignmentNotAllowed):
        return REASON_ASSIGN_CAPABILITY
    return REASON_ASSIGN_CAPABILITY