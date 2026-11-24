"""SQLAlchemy adapter for security.authorization_denial_records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid7

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.review_assignment_denial_audit import (
    AssignmentDenialRecord,
)


class SqlAlchemyAuthorizationDenialRecords:
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