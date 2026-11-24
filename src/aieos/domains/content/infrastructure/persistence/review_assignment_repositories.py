"""SQLAlchemy persistence for reviewer assignment issuances and current slot."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    ReviewAssignmentId,
)
from aieos.domains.content.domain.review_assignment import (
    ReviewAssignmentAssignedEvent,
    ReviewAssignmentRevokedEvent,
    ReviewCurrentAssignmentSlot,
)
from aieos.domains.content.infrastructure.persistence.mapping import (
    review_assignment_assigned_from_row,
    review_current_slot_from_row,
)
from aieos.domains.content.infrastructure.persistence.models import (
    review_assignment_issuances_table,
    review_assignment_revocations_table,
    review_current_assignments_table,
)


class SqlAlchemyReviewAssignmentRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert_assigned(self, event: ReviewAssignmentAssignedEvent) -> None:
        self._connection.execute(
            insert(review_assignment_issuances_table).values(
                assignment_id=event.assignment_id.value,
                tenant_id=event.tenant_id,
                content_id=event.content_id.value,
                version_id=event.version_id.value,
                reviewer_principal_id=event.reviewer_principal_id,
                issuer_principal_id=event.issuer_principal_id,
                class_ref=event.class_ref,
                issued_at=event.issued_at,
                valid_until=event.valid_until,
                correlation_id=event.correlation_id,
            )
        )

    def insert_revoked(self, event: ReviewAssignmentRevokedEvent) -> None:
        self._connection.execute(
            insert(review_assignment_revocations_table).values(
                revocation_id=event.revocation_id,
                assignment_id=event.assignment_id.value,
                tenant_id=event.tenant_id,
                content_id=event.content_id.value,
                version_id=event.version_id.value,
                revoked_at=event.revoked_at,
                revoked_by_principal_id=event.revoked_by_principal_id,
                correlation_id=event.correlation_id,
            )
        )

    def get_assigned(
        self, assignment_id: ReviewAssignmentId
    ) -> ReviewAssignmentAssignedEvent | None:
        row = self._connection.execute(
            select(review_assignment_issuances_table).where(
                review_assignment_issuances_table.c.assignment_id == assignment_id.value
            )
        ).one_or_none()
        if row is None:
            return None
        return review_assignment_assigned_from_row(row)

    def has_revoked(self, assignment_id: ReviewAssignmentId) -> bool:
        row = self._connection.execute(
            select(review_assignment_revocations_table.c.revocation_id).where(
                review_assignment_revocations_table.c.assignment_id == assignment_id.value
            )
        ).one_or_none()
        return row is not None

    def has_live_issuance_for_version(
        self,
        content_id: ContentId,
        version_id: ContentVersionId,
        *,
        now: datetime,
    ) -> bool:
        return bool(self.live_issuance_ids_for_version(content_id, version_id, now=now))

    def live_issuance_ids_for_version(
        self,
        content_id: ContentId,
        version_id: ContentVersionId,
        *,
        now: datetime,
    ) -> tuple[ReviewAssignmentId, ...]:
        rows = self._connection.execute(
            select(review_assignment_issuances_table.c.assignment_id)
            .select_from(review_assignment_issuances_table)
            .outerjoin(
                review_assignment_revocations_table,
                review_assignment_revocations_table.c.assignment_id
                == review_assignment_issuances_table.c.assignment_id,
            )
            .where(
                review_assignment_issuances_table.c.content_id == content_id.value,
                review_assignment_issuances_table.c.version_id == version_id.value,
                review_assignment_issuances_table.c.valid_until > now,
                review_assignment_revocations_table.c.revocation_id.is_(None),
            )
        ).scalars().all()
        return tuple(ReviewAssignmentId(value) for value in rows)

    def get_slot_for_update(
        self,
        content_id: ContentId,
        version_id: ContentVersionId,
    ) -> ReviewCurrentAssignmentSlot | None:
        row = self._connection.execute(
            select(review_current_assignments_table)
            .where(
                review_current_assignments_table.c.content_id == content_id.value,
                review_current_assignments_table.c.version_id == version_id.value,
            )
            .with_for_update()
        ).one_or_none()
        if row is None:
            return None
        return review_current_slot_from_row(row)

    def ensure_slot(
        self,
        *,
        tenant_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
        now: datetime,
    ) -> ReviewCurrentAssignmentSlot:
        existing = self.get_slot_for_update(content_id, version_id)
        if existing is not None:
            return existing
        self._connection.execute(
            insert(review_current_assignments_table).values(
                tenant_id=tenant_id,
                content_id=content_id.value,
                version_id=version_id.value,
                active_assignment_id=None,
                updated_at=now,
            )
        )
        row = self._connection.execute(
            select(review_current_assignments_table)
            .where(
                review_current_assignments_table.c.content_id == content_id.value,
                review_current_assignments_table.c.version_id == version_id.value,
            )
            .with_for_update()
        ).one()
        return review_current_slot_from_row(row)

    def set_active_assignment(
        self,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        active_assignment_id: ReviewAssignmentId | None,
        now: datetime,
    ) -> None:
        self._connection.execute(
            update(review_current_assignments_table)
            .where(
                review_current_assignments_table.c.content_id == content_id.value,
                review_current_assignments_table.c.version_id == version_id.value,
            )
            .values(
                active_assignment_id=(
                    None if active_assignment_id is None else active_assignment_id.value
                ),
                updated_at=now,
            )
        )