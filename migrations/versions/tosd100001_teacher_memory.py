"""TOS-DEV10-I03 Teacher Memory v1 — table, RLS, and audit vocabulary.

Creates teaching.teacher_memories (durable teacher preference profile) and
extends security.audit_records CHECK vocabulary for memory create/update.

Deliberately absent:
  * Continuous Context / chat history / learner data
  * automatic preference inference
  * principal_kind column / represented-principal GUC (not yet in runtime)
  * DELETE grant / soft-delete lifecycle

Revision ID: tosd100001
Revises: tosd090002
Create Date: 2026-09-06
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "tosd100001"
down_revision: str | None = "tosd090002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_DOWNGRADE_BLOCKED_TABLE = (
    "TOS-DEV10-I03 downgrade refused: Teacher Memory evidence exists "
    "and must not be deleted"
)
_DOWNGRADE_BLOCKED_AUDIT = (
    "TOS-DEV10-I03 downgrade refused: Teacher Memory security audit "
    "evidence exists and must not be deleted or rewritten"
)

_CONTENT_ACTIONS_SQL = """
                    'content.create',
                    'content.version.create',
                    'content.review.submit',
                    'content.review.approve',
                    'content.review.request_changes',
                    'content.review.reject',
                    'content.publish',
                    'content.ai.materialize',
                    'content.migration.import'
"""
_ASSET_ACTIONS_SQL = """
                    'asset.create',
                    'asset.revision.register',
                    'asset.revision.activate',
                    'asset.lifecycle.withdraw',
                    'asset.lifecycle.restore',
                    'asset.lifecycle.delete',
                    'asset.quarantine.set',
                    'asset.quarantine.clear',
                    'asset.safety.pass',
                    'asset.safety.fail'
"""
_TEACHING_BASE_ACTIONS_SQL = """
                    'teaching.assignment.create',
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel',
                    'teaching.execution.start',
                    'teaching.execution.complete',
                    'teaching.execution.cancel',
                    'teaching.execution.observation.create',
                    'teaching.execution.observation.correct',
                    'teaching.work.remediation.create'
"""
_MEMORY_ACTIONS_SQL = """
                    'teaching.memory.create',
                    'teaching.memory.update'
"""
_ASSESSMENT_ACTIONS_SQL = """
                    'assessment.classroom.record',
                    'assessment.classroom.correct',
                    'assessment.classroom.void'
"""
_CONTENT_INCREMENT_SQL = """
                    'content.version.create',
                    'content.review.submit',
                    'content.review.approve',
                    'content.review.request_changes',
                    'content.review.reject',
                    'content.publish',
                    'content.ai.materialize'
"""
_ASSET_INCREMENT_SQL = """
                    'asset.revision.activate',
                    'asset.lifecycle.withdraw',
                    'asset.lifecycle.restore',
                    'asset.lifecycle.delete',
                    'asset.quarantine.set',
                    'asset.quarantine.clear',
                    'asset.safety.pass',
                    'asset.safety.fail'
"""
_TEACHING_CREATE_SQL = """
                    'teaching.assignment.create',
                    'teaching.execution.start',
                    'teaching.execution.observation.create',
                    'teaching.work.remediation.create'
"""
_TEACHING_INCREMENT_SQL = """
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel',
                    'teaching.execution.complete',
                    'teaching.execution.cancel',
                    'teaching.execution.observation.correct'
"""
_MEMORY_CREATE_SQL = """
                    'teaching.memory.create'
"""
_MEMORY_INCREMENT_SQL = """
                    'teaching.memory.update'
"""
_ASSESSMENT_CREATE_SQL = """
                    'assessment.classroom.record'
"""
_ASSESSMENT_INCREMENT_SQL = """
                    'assessment.classroom.correct',
                    'assessment.classroom.void'
"""


def _joined(*parts: str) -> str:
    return ",\n".join(part.strip("\n") for part in parts)


_BASE_ACTIONS_SQL = _joined(
    _CONTENT_ACTIONS_SQL,
    _ASSET_ACTIONS_SQL,
    _TEACHING_BASE_ACTIONS_SQL,
    _ASSESSMENT_ACTIONS_SQL,
)
_UPGRADE_ACTIONS_SQL = _joined(_BASE_ACTIONS_SQL, _MEMORY_ACTIONS_SQL)
_BASE_PRIMARY_ACTIONS_SQL = _joined(
    _CONTENT_ACTIONS_SQL, _TEACHING_BASE_ACTIONS_SQL, _ASSESSMENT_ACTIONS_SQL
)
_UPGRADE_PRIMARY_ACTIONS_SQL = _joined(
    _BASE_PRIMARY_ACTIONS_SQL, _MEMORY_ACTIONS_SQL
)


def _require_role(env_name: str, *, purpose: str) -> str:
    role = os.environ.get(env_name, "").strip()
    if not role:
        raise RuntimeError(
            f"{env_name} must be set to the {purpose}; Alembic will not "
            "silently alter security objects as the migrator or content owner."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(
            f"{env_name} must be a lowercase unquoted PostgreSQL identifier"
        )
    return role


def _constraint_statements(
    *,
    actions_sql: str,
    primary_actions_sql: str,
    include_memory: bool,
) -> tuple[str, ...]:
    memory_create_clause = ""
    memory_increment_clause = ""
    if include_memory:
        memory_create_clause = f"""
                OR (action IN ({_MEMORY_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)"""
        memory_increment_clause = f"""
                OR (action IN ({_MEMORY_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)"""
    return (
        "ALTER TABLE security.audit_records DROP CONSTRAINT ck_audit_records_action",
        f"""
        ALTER TABLE security.audit_records
            ADD CONSTRAINT ck_audit_records_action
            CHECK (action IN (
{actions_sql}
            ))
        """,
        """
        ALTER TABLE security.audit_records
            DROP CONSTRAINT ck_audit_records_primary_revision_family
        """,
        f"""
        ALTER TABLE security.audit_records
            ADD CONSTRAINT ck_audit_records_primary_revision_family
            CHECK (
                (
                    action IN (
{primary_actions_sql}
                    )
                    AND primary_resource_revision IS NOT NULL
                    AND primary_resource_revision = resource_revision_after
                )
                OR (
                    action IN (
{_ASSET_ACTIONS_SQL}
                    )
                    AND primary_resource_revision IS NULL
                )
            )
        """,
        """
        ALTER TABLE security.audit_records
            DROP CONSTRAINT ck_audit_records_revision_semantics
        """,
        f"""
        ALTER TABLE security.audit_records
            ADD CONSTRAINT ck_audit_records_revision_semantics
            CHECK (
                (action = 'content.create'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action = 'content.migration.import'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 1)
                OR (action IN ({_CONTENT_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action = 'asset.create'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action = 'asset.revision.register'
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before)
                OR (action IN ({_ASSET_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({_TEACHING_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({_TEACHING_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({_ASSESSMENT_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({_ASSESSMENT_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
{memory_create_clause}
{memory_increment_clause}
            )
        """,
    )


TABLE_UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE teaching.teacher_memories (
        memory_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        teacher_principal_id UUID NOT NULL,
        schema_version INTEGER NOT NULL,
        preferences JSONB NOT NULL,
        aggregate_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_teaching_teacher_memories
            PRIMARY KEY (memory_id),
        CONSTRAINT uq_teaching_teacher_memories_tenant_teacher
            UNIQUE (tenant_id, teacher_principal_id),
        CONSTRAINT uq_teaching_teacher_memories_tenant_memory
            UNIQUE (tenant_id, memory_id),
        CONSTRAINT ck_teaching_teacher_memories_aggregate_revision_nonnegative
            CHECK (aggregate_revision >= 0),
        CONSTRAINT ck_teaching_teacher_memories_schema_version
            CHECK (schema_version = 1),
        CONSTRAINT ck_teaching_teacher_memories_preferences_object
            CHECK (jsonb_typeof(preferences) = 'object'),
        CONSTRAINT ck_teaching_teacher_memories_updated_after_created
            CHECK (updated_at >= created_at)
    )
    """,
    """
    CREATE INDEX ix_teaching_teacher_memories_tenant_teacher
        ON teaching.teacher_memories (tenant_id, teacher_principal_id)
    """,
    "ALTER TABLE teaching.teacher_memories ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE teaching.teacher_memories FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY teaching_teacher_memories_tenant_isolation
        ON teaching.teacher_memories
        FOR ALL
        USING (tenant_id = teaching.current_tenant_id())
        WITH CHECK (tenant_id = teaching.current_tenant_id())
    """,
)

TABLE_DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    """
    DROP POLICY IF EXISTS teaching_teacher_memories_tenant_isolation
        ON teaching.teacher_memories
    """,
    "DROP TABLE IF EXISTS teaching.teacher_memories",
)

UPGRADE_AUDIT_STATEMENTS = _constraint_statements(
    actions_sql=_UPGRADE_ACTIONS_SQL,
    primary_actions_sql=_UPGRADE_PRIMARY_ACTIONS_SQL,
    include_memory=True,
)
DOWNGRADE_AUDIT_STATEMENTS = _constraint_statements(
    actions_sql=_BASE_ACTIONS_SQL,
    primary_actions_sql=_BASE_PRIMARY_ACTIONS_SQL,
    include_memory=False,
)


def upgrade() -> None:
    for statement in TABLE_UPGRADE_STATEMENTS:
        op.execute(statement)

    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV, purpose="security schema-owner role"
    )
    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in UPGRADE_AUDIT_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")


def downgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV, purpose="security schema-owner role"
    )

    op.execute(f"SET LOCAL ROLE {security_owner}")
    op.execute("ALTER TABLE security.audit_records DISABLE ROW LEVEL SECURITY")
    blocked_audit = bool(
        op.get_bind()
        .execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM security.audit_records
                    WHERE action IN (
                        'teaching.memory.create',
                        'teaching.memory.update'
                    )
                )
                """
            )
        )
        .scalar()
    )
    if blocked_audit:
        op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
        op.execute(f"SET LOCAL ROLE {content_owner}")
        raise RuntimeError(_DOWNGRADE_BLOCKED_AUDIT)
    for statement in DOWNGRADE_AUDIT_STATEMENTS:
        op.execute(statement)
    op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
    op.execute(f"SET LOCAL ROLE {content_owner}")

    bind = op.get_bind()
    table_exists = bind.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'teaching'
                  AND table_name = 'teacher_memories'
            )
            """
        )
    ).scalar()
    if table_exists:
        op.execute(
            "ALTER TABLE teaching.teacher_memories DISABLE ROW LEVEL SECURITY"
        )
        blocked = bind.execute(
            text("SELECT EXISTS (SELECT 1 FROM teaching.teacher_memories)")
        ).scalar()
        if blocked:
            op.execute(
                "ALTER TABLE teaching.teacher_memories ENABLE ROW LEVEL SECURITY"
            )
            op.execute(
                "ALTER TABLE teaching.teacher_memories FORCE ROW LEVEL SECURITY"
            )
            raise RuntimeError(_DOWNGRADE_BLOCKED_TABLE)
    for statement in TABLE_DOWNGRADE_STATEMENTS:
        op.execute(statement)
