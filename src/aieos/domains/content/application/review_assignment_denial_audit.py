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
REASON_REPLAY_ENTITLEMENT = "replay.entitlement.denied"
REASON_ASSIGNMENT_CONFLICT = "assignment.conflict"
REASON_ATTESTATION_BLOCKED = "assignment.attestation.blocked"
REASON_NOT_ALLOWED = "assign.not.allowed"
REASON_PERSISTENCE_INVARIANT = "persistence.invariant.violation"
REASON_REVIEW_CAPABILITY = "review.capability.denied"
REASON_AUTHORITY_UNAVAILABLE = "authority.unavailable"
REASON_SCHOOL_CONTEXT_CONTRACT = "school.context.contract.invalid"


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
        PersistenceInvariantViolation,
        ReviewAssignmentConflict,
        ReviewAssignmentForbidden,
        ReviewAssignmentNotAllowed,
        ReviewForbidden,
    )
    from aieos.domains.teaching.application.errors import (
        ClassRefNotAssignable,
        SchoolContextContractError,
        SchoolContextUnavailable,
    )
    from aieos.platform.security import UnauthorizedError
    from aieos.platform.security.context import AuthorizationUnavailableError

    if isinstance(exc, AuthorizationUnavailableError):
        return REASON_AUTHORITY_UNAVAILABLE
    if isinstance(exc, UnauthorizedError):
        return REASON_PRINCIPAL_KIND
    if isinstance(exc, ReviewForbidden):
        return REASON_REVIEW_CAPABILITY
    if isinstance(exc, SchoolContextUnavailable):
        return REASON_SCHOOL_CONTEXT
    if isinstance(exc, SchoolContextContractError):
        return REASON_SCHOOL_CONTEXT_CONTRACT
    if isinstance(exc, ClassRefNotAssignable):
        return REASON_ISSUER_CLASS
    message = str(exc).lower()
    if isinstance(exc, PersistenceInvariantViolation):
        if "replay" in message or "entitlement" in message:
            return REASON_REPLAY_ENTITLEMENT
        return REASON_PERSISTENCE_INVARIANT
    if isinstance(exc, ReviewAssignmentConflict):
        if "attestation" in message:
            return REASON_ATTESTATION_BLOCKED
        return REASON_ASSIGNMENT_CONFLICT
    if isinstance(exc, ReviewAssignmentForbidden):
        if "effective actor" in message:
            return REASON_EFFECTIVE_ACTOR_MISMATCH
        if "owner" in message:
            return REASON_ISSUER_NOT_OWNER
        if "creator" in message:
            return REASON_ISSUER_NOT_CREATOR
        if "reviewer" in message and "class" in message:
            return REASON_REVIEWER_CLASS
        if "class" in message:
            return REASON_ISSUER_CLASS
        if "only the original issuer" in message:
            return REASON_REVOKE_CAPABILITY
        return REASON_ASSIGN_CAPABILITY
    if isinstance(exc, ReviewAssignmentNotAllowed):
        if "distinct" in message and "reviewer" in message:
            return REASON_NOT_ALLOWED
        return REASON_NOT_ALLOWED
    return REASON_ASSIGN_CAPABILITY