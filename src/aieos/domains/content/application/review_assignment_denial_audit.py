"""Independent security denial evidence for assignment commands (ADR-O64-05)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid7

from sqlalchemy import text
from sqlalchemy.engine import Engine

CONTENT_REVIEW_ASSIGN_DENIED = "content.review.assign.denied"
CONTENT_REVIEW_REVOKE_DENIED = "content.review.revoke.denied"


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


def record_assignment_denial(
    denial_audit: ReviewAssignmentDenialAuditPort | None,
    record: AssignmentDenialRecord,
) -> None:
    """Append denial evidence outside rolled-back business transactions; never mask forbid."""
    if denial_audit is None:
        return
    try:
        denial_audit.record(record)
    except Exception:
        return


class SqlAlchemyReviewAssignmentDenialAudit:
    """Append-only security.authorization_denial_records writer."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, record: AssignmentDenialRecord) -> None:
        occurred_at = datetime.now(UTC)
        with self._engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(record.tenant_id)},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO security.authorization_denial_records (
                        denial_record_id,
                        tenant_id,
                        action,
                        principal_id,
                        content_id,
                        version_id,
                        correlation_id,
                        reason_code,
                        occurred_at
                    ) VALUES (
                        :denial_record_id,
                        :tenant_id,
                        :action,
                        :principal_id,
                        :content_id,
                        :version_id,
                        :correlation_id,
                        :reason_code,
                        :occurred_at
                    )
                    """
                ),
                {
                    "denial_record_id": uuid7(),
                    "tenant_id": record.tenant_id,
                    "action": record.action,
                    "principal_id": record.principal_id,
                    "content_id": record.content_id,
                    "version_id": record.version_id,
                    "correlation_id": record.correlation_id,
                    "reason_code": record.reason_code[:64],
                    "occurred_at": occurred_at,
                },
            )