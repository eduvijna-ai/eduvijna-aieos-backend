"""Content-owned immutable reviewer assignment event authority (ADR-AIEOS-064 O64-04)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    ReviewAssignmentId,
    require_foreign_uuid,
)


class ReviewAssignmentEventKind(StrEnum):
    ASSIGNED = "ASSIGNED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class ReviewAssignmentAssignedEvent:
    event_id: UUID
    assignment_id: ReviewAssignmentId
    tenant_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    reviewer_principal_id: UUID
    issuer_principal_id: UUID
    class_ref: str
    issued_at: datetime
    valid_until: datetime
    correlation_id: UUID

    def __post_init__(self) -> None:
        require_foreign_uuid(self.event_id, label="event_id")
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(self.reviewer_principal_id, label="reviewer_principal_id")
        require_foreign_uuid(self.issuer_principal_id, label="issuer_principal_id")
        require_foreign_uuid(self.correlation_id, label="correlation_id")
        normalized = self.class_ref.strip()
        if not normalized:
            raise ValueError("class_ref must be non-empty")
        if self.valid_until <= self.issued_at:
            raise ValueError("valid_until must be after issued_at")


@dataclass(frozen=True, slots=True)
class ReviewAssignmentRevokedEvent:
    event_id: UUID
    assignment_id: ReviewAssignmentId
    tenant_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    revoked_at: datetime
    revoked_by_principal_id: UUID
    correlation_id: UUID

    def __post_init__(self) -> None:
        require_foreign_uuid(self.event_id, label="event_id")
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(self.revoked_by_principal_id, label="revoked_by_principal_id")
        require_foreign_uuid(self.correlation_id, label="correlation_id")


@dataclass(frozen=True, slots=True)
class ReviewCurrentAssignmentSlot:
    tenant_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    active_assignment_id: ReviewAssignmentId | None
    updated_at: datetime

    def __post_init__(self) -> None:
        require_foreign_uuid(self.tenant_id, label="tenant_id")


@dataclass(frozen=True, slots=True)
class ActiveReviewAssignment:
    """Resolved active assignment for guard lookup (under lock)."""

    assignment_id: ReviewAssignmentId
    tenant_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    reviewer_principal_id: UUID
    issuer_principal_id: UUID
    class_ref: str
    issued_at: datetime
    valid_until: datetime
    revoked: bool